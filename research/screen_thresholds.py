"""Offline minute-quote sensitivity screen. Not an exchange-execution backtest."""
from pathlib import Path
import csv
import datetime as dt
from decimal import Decimal,InvalidOperation
import gzip
import hashlib
import json
import math
import os
import random
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parent.parent
DATA=ROOT/'research/threshold_data'
QTY=10
MODES=('touch_limit','next_open_recovery')


def price(value):
    try:n=Decimal(str(value))*10000
    except (InvalidOperation,TypeError,ValueError):raise ValueError('Missing or invalid quote') from None
    if not n.is_finite() or n!=int(n) or not 0<=n<=10000:raise ValueError('Invalid fixed-point quote')
    return int(n)


def fee(p,qty=QTY):
    return (7*qty*p*(10000-p)+999999)//1000000


def quotes(market,payload):
    candles={v['end_period_ts']:v for v in payload['candlesticks']};bars=[]
    for remaining in (240,180,120,60):
        c=candles[int(market['close'])-remaining+60];y=c['yes_bid'];a=c['yes_ask']
        yo,ao,yl,yh,al,ah=map(price,(y['open_dollars'],a['open_dollars'],y['low_dollars'],y['high_dollars'],a['low_dollars'],a['high_dollars']))
        if not (yl<=yo<=yh and al<=ao<=ah and yo<=ao):raise ValueError('Incoherent candle')
        bars.append({'remaining':remaining,'yes':{'ask':ao,'bid':yo,'low':yl,'high':yh},'no':{'ask':10000-yo,'bid':10000-ao,'low':10000-ah,'high':10000-al}})
    return {**market,'bars':bars}


def simulate(market,low,high,stop,mode,window=240):
    lo,hi,limit=low*100,high*100,stop*100
    held=None;basis=0;latched=False;net=0;buys=0;wins=0;losses=0;exits=0;triggers=0;unfilled=0;previous=None
    def close(p):
        nonlocal held,basis,latched,net,wins,losses,exits
        pnl=QTY*p-fee(p)-basis;net+=pnl;wins+=pnl>0;losses+=pnl<0;held=None;basis=0;latched=False;exits+=1
    for bar in market['bars']:
        if held is None:
            if not 30<bar['remaining']<=window:continue
            candidates=[s for s in ('yes','no') if lo<=bar[s]['ask']<=hi]
            tie=('no' if previous=='yes' else 'yes') if previous else ('yes','no')[hashlib.sha256(market['ticker'].encode()).digest()[0]%2]
            candidates.sort(key=lambda s:(bar[s]['ask'],s!=tie))
            if not candidates:continue
            held=candidates[0];previous=held;p=bar[held]['ask'];basis=QTY*p+fee(p);buys+=1
        b=bar[held]
        if stop and not latched and b['bid']<=limit:latched=True;triggers+=1
        if latched and b['bid']>=limit:
            close(b['bid']);continue
        if stop and not latched and b['low']<=limit:latched=True;triggers+=1
        if latched and mode=='touch_limit' and (b['bid']>=limit or b['high']>=limit):
            close(limit)
        # One open-time decision per minute. No same-minute re-entry after a sale.
    if held:
        payout=QTY*10000 if held==market['result'] else 0;pnl=payout-basis;net+=pnl;wins+=pnl>0;losses+=pnl<0
        if latched:unfilled=1
    return (net,buys,wins,losses,triggers,exits,unfilled)


def stats(values):
    net=sum(v[0] for v in values);cash=0;peak=0;drawdown=0
    for v in values:
        cash+=v[0];peak=max(peak,cash);drawdown=max(drawdown,peak-cash)
    return {'net_dollars':net/10000,'max_drawdown_dollars':drawdown/10000,'worst_market_dollars':min((v[0]/10000 for v in values),default=0),'buy_cycles':sum(v[1] for v in values),'winning_cycles':sum(v[2] for v in values),'losing_cycles':sum(v[3] for v in values),'stop_triggers':sum(v[4] for v in values),'completed_sales':sum(v[5] for v in values),'settled_with_unfilled_stop':sum(v[6] for v in values),'markets_traded':sum(v[1]>0 for v in values),'markets':len(values)}


def choose(candidates,baseline):
    # Only TRAIN and VALIDATION summaries are accepted; held-out values cannot
    # influence parameter selection. Keep the baseline if no robust candidate.
    eligible=[]
    for key,v in candidates.items():
        if key[2]==0:continue  # No-stop variants are benchmarks, never protected candidates.
        if all(v['train'][m]['net_dollars']>=baseline['train'][m]['net_dollars']-1e-9 and v['train'][m]['max_drawdown_dollars']<=baseline['train'][m]['max_drawdown_dollars']+1e-9 and v['train'][m]['buy_cycles']>=min(20,baseline['train'][m]['buy_cycles']//2) for m in MODES):eligible.append(key)
    eligible.sort(key=lambda k:min(candidates[k]['train'][m]['net_dollars']-baseline['train'][m]['net_dollars'] for m in MODES),reverse=True)
    finalists=eligible  # Validate every training-qualified setting; similar variants must not crowd out other buy bands.
    acceptable=[k for k in finalists if all(candidates[k]['validation'][m]['net_dollars']>=baseline['validation'][m]['net_dollars']-1e-9 and candidates[k]['validation'][m]['max_drawdown_dollars']<=baseline['validation'][m]['max_drawdown_dollars']+1e-9 for m in MODES)]
    if not acceptable:return (95,99,71),finalists
    acceptable.sort(key=lambda k:(min(candidates[k]['validation'][m]['net_dollars']-baseline['validation'][m]['net_dollars'] for m in MODES),min(candidates[k]['train'][m]['net_dollars']-baseline['train'][m]['net_dollars'] for m in MODES)),reverse=True)
    return acceptable[0],finalists


def bootstrap(candidate,baseline,seed=20261114):
    differences=[(a[0]-b[0])/10000 for a,b in zip(candidate,baseline)];n=len(differences)
    if not n:return None
    rng=random.Random(seed);values=[]
    for _ in range(2000):
        draw=[]
        while len(draw)<n:
            start=rng.randrange(n);draw.extend(differences[(start+j)%n] for j in range(4))
        values.append(sum(draw[:n]))
    values.sort();return {'paired_net_difference_dollars':sum(differences),'moving_block_markets':4,'bootstrap_samples':2000,'interval_95_percent_dollars':[values[49],values[1949]]}


def main():
    os.umask(0o077);manifest=json.loads((DATA/'manifest.json').read_text())
    if manifest.get('missing'):raise RuntimeError('Collection incomplete')
    markets=[];invalid=[]
    for m in manifest['markets']:
        try:
            with gzip.open(DATA/(m['ticker']+'.json.gz'),'rt') as f:markets.append(quotes(m,json.load(f)))
        except (KeyError,ValueError,TypeError,OSError) as exc:invalid.append({'ticker':m['ticker'],'reason':type(exc).__name__})
    n=len(markets);a=int(n*.6);b=int(n*.8)
    if n<50:raise RuntimeError('Insufficient quote coverage')
    bands=[(lo,hi) for lo in range(90,100) for hi in range(max(95,lo),100)]
    configs=[(lo,hi,stop) for lo,hi in bands for stop in list(range(50,91))+[0] if not stop or stop<lo]
    basekey=(95,99,71);summaries={};series={}
    for key in configs:
        lo,hi,stop=key;per={m:[simulate(v,lo,hi,stop,m) for v in markets] for m in MODES};series[key]=per
        summaries[key]={'train':{m:stats(per[m][:a]) for m in MODES},'validation':{m:stats(per[m][a:b]) for m in MODES}}
    selected,finalists=choose(summaries,summaries[basekey])
    comparisons={}
    for label,key in (('current_baseline',basekey),('selected_before_holdout',selected),('old_86_stop',(95,99,86)),('hold_to_settlement_benchmark',(95,99,0))):
        per=series[key];comparisons[label]={'buy_min_cents':key[0],'buy_max_cents':key[1],'stop_and_sell_limit_cents':key[2],'train':summaries[key]['train'],'validation':summaries[key]['validation'],'holdout':{m:stats(per[m][b:]) for m in MODES},'whole_sample_descriptive':{m:stats(per[m]) for m in MODES}}
    baseline3={m:[simulate(v,95,99,71,m,180) for v in markets] for m in MODES}
    comparisons['three_minute_baseline']={'holdout':{m:stats(baseline3[m][b:]) for m in MODES},'whole_sample_descriptive':{m:stats(baseline3[m]) for m in MODES}}
    intervals={m:bootstrap(series[selected][m][b:],series[basekey][m][b:]) for m in MODES}
    robust=selected!=basekey and all(intervals[m]['interval_95_percent_dollars'][0]>0 and comparisons['selected_before_holdout']['holdout'][m]['max_drawdown_dollars']<=comparisons['current_baseline']['holdout'][m]['max_drawdown_dollars'] for m in MODES)
    result={'data':{'discovered_markets':len(manifest['markets']),'modeled_markets':n,'invalid_markets':invalid,'first_close':markets[0]['close'],'last_close':markets[-1]['close'],'public_requests':manifest.get('public_requests'),'train_markets':a,'validation_markets':b-a,'holdout_markets':n-b,'holdout_first_close':markets[b]['close']},'method':{'quantity_per_entry':QTY,'buy_checkpoints_seconds_remaining':[240,180,120,60],'closing_purchase_block_seconds':30,'quote_resolution_seconds':60,'fees':'Standard multiplier 1 taker fee on all buys and sells, rounded upward to $0.0001. No settlement fee. Event overrides not independently reconstructed.','touch_limit':'After a stop crossing, assume sufficient executable volume at the stop limit when the candle range permits a touch/recovery. Intraminute gaps and queue competition can invalidate this.','next_open_recovery':'Stop is latched from the candle low; exit only at a subsequent observed minute-opening bid at/above limit. No guaranteed sale below limit; unresolved positions settle.','selection':'Chronological 60% train / 20% validation / 20% untouched holdout. Rank train candidates improving profit without worsening drawdown in both execution assumptions; validate every training-qualified protected candidate; freeze selected candidate before holdout comparison.','cash':'Fixed ten-share entries, assumed funded; no account balance or compounding replay.','coverage':'All settled markets in the frozen contiguous discovery period, including markets never traded by the bot. No entries in final 30 seconds. Minute-only checkpoints miss some real opportunities and cannot reproduce subsecond execution or unlimited intraminute re-entry.'},'tested_configurations':len(configs),'comparisons':comparisons,'selected_vs_baseline_holdout_bootstrap':intervals,'candidate_passed_both_holdout_checks':robust,'training_finalists':[list(k) for k in finalists],'application_changes':False,'live_orders':0}
    (ROOT/'docs/THRESHOLD_STUDY.json').write_text(json.dumps(result,indent=2)+'\n')
    with (ROOT/'docs/THRESHOLD_TRAIN_VALIDATION.csv').open('w',newline='') as f:
        writer=csv.writer(f);writer.writerow(['buy_min','buy_max','stop','train_touch_net','train_recovery_net','validation_touch_net','validation_recovery_net'])
        for key,v in summaries.items():writer.writerow([*key,v['train'][MODES[0]]['net_dollars'],v['train'][MODES[1]]['net_dollars'],v['validation'][MODES[0]]['net_dollars'],v['validation'][MODES[1]]['net_dollars']])
    with (ROOT/'docs/THRESHOLD_MARKET_BASELINE.csv').open('w',newline='') as f:
        writer=csv.writer(f);writer.writerow(['ticker','close_pacific','result','split','baseline_touch_net','baseline_recovery_net','candidate_touch_net','candidate_recovery_net'])
        for i,m in enumerate(markets):writer.writerow([m['ticker'],dt.datetime.fromtimestamp(m['close'],ZoneInfo('America/Los_Angeles')).isoformat(),m['result'],'train' if i<a else 'validation' if i<b else 'holdout',series[basekey][MODES[0]][i][0]/10000,series[basekey][MODES[1]][i][0]/10000,series[selected][MODES[0]][i][0]/10000,series[selected][MODES[1]][i][0]/10000])
    print(json.dumps(result,indent=2),flush=True)

if __name__=='__main__':main()
