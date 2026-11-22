"""One-shot read-only account-history/BRTI study. No order or service operations."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from decimal import Decimal as D
import json
from pathlib import Path
import sqlite3
import threading
import time
import urllib.request
from urllib.parse import urlencode


def main():
    from kalshi_bot.credentials import load
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ed25519,padding
    signer=load('/home/mint/.config/kalshi-bitcoin-bot/production','production')
    dbpath=Path('/home/mint/.local/state/kalshi-bitcoin-bot/production-trading/desktop-trading.sqlite3')
    db=sqlite3.connect(dbpath.as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
    start=db.execute("select min(intent_at) from orders where ticker='KXBTC15M-26NOV200100-00' and action='buy' and cast(filled as real)>0").fetchone()[0]
    entries=[]
    for row in db.execute("select * from orders where action='buy' and cast(filled as real)>0 and intent_at>=? order by intent_at",(start,)):
        settlement=db.execute('select payload from settlements where ticker=?',(row['ticker'],)).fetchone()
        if not settlement or json.loads(settlement[0]).get('market_result')!=row['side']:continue
        fills=[]
        for f in db.execute('select payload from fills where order_id=?',(row['exchange_id'],)):
            f=json.loads(f[0]);fills.append(datetime.fromisoformat(f['created_time'].replace('Z','+00:00')).timestamp())
        if not fills:raise RuntimeError('No confirmed entry fill time')
        entry=min(fills)
        sold=db.execute("select count(*) from orders where ticker=? and action='sell' and cast(filled as real)>0",(row['ticker'],)).fetchone()[0]>0
        entries.append({'ticker':row['ticker'],'side':row['side'],'entry':entry,'close':row['cutoff'],'sold':sold})
    db.close()
    lock=threading.Lock();last=[0.]
    def get(path,params=None,authenticated=False):
        if not (path.startswith('/markets/KXBTC15M-') or path=='/cfbenchmarks/history/values'):raise ValueError('Audit endpoint denied')
        target='/trade-api/v2'+path
        for attempt in range(3):
            with lock:
                delay=.55-(time.monotonic()-last[0])
                if delay>0:time.sleep(delay)
                last[0]=time.monotonic()
            headers={}
            if authenticated:
                stamp=str(int(time.time()*1000));msg=(stamp+'GET'+target).encode();key=signer._key
                sig=key.sign(msg) if isinstance(key,ed25519.Ed25519PrivateKey) else key.sign(msg,padding.PSS(mgf=padding.MGF1(hashes.SHA256()),salt_length=32),hashes.SHA256())
                headers={'KALSHI-ACCESS-KEY':signer.key_id,'KALSHI-ACCESS-TIMESTAMP':stamp,'KALSHI-ACCESS-SIGNATURE':base64.b64encode(sig).decode()}
            request=urllib.request.Request('https://external-api.kalshi.com'+target+(' ?'+urlencode(params) if params else '').replace(' ?','?'),headers=headers,method='GET')
            try:
                with urllib.request.urlopen(request,timeout=35) as response:
                    raw=response.read(16*1024*1024+1)
                    if len(raw)>16*1024*1024:raise ValueError('Response too large')
                    return json.loads(raw)
            except Exception:
                if attempt==2:raise RuntimeError('Official historical data unavailable for '+path) from None
                time.sleep(2**attempt)
    hours=sorted({h for e in entries for h in range(int(e['entry']//3600)*3600,int((e['close']-0.001)//3600)*3600+1,3600)})
    def hour(h):
        x=get('/cfbenchmarks/history/values',{'id':'BRTI','timespan':'HOUR','timestamp':datetime.fromtimestamp(h,timezone.utc).strftime('%Y-%m-%dT%H:00:00.000Z')},True)
        points=x.get('data',x).get('payload',[])
        if not points:raise RuntimeError('Missing historical BRTI hour')
        return h,[(float(p['time'])/1000,D(p['value'])) for p in points]
    with ThreadPoolExecutor(max_workers=3) as pool:
        history=dict(pool.map(hour,hours))
        targets=dict(pool.map(lambda t:(t,D(str(get('/markets/'+t)['market']['floor_strike']))), sorted({e['ticker'] for e in entries})))
    print('HISTORY_HOURS',len(hours),'WINNING_ENTRIES',len(entries),flush=True)
    results=[]
    for e in entries:
        target=targets[e['ticker']];direction=1 if e['side']=='yes' else -1
        points=sorted(p for h,points in history.items() if h<=e['close'] and h+3600>=e['entry'] for p in points)
        before=[p for p in points if p[0]<=e['entry']]
        entry=before[-1] if before else min(points,key=lambda p:abs(p[0]-e['entry']))
        after=[p for p in points if e['entry']<=p[0]<e['close']]
        if not after:raise RuntimeError('No ticks after entry')
        minimum=min(after,key=lambda p:direction*(p[1]-target))
        closest=min(after,key=lambda p:abs(p[1]-target))
        r={**e,'target':str(target),'entry_btc':str(entry[1]),'entry_sample_age':e['entry']-entry[0],
           'entry_cushion':str(direction*(entry[1]-target)),
           'minimum_cushion':str(direction*(minimum[1]-target)),'minimum_at':minimum[0],
           'closest_absolute_distance':str(abs(closest[1]-target)),'closest_at':closest[0],
           'ticks':len(after),'max_gap_seconds':max([after[i][0]-after[i-1][0] for i in range(1,len(after))]+[after[0][0]-e['entry'],e['close']-after[-1][0]])}
        results.append(r);print(json.dumps(r),flush=True)
    print('SUMMARY',json.dumps({group:{'count':len(rs),'entered_under_35':sum(D(r['entry_cushion'])<35 for r in rs),'reached_20':sum(D(r['minimum_cushion'])<=20 for r in rs),'crossed_target':sum(D(r['minimum_cushion'])<0 for r in rs)} for group,rs in [('held',[r for r in results if not r['sold']]),('sold',[r for r in results if r['sold']])]}),flush=True)


if __name__=='__main__':main()
