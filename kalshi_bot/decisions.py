"""Independent 4 Hz strategy assessment. No network or order-write capability.

Exchange execution remains single-owner and repeats its fresh safety checks.
This loop reads the committed journal through a private read-only connection.
"""
import json
import sqlite3
import threading
import time
from pathlib import Path
from decimal import Decimal as D
from .account import cutoff
from .eligibility import eligible_sides,ordered_candidates,entry_limit
from .strategy import entry_open,ENTRY_CUTOFF_SECONDS
from .strategy import policy

INTERVAL_SECONDS=.25


def read_context(db,ticker):
    db.execute('BEGIN')
    try:
        held={};closes={}
        for row in db.execute("SELECT o.ticker,o.side,o.action,o.filled,o.cutoff FROM orders o JOIN markets m USING(ticker) WHERE m.settled=0 AND (o.counted=1 OR o.action='sell')"):
            t,side,action,filled,close=row
            key=(t,side);held[key]=held.get(key,D(0))+D(filled)*(1 if action=='buy' else -1)
            closes[t]=min(closes.get(t,close),close)
        held={key:n for key,n in held.items() if n}
        active=db.execute("SELECT ticker,action,side,status,quantity,filled,cutoff FROM orders WHERE status NOT IN ('executed','canceled','rejected','expired') LIMIT 2").fetchall()
        names=('ticker','action','side','status','quantity','filled','cutoff')
        active=[dict(zip(names,row)) for row in active]
        markets={t:{'buys':buys,'stop':bool(stop)} for t,buys,stop in db.execute('SELECT ticker,buys,stop FROM markets WHERE settled=0')}
        snapshot=db.execute('SELECT at FROM snapshots ORDER BY id DESC LIMIT 1').fetchone()
        previous=db.execute("SELECT side FROM orders WHERE ticker=? AND action='buy' AND counted=1 ORDER BY rowid DESC LIMIT 1",(ticker,)).fetchone() if ticker else None
        return {'held':held,'closes':closes,'active':active,'markets':markets,
                'account_at':snapshot[0] if snapshot else None,'previous_side':previous[0] if previous else None}
    finally:db.rollback()


def assess(context,market,prices,risk_prices,running,phase,now):
    sides=eligible_sides(prices)
    checked=context.get('account_at');age=now-checked if checked is not None else None
    result={'eligible_sides':sides,'entries_enabled':bool(running),'ticker':market.get('ticker') if market else None,
            'account_snapshot_age_seconds':round(age,3) if age is not None else None,
            'requires_fresh_execution_checks':True}
    def decision(action,reason,**values):return {**result,'action':action,'reason':reason,**values}
    held=context['held'];active=context['active']
    if len(held)>1 or len(active)>1 or any(n<0 for n in held.values()):
        return decision('blocked','Account exposure inconsistent; reconciliation required')
    order=active[0] if active else None
    if held or (order and order['action']=='buy'):
        (ticker,side),quantity=next(iter(held.items())) if held else ((order['ticker'],order['side']),D(0))
        close=context['closes'].get(ticker,order['cutoff'] if order else None)
        if market and market.get('ticker')==ticker:close=min(close,cutoff(market)) if close else cutoff(market)
        if close is not None and now>=close:
            return decision('await_settlement','Trading closed; awaiting settlement',ticker=ticker)
        bid=risk_prices.get(side+'_bid') if risk_prices else None
        stop=context['markets'].get(ticker,{}).get('stop',False) or (bid is not None and D(str(bid))<=D(policy().sell_cents)/100)
        if stop:
            if order and order['action']=='buy':
                action,reason='cancel_buy_required','Stop latched; residual buy must be reconciled/canceled before selling'
            elif order:
                action,reason='maintain_exit','Protective sell open; remaining shares stay covered'
            else:action,reason='protective_exit_required','Protective exit required for all remaining shares; execution checks required'
            return decision(action,reason,ticker=ticker,side=side,sell_quantity=str(quantity),stop_latched=True)
        if held:return decision('hold','Holding position; protective threshold not reached',ticker=ticker,side=side)
    if order:return decision('pending_order','Existing order must be reconciled; duplicate buy blocked',ticker=order['ticker'])
    if not running:return decision('paused','New buys paused; protective checks continue')
    if not market:return decision('no_market','Waiting for an active Bitcoin market')
    ticker=market['ticker']
    if now>=cutoff(market):return decision('closed','Market closed; awaiting rollover')
    if cutoff(market)-now<=ENTRY_CUTOFF_SECONDS:
        return decision('entry_closed','New purchases blocked during final 30 seconds')
    if market.get('status') not in ('open','active') or market.get('market_type')!='binary' or not entry_open(cutoff(market),now):
        return decision('outside_window','Waiting for the final four-minute entry window')
    if prices is None:return decision('stale','Fresh sequenced prices unavailable; entry blocked')
    if not sides:return decision('no_entry',f'No qualifying {policy().buy_min_cents}–99c executable ask')
    side=ordered_candidates(prices,ticker,context.get('previous_side'))[0]
    values={'side':side,'limit':str(entry_limit(prices[side+'_ask']))}
    if phase.startswith('Blocked:'):
        return decision('await_account',side.upper()+' qualifies; '+phase,**values)
    if age is None or not 0<=age<=1:
        return decision('await_account',side.upper()+' qualifies; awaiting fresh account reconciliation',**values)
    return decision('entry_candidate',side.upper()+' qualifies; fresh cash, fee, cutoff and execution checks required',**values)


class DecisionLoop:
    def __init__(self,journal_path,inputs,feed,changed,diagnostics=None):
        self.path=Path(journal_path);self.inputs=inputs;self.feed=feed;self.changed=changed;self.diagnostics=diagnostics
        self.stop=threading.Event();self.lock=threading.Lock();self.thread=None;self.latest=None
        self.count=0;self.previous=None;self.max_gap=0.;self.last_signature=None;self.last_record=0.

    def snapshot(self):
        with self.lock:return dict(self.latest) if self.latest else None

    def start(self):
        self.thread=threading.Thread(target=self._run,name='strategy-decisions',daemon=True);self.thread.start()

    def _run(self):
        db=None
        try:
            while not self.stop.is_set():
                started=time.monotonic()
                try:
                    if db is None:
                        db=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.05)
                        db.execute('PRAGMA query_only=ON')
                    market,running,phase=self.inputs()
                    context=read_context(db,market['ticker'] if market else None)
                    prices=self.feed.quote(market['ticker']) if market else None
                    held=context['held'];active=context['active']
                    risk=next(iter(held))[0] if held else active[0]['ticker'] if active and active[0]['action']=='buy' else None
                    risk_prices=self.feed.quote(risk) if risk else None
                    result=assess(context,market,prices,risk_prices,running,phase,time.time())
                except Exception as exc:
                    result={'action':'unavailable','reason':'Strategy account view unavailable ('+type(exc).__name__+')',
                            'eligible_sides':[],'requires_fresh_execution_checks':True}
                self.count+=1
                gap=started-self.previous if self.previous is not None else None
                if gap is not None:self.max_gap=max(self.max_gap,gap)
                self.previous=started
                result.update(decision_at=time.time(),decision_mono=started,decision_count=self.count,
                              target_interval_ms=INTERVAL_SECONDS*1000,
                              actual_interval_ms=round(gap*1000,3) if gap is not None else None,
                              max_interval_ms=round(self.max_gap*1000,3),execution_authorized=False)
                with self.lock:self.latest=result
                self.changed()
                signature=json.dumps({k:v for k,v in result.items() if k not in ('decision_at','decision_mono','decision_count',
                    'actual_interval_ms','max_interval_ms','account_snapshot_age_seconds')},sort_keys=True)
                if self.diagnostics and (signature!=self.last_signature or started-self.last_record>=1):
                    self.diagnostics.record('strategy_decision',result);self.last_record=started;self.last_signature=signature
                self.stop.wait(max(0,INTERVAL_SECONDS-(time.monotonic()-started)))
        finally:
            if db is not None:db.close()

    def close(self):
        self.stop.set()
        if self.thread and self.thread.ident is not None:self.thread.join(timeout=1)
