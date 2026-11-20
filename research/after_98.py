"""Public historical 98c-touch study, no account access."""
import json,gzip,datetime,email.utils,collections,statistics,os
from pathlib import Path
from volatility_15days import get,ROOT

def main():
 os.umask(0o077)
 _,date=get('/exchange/status');now=email.utils.parsedate_to_datetime(date).timestamp();end=int(now)//900*900-900;start=end-1100*900
 markets=[];cursor=None
 while True:
  p={'series_ticker':'KXBTC15M','min_close_ts':start,'max_close_ts':end+1,'limit':200}
  if cursor:p['cursor']=cursor
  d,_=get('/markets',p);markets+=d['markets'];cursor=d.get('cursor')
  if not cursor:break
 def close(m):return int(datetime.datetime.fromisoformat(m['close_time'].replace('Z','+00:00')).timestamp())
 markets=sorted({m['ticker']:m for m in markets if m.get('result') in ('yes','no') and close(m)<=end}.values(),key=close)[-1000:]
 books={}
 for p in (ROOT/'research/volatility_15days').glob('*.json.gz'):
  d=json.loads(gzip.decompress(p.read_bytes()))
  for m in d['markets']:books[m['market_ticker']]=m['candlesticks']
 missing=[m for m in markets if m['ticker'] not in books]
 for i in range(0,len(missing),20):
  batch=missing[i:i+20];d,_=get('/markets/candlesticks',{'market_tickers':','.join(m['ticker'] for m in batch),'start_ts':close(batch[0])-900,'end_ts':close(batch[-1]),'period_interval':1})
  for m in d['markets']:books[m['market_ticker']]=m['candlesticks']
 out=[];bad=[]
 for m in markets:
  bars=sorted([b for b in books.get(m['ticker'],[]) if close(m)-900<b['end_period_ts']<=close(m)],key=lambda b:b['end_period_ts'])
  if len(bars)!=15:bad.append(m['ticker']);continue
  for side in ('yes','no'):
   rr=[]
   try:
    for b in bars:
     lo=float(b['price']['low_dollars']);hi=float(b['price']['high_dollars'])
     rr.append((hi if side=='yes' else 1-lo,lo if side=='yes' else 1-hi,b['end_period_ts']))
   except (ValueError,TypeError,KeyError):bad.append(m['ticker']);continue
   idx=next((i for i,r in enumerate(rr) if r[0]>=.98-1e-9),None)
   if idx is None:continue
   later=rr[idx+1:];low=min((r[1] for r in later),default=None);incl=min(r[1] for r in rr[idx:])
   out.append({'ticker':m['ticker'],'side':side,'result':m['result'],'won':side==m['result'],'touch_minute_end':rr[idx][2],'close':close(m),'later_low_cents':None if low is None else round(low*100,4),'touch_and_later_low_cents':round(incl*100,4)})
 later=[x for x in out if x['later_low_cents'] is not None];thresholds=[98,95,92,90,86,80,71,63,50,25,10]
 summary={'server_date':date,'markets':len(markets),'first_close':close(markets[0]),'last_close':close(markets[-1]),'bad':sorted(set(bad)),'markets_with_98':len({x['ticker'] for x in out}),'side_touches':len(out),'wins':sum(x['won'] for x in out),'losses':sum(not x['won'] for x in out),'both_sides_touched':sum(n==2 for n in collections.Counter(x['ticker'] for x in out).values()),'later_minutes_available':len(later),'first_touch_in_final_minute':len(out)-len(later),'later_drop_counts':{str(t):sum(x['later_low_cents']<t-1e-7 for x in later) for t in thresholds},'including_touch_minute_counts_order_uncertain':{str(t):sum(x['touch_and_later_low_cents']<t-1e-7 for x in out) for t in thresholds},'median_later_low':statistics.median(x['later_low_cents'] for x in later),'worst_later_low':min(x['later_low_cents'] for x in later),'rows':out}
 path=ROOT/'docs/AFTER_98_1000.json';path.write_text(json.dumps(summary,indent=2));print(json.dumps({k:v for k,v in summary.items() if k!='rows'}),flush=True)
 print('LOSING_TOUCHES',json.dumps([x for x in out if not x['won']]),flush=True)
if __name__=='__main__':main()
