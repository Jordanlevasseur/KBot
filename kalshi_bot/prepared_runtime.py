"""Standard prepared execution runtime; sole journal/order owner plus GET workers."""
import json
import sqlite3
from pathlib import Path
import signal
import threading
import time
from decimal import Decimal as D
from .account import cutoff
from .api import Client,GatewayTransport,RateGate
from .activity import Wakeup
from .buying_power import cash_cents
from .control import CommandPipe
from .connection_status import ConnectionHealth
from .credentials import load
from .diagnostics import Diagnostics,failure_reason
from .execution import Journal
from .market_data import MarketMonitor,quote_state
from .missing_order import MissingOrderVerifier
from .prepared import Inputs,Refreshers,PreparedEngine,FillHistory
from .protective import exit_state
from .streams import BookHub
from .trade_api import TradingSigner,OrderBroker,OrderNotSent
from .strategy import policy,set_policy,VolatilityTracker


class Target:
    def __init__(self):self.lock=threading.Lock();self.value=None
    def set(self,value):
        with self.lock:self.value=dict(value) if value else None
    def get(self):
        with self.lock:return [dict(self.value)] if self.value else []


def state(journal,inputs,engine,feed,monitor,market,result,environment,health=None,volatility_values=None):
    held=journal.position();active=journal.active();position=None
    if held:
        (ticker,side),quantity=next(iter(held.items()));position={'ticker':ticker,'side':side,'qty':str(quantity)}
    else:ticker=active['ticker'] if active else market['ticker'] if market else result.get('ticker','Scanning markets')
    row=journal.db.execute('SELECT buys,stop FROM markets WHERE ticker=?',(ticker,)).fetchone()
    history=[]
    for r in journal.db.execute('SELECT fills.payload,orders.action,orders.side,orders.ticker FROM fills JOIN orders ON orders.exchange_id=fills.order_id ORDER BY fills.rowid DESC LIMIT 100'):
        fill=json.loads(r['payload']);history.append({'id':fill['fill_id'],'ticker':r['ticker'],'action':r['action'],'side':r['side'],
            'qty':fill['count_fp'],'price':str(D(fill[r['side']+'_price_dollars'])*100),'fee':str(D(fill.get('fee_cost','0'))*100)})
    entry=None
    if position:
        buy=journal.db.execute("SELECT exchange_id FROM orders WHERE ticker=? AND side=? AND action='buy' AND counted=1 ORDER BY rowid DESC LIMIT 1",(ticker,position['side'])).fetchone()
        if buy:
            fills=[json.loads(r[0]) for r in journal.db.execute('SELECT payload FROM fills WHERE order_id=?',(buy[0],))]
            n=sum((D(f['count_fp']) for f in fills),D(0))
            if n:entry=str(100*sum((D(f['count_fp'])*D(f[position['side']+'_price_dollars']) for f in fills),D(0))/n)
    close=active['cutoff'] if active else cutoff(market) if market and market.get('close_time') else result.get('cutoff')
    prepared=engine.status(market);view=inputs.view();confirmed=view['confirmed']
    connection=health.snapshot() if health is not None else {'account_connected':confirmed is not None,'connection_phase':'connected' if confirmed is not None else 'connecting'}
    volatility_payload=dict(volatility_values or {}); volatility_status=volatility_payload.pop('status',None)
    return {'mode':environment+'-trading','connection':environment.capitalize()+' account '+connection['connection_phase'],
            **connection,'account_checks_ready':confirmed is not None,'blocked':False,'status':result.get('status',prepared['reason']),
            'execution_at':monitor.execution_at,'running':journal.running,'cash_cents':prepared['cash_cents'],'cash_scope':'market',
            'pnl_cents':None,'market':ticker,'remaining':max(0,close-time.time()) if close else None,
            'clock_rate':1,'clock_sample_mono':time.monotonic(),'buys':row['buys'] if row else 0,'position':position,
            'selected_side':position['side'] if position else active['side'] if active else result.get('side'),
            'entry_cents':entry,**quote_state(feed.quote(ticker),position,ticker),**volatility_payload,'volatility_status':volatility_status,
            'stop':bool(row and row['stop'] and position),
            'active_orders':[active] if active else [],'protective_exit':exit_state(journal,ticker),'history':history,
            'events':[{'kind':'audit','at':r['at'],'detail':r['event']} for r in journal.db.execute('SELECT at,event FROM audit ORDER BY id DESC LIMIT 30')],
            'prepared_entry':prepared,'buy_min_cents':policy().buy_min_cents,'sell_cents':policy().sell_cents,
            'notice':'Prepared execution: background validation; cached orders are single-use. '+('REAL MONEY. ' if environment=='production' else 'Demo funds. ')+str(prepared['reason'] or 'Status not specified')}


def run(directory,fee_reserve,environment,authorize_live,emit):
    if environment=='production' and not authorize_live:raise ValueError('Explicit live-session authorization required')
    # Bounded stacks/allocator arenas fit concurrent readers within the existing
    # 500 MiB virtual-address limit; resident memory does not require a higher cap.
    threading.stack_size(1024*1024)
    signer=load('/credentials',environment,signer_class=TradingSigner)
    journal=Journal(Path(directory)/'desktop-trading.sqlite3',environment+':'+signer.fingerprint)
    inputs=Inputs();target=Target();wakeup=Wakeup();controls=CommandPipe(__import__('sys').stdin)
    # Keep the runtime notification separate from the optional wakeup argument
    # used by CommandPipe.poll().  The first poll in each execution tick does
    # not pass that optional argument; a queued UI refresh must still wake the
    # runtime instead of attempting ``None.notify()`` and killing the worker.
    runtime_wakeup=wakeup
    alive=True;stop=threading.Event()
    def sleep(seconds):
        if stop.wait(seconds):raise RuntimeError('Prepared reader stopping')
    gate=RateGate(sleep=sleep)
    transport=GatewayTransport('/gateway/gateway.sock')
    slots=threading.BoundedSemaphore(4);health=ConnectionHealth()
    def bounded_transport(url,headers):
        if stop.is_set():raise RuntimeError('Prepared reader stopping')
        with slots:
            try:
                response=transport(url,headers);health.record(url,response[0]);return response
            except Exception as exc:
                health.record(url,getattr(exc,'status',0));raise
    def client():return Client(signer,bounded_transport,gate=gate,sleep=sleep)
    broker=OrderBroker(signer,'/gateway/gateway.sock',True)
    feed=BookHub(signer,'/gateway/gateway.sock',Path(directory)/'desktop-trading.sqlite3');feed.subscribe(wakeup)
    # Keep high-frequency monitor/volatility history out of the order journal.
    # Both use the same validated display quote, but separate SQLite writers
    # cannot lock order reconciliation or delay protective execution.
    diagnostics=Diagnostics(Path(directory)/'volatility-diagnostics.sqlite3')
    # Journal order/fill lifecycle events share the same bounded, sanitized
    # recorder as market observations and decisions.
    journal.research_event=diagnostics.record
    monitor=MarketMonitor(client(),signer,'/gateway/gateway.sock',emit,feed=feed,strategy_feed=feed,diagnostics=diagnostics,
                          decision_journal=Path(directory)/'desktop-trading.sqlite3',strategy_wakeup=wakeup)
    journal.on_intent=feed.arm
    missing_verifier=MissingOrderVerifier(client)
    engine=PreparedEngine(journal,inputs,feed,broker,D(fee_reserve) if fee_reserve is not None else D(0),
                          missing_verifier=missing_verifier)
    fill_history=FillHistory();fill_history.update(journal)
    readers=Refreshers(inputs,client,target.get,wakeup.notify,fill_source=fill_history.snapshot)
    def poll(timeout=0,wakeup=None):
        nonlocal alive
        for command in controls.poll(timeout,wakeup=wakeup):
            name=command.get('command')
            if name=='quit':alive=False;journal.running=False;inputs.invalidate('Worker stopping')
            elif name=='pause':journal.running=False;inputs.cash_uncertain()
            elif name=='start' and alive:journal.running=True
            elif name=='refresh':monitor.refresh();runtime_wakeup.notify()
            elif name=='strategy':
                try:
                    if journal.running or journal.position() or journal.active() or not journal.ready:
                        raise ValueError('Pause entries and wait for a confirmed flat account before changing strategy')
                    configured=set_policy(command.get('buy_min_cents'),command.get('sell_cents'),command.get('entry_window_seconds',240),command.get('entry_cutoff_seconds',30),command.get('max_volatility_pct',100),command.get('max_volatility_5m_pct',100),command.get('max_shock_1m_pct',100),command.get('target_buy_usd',50),command.get('target_exit_usd',35))
                    engine.plans={};engine.sell=None;engine.revision=-1
                    inputs.invalidate('Strategy changed; rebuilding prepared orders')
                    from dataclasses import asdict
                    emit('strategy',**asdict(configured))
                except (ValueError,TypeError) as exc:
                    emit('strategy-error',message=str(exc))
            monitor.set_execution(journal.running,'Entries '+('enabled' if journal.running else 'paused'))
    def before_submit():
        poll()
        if not alive:raise OrderNotSent('Worker stopping before submission')
    journal.before_submit=before_submit
    def stopping(*_):
        nonlocal alive
        alive=False;journal.running=False;inputs.invalidate('Worker stopping')
    signal.signal(signal.SIGTERM,stopping);signal.signal(signal.SIGINT,stopping)
    last_market=None;last_volatility_ticker=None;last_state=0.;last_status=None;last_revision=-1;last_risk=None
    last_block_reason=None;last_block_log_at=0.
    result={};phase=None
    volatility=VolatilityTracker()
    last_volatility_calc=0.0
    last_volatility_sample=0.0
    volatility_values={'30s':None,'1m':None,'5m':None,'15m':None}
    monitor.volatility_provider=lambda:dict(volatility_values)
    from .entry_guard import entry_verdict,reference_evidence
    guard_evidence={}
    def guard_market(ticker):
        verified=inputs.view()['market']
        return verified[0] if verified and verified[0].get('ticker')==ticker and time.monotonic()-verified[1]<=5 else None
    def entry_guard(ticker,side):
        nonlocal guard_evidence
        verified=guard_market(ticker)
        if ticker!=last_volatility_ticker:
            return 'Entry blocked: volatility belongs to another market'
        reason,guard_evidence=entry_verdict(verified,monitor.reference.snapshot() if monitor.reference else None,side,volatility)
        return reason
    engine.entry_guard=entry_guard
    def research_event(kind,values):
        payload=dict(values)
        if kind in ('order_intent','order_ack','fill'):
            if kind=='order_intent' and values.get('action')=='buy':payload['entry_approval']=dict(guard_evidence)
            try:
                payload['bitcoin_reference']=reference_evidence(guard_market(values.get('ticker')),
                    monitor.reference.snapshot() if monitor.reference else None,values.get('side'))
            except Exception:payload['bitcoin_reference']={'status':'unavailable'}
        diagnostics.record(kind,payload)
    journal.research_event=research_event
    try:
        emit('ready',mode=environment+'-trading',prepared_entry=True)
        monitor.start();readers.start()
        while alive:
            started=time.monotonic();poll()
            if not alive:break
            values=monitor.markets();market=values[0] if values else None
            held=journal.position();active=journal.active()
            risk=next(iter(held))[0] if held else active['ticker'] if active else None
            current_view=inputs.view()['market']
            if risk and (not market or market['ticker']!=risk):
                market=current_view[0] if current_view and current_view[0]['ticker']==risk else {'ticker':risk}
            if market and market.get('ticker')!=last_market:
                inputs.invalidate('Market changed; preparing fresh market state');last_market=market['ticker']
                volatility.reset()
                last_volatility_sample=0.0
                volatility_values.update({'30s':None,'1m':None,'5m':None,'15m':None,
                    'current_volatility_30s_pct':None,'market_high_cents':None,'market_low_cents':None,
                    'market_range_cents':None,'range_1m_cents':None,'range_5m_cents':None,'range_15m_cents':None,
                    'avg_1m_cents':None,'avg_5m_cents':None,'avg_15m_cents':None,'sample_count':0,
                    'missing_count':0,'coverage_seconds':0.0,'status':'WARMING UP'})
            target.set(market)
            try:
                # A ready quote gets first priority over newly arrived background work.
                # apply/build may refresh a blocked state, but never precede a ready send.
                # Use the exact validated quote already consumed by the
                # dashboard, preventing a second stream read from diverging.
                sampled_ticker=market['ticker'] if market else None
                quote=feed.frame(sampled_ticker).get('prices') if sampled_ticker else None
                # Ignore late callbacks from a previous 15-minute market.
                if market and sampled_ticker and sampled_ticker!=market['ticker']:
                    sampled_ticker=None; quote=None
                # No display-cache fallback: missing or stale frames must stay
                # missing instead of manufacturing valid volatility samples.
                if sampled_ticker and sampled_ticker!=last_volatility_ticker:
                    volatility.reset(); last_volatility_sample=0.0; last_volatility_ticker=sampled_ticker
                now_wall=time.time(); now_mono=time.monotonic()
                if now_mono-last_volatility_calc>=.1:
                    if now_wall-last_volatility_sample>=1.0:
                        volatility.add(now_wall,quote)
                        last_volatility_sample=now_wall
                    volatility_values['30s']=volatility.window_pct(now_wall,30)
                    volatility_values['1m']=volatility.window_pct(now_wall,60)
                    volatility_values['5m']=volatility.window_pct(now_wall,300)
                    volatility_values['15m']=volatility.window_pct(now_wall,900)
                    metrics=volatility.snapshot(now_wall)
                    volatility_values.update({'current_volatility_30s_pct':volatility_values['30s'],**metrics})
                    last_volatility_calc=now_mono
                vol30=volatility_values['30s']; shock=volatility_values['1m']; vol5=volatility_values['5m']; avg_vol=volatility_values['15m']
                held_now=journal.position();active_now=journal.active()
                for (held_ticker,held_side),quantity in held_now.items():
                    try:
                        evidence=reference_evidence(guard_market(held_ticker),
                            monitor.reference.snapshot() if monitor.reference else None,held_side)
                        if D(evidence['held_side_cushion_usd'])<=D(str(policy().target_exit_usd)):
                            with journal.db:
                                changed=journal.db.execute('UPDATE markets SET stop=1 WHERE ticker=? AND stop=0',(held_ticker,)).rowcount
                            if changed:diagnostics.record('target_exit_trigger',evidence)
                    except (ValueError,TypeError,RuntimeError,KeyError):
                        pass # Existing price-based protective exits remain active.
                result=engine.execute(market)
                result['volatility_30s_pct']=vol30
                result['volatility_5m_pct']=vol5
                result['volatility_15m_pct']=avg_vol
                if not alive:break
                if engine.apply():fill_history.update(journal)
                engine.build(market)
            except Exception as exc:
                # The reconciliation engine already invalidates on failure.
                # Do not invalidate it *again* on every 100 Hz execution tick.
                if inputs.view()['account'] is not None:
                    inputs.invalidate('Execution uncertain or blocked; asynchronous reconciliation required')
                journal.ready=False
                reason=failure_reason(exc)
                result={'status':'Blocked: '+reason,'ticker':market.get('ticker') if market else None}
                now_log=time.monotonic()
                if reason!=last_block_reason or now_log-last_block_log_at>=30:
                    journal.log('Prepared execution blocked: '+reason)
                    last_block_reason=reason;last_block_log_at=now_log
            held=journal.position();active=journal.active()
            position=None
            if held:
                (t,side),n=next(iter(held.items()));position={'ticker':t,'side':side,'qty':str(n)}
            risk_signature=(str(position),active['client_id'] if active else None)
            if risk_signature!=last_risk:
                monitor.set_position(position,active['ticker'] if active else None,active['side'] if active else None);last_risk=risk_signature
            status=result.get('status','Preparing')
            if (journal.running,status)!=phase:
                monitor.set_execution(journal.running,status);phase=(journal.running,status)
                diagnostics.record('prepared_execution',{'status':status,'prepared':engine.status(market)})
            if status!=last_status or time.monotonic()-last_state>=1 or last_revision!=engine.revision:
                diagnostics.record('volatility_reading',{'ticker':sampled_ticker or (market.get('ticker') if market else None),
                    'yes_side_only':True,'sample_interval_seconds':1,
                    'seconds_remaining':max(0,cutoff(market)-time.time()) if market and market.get('close_time') else None,
                    'yes_price_cents':(float(quote['yes_ask'])*100 if quote and quote.get('yes_ask') is not None else None),
                    'volatility_30s_pct':volatility_values['30s'],'volatility_1m_pct':volatility_values['1m'],
                    'volatility_5m_pct':volatility_values['5m'],'volatility_15m_pct':volatility_values['15m'],
                    **{k:volatility_values.get(k) for k in ('market_high_cents','market_low_cents','market_range_cents','range_1m_cents','range_5m_cents','range_15m_cents','avg_1m_cents','avg_5m_cents','avg_15m_cents','sample_count','missing_count','coverage_seconds','status')}})
                try:
                    emit('state',state=state(journal,inputs,engine,feed,monitor,market,result,environment,health,volatility_values))
                except (sqlite3.Error,TypeError,AttributeError) as exc:
                    # A stale/locked read must not stop market monitoring or
                    # protective execution. The next state tick retries it.
                    diagnostics.record('state_update_error',{'error':type(exc).__name__})
                last_state=time.monotonic();last_status=status;last_revision=engine.revision
            # Event driven, with a 100 Hz ceiling to coalesce book bursts on this CPU.
            poll(max(0,.01-(time.monotonic()-started)))
            poll(0,wakeup=wakeup)
    finally:
        journal.running=False;inputs.invalidate('Restart requires fresh preparation');stop.set();readers.close()
        active=journal.active()
        if active and active['action']=='buy' and active['exchange_id']:
            try:journal.cancel(broker)
            except Exception:journal.log('Shutdown cancellation uncertain; reconciliation required')
        missing_verifier.close()
        feed.unsubscribe(wakeup);wakeup.close();monitor.close();feed.close();diagnostics.close();journal.close()
