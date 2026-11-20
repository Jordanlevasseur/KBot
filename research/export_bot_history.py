#!/usr/bin/env python3
"""Read-only, analysis-ready export of bot trading and volatility history."""
import csv, hashlib, json, os, sqlite3, sys, time
from collections import defaultdict

def ro(path):
    return sqlite3.connect('file:'+os.path.abspath(path)+'?immutable=1',uri=True)
def rows(db,sql,args=()):
    db.row_factory=sqlite3.Row
    return [dict(r) for r in db.execute(sql,args)]
def payload_rows(db,kind):
    out=[]
    for r in rows(db,'select id,at,payload from execution_events where kind=? order by at,id',(kind,)):
        try: d=json.loads(r['payload'])
        except Exception: d={}
        d.update(record_id=r['id'],observed_at=r['at'],event_kind=kind); out.append(d)
    return out
def write_csv(path,records):
    fields=list(records[0]) if records else ['record_id']
    with open(path,'w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(records)
def main():
    if len(sys.argv)!=3: raise SystemExit('usage: export_bot_history.py DB_PATH OUTPUT_DIR')
    source=os.path.abspath(sys.argv[1]); out=os.path.abspath(sys.argv[2]); os.makedirs(out,mode=0o700,exist_ok=True)
    diag_path=os.path.join(os.path.dirname(source),'volatility-diagnostics.sqlite3')
    trade=ro(source); diag=ro(diag_path) if os.path.exists(diag_path) else None
    manifest={'exported_at_utc':time.time(),'source_database':os.path.basename(source),'diagnostics_database':os.path.basename(diag_path) if diag else None,'schema_version':2,'files':{}}
    def save(name,records):
        path=os.path.join(out,name); write_csv(path,records); manifest['files'][name]={'rows':len(records),'sha256':hashlib.sha256(open(path,'rb').read()).hexdigest()}; print(path,len(records)); return records
    raw=[]
    if diag:
        has=diag.execute("select 1 from sqlite_master where name='volatility_history'").fetchone()
        raw=rows(diag,'select * from volatility_history') if has else payload_rows(diag,'volatility_reading')
    save('volatility_raw_observations.csv',raw)
    changes=[{'ticker':d.get('ticker'),'observed_at':d.get('at',d.get('observed_at')),'side':'yes','price_cents':d.get('price_cents',d.get('yes_price_cents')),'source':d.get('source','yes_ask')} for d in raw if d.get('price_cents',d.get('yes_price_cents')) is not None]
    save('volatility_price_changes.csv',changes)
    grouped=defaultdict(list)
    for d in raw:
        if d.get('ticker'): grouped[d['ticker']].append(d)
    summary=[]
    for ticker,items in grouped.items():
        valid=[x for x in items if x.get('market_range_cents') is not None]
        summary.append({'ticker':ticker,'observations':len(items),'valid_observations':len(valid),'missing_observations':sum((x.get('missing_count') or 0) for x in items),'max_market_range_cents':max((x.get('market_range_cents') or 0 for x in valid),default=None),'quality':'SUFFICIENT' if len(valid)>=30 else 'INSUFFICIENT'})
    save('volatility_market_summary.csv',summary)
    entry=[d for d in raw if d.get('seconds_remaining') is not None and 30<float(d['seconds_remaining'])<=240]
    save('volatility_entry_window.csv',entry)
    orders=rows(trade,'select * from orders order by intent_at,rowid')
    trades=[]
    for o in orders:
        if o.get('action')!='buy': continue
        candidates=[x for x in entry if x.get('ticker')==o.get('ticker') and float(x.get('observed_at',0))<=float(o.get('intent_at') or 0)]
        match=min(candidates,key=lambda x:abs(float(x.get('observed_at',0))-float(o.get('intent_at') or 0)),default={})
        trades.append({'client_id':o.get('client_id'),'ticker':o.get('ticker'),'side':o.get('side'),'intent_at':o.get('intent_at'),'price':o.get('price'),'quantity':o.get('quantity'),'status':o.get('status'),'filled':o.get('filled'),'volatility_at_entry':match.get('avg_1m_cents'),'volatility_quality':match.get('status','MISSING')})
    save('volatility_trade_analysis.csv',trades)
    quality=[{'category':'markets','value':len(grouped),'detail':'unique volatility tickers'},{'category':'sufficient_markets','value':sum(x['quality']=='SUFFICIENT' for x in summary),'detail':'at least 30 valid observations'},{'category':'entry_window_observations','value':len(entry),'detail':'31-240 seconds remaining'},{'category':'matched_trade_entries','value':sum(x['volatility_quality']!='MISSING' for x in trades),'detail':'pre-entry volatility available'},{'category':'unresolved_orders','value':sum(o['status'] not in ('executed','canceled','rejected','expired') for o in orders),'detail':'requires reconciliation'}]
    save('volatility_data_quality.csv',quality)
    summary_path=os.path.join(out,'volatility_diagnostics_summary.txt')
    open(summary_path,'w').write(f"Markets monitored: {len(grouped)}\nSufficient coverage: {sum(x['quality']=='SUFFICIENT' for x in summary)}\nEntry-window observations: {len(entry)}\nMatched trade entries: {sum(x['volatility_quality']!='MISSING' for x in trades)}\n\nDo not infer a profitable threshold unless matched pre-entry readings have sufficient coverage and confirmed fills.\n")
    dictionary=os.path.join(out,'data_dictionary.md'); open(dictionary,'w').write('# Bot research export\n\nTimestamps are Unix UTC seconds. Prices and ranges are cents. Window ranges are high-minus-low values. Average fields are averages of rolling 30-second ranges. Missing data is retained and never treated as zero.\n')
    manifest['files']['volatility_diagnostics_summary.txt']={'sha256':hashlib.sha256(open(summary_path,'rb').read()).hexdigest()}; manifest['files']['data_dictionary.md']={'sha256':hashlib.sha256(open(dictionary,'rb').read()).hexdigest()}
    json.dump(manifest,open(os.path.join(out,'export_manifest.json'),'w'),indent=2); print(os.path.join(out,'export_manifest.json')); trade.close(); diag and diag.close()
if __name__=='__main__': main()
