"""Read-only discovery and display updates independent of portfolio requests."""
import threading
import time
from decimal import Decimal
from .account import cutoff
from .discovery import open_markets
from .streams import BookFeed,BookHub
from .eligibility import eligible_sides,entry_limit
from .strategy import entry_open,VolatilityTracker
from .activity import Wakeup
from .reference_display import ReferenceFeed


def quote_state(prices,position,ticker):
    values={s+'_'+kind+'_cents':str(Decimal(str(prices[s+'_'+kind]))*100)
            if prices and prices.get(s+'_'+kind) is not None else None
            for s in ('yes','no') for kind in ('bid','ask')}
    side=position.get('side') if position and position.get('ticker')==ticker else None
    values.update(bid_side=side if side in ('yes','no') else None,
                  bid_cents=values.get(side+'_bid_cents') if side in ('yes','no') else None)
    return values


class MarketMonitor:
    """Two bounded background loops; neither may submit/cancel orders.

    The display has its own stream so a held expiring position's protective stream
    is never switched away by discovery of the next contract.
    """
    def __init__(self,client,signer,socket_path,emit,*,strategy_feed=None,feed=None,diagnostics=None,decision_journal=None,strategy_wakeup=None):
        self.client=client
        self.reference=ReferenceFeed(signer,socket_path) if signer is not None else None
        self.feed=feed if feed is not None else BookFeed(signer,socket_path,None)
        self.strategy_feed=strategy_feed
        self.emit=emit
        self.lock=threading.Lock()
        self.stop=threading.Event()
        self.discovery_wakeup=threading.Event()
        self.market=None
        self.latest_ticker=None; self.latest_prices=None; self.latest_health=None
        self.volatility=VolatilityTracker(); self.volatility_ticker=None; self.volatility_sample_at=0.0; self.volatility_values={}
        self.discovery_error=None
        self.position=None
        self.threads=[]
        self.pending_ticker=None;self.running=False;self.phase='Waiting for account checks';self.details={}
        self.diagnostics=diagnostics;self.last_observation=None
        self.execution_at=0
        self.decisions=None
        self.strategy_wakeup=strategy_wakeup
        if decision_journal is not None:
            from .decisions import DecisionLoop
            self.decisions=DecisionLoop(decision_journal,self.decision_inputs,self.feed,self.wakeup_decision,diagnostics)
        self.wakeup=Wakeup()
        if isinstance(self.feed,BookHub):self.feed.subscribe(self.wakeup)
        else:self.feed.on_update=self.wakeup.notify

    def decision_inputs(self):
        with self.lock:
            current=self.market if self.market and not self.discovery_error else None
            return dict(current) if current else None,self.running,self.phase

    def wakeup_decision(self):
        self.wakeup.notify()
        decision=self.decisions.snapshot() if self.decisions else None
        if self.strategy_wakeup is not None and decision and decision['action'] in (
                'entry_candidate','await_account','protective_exit_required','cancel_buy_required'):
            self.strategy_wakeup.notify()


    def set_position(self,position,pending_ticker=None,pending_side=None):
        with self.lock:
            self.position=dict(position) if position else None
            self.pending_ticker=pending_ticker
        if isinstance(self.feed,BookHub):
            if position:self.feed.arm(position['ticker'],position['side'])
            elif pending_ticker and pending_side:self.feed.arm(pending_ticker,pending_side)
            elif not pending_ticker:self.feed.clear_flat_risk()
        self._retain()
        self.wakeup.notify()

    def set_execution(self,running,phase,details=None):
        with self.lock:
            self.running=bool(running);self.phase=phase;self.details=details or {};self.execution_at=time.monotonic()
        if self.diagnostics:self.diagnostics.record('execution_stage',{'entries_enabled':bool(running),'stage':phase,**(details or {})})
        self.wakeup.notify()

    def markets(self):
        with self.lock:
            return [dict(self.market)] if self.market and not self.discovery_error and cutoff(self.market)>time.time() else []

    def display_quote(self,ticker):
        """Return the exact validated quote most recently used by the UI."""
        with self.lock:
            return dict(self.latest_prices) if ticker and ticker==self.latest_ticker and self.latest_prices else None

    def display_snapshot(self):
        with self.lock:
            return self.latest_ticker, (dict(self.latest_prices) if self.latest_prices else None)

    def _retain(self):
        if not isinstance(self.feed,BookHub):return
        with self.lock:
            current=self.market['ticker'] if self.market else None
            protected=self.position['ticker'] if self.position else self.pending_ticker
        self.feed.retain(current,protected)

    def refresh(self):
        """Wake discovery without reconnecting the worker or changing entries."""
        self.discovery_wakeup.set();self.wakeup.notify()

    def discover_once(self,now=None):
        try:
            markets=open_markets(self.client,now)
            market=markets[0] if markets else None
            with self.lock:
                self.market=market
                self.discovery_error=None
            if market: self.feed.watch(market['ticker'])
            self._retain()
        except Exception as exc:
            with self.lock:
                self.discovery_error=type(exc).__name__
                current=time.time() if now is None else now
                if self.market and cutoff(self.market)<=current: self.market=None
        self.wakeup.notify()

    def publish_once(self,now=None):
        now=time.time() if now is None else now
        with self.lock:
            market=self.market
            error=self.discovery_error
            position=self.position
            pending_ticker=self.pending_ticker
            running,phase,details,execution_at=self.running,self.phase,dict(self.details),self.execution_at
        current=market if market and now<cutoff(market) else None
        ticker=current['ticker'] if current else None
        if isinstance(self.feed,BookHub):
            health=self.feed.frame(ticker);prices=health['prices'] if ticker else None
        else:
            health=self.feed.health();prices=self.feed.quote(ticker) if ticker else None
        with self.lock:
            self.latest_ticker=ticker; self.latest_prices=dict(prices) if prices else None; self.latest_health=dict(health)
            if ticker and ticker!=self.volatility_ticker:
                self.volatility.reset(); self.volatility_ticker=ticker; self.volatility_sample_at=0.0
            if ticker and prices and now-self.volatility_sample_at>=1.0:
                self.volatility.add(now,prices); self.volatility_sample_at=now
                self.volatility_values=self.volatility.snapshot(now)
            volatility=dict(self.volatility_values)
        provider=getattr(self,'volatility_provider',None)
        if provider is not None:
            volatility=dict(provider())
        held_prices=(self.strategy_feed.quote(position['ticker']) if self.strategy_feed else
                     prices if position and position.get('ticker')==ticker else None) if position else None
        held_quote=quote_state(held_prices,position,position['ticker'] if position else None)
        eligible=eligible_sides(prices)
        limits={side:str(entry_limit(prices[side+'_ask'])) for side in eligible}
        window=bool(current and entry_open(cutoff(current),now))
        observation={'ticker':ticker,'cutoff':cutoff(current) if current else None,'window_open':window,
                     'remaining_seconds':max(0,cutoff(current)-now) if current else None,
                     'eligible_sides':eligible,'entries_enabled':running,'stage':phase,
                     'eligible_limits_dollars':limits,
                     'yes_ask':str(prices['yes_ask']) if prices and prices['yes_ask'] is not None else None,
                     'no_ask':str(prices['no_ask']) if prices and prices['no_ask'] is not None else None,
                     'yes_bid':str(prices['yes_bid']) if prices and prices.get('yes_bid') is not None else None,
                     'no_bid':str(prices['no_bid']) if prices and prices.get('no_bid') is not None else None,
                     'book_status':health['status'],'book_error':health['error'],'position_open':bool(position),'pending_order':bool(pending_ticker)}
        signature=str({k:v for k,v in observation.items() if window or eligible or k not in ('yes_ask','no_ask')})
        if self.diagnostics and signature!=self.last_observation:
            self.diagnostics.record('market_observation',{**observation,'sequence':health.get('sequence'),
                                    'subscription':health.get('subscription'),'verified_age':health.get('verified_age')})
            self.last_observation=signature
        self.emit('market',market=ticker,cutoff=cutoff(current) if current else None,
                  btc_target=current.get('floor_strike') if current and not error else None,
                  btc_reference=self.reference.snapshot() if self.reference else {},
                  remaining=max(0,cutoff(current)-now) if current else None,
                  clock_sample_mono=time.monotonic(),
                  **quote_state(prices,None,ticker),
                  market_health=health['status'] if ticker else ('discovery error' if error else 'discovering next market'),
                  market_last_update=health['last_update'] if ticker else None,
                  market_verified_age=health.get('verified_age') if ticker else None,
                  market_error=error or health['error'],
                  eligible_sides=eligible,entry_window_open=window,execution_stage=phase,
                  eligible_limits_cents={side:str(Decimal(limit)*100) for side,limit in limits.items()},
                  entries_enabled=running,execution_at=execution_at,
                  volatility_source='YES ask shown in dashboard',**volatility,
                  strategy_decision=self.decisions.snapshot() if self.decisions else None,
                  diagnostics_error=self.diagnostics.error if self.diagnostics else None,
                  diagnostics_dropped=self.diagnostics.dropped if self.diagnostics else 0,
                  position_ticker=position['ticker'] if position else None,
                  position_bid_side=held_quote['bid_side'],position_bid_cents=held_quote['bid_cents'])

    def start(self):
        if self.reference:self.reference.start()
        if self.decisions:self.decisions.start()
        def discover():
            while not self.stop.is_set():
                self.discovery_wakeup.clear();self.discover_once()
                with self.lock: market=self.market
                wait=max(.25,min(10,cutoff(market)-time.time()+.05)) if market else 2
                self.discovery_wakeup.wait(wait)
        def publish():
            while not self.stop.is_set():
                started=time.monotonic()
                self.publish_once()
                self.wakeup.wait(.5)
                # Coalesce bursts to at most 20 frames/s without delaying the
                # independent trading subscriber or the durable stop recorder.
                self.stop.wait(max(0,.05-(time.monotonic()-started)))
        for work in (discover,publish):
            thread=threading.Thread(target=work,daemon=True)
            thread.start()
            self.threads.append(thread)

    def close(self):
        if self.reference:self.reference.close()
        self.stop.set();self.discovery_wakeup.set()
        if self.decisions:self.decisions.close()
        self.wakeup.notify()
        for thread in self.threads: thread.join(timeout=.3)
        if isinstance(self.feed,BookHub):self.feed.unsubscribe(self.wakeup)
        self.feed.close()
        self.wakeup.close()
