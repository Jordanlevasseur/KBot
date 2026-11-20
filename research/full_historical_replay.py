#!/usr/bin/env python3
"""Read-only replay of archived Kalshi BTC 15-minute minute quotes.

This is deliberately a conservative research screen, not an execution claim:
an entry is assumed filled at a minute-opening executable ask; a stop is only
counted filled at a later minute-opening bid at/above the sell limit.  It never
opens the bot databases or makes network requests.
"""
from __future__ import annotations
import csv
from decimal import Decimal, ROUND_CEILING
import gzip
import json
from pathlib import Path
from statistics import median

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'research'/'threshold_10000_data'
QTY=10
WINDOWS=(180,240,300)
BUY_MINS=tuple(range(92,100))
# Representative whole-cent sell limits plus every currently/recently discussed
# level. A 43-level sweep is equivalent in method but too slow for laptop use.
STOPS=(50,55,60,63,65,70,71,75,80,85,86,90,92)


def cents(value):
    return int(Decimal(str(value))*10000)


def opening(quote):
    return quote.get('open_dollars',quote.get('open'))


def fee(price):
    """Taker fee in centicents, rounded upward per the published formula."""
    value=Decimal('0.07')*QTY*Decimal(price)/10000*(1-Decimal(price)/10000)
    return int((value*10000).to_integral_value(rounding=ROUND_CEILING))


def quotes(meta, payload):
    candles={c['end_period_ts']:c for c in payload['candlesticks']}
    bars=[]
    for remaining in range(900,0,-60):
        item=candles.get(int(meta['close'])-remaining+60)
        if item is None: continue
        try:
            ask=item['yes_ask'];bid=item['yes_bid']
            bars.append({'remaining':remaining,
                'yes_ask':cents(opening(ask)), 'yes_bid':cents(opening(bid)),
                'no_ask':10000-cents(opening(bid)), 'no_bid':10000-cents(opening(ask))})
        except (KeyError, ArithmeticError): return None
    return bars if len(bars)>=15 else None


def replay(market, buy_min, stop, window):
    """One first eligible entry, no re-entry. All amounts are centicents."""
    held=None;entry=0;cost=0;latched=False;stop_filled=False;stop_hit=False
    for bar in market['bars']:
        remaining=bar['remaining']
        if held is None:
            if 30 < remaining <= window:
                candidates=[side for side in ('yes','no') if buy_min*100<=bar[side+'_ask']<=9900]
                if candidates:
                    # Deterministic priority: higher implied probability, then YES.
                    held=sorted(candidates,key=lambda s:(-bar[s+'_ask'],s))[0]
                    entry=bar[held+'_ask'];cost=QTY*entry+fee(entry)
            continue
        bid=bar[held+'_bid']
        if bid<=stop*100: latched=True;stop_hit=True
        # Lower-bound confirmation: the observed *next-minute opening* bid
        # must still meet the sell limit. Same-minute candle touches do not count.
        if latched and bid>=stop*100:
            proceeds=QTY*bid-fee(bid)
            return {'pnl':proceeds-cost,'entered':True,'won':proceeds>cost,
                    'stop_hit':stop_hit,'stop_filled':True,'settled':False,
                    'entry_remaining':remaining,'entry_cents':entry/100}
    if held is None:return {'pnl':0,'entered':False}
    proceeds=QTY*10000 if held==market['result'] else 0
    return {'pnl':proceeds-cost,'entered':True,'won':proceeds>cost,
            'stop_hit':stop_hit,'stop_filled':False,'settled':True,
            'entry_remaining':next(b['remaining'] for b in market['bars'] if b[held+'_ask']==entry),
            'entry_cents':entry/100}


def aggregate(rows):
    equity=peak=drawdown=0
    for row in rows:
        equity+=row['pnl'];peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
    entered=[r for r in rows if r['entered']]
    return {'net_dollars':round(equity/10000,4),'max_drawdown_dollars':round(drawdown/10000,4),
        'entries':len(entered),'entry_rate_percent':round(100*len(entered)/len(rows),2) if rows else None,
        'wins':sum(r['won'] for r in entered),'losses':sum(not r['won'] for r in entered),
        'win_rate_percent':round(100*sum(r['won'] for r in entered)/len(entered),2) if entered else None,
        'stops_triggered':sum(r['stop_hit'] for r in entered),'stops_confirmed_filled':sum(r['stop_filled'] for r in entered),
        'stops_unfilled_then_settled':sum(r['stop_hit'] and r['settled'] for r in entered),
        'median_entry_seconds_remaining':median(r['entry_remaining'] for r in entered) if entered else None,
        'median_entry_cents':median(r['entry_cents'] for r in entered) if entered else None}


def main():
    manifest=json.loads((DATA/'manifest.json').read_text())
    metadata={m['ticker']:m for m in manifest['markets'] if m.get('result') in ('yes','no')}
    markets=[];skipped=0
    for path in DATA.glob('*.json.gz'):
        try:
            payload=json.load(gzip.open(path,'rt'))
            payloads=payload.get('markets',[payload])
            for item in payloads:
                ticker=item.get('market_ticker',item.get('ticker'));meta=metadata.get(ticker)
                bars=quotes(meta,item) if meta else None
                if bars:markets.append({**meta,'bars':bars})
                else:skipped+=1
        except (OSError,KeyError,ValueError,ArithmeticError):skipped+=1
    markets.sort(key=lambda m:m['close'])
    if len(markets)<50:
        raise RuntimeError('Insufficient complete archived markets: '+str(len(markets)))
    # Chronological 60/20/20 split: select only on train+validation, then report holdout.
    a=int(len(markets)*.6);b=int(len(markets)*.8)
    split={'train':markets[:a],'validation':markets[a:b],'holdout':markets[b:]}
    all_rows={};summaries=[]
    for window in WINDOWS:
        for buy in BUY_MINS:
            for stop in STOPS:
                if stop>=buy:continue
                key=(window,buy,stop)
                rows={name:[replay(m,buy,stop,window) for m in values] for name,values in split.items()}
                all_rows[key]=rows
                summaries.append({'entry_window_seconds':window,'buy_min_cents':buy,'buy_max_cents':99,'stop_limit_cents':stop,
                    **{name:aggregate(values) for name,values in rows.items()}})
    def score(item):
        train=item['train'];valid=item['validation']
        # Select from training/validation only; require both positive and at least 20 holds each.
        if train['entries']<20 or valid['entries']<20 or train['net_dollars']<=0 or valid['net_dollars']<=0:return None
        return min(train['net_dollars'],valid['net_dollars'])-0.25*max(train['max_drawdown_dollars'],valid['max_drawdown_dollars'])
    selected={}
    for window in WINDOWS:
        candidates=[x for x in summaries if x['entry_window_seconds']==window and score(x) is not None]
        best=max(candidates,key=score) if candidates else None
        selected[str(window)]=best
    by_buy={}
    for buy in BUY_MINS:
        candidates=[x for x in summaries if x['buy_min_cents']==buy and score(x) is not None]
        by_buy[str(buy)]=max(candidates,key=score) if candidates else None
    result={'method':{'markets_from_archive':len(markets),'archive_manifest_markets':len(manifest['markets']),'skipped_missing_or_short_quote_history':skipped,
        'quote_resolution':'one minute','entry':'first observed opening executable ask between buy minimum and 99 cents, final selected window, not final 30 seconds',
        'sell':'stop latches at observed executable bid <= limit; counted filled only when a later minute opening bid is >= limit',
        'position_sizing':'fixed 10 contracts; no compounding, no rebuys, one position per market','fees':'taker formula 0.07*C*P*(1-P), rounded upward to centicent on assumed fills','selection':'chronological 60% train, 20% validation, untouched final 20% holdout; holdout did not select settings',
        'limitation':'minute candles cannot prove order-queue priority, same-minute timing, fills, or 30-second volatility. Results are a conservative screen, not a live-execution backtest.'},
        'selected_by_entry_window':selected,'best_for_each_buy_minimum':by_buy,'all_configurations':summaries}
    (ROOT/'docs'/'FULL_HISTORICAL_REPLAY.json').write_text(json.dumps(result,indent=2)+'\n')
    with (ROOT/'docs'/'FULL_HISTORICAL_REPLAY.csv').open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['window_seconds','buy_min_cents','stop_cents','split','net_dollars','drawdown_dollars','entries','wins','losses','stops','filled_stops','unfilled_stops'])
        for item in summaries:
            for name in split:
                stat=item[name];writer.writerow([item['entry_window_seconds'],item['buy_min_cents'],item['stop_limit_cents'],name,stat['net_dollars'],stat['max_drawdown_dollars'],stat['entries'],stat['wins'],stat['losses'],stat['stops_triggered'],stat['stops_confirmed_filled'],stat['stops_unfilled_then_settled']])
    print(json.dumps({'markets':len(markets),'configs':len(summaries),'selected_by_entry_window':selected,'best_for_each_buy_minimum':by_buy},indent=2))


if __name__=='__main__':main()
