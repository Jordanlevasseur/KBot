"""Read-only realized profit display. No broker, API client or journal writer."""
from collections import defaultdict,deque
from datetime import datetime
from decimal import Decimal as D,ROUND_HALF_UP
import json
import math
import os
from pathlib import Path
import queue
import sqlite3
import threading


def number(value):
    result=D(str(value))
    if not result.is_finite():raise ValueError('Invalid profit input')
    return result


def realized(fills,settlements):
    """Allocate confirmed exit proceeds/both fees to BUY fills using FIFO.

    Prices/fees/payouts are cents. Aggregate settlement fees are not charged twice:
    trading fees are already included in each fill. No unrealized prices are used.
    """
    lots=defaultdict(deque);results={};invalid=set()
    for row in fills:
        ticker=row['ticker'];side=row['side'];key=(ticker,side);identity=str(row['id'])
        qty,price,fee=map(number,(row['qty'],row['price'],row['fee']))
        if qty<=0 or not 0<=price<=100 or fee<0 or side not in ('yes','no'):raise ValueError('Invalid fill')
        if row['action']=='buy':
            results[identity]={'profit_cents':D(0),'status':'open'}
            lots[key].append({'id':identity,'qty':qty,'basis':qty*price+fee})
        elif row['action']=='sell':
            if sum((lot['qty'] for lot in lots[key]),D(0))<qty:
                invalid.add(ticker);continue
            remaining=qty;remaining_fee=fee
            while remaining:
                lot=lots[key][0];n=min(remaining,lot['qty'])
                basis=lot['basis'] if n==lot['qty'] else lot['basis']*n/lot['qty']
                charge=remaining_fee if n==remaining else fee*n/qty
                value=results[lot['id']];value['profit_cents']+=n*price-charge-basis
                lot['qty']-=n;lot['basis']-=basis;remaining-=n;remaining_fee-=charge
                value['status']='closed' if lot['qty']==0 else 'partial'
                if not lot['qty']:lots[key].popleft()
        else:raise ValueError('Invalid fill action')
    for ticker,payout in settlements.items():
        pending=[lot for (market,_),values in lots.items() if market==ticker for lot in values]
        if not pending:continue
        sides={side for (market,side),values in lots.items() if market==ticker and values}
        if len(sides)!=1:invalid.add(ticker);continue
        qty=sum((lot['qty'] for lot in pending),D(0));payout=number(payout)
        if payout<0 or payout>qty*100:
            invalid.add(ticker);continue
        remaining_qty=qty;remaining_payout=payout
        for lot in pending:
            proceeds=remaining_payout if lot['qty']==remaining_qty else payout*lot['qty']/qty
            results[lot['id']]['profit_cents']+=proceeds-lot['basis'];results[lot['id']]['status']='closed'
            remaining_payout-=proceeds;remaining_qty-=lot['qty']
    for row in fills:
        if row['ticker'] in invalid and str(row['id']) in results:results[str(row['id'])]={'profit_cents':None,'status':'unavailable'}
    return {key:{**value,'profit_cents':str(value['profit_cents']) if value['profit_cents'] is not None else None} for key,value in results.items()}


def timestamp(payload,fallback):
    try:
        value=payload.get('created_time')
        if value:
            parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
            if parsed.utcoffset() is not None:return parsed.timestamp()
        if payload.get('ts') is not None:
            result=float(payload['ts'])
            if math.isfinite(result):return result
    except (ValueError,TypeError,AttributeError,OverflowError):pass
    return fallback


def from_database(path,tickers,simulation=False):
    """Short read-only snapshot of visible markets, including earlier entry fills."""
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_uid!=os.getuid():return {}
    tickers=tuple(sorted(set(tickers)))
    if not tickers or len(tickers)>100:return {}
    params=','.join('?' for _ in tickers)
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.05);db.row_factory=sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        if simulation:
            rows=[dict(row) for row in db.execute('SELECT f.id,o.ticker,o.side,o.action,f.qty,f.price,f.fee FROM fills f JOIN orders o ON o.id=f.order_id WHERE o.ticker IN ('+params+') ORDER BY f.id',tickers)]
            settlements={row['ticker']:row['payout'] for row in db.execute('SELECT ticker,payout FROM settlements WHERE ticker IN ('+params+')',tickers)}
        else:
            rows=[]
            for row in db.execute('SELECT f.rowid AS ordinal,f.payload,o.rowid AS order_ordinal,o.ticker,o.side,o.action FROM fills f JOIN orders o ON o.exchange_id=f.order_id WHERE o.ticker IN ('+params+')',tickers):
                fill=json.loads(row['payload']);rows.append({'id':fill['fill_id'],'ticker':row['ticker'],'side':row['side'],'action':row['action'],
                    'qty':fill['count_fp'],'price':number(fill[row['side']+'_price_dollars'])*100,'fee':number(fill['fee_cost'])*100,
                    '_order':row['order_ordinal'],'_at':timestamp(fill,row['ordinal']),'_ordinal':row['ordinal']})
            rows.sort(key=lambda row:(row['_order'],row['_at'],row['_ordinal']))
            settlements={}
            for row in db.execute('SELECT ticker,payload FROM settlements WHERE ticker IN ('+params+')',tickers):
                value=json.loads(row['payload'])
                if value.get('revenue') is not None:settlements[row['ticker']]=number(value['revenue'])
        return realized(rows,settlements)
    finally:db.close()



def trade_times_from_database(path,tickers):
    """Read exchange fill times for BUY and SELL rows; never use order/receipt time."""
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_uid!=os.getuid():return {}
    tickers=tuple(sorted(set(tickers)))
    if not tickers or len(tickers)>100:return {}
    params=','.join('?' for _ in tickers)
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.05)
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        result={}
        for identity,payload in db.execute('SELECT f.id,f.payload FROM fills f JOIN orders o ON o.exchange_id=f.order_id WHERE o.ticker IN ('+params+')',tickers):
            value=json.loads(payload)
            if not isinstance(value,dict):continue
            result[str(identity)]={k:value[k] for k in ('created_time','ts') if value.get(k) is not None}
        return result
    finally:db.close()


def trade_time_text(value):
    """Local date/time, seconds and zone; absent timestamps remain unknown."""
    if not isinstance(value,dict):return '—'
    created=value.get('created_time')
    if isinstance(created,str):
        try:
            parsed=datetime.fromisoformat(created.replace('Z','+00:00'))
            if parsed.utcoffset() is not None:
                return parsed.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
        except (ValueError,OverflowError,OSError):pass
    ts=value.get('ts')
    if ts is not None and not isinstance(ts,bool):
        try:
            stamp=float(ts)
            if math.isfinite(stamp):
                return datetime.fromtimestamp(stamp).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
        except (ValueError,TypeError,OverflowError,OSError):pass
    return '—'


def profit_text(value):
    if not value:return '—'
    if value['status']=='open':return 'Open'
    if value['profit_cents'] is None:return '—'
    dollars=(number(value['profit_cents'])/100).quantize(D('.01'),rounding=ROUND_HALF_UP)
    sign='+' if dollars>0 else '-' if dollars<0 else ''
    return sign+'$'+format(abs(dollars),'.2f')+(' (partial)' if value['status']=='partial' else '')


class ProfitReader:
    """One coalescing GUI background reader; never blocks the trading worker."""
    def __init__(self,events):
        self.events=events;self.jobs=queue.Queue(maxsize=1);self.stop=threading.Event()
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()
    def request(self,key,generation,mode,path,tickers):
        if self.stop.is_set():return
        job=(key,generation,mode,path,tuple(tickers))
        try:self.jobs.put_nowait(job)
        except queue.Full:
            try:self.jobs.get_nowait()
            except queue.Empty:pass
            try:self.jobs.put_nowait(job)
            except queue.Full:pass
    def _run(self):
        while not self.stop.is_set():
            try:key,generation,mode,path,tickers=self.jobs.get(timeout=.1)
            except queue.Empty:continue
            try:values=from_database(path,tickers,simulation=mode=='simulation')
            except (sqlite3.Error,ValueError,KeyError,TypeError,ArithmeticError,OSError):values={}
            # Independent of profit completeness: a missing fee must not hide
            # a confirmed fill's time. Simulation's legacy fills have no date.
            try:times=trade_times_from_database(path,tickers) if mode!='simulation' else {}
            except (sqlite3.Error,ValueError,KeyError,TypeError,ArithmeticError,OSError):times={}
            if not self.stop.is_set():self.events.put((generation,{'type':'profit-ready','key':key,'mode':mode,'values':values,'trade_times':times}))
    def close(self):
        self.stop.set()
        if self.thread is not threading.current_thread():self.thread.join(timeout=.2)
