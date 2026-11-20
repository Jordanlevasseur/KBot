"""Render the completed public candle study and export reusable minute data."""
import csv,gzip,json,os,datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from threshold_10000 import ROOT,OUT,price

def main():
 os.umask(0o077);r=json.loads((ROOT/'docs/THRESHOLD_10000.json').read_text());meta=json.loads((OUT/'manifest.json').read_text());tz=ZoneInfo('America/Los_Angeles')
 def local(t):return datetime.datetime.fromtimestamp(t,tz).isoformat()
 pair_rows=[]
 with (ROOT/'docs/THRESHOLD_10000_PAIRS.csv').open() as f:
  for x in csv.DictReader(f):
   for k in ('won','definite_dip','possible_dip','confirmed_trade_recovery','first_touch_final_minute'):x[k]=x[k]=='True'
   x['touch_minute_end']=int(x['touch_minute_end']);pair_rows.append(x)
 first_summary={}
 for threshold in ('96.0','97.0','98.0'):
  groups={}
  for x in pair_rows:
   if x['threshold_cents']==threshold:groups.setdefault(x['ticker'],[]).append(x)
  first=[];ambiguous=0
  for values in groups.values():
   end=min(x['touch_minute_end'] for x in values);earliest=[x for x in values if x['touch_minute_end']==end]
   if len(earliest)>1:ambiguous+=1
   else:first.append(earliest[0])
  n=len(groups);lost=sum(not x['won'] for x in first);dipwin=sum(x['won'] and x['definite_dip'] for x in first)
  first_summary[threshold]={'qualifying_markets':n,'first_side_known':len(first),'first_side_order_unknown':ambiguous,'known_first_side_lost':lost,'flip_percent_lower':100*lost/n,'flip_percent_upper':100*(lost+ambiguous)/n,'known_first_side_won':sum(x['won'] for x in first),'confirmed_first_side_dipped_and_won':dipwin,'confirmed_dip_win_percent_of_all_qualifying':100*dipwin/n,'same_minute_only_dip_uncertain_known_first':sum(x['possible_dip'] and not x['definite_dip'] for x in first)}
 r['first_opportunity_per_market']=first_summary
 (ROOT/'docs/THRESHOLD_10000.json').write_text(json.dumps(r,indent=2))
 lines=['# Final-four-minute threshold study: latest 10,000 markets','',f"Completed markets selected: {r['selected_markets']}; valid analyzed markets: {r['valid_markets']}; excluded: {len(r['bad'])}.",f"First close: {local(r['first_close'])}; last close: {local(r['last_close'])}. Discovery anchored to Kalshi server Date: {r['server_date']}.",'','## What the counts mean','','Threshold means trade price at or above 96, 97, or 98 cents, not the Bitcoin dollar price. We examine the first qualifying minute inside the final four minutes, once per side per threshold per market. A side may already have reached the threshold before this window; this is not an assertion that its first lifetime crossing occurred here. YES and NO receive symmetric treatment. A market can contribute two side events if both sides qualify. These are overlapping cohorts, not independent experiments.','', 'Confirmed dip means a price below the threshold at the close of the touch minute, or a low below it in a later minute. The touch-minute low could have preceded the qualifying high and is not counted as an after-touch dip unless the close proves it. Thus confirmed-dip counts are conservative lower bounds. The separate uncertainty column counts cases where a same-minute dip is possible but not confirmed. High/low/close do not establish every intraminute path. Settlement direction, unlike path ordering, is known.','','## Main results','','| Price | Side events | Same side won | Opposite won | Confirmed dip then same side won | Confirmed dip then opposite won | Additional dip cases uncertain |','|---|---:|---:|---:|---:|---:|---:|']
 def cell(v):return f"{v['count']:,} ({v['percent']:.2f}%)"
 for t,s in r['summaries'].items():lines.append(f"| {t}¢ | {s['side_touches']:,} | {cell(s['wins'])} | {cell(s['opposite_won'])} | {cell(s['confirmed_dip_same_side_won'])} | {cell(s['confirmed_dip_opposite_won'])} | {cell(s['touch_minute_only_dip_uncertain'])} |")
 lines+=['','Percentages above use all qualifying side events at that threshold. “Dip and win” means the original side ultimately settled as winner, not necessarily that a trade returned to the threshold before close. Actual confirmed return trades are tabulated separately below.','','| Price | Confirmed dips | Of those dips, original side won | Of those dips, opposite won | Confirmed trade recovery after dip |','|---|---:|---:|---:|---:|']
 for t,s in r['summaries'].items():
  n=s['confirmed_dip']['count'];w=s['confirmed_dip_same_side_won']['count'];l=s['confirmed_dip_opposite_won']['count'];lines.append(f"| {t}¢ | {n:,} | {w:,} ({100*w/n:.2f}%) | {l:,} ({100*l/n:.2f}%) | {s['confirmed_dip_then_trade_recovery']['count']:,} |")
 lines+=['','## Depth after touching','','For each cell: total confirmed at-or-below events / eventual original-side winners / opposite-side winners. All are trade prices, not executable full-size bids.','', '| Low at or below | After 96¢ | After 97¢ | After 98¢ |','|---|---:|---:|---:|']
 for d in ('95','92','90','86','80','71','63','50','25','10'):
  cells=[]
  for t in ('96','97','98'):
   x=r['summaries'][t]['depth_counts'][d];cells.append(f"{x['at_or_below']} / {x['same_side_won']} / {x['opposite_won']}")
  lines.append('| '+d+'¢ | '+' | '.join(cells)+' |')
 lines+=['','## YES versus NO','','| Price | Side | Qualifying events | Opposite side won |','|---|---|---:|---:|']
 for t,sides in r['by_side'].items():
  for side,s in sides.items():lines.append(f"| {t}¢ | {side.upper()} | {s['side_touches']} | {cell(s['opposite_won'])} |")
 lines+=['','## Limits and use','','This is a descriptive candle study, not a trading backtest or profit forecast. It includes the last 30 seconds when your bot is not allowed to buy. Late inevitable winners can make settlement win rates look more favorable than an executable entry strategy. Price improvement, fees, latency, partial fills, queue position, depth, cash, orders and rebuys are not simulated. A threshold touch does not prove an entry at exactly that price could fill. Existing bid/ask, volume and open-interest candles are retained; historical full-depth order books and underlying Bitcoin tick paths are not provided by these candles. Do not interpret a traded-price crossing as proof a protective limit sell would execute. No settings, services, credentials or production journals were changed.','','Artifacts: THRESHOLD_10000.json (summaries, side splits, coverage and exclusions); THRESHOLD_10000_PAIRS.csv (each side/threshold event and confirmed/possible low); THRESHOLD_10000_MINUTES.csv (all final-four-minute candles, trade/quote ranges, volumes and open interest); research/threshold_10000_data (discovery and compressed raw public candles). Six synthetic analysis tests passed.','','Sources: [Kalshi batch candles](https://docs.kalshi.com/api-reference/market/batch-get-market-candlesticks), [historical markets](https://docs.kalshi.com/api-reference/historical/get-historical-markets).']
 first_lines=['## First qualifying side per market','','This view avoids counting a later winning opposite-side touch as a new success for the original opportunity. When both sides first qualify in the same minute, their order is unknown. Flip-rate ranges allocate those ambiguous cases to either outcome; they are data-order bounds, not statistical confidence intervals. Confirmed dip-and-win counts remain lower bounds.','','| Threshold | Markets | Known original side dipped and won | Known original side lost | Both-side first-touch order unknown | Flip-rate bounds |','|---|---:|---:|---:|---:|---:|']
 for t,x in first_summary.items():first_lines.append(f"| {float(t):.0f}¢ | {x['qualifying_markets']} | {x['confirmed_first_side_dipped_and_won']} ({x['confirmed_dip_win_percent_of_all_qualifying']:.2f}%) | {x['known_first_side_lost']} | {x['first_side_order_unknown']} | {x['flip_percent_lower']:.2f}–{x['flip_percent_upper']:.2f}% |")
 lines[2:2]=first_lines+['']
 (ROOT/'docs/THRESHOLD_10000.md').write_text('\n'.join(lines)+'\n')
 books={}
 for folder,pattern in [(ROOT/'research/volatility_15days','*.json.gz'),(OUT,'batch-*.json.gz')]:
  for p in folder.glob(pattern):
   for m in json.loads(gzip.decompress(p.read_bytes())).get('markets',[]):books[m['market_ticker']]=m['candlesticks']
 for p in OUT.glob('KX*.json.gz'):
  d=json.loads(gzip.decompress(p.read_bytes()));books[d['ticker']]=d['candlesticks']
 with (ROOT/'docs/THRESHOLD_10000_MINUTES.csv').open('w') as f:
  writer=None;count=0
  for m in meta['markets']:
   for b in sorted(books.get(m['ticker'],[]),key=lambda b:b['end_period_ts']):
    if not m['close']-240<b['end_period_ts']<=m['close']:continue
    row={'ticker':m['ticker'],'result':m['result'],'market_close_pacific':local(m['close']),'minute_end_pacific':local(b['end_period_ts']),'seconds_remaining_at_start':m['close']-b['end_period_ts']+60,'volume_fp':b.get('volume_fp'),'open_interest_fp':b.get('open_interest_fp')}
    for field in ('price','yes_bid','yes_ask'):
     for part in ('open','high','low','close'):
      try:row[field+'_'+part+'_cents']=price(b[field],part)/100
      except (KeyError,ValueError):row[field+'_'+part+'_cents']=None
    for field,other in [('no_bid','yes_ask'),('no_ask','yes_bid')]:
     for part in ('open','high','low','close'):
      inverse={'high':'low','low':'high'}.get(part,part)
      try:row[field+'_'+part+'_cents']=(10000-price(b[other],inverse))/100
      except (KeyError,ValueError):row[field+'_'+part+'_cents']=None
    if writer is None:writer=csv.DictWriter(f,fieldnames=list(row));writer.writeheader()
    writer.writerow(row);count+=1
 print('REPORT_WRITTEN','minute_rows',count)
if __name__=='__main__':main()
