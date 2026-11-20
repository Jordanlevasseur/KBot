"""Public-only candle study; never imports or changes the trading application."""
import csv,datetime,email.utils,gzip,json,os,statistics,time,urllib.error
from collections import defaultdict,Counter
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from volatility_15days import get,ROOT
OUT=ROOT/'research/threshold_10000_data'
def ts(x):return int(datetime.datetime.fromisoformat(x.replace('Z','+00:00')).timestamp())
def price(d,k):
 v=d.get(k+'_dollars',d.get(k))
 if v is None:raise ValueError('Missing price')
 n=int(Decimal(str(v))*10000)
 if not 0<=n<=10000:raise ValueError('Invalid price')
 return n

def analyze(bars,side,threshold,result):
 values=[]
 for b in bars:
  d=b['price'];lo,hi,cl=price(d,'low'),price(d,'high'),price(d,'close')
  if lo>hi or not lo<=cl<=hi:raise ValueError('Invalid OHLC')
  values.append((hi,lo,cl) if side=='yes' else (10000-lo,10000-hi,10000-cl))
 idx=next((i for i,v in enumerate(values) if v[0]>=threshold),None)
 if idx is None:return None
 # Touch-bar close is after its high. Touch-bar LOW may precede that high.
 lows=[values[idx][2]]+[v[1] for v in values[idx+1:]]
 low=min(lows);possible=min(v[1] for v in values[idx:]);dip=False;recovered=False
 for i in range(idx,len(values)):
  hi,lo,cl=values[i]
  if dip and hi>=threshold:recovered=True
  if i==idx:
   if cl<threshold:dip=True
  elif lo<threshold:
   dip=True
   if cl>=threshold:recovered=True
 return {'threshold_cents':threshold/100,'side':side,'won':side==result,'touch_minute_end':bars[idx]['end_period_ts'],'touch_index':idx,'definite_dip':low<threshold,'possible_dip':possible<threshold,'confirmed_low_cents':low/100,'possible_low_cents':possible/100,'confirmed_trade_recovery':recovered,'first_touch_final_minute':idx==3}

def summary(rows):
 n=len(rows);d=[r for r in rows if r['definite_dip']];amb=[r for r in rows if r['possible_dip'] and not r['definite_dip']]
 def rate(k):return {'count':k,'percent':100*k/n if n else None}
 return {'side_touches':n,'distinct_markets':len({r['ticker'] for r in rows}),'wins':rate(sum(r['won'] for r in rows)),'opposite_won':rate(sum(not r['won'] for r in rows)),'confirmed_dip':rate(len(d)),'confirmed_dip_same_side_won':rate(sum(r['won'] for r in d)),'confirmed_dip_opposite_won':rate(sum(not r['won'] for r in d)),'touch_minute_only_dip_uncertain':rate(len(amb)),'confirmed_dip_then_trade_recovery':rate(sum(r['confirmed_trade_recovery'] for r in rows)),'median_confirmed_low_cents':statistics.median(r['confirmed_low_cents'] for r in rows) if rows else None,'depth_counts':{str(c):{'at_or_below':sum(r['confirmed_low_cents']<=c for r in rows),'same_side_won':sum(r['confirmed_low_cents']<=c and r['won'] for r in rows),'opposite_won':sum(r['confirmed_low_cents']<=c and not r['won'] for r in rows)} for c in (95,92,90,86,80,71,63,50,25,10)}}

def main():
 os.umask(0o077);OUT.mkdir(exist_ok=True);manifest=OUT/'manifest.json'
 if manifest.exists():meta=json.loads(manifest.read_text());selected=meta['markets'];print('RESUME',len(selected),flush=True)
 else:
  _,date=get('/exchange/status');end=int(email.utils.parsedate_to_datetime(date).timestamp())//900*900-900
  selected=[];seen=set();start=end-130*86400
  for path in ('/markets','/historical/markets'):
   cursor=None
   while True:
    p={'series_ticker':'KXBTC15M','min_close_ts':start,'max_close_ts':end+1,'limit':1000 if path.startswith('/historical') else 200}
    if cursor:p['cursor']=cursor
    try:d,_=get(path,p)
    except urllib.error.HTTPError as e:
     print('DISCOVERY_ENDPOINT_ERROR',path,e.code,flush=True);break
    for m in d.get('markets',[]):
     if m['ticker'].startswith('KXBTC15M-') and m.get('result') in ('yes','no') and start<ts(m['close_time'])<=end and m['ticker'] not in seen:
      selected.append({'ticker':m['ticker'],'close':ts(m['close_time']),'result':m['result'],'source':path});seen.add(m['ticker'])
    cursor=d.get('cursor');print('DISCOVERY',path,len(selected),flush=True)
    if path.startswith('/historical') and len(selected)>=10000:
     boundary=sorted(m['close'] for m in selected)[-10000]
     page_closes=[ts(m['close_time']) for m in d.get('markets',[]) if m['ticker'].startswith('KXBTC15M-')]
     if page_closes and max(page_closes)<boundary:break
    if not cursor:break
   if len(selected)>=10000:break
  selected=sorted(selected,key=lambda m:m['close'])[-10000:];meta={'server_date':date,'requested_markets':10000,'markets':selected,'public_only':True};manifest.write_text(json.dumps(meta,indent=2));print('SELECTED',len(selected),flush=True)
 books={}
 for p in (ROOT/'research/volatility_15days').glob('*.json.gz'):
  for m in json.loads(gzip.decompress(p.read_bytes())).get('markets',[]):books[m['market_ticker']]=m['candlesticks']
 for p in OUT.glob('batch-*.json.gz'):
  for m in json.loads(gzip.decompress(p.read_bytes())).get('markets',[]):books[m['market_ticker']]=m['candlesticks']
 for p in OUT.glob('KX*.json.gz'):
  d=json.loads(gzip.decompress(p.read_bytes()));books[d['ticker']]=d['candlesticks']
 missing=[m for m in selected if m['ticker'] not in books];i=0;errors=[]
 while i<len(missing):
  batch=[missing[i]];j=i+1
  while j<len(missing) and len(batch)<25 and ((missing[j]['close']-batch[0]['close']+240)//60)*(len(batch)+1)<=9900:batch.append(missing[j]);j+=1
  try:
   d,_=get('/markets/candlesticks',{'market_tickers':','.join(m['ticker'] for m in batch),'start_ts':batch[0]['close']-240,'end_ts':batch[-1]['close'],'period_interval':1})
   (OUT/('batch-'+str(batch[0]['close'])+'.json.gz')).write_bytes(gzip.compress(json.dumps(d).encode()))
   for m in d.get('markets',[]):books[m['market_ticker']]=m['candlesticks']
  except urllib.error.HTTPError as e:errors.append({'tickers':[m['ticker'] for m in batch],'http_status':e.code})
  i=j
  if i%100<25 or i==len(missing):print('CANDLES',i,'/',len(missing),flush=True)
 rows=[];bad=[];valid=0
 for m in selected:
  bars=sorted([b for b in books.get(m['ticker'],[]) if m['close']-240<b['end_period_ts']<=m['close']],key=lambda b:b['end_period_ts'])
  if [b['end_period_ts'] for b in bars]!=list(range(m['close']-180,m['close']+1,60)):
   # Archives are fetched only when the current batch endpoint has no candles.
   try:
    d,_=get('/historical/markets/'+m['ticker']+'/candlesticks',{'start_ts':m['close']-240,'end_ts':m['close'],'period_interval':1});bars=sorted([b for b in d.get('candlesticks',[]) if m['close']-240<b['end_period_ts']<=m['close']],key=lambda b:b['end_period_ts']);(OUT/(m['ticker']+'.json.gz')).write_bytes(gzip.compress(json.dumps(d).encode()))
   except Exception as e:bad.append({'ticker':m['ticker'],'reason':type(e).__name__});continue
  if len(bars)!=4:bad.append({'ticker':m['ticker'],'reason':'Missing candles'});continue
  tmp=[]
  try:
   for side in ('yes','no'):
    for threshold in (9600,9700,9800):
     r=analyze(bars,side,threshold,m['result'])
     if r:tmp.append(dict(r,ticker=m['ticker'],close=m['close'],result=m['result']))
  except ValueError as e:bad.append({'ticker':m['ticker'],'reason':str(e)});continue
  rows+=tmp;valid+=1
 report={'server_date':meta['server_date'],'requested_markets':10000,'selected_markets':len(selected),'valid_markets':valid,'first_close':selected[0]['close'],'last_close':selected[-1]['close'],'bad':bad,'request_errors':errors,'window':'Final 240 seconds including final 30 seconds; not a bot-entry replay','trigger':'First one-minute trade high >= threshold within window, once per side per market; may have reached threshold before window','summaries':{str(t):summary([r for r in rows if r['threshold_cents']==t]) for t in (96,97,98)}}
 report['by_side']={str(t):{s:summary([r for r in rows if r['threshold_cents']==t and r['side']==s]) for s in ('yes','no')} for t in (96,97,98)}
 (ROOT/'docs/THRESHOLD_10000.json').write_text(json.dumps(report,indent=2))
 if rows:
  with (ROOT/'docs/THRESHOLD_10000_PAIRS.csv').open('w') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 print('COMPLETE',json.dumps({k:v for k,v in report.items() if k!='by_side'}),flush=True)
if __name__=='__main__':main()
