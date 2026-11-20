"""Public, unauthenticated research data only; never imports bot runtime or keys."""
import datetime as dt
import gzip
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.request
import urllib.parse
import urllib.error

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'research'/'threshold_data'
BASE='https://external-api.kalshi.com/trade-api/v2'


def main():
    os.umask(0o077);OUT.mkdir(exist_ok=True)
    journal=Path('/home/mint/.local/state/kalshi-bitcoin-bot/production-trading/desktop-trading.sqlite3')
    db=sqlite3.connect(journal.as_uri()+'?mode=ro',uri=True);db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
    first=db.execute('SELECT min(cutoff) FROM orders').fetchone()[0]
    latest=db.execute('SELECT max(at) FROM snapshots').fetchone()[0];last=int(latest)//900*900;db.close()
    calls=0;last_call=0.
    def get(path,params=None):
        nonlocal calls,last_call
        if not (path=='/markets' or path=='/series/KXBTC15M' or path.startswith('/series/KXBTC15M/markets/')):raise ValueError('Research endpoint denied')
        url=BASE+path+('?' + urllib.parse.urlencode(params) if params else '')
        for attempt in range(3):
            time.sleep(max(0,1-(time.monotonic()-last_call)));last_call=time.monotonic();calls+=1
            try:
                with urllib.request.urlopen(urllib.request.Request(url,headers={'Accept':'application/json'}),timeout=15) as response:raw=response.read(4000001)
                if len(raw)>4000000:raise ValueError('Oversized response')
                return json.loads(raw)
            except (OSError,urllib.error.URLError) as exc:
                if attempt==2:raise
                print('RETRY',path,type(exc).__name__,flush=True);time.sleep(2*(attempt+1))
    series=get('/series/KXBTC15M')['series'];markets=[];params={'series_ticker':'KXBTC15M','min_close_ts':int(first)-1,'max_close_ts':last+1,'limit':200};cursors=set()
    while True:
        page=get('/markets',params);markets.extend(page['markets']);cursor=page.get('cursor')
        if not cursor:break
        if cursor in cursors:raise ValueError('Repeated cursor')
        cursors.add(cursor);params['cursor']=cursor
    selected=[];excluded=[]
    def timestamp(value):return dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    for m in markets:
        ticker=m['ticker'];close=timestamp(m['close_time'])
        if not ticker.startswith('KXBTC15M-') or not first<=close<=last:continue
        if m.get('result') not in ('yes','no'):
            excluded.append({'ticker':ticker,'reason':'No confirmed binary settlement result'});continue
        selected.append({'ticker':ticker,'close':close,'result':m['result'],'event_ticker':m.get('event_ticker'),'status':m.get('status')})
    selected=sorted({m['ticker']:m for m in selected}.values(),key=lambda m:m['close'])
    manifest={'first_close':first,'last_close':last,'asof_saved_snapshot':latest,'expected_quarter_hour_closures':(last-first)//900+1,'series':{k:series.get(k) for k in ('ticker','fee_type','fee_multiplier')},'markets':selected,'excluded':excluded,'account_requests':0,'application_changes':False}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print('DISCOVERY',json.dumps({'markets':len(selected),'expected':manifest['expected_quarter_hour_closures'],'excluded':len(excluded),'first':first,'last':last}),flush=True)
    missing=[]
    for i,m in enumerate(selected,1):
        path=OUT/(m['ticker']+'.json.gz')
        if path.exists():
            with gzip.open(path,'rt') as f:value=json.load(f)
            if value.get('ticker')!=m['ticker']:raise ValueError('Cached ticker mismatch')
        else:
            try:
                value=get('/series/KXBTC15M/markets/'+m['ticker']+'/candlesticks',{'start_ts':int(m['close'])-240,'end_ts':int(m['close']),'period_interval':1})
                if value.get('ticker')!=m['ticker']:raise ValueError('Unexpected candle ticker')
                with gzip.open(path,'wt') as f:json.dump(value,f)
            except (OSError,ValueError) as exc:missing.append({'ticker':m['ticker'],'reason':type(exc).__name__});continue
        if i%10==0 or i==len(selected):print('CANDLES',i,'of',len(selected),flush=True)
    manifest.update(public_requests=calls,missing=missing);(OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('COMPLETE',json.dumps({'markets':len(selected),'requests':calls,'missing':missing}),flush=True)

if __name__=='__main__':main()
