"""JSON-lines IPC worker. Runs only inside the application's Linux sandbox."""
import argparse
import json
import resource
import select
import signal
import sys
import time
from pathlib import Path
from decimal import Decimal
import threading
from .market_data import MarketMonitor,quote_state
from .control import CommandPipe
from .diagnostics import Diagnostics,failure_reason
from .activity import Wakeup
from .protective import exit_state
from .buying_power import cash_cents
from .security import check_isolation

OUTPUT_LOCK=threading.Lock()

def emit(kind,**values):
    with OUTPUT_LOCK:
        print(json.dumps({'type':kind,**values},default=str),flush=True)


def simulation_state(engine,q):
    from .strategy import policy
    state=engine.status()
    market=engine.row('SELECT * FROM markets WHERE ticker=?',(q.ticker,)) or {}
    position=state['positions'][0] if state['positions'] else None
    entry=None
    if position:
        row=engine.row("SELECT sum(f.qty*f.price) AS total,sum(f.qty) AS quantity FROM fills f JOIN orders o ON o.id=f.order_id WHERE o.id=(SELECT max(id) FROM orders WHERE ticker=? AND action='buy' AND counted=1)",(position['ticker'],))
        if row and row['quantity']:
            entry=round(row['total']/row['quantity'],4)
    history=[dict(r) for r in engine.db.execute('SELECT f.id,o.ticker,o.action,o.side,f.qty,f.price,f.fee FROM fills f JOIN orders o ON o.id=f.order_id ORDER BY f.id DESC LIMIT 100')]
    return {'mode':'simulation','connection':'Offline simulation','running':bool(state['account']['running']),
            'cash_cents':state['account']['cash'],'pnl_cents':state['account']['pnl'],
            'market':q.ticker,'remaining':max(0,q.cutoff-q.now),'buys':market.get('buys',0),
            'position':position,'entry_cents':entry,
            **quote_state({s+'_'+kind:Decimal(getattr(q,s+'_'+kind))/100 if getattr(q,s+'_'+kind) is not None else None for s in ('yes','no') for kind in ('bid','ask')},position,q.ticker),
            'stop':bool(position and position['stop']),'active_orders':state['active_orders'],
            'buy_min_cents':policy().buy_min_cents,'sell_cents':policy().sell_cents,
            'history':history,'events':state['recent_events'],
            'notice':'Simulated prices and money. Pausing entries keeps protective exits running.'}


def run_simulation(directory):
    from .core import Engine
    from .__main__ import feed
    engine=Engine(directory/'desktop-simulation.sqlite3')
    saved=engine.row('SELECT max(last_tick) AS tick FROM markets')['tick']
    tick=max(775,saved+1 if saved is not None else 775)
    from .clocks import ReplayClock
    clock=ReplayClock(tick,rate=1)
    alive=True
    def stopping(*_):
        nonlocal alive
        alive=False
    signal.signal(signal.SIGTERM,stopping)
    signal.signal(signal.SIGINT,stopping)
    try:
        emit('ready',mode='simulation')
        while alive:
            q=feed(engine,tick)
            state=simulation_state(engine,q)
            state.update(remaining=max(0,q.cutoff-clock.now()),clock_rate=clock.rate,clock_sample_mono=time.monotonic())
            emit('state',state=state)
            deadline=time.monotonic()+1
            while alive and time.monotonic()<deadline:
                ready,_,_=select.select([sys.stdin],[],[],max(0,deadline-time.monotonic()))
                if not ready: break
                line=sys.stdin.readline(4097)
                if not line: alive=False; break
                try:
                    message=json.loads(line)
                    command=message['command']
                    if command=='start': engine.start()
                    elif command=='pause': engine.stop()
                    elif command=='strategy':
                        from .strategy import set_policy
                        current=engine.status()
                        if current['account']['running'] or current['positions'] or current['active_orders']:
                            raise ValueError('Pause entries and close positions/orders before changing strategy')
                        configured=set_policy(message.get('buy_min_cents'),message.get('sell_cents'),message.get('entry_window_seconds',240),message.get('entry_cutoff_seconds',30),message.get('max_volatility_pct',100),message.get('max_volatility_5m_pct',100),message.get('max_shock_1m_pct',100),message.get('target_buy_usd',50),message.get('target_exit_usd',35))
                        from dataclasses import asdict
                        emit('strategy',**asdict(configured))
                    elif command=='refresh': pass
                    elif command=='quit': alive=False
                    elif command=='speed' and message.get('value') in (1,5,15): clock.set_rate(message['value'])
                    else: continue
                    state=simulation_state(engine,q)
                    state.update(remaining=max(0,q.cutoff-clock.now()),clock_rate=clock.rate,clock_sample_mono=time.monotonic())
                    emit('state',state=state)
                except (ValueError,KeyError,TypeError):
                    emit('error',message='Invalid desktop command; no action taken')
            if alive:
                # Advance by elapsed monotonic time, including database latency.
                target=int(clock.now())
                while tick<target:
                    tick+=1
                    q=feed(engine,tick)
    finally:
        engine.stop()
        emit('state',state=simulation_state(engine,q))
        engine.close()


def run_account(directory,environment,preflight):
    from .credentials import load
    signer=load('/credentials',environment)
    if preflight:
        emit('preflight',message='Credential format, environment, and Linux isolation passed. No connection attempted.')
        return
    from .api import Client,GatewayTransport,RateGate
    from .account import AccountStore,snapshot
    gate=RateGate()
    client=Client(signer,GatewayTransport('/gateway/gateway.sock'),gate=gate)
    monitor=MarketMonitor(Client(signer,GatewayTransport('/gateway/gateway.sock'),gate=gate),signer,'/gateway/gateway.sock',emit)
    store=AccountStore(directory/'desktop-account.sqlite3',environment,signer.fingerprint)
    due=0
    polling=True
    refresh_pending=False
    state=None
    try:
        emit('ready',mode=environment+'-monitor')
        monitor.start()
        while True:
            if (polling or refresh_pending) and time.monotonic()>=due:
                refresh_pending=False
                try:
                    raw=snapshot(client)
                    store.save(raw)
                    p=raw['positions'][0] if raw['positions'] else None
                    monitor.set_position(p)
                    market=raw.get('market') or {}
                    prices=market.get('prices_dollars') or {}
                    state={'mode':environment+'-monitor','connection':'Connected • read only','running':False,'account_connected':True,
                           'cash_cents':raw['cash_cents'],'pnl_cents':None,'market':market.get('ticker','No open market'),
                           'remaining':max(0,market['cutoff']-time.time()) if market.get('cutoff') else None,'clock_rate':1,'clock_sample_mono':time.monotonic(),'buys':None,'position':p,
                           'entry_cents':None,**quote_state(prices,p,market.get('ticker')),
                           'stop':False,'active_orders':raw['open_orders'],'history':raw['fills'][:100],
                           'events':[], 'notice':'Read-only account view. Existing positions are not managed or protected by this application.'}
                    emit('state',state=state)
                except Exception as exc:
                    store.log('Refresh failed: '+type(exc).__name__)
                    emit('error',message='Account refresh failed ('+type(exc).__name__+'). Check gateway, permissions, and credentials; displayed data is stale.')
                due=time.monotonic()+5
            ready,_,_=select.select([sys.stdin],[],[],1)
            if ready:
                line=sys.stdin.readline(4097)
                if not line: break
                message=json.loads(line)
                command=message.get('command')
                if command=='quit': break
                if command=='start': polling=True; due=0
                elif command=='refresh': refresh_pending=True; due=0
                elif command=='pause': polling=False
    finally:
        monitor.close()
        store.close()


def run_trading(directory,fee_reserve,preflight,environment,authorize_live=False):
    if not preflight:
        from .prepared_runtime import run
        return run(directory,fee_reserve,environment,authorize_live,emit)
    return _run_legacy_trading(directory,fee_reserve,preflight,environment,authorize_live)


def _run_legacy_trading(directory,fee_reserve,preflight,environment,authorize_live=False):
    """Credential preflight and retained synchronous regression reference."""
    from decimal import Decimal
    from .api import Client,GatewayTransport,RateGate
    from .credentials import load
    from .execution import Journal
    from .trade_api import TradingSigner,OrderBroker
    from .trader import cycle
    if environment=='production' and not preflight and not authorize_live:
        raise ValueError('Explicit live-session authorization is required')
    signer=load('/credentials',environment,signer_class=TradingSigner)
    if preflight:
        emit('preflight',message=environment.capitalize()+' trading credentials and isolation passed. No connection attempted.')
        return
    class CachedClient(Client):
        cash=None
        cash_scope='account'
        before_request=None
        def get(self,path,params=None):
            if self.before_request: self.before_request()
            value=super().get(path,params)
            if path=='/portfolio/balance':
                self.cash=cash_cents(value)
                self.cash_scope='market' if params and 'exchange_index' in params else 'account'
            return value
    gate=RateGate()
    client=CachedClient(signer,GatewayTransport('/gateway/gateway.sock'),gate=gate)
    broker=OrderBroker(signer,'/gateway/gateway.sock',True)
    journal=Journal(directory/'desktop-trading.sqlite3',environment+':'+signer.fingerprint)
    from .streams import BookHub
    feed=BookHub(signer,'/gateway/gateway.sock',directory/'desktop-trading.sqlite3')
    wakeup=Wakeup()
    feed.subscribe(wakeup)
    diagnostics=Diagnostics(directory/'desktop-trading.sqlite3')
    journal.on_intent=feed.arm
    monitor=MarketMonitor(Client(signer,GatewayTransport('/gateway/gateway.sock'),gate=gate),signer,'/gateway/gateway.sock',emit,
                          strategy_feed=feed,feed=feed,diagnostics=diagnostics,decision_journal=directory/'desktop-trading.sqlite3',strategy_wakeup=wakeup)
    alive=True
    controls=CommandPipe(sys.stdin)
    def command(message):
        nonlocal alive
        name=message.get('command')
        if name=='quit': alive=False; journal.running=False
        elif name=='start' and alive: journal.running=True
        elif name=='pause': journal.running=False
        monitor.set_execution(journal.running,'Entries '+('enabled' if journal.running else 'paused'))

    def poll_controls(timeout=0,wakeup=None):
        # Read controls between REST requests and immediately before any write.
        # A queued Pause must not wait until after a long entry cycle submits.
        messages=controls.poll(timeout,wakeup=wakeup)
        for message in messages: command(message)
        return bool(messages)
    client.before_request=poll_controls
    journal.before_submit=poll_controls
    def stopping(*_):
        nonlocal alive
        alive=False
        journal.running=False
    signal.signal(signal.SIGTERM,stopping)
    signal.signal(signal.SIGINT,stopping)
    try:
        emit('ready',mode=environment+'-trading')
        restored=journal.position()
        if restored:
            (t,s),n=next(iter(restored.items()))
            monitor.set_position({'ticker':t,'side':s,'qty':str(n)})
        elif journal.active():monitor.set_position(None,journal.active()['ticker'],journal.active()['side'])
        monitor.start()
        last_result={}
        while alive:
            result={}
            failed=False
            try:
                result=cycle(journal,client,broker,Decimal(fee_reserve) if fee_reserve is not None else None,book_feed=feed,
                             market_source=monitor.markets,progress=lambda stage,**v:monitor.set_execution(journal.running,stage,v))
                last_result=result
            except Exception as exc:
                journal.ready=False
                reason=failure_reason(exc)
                journal.log(environment+' cycle blocked: '+reason)
                pending=journal.active()
                detail=' Existing order retained for reconciliation; duplicate submission blocked.' if pending else ' No unresolved order; new entries require fresh checks.'
                emit('error',message=environment.capitalize()+' execution blocked: '+reason+'.'+detail)
                failed=True
                result={**last_result,'status':'Blocked: '+reason}
            held=journal.position()
            if held:
                (ticker,side),quantity=next(iter(held.items()))
                position={'ticker':ticker,'side':side,'qty':str(quantity)}
                row=journal.db.execute('SELECT stop,buys FROM markets WHERE ticker=?',(ticker,)).fetchone()
            else:
                ticker=result.get('ticker','Scanning Bitcoin 15-minute markets')
                position=None
                row=journal.db.execute('SELECT stop,buys FROM markets WHERE ticker=?',(ticker,)).fetchone()
            history=[]
            for fillrow in journal.db.execute('SELECT fills.payload,orders.action,orders.side,orders.ticker FROM fills JOIN orders ON orders.exchange_id=fills.order_id ORDER BY fills.rowid DESC LIMIT 100'):
                fill=json.loads(fillrow['payload'])
                own=fillrow
                history.append({'id':fill['fill_id'],'ticker':own['ticker'],'action':own['action'],'side':own['side'],
                                'qty':fill['count_fp'],'price':str(Decimal(fill[own['side']+'_price_dollars'])*100),
                                'fee':str(Decimal(fill.get('fee_cost','0'))*100)})
            active=journal.active()
            monitor.set_position(position,active['ticker'] if active else None,active['side'] if active else None)
            entry=None
            if position:
                buy=journal.db.execute("SELECT exchange_id FROM orders WHERE ticker=? AND side=? AND action='buy' AND counted=1 ORDER BY rowid DESC LIMIT 1",(position['ticker'],position['side'])).fetchone()
                if buy:
                    entry_fills=[json.loads(r[0]) for r in journal.db.execute('SELECT payload FROM fills WHERE order_id=?',(buy['exchange_id'],))]
                    count=sum((Decimal(f['count_fp']) for f in entry_fills),Decimal(0))
                    if count: entry=str(sum((Decimal(f['count_fp'])*Decimal(f[position['side']+'_price_dollars']) for f in entry_fills),Decimal(0))/count*100)
            prices=feed.quote(ticker)
            events=[{'kind':'audit','detail':r['event'],'at':r['at']} for r in journal.db.execute('SELECT at,event FROM audit ORDER BY id DESC LIMIT 30')]
            emit('state',state={'mode':environment+'-trading','connection':'Reconciliation blocked' if failed else environment.capitalize()+' account connected',
                               'blocked':failed,'status':result.get('status','Scanning markets'),
                               'account_connected':not failed,'selected_side':position['side'] if position else active['side'] if active else result.get('side'),
                               'execution_at':monitor.execution_at,
                               'entry_cents':entry,
                               'running':journal.running,'cash_cents':client.cash,'cash_scope':client.cash_scope,'pnl_cents':None,'market':ticker,
                               'remaining':max(0,(active['cutoff'] if active else result['cutoff'])-time.time()) if active or result.get('cutoff') else None,
                               'clock_rate':1,'clock_sample_mono':time.monotonic(),
                               'buys':row['buys'] if row else 0,'position':position,
                               **quote_state(prices,position,ticker),
                               'stop':bool(position and row and row['stop']), 'active_orders':[active] if active else [],
                               'protective_exit':exit_state(journal,ticker),
                               'history':history,'events':events,
                               'notice':('REAL MONEY • '+('new buys enabled. ' if journal.running else 'new buys paused. ') if environment=='production' else 'Demo funds only. ')+'Sequenced order-book stream; automatic series/event fee reserve.'})
            # No optional post-cycle sleep. Resume fresh checks immediately;
            # the shared API rate gate and server backoff still enforce limits.
            # Drain price notifications and process queued controls without delay.
            poll_controls(0,wakeup=wakeup)
    finally:
        journal.running=False
        active=journal.active()
        if active and active['action']=='buy' and active['exchange_id']:
            try: journal.cancel(broker)
            except Exception: journal.log('Shutdown cancellation unconfirmed; reconcile at restart')
        feed.unsubscribe(wakeup)
        wakeup.close()
        feed.close()
        monitor.close()
        diagnostics.close()
        journal.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['simulation','demo-monitor','production-monitor','demo-trading','production-trading'],default='simulation')
    p.add_argument('--preflight',action='store_true')
    p.add_argument('--fee-reserve')
    p.add_argument('--authorize-live-orders',action='store_true')
    p.add_argument('--buy-min-cents',type=int,default=96)
    p.add_argument('--sell-cents',type=int,default=92)
    p.add_argument('--entry-window-seconds',type=int,default=240)
    p.add_argument('--entry-cutoff-seconds',type=int,default=30)
    p.add_argument('--max-volatility-pct',type=float,default=100.0)
    p.add_argument('--max-volatility-5m-pct',type=float,default=100.0)
    p.add_argument('--max-shock-1m-pct',type=float,default=100.0)
    p.add_argument('--target-buy-usd',type=float,default=50.0)
    p.add_argument('--target-exit-usd',type=float,default=35.0)
    a=p.parse_args()
    try:
        from .strategy import set_policy
        set_policy(a.buy_min_cents,a.sell_cents,a.entry_window_seconds,a.entry_cutoff_seconds,a.max_volatility_pct,a.max_volatility_5m_pct,a.max_shock_1m_pct,a.target_buy_usd,a.target_exit_usd)
        check_isolation()
        resource.setrlimit(resource.RLIMIT_AS,(500*1024*1024,500*1024*1024))
        directory=Path('/state')
        if a.mode=='simulation': run_simulation(directory)
        elif a.mode.endswith('-trading'): run_trading(directory,a.fee_reserve,a.preflight,a.mode.split('-')[0],a.authorize_live_orders)
        else: run_account(directory,a.mode.split('-')[0],a.preflight)
    except Exception as exc:
        import traceback
        location = ' -> '.join(
            f'{Path(frame.filename).name}:{frame.lineno}:{frame.name}'
            for frame in traceback.extract_tb(exc.__traceback__)[-6:]
        )
        emit('error',message='Worker stopped safely ('+type(exc).__name__+') at '+location+'. No fallback outside the sandbox.')
        raise SystemExit(1)


if __name__=='__main__': main()
