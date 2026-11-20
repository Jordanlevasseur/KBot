"""Bounded public archive retrieval with shared pacing and 429 backoff."""
import concurrent.futures,gzip,json,os,threading,time,urllib.request,urllib.error,urllib.parse
from threshold_10000 import OUT,ROOT
BASE='https://external-api.kalshi.com/trade-api/v2'
lock=threading.Lock();next_at=0.;spacing=.2;rate_limits=0

def pace():
 global next_at
 with lock:
  now=time.monotonic();when=max(now,next_at);next_at=when+spacing
 time.sleep(max(0,when-time.monotonic()))
def fetch(m):
 global next_at,spacing,rate_limits
 path=OUT/(m['ticker']+'.json.gz')
 if path.exists():return None
 target='/historical/markets/'+m['ticker']+'/candlesticks'
 url=BASE+target+'?'+urllib.parse.urlencode({'start_ts':m['close']-240,'end_ts':m['close'],'period_interval':1})
 for attempt in range(7):
  pace()
  try:
   with urllib.request.urlopen(url,timeout=25) as r:d=json.load(r)
   if d.get('ticker')!=m['ticker']:raise ValueError('Ticker mismatch')
   tmp=path.with_suffix('.tmp');tmp.write_bytes(gzip.compress(json.dumps(d).encode()));tmp.replace(path);return None
  except urllib.error.HTTPError as e:
   if e.code not in (429,500,502,503,504):return {'ticker':m['ticker'],'http_status':e.code}
   if e.code==429:
    with lock:spacing=min(2,spacing*1.5);rate_limits+=1;next_at=max(next_at,time.monotonic()+min(30,2**attempt))
   time.sleep(min(30,2**attempt))
  except (OSError,ValueError):time.sleep(min(30,2**attempt))
 return {'ticker':m['ticker'],'reason':'Retries exhausted'}
def main():
 os.umask(0o077);meta=json.loads((OUT/'manifest.json').read_text());have=set()
 for folder,pattern in [(ROOT/'research/volatility_15days','*.json.gz'),(OUT,'batch-*.json.gz')]:
  for p in folder.glob(pattern):
   for m in json.loads(gzip.decompress(p.read_bytes())).get('markets',[]):
    if m.get('candlesticks'):have.add(m['market_ticker'])
 todo=[m for m in meta['markets'] if m['ticker'] not in have and not (OUT/(m['ticker']+'.json.gz')).exists()];print('ARCHIVE_PENDING',len(todo),flush=True);errors=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
  for i,error in enumerate(pool.map(fetch,todo),1):
   if error:errors.append(error)
   if i%100==0 or i==len(todo):print('ARCHIVE',i,'/',len(todo),'errors',len(errors),'rate_limits',rate_limits,'spacing',round(spacing,3),flush=True)
 (OUT/'archive_download_status.json').write_text(json.dumps({'errors':errors,'rate_limits':rate_limits,'minimum_spacing':spacing},indent=2));print('ARCHIVE_COMPLETE',len(errors),flush=True)
if __name__=='__main__':main()
