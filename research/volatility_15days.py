"""Unauthenticated public research; no bot imports or account access."""
import json,gzip,time,datetime,statistics,collections,urllib.request,urllib.parse,email.utils,os
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parent.parent;OUT=ROOT/'research/volatility_15days';BASE='https://external-api.kalshi.com/trade-api/v2';last=0

def get(path,params={}):
 global last
 for attempt in range(4):
  time.sleep(max(0,1-(time.monotonic()-last)));last=time.monotonic()
  try:
   with urllib.request.urlopen(BASE+path+'?'+urllib.parse.urlencode(params),timeout=25) as r:return json.load(r),r.headers.get('Date')
  except Exception:
   if attempt==3:raise
   time.sleep(2**attempt)
def stats(rows):
 xs=sorted(r['range'] for r in rows);n=len(xs)
 return {'minutes':n,'markets':len({r['ticker'] for r in rows}),'mean_cents':statistics.mean(xs),'median_cents':statistics.median(xs),'p95_cents':xs[min(n-1,int(.95*n))],'pct_range_at_least_20c':100*sum(x>=20 for x in xs)/n}
def main():
 os.umask(0o077);OUT.mkdir(exist_ok=True)
 _,date=get('/exchange/status');now=email.utils.parsedate_to_datetime(date).timestamp();end=int(now)//900*900-900;start=end-15*86400;markets=[];cursor=None
 while True:
  p={'series_ticker':'KXBTC15M','min_close_ts':start,'max_close_ts':end+1,'limit':200}
  if cursor:p['cursor']=cursor
  d,_=get('/markets',p);markets.extend(d['markets']);cursor=d.get('cursor')
  if not cursor:break
 selected=[]
 for m in markets:
  close=int(datetime.datetime.fromisoformat(m['close_time'].replace('Z','+00:00')).timestamp())
  if start<close<=end and m.get('result') in ('yes','no'):selected.append({'ticker':m['ticker'],'close':close})
 selected=sorted({m['ticker']:m for m in selected}.values(),key=lambda m:m['close']);(OUT/'manifest.json').write_text(json.dumps({'start_exclusive':start,'end_inclusive':end,'server_date':date,'expected_markets':1440,'markets':selected},indent=2));print('DISCOVERED',len(selected),'of expected 1440',flush=True)
 rows=[];missing=[]
 for i in range(0,len(selected),20):
  batch=selected[i:i+20];cache=OUT/(str(batch[0]['close'])+'.json.gz')
  if cache.exists():d=json.loads(gzip.decompress(cache.read_bytes()))
  else:
   d,_=get('/markets/candlesticks',{'market_tickers':','.join(m['ticker'] for m in batch),'start_ts':batch[0]['close']-900,'end_ts':batch[-1]['close'],'period_interval':1})
   cache.write_bytes(gzip.compress(json.dumps(d).encode()))
  books={m['market_ticker']:m['candlesticks'] for m in d['markets']}
  for m in batch:
   seen=set()
   for b in books.get(m['ticker'],[]):
    rem=m['close']-b['end_period_ts']+60
    if rem not in range(60,901,60) or b['end_period_ts'] in seen:continue
    seen.add(b['end_period_ts'])
    try:lo=float(b['price']['low_dollars']);hi=float(b['price']['high_dollars'])
    except (TypeError,KeyError):continue
    if not 0<=lo<=hi<=1:continue
    dt=datetime.datetime.fromtimestamp(b['end_period_ts']-60,ZoneInfo('America/Los_Angeles'))
    rows.append({'ticker':m['ticker'],'remaining':rem,'hour':dt.hour,'date':dt.strftime('%Y-%m-%d'),'range':(hi-lo)*100})
   if len(seen)!=15:missing.append({'ticker':m['ticker'],'bar_count':len(seen)})
  print('COLLECTED',min(i+20,len(selected)),'/',len(selected),flush=True)
 groups={}
 for label,subset in [('closing_four', [r for r in rows if r['remaining']<=240]),('full_fifteen',rows)]:
  hours=collections.defaultdict(list);mins=collections.defaultdict(list)
  for r in subset:hours[r['hour']].append(r);mins[r['remaining']].append(r)
  groups[label]={'hours':{str(h):stats(v) for h,v in hours.items()},'minutes_remaining':{str(m):stats(v) for m,v in mins.items()}}
 report={'start_exclusive':start,'end_inclusive':end,'server_date':date,'markets':len(selected),'expected_markets':1440,'valid_minutes':len(rows),'expected_minutes':len(selected)*15,'missing_bars':missing,'metric':'one-minute trade high minus low, cents; YES/NO complements have equal ranges','groups':groups}
 (ROOT/'docs/VOLATILITY_15D.json').write_text(json.dumps(report,indent=2));print('REPORT',json.dumps(report),flush=True)
if __name__=='__main__':main()
