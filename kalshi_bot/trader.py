"""Headless/terminal external trading candidate. Offline preflight is the default."""
import argparse
import json
import select
import resource
import signal
import sys
import time
from decimal import Decimal
from .account import cutoff
from .api import APIError, Client, GatewayTransport, SchemaError, book_prices
from .credentials import load
from .execution import Journal, affordable
from .security import check_isolation
from .trade_api import OrderBroker, TradingSigner
from .discovery import open_markets
from .eligibility import eligible_sides,ordered_candidates,entry_limit
from .diagnostics import failure_reason
from .streams import BookHub
from .strategy import ENTRY_WINDOW_SECONDS,ENTRY_CUTOFF_SECONDS,entry_open
from .buying_power import market_cash,market_scope
from .strategy import policy


def cycle(journal,client,broker,fee_reserve,now=None,book_feed=None,market_source=None,progress=None):
    started=time.monotonic()
    evidence={'stage':'Starting account reconciliation'}
    before=journal.db.execute('SELECT client_id FROM orders ORDER BY rowid DESC LIMIT 1').fetchone()
    def step(stage,**values):
        evidence.update(stage=stage,**values)
        if progress:progress(stage,**values)
    step('Reconciling account')
    try:
        result=_cycle(journal,client,broker,fee_reserve,now,book_feed,market_source,step)
    except Exception as exc:
        last=journal.db.execute('SELECT client_id,status FROM orders ORDER BY rowid DESC LIMIT 1').fetchone()
        new=bool(last and (not before or last['client_id']!=before['client_id']))
        outcome=('rejected' if last['status']=='rejected' else 'submission uncertain') if new else ('recovery attempted; awaiting reconciliation' if evidence.get('submission_attempted') else 'blocked before submission')
        failure={**evidence,'reason':failure_reason(exc),'exception':type(exc).__name__,'http_status':getattr(exc,'status',None),
                 'exchange_code':getattr(exc,'code',None),
                 'entries_enabled':bool(journal.running),'outcome':outcome,'cycle_seconds':round(time.monotonic()-started,3)}
        journal.log('Decision failure: '+json.dumps(failure,sort_keys=True,default=str))
        step('Blocked: '+failure['reason'],outcome=outcome)
        raise
    result['entries_enabled']=bool(journal.running)
    result['cycle_seconds']=round(time.monotonic()-started,3)
    result['decision_at']=time.time()
    from .protective import exit_state
    result['protective_exit']=exit_state(journal,result.get('ticker'))
    # Record the decision rather than just cash. Repeated unchanged waiting
    # states are rate limited; entry-window price checks remain inspectable.
    stamp=time.monotonic()
    detail={k:v for k,v in result.items() if k not in ('cycle_seconds','decision_at')}
    text=json.dumps(detail,sort_keys=True,default=str)
    if text!=getattr(journal,'_last_decision',None) or stamp-getattr(journal,'_last_decision_at',0)>=30:
        journal.log('Decision: '+json.dumps(result,sort_keys=True,default=str))
        journal._last_decision=text
        journal._last_decision_at=stamp
    if progress:progress(result['status'],ticker=result.get('ticker'),side=result.get('side'))
    return result


def _cycle(journal,client,broker,fee_reserve,now=None,book_feed=None,market_source=None,step=lambda *a,**k:None):
    use_wall_clock=now is None
    now=time.time() if use_wall_clock else now
    if book_feed:
        existing=journal.position()
        if existing: book_feed.watch(next(iter(existing))[0])
        elif journal.active(): book_feed.watch(journal.active()['ticker'])
    # Read orders, then fills, then positions. Any projection lag blocks writes.
    orders=client.pages('/portfolio/orders')
    fills=client.pages('/portfolio/fills')
    positions=client.pages('/portfolio/positions')
    settlements=client.pages('/portfolio/settlements')
    journal.recover_pending(broker,orders,now,progress=step)
    journal.reconcile(orders,fills,positions,settlements)
    cash_read=False
    def cash(market=None):
        nonlocal cash_read
        cents=market_cash(client,market) if market is not None else client.get('/portfolio/balance')['balance']
        journal.log(('Reconciled Bitcoin market cash cents: ' if market is not None else 'Reconciled cash cents: ')+str(cents))
        cash_read=True
        return cents
    # Held/pending risk keeps the original balance check before any action.
    # A flat candidate reads cash once, at sizing, rather than twice in the
    # same cycle. No previous-cycle cash or account snapshot authorizes a write.
    if journal.position() or journal.active():cash()
    result=_evaluate(journal,client,broker,fee_reserve,now,book_feed,market_source,step,use_wall_clock,cash)
    if not cash_read:cash()  # Keep paused/nonqualifying dashboard cash current.
    return result


def _evaluate(journal,client,broker,fee_reserve,now,book_feed,market_source,step,use_wall_clock,read_cash):
    # Reconciliation may take several seconds or cross a market boundary.
    if use_wall_clock: now=time.time()
    held=journal.position()
    active=journal.active()
    if held:
        (ticker,side),quantity=next(iter(held.items()))
        market=client.get('/markets/'+ticker)['market']
        close=cutoff(market)
        if use_wall_clock: now=time.time()
        if now>=close or market.get('status') not in ('open','active'):
            return {'status':'Trading closed; awaiting account settlement','position':str(quantity),'ticker':ticker,'cutoff':close}
        if book_feed:
            book_feed.watch(ticker)
            prices=book_feed.quote(ticker)
            if prices is None:
                row=journal.db.execute('SELECT stop FROM markets WHERE ticker=?',(ticker,)).fetchone()
                if not row or not row[0]:
                    return {'status':'Waiting for a fresh sequenced order-book snapshot','ticker':ticker,'cutoff':close}
                prices={'yes_bid':None,'no_bid':None}
        else: prices=book_prices(client.get('/markets/'+ticker+'/orderbook',{'depth':10}))
        stop=journal.latch_stop(ticker,side,prices[side+'_bid'])
        if active and active['action']=='buy' and (stop or not journal.running or not entry_open(close,now)):
            step('Canceling residual buy before protective exit' if stop else 'Canceling paused buy',ticker=ticker,side=side,quantity=str(quantity))
            journal.cancel(broker)
        elif stop and not active:
            send_now=time.time() if use_wall_clock else now
            if send_now<close:
                from .exit_price import protective_limit
                stop=protective_limit(market,side)
                step('Submitting protective sell',ticker=ticker,side=side,limit=str(stop),quantity=str(quantity))
                journal.submit(broker,ticker,'sell',side,stop,quantity,close,send_now)
        return {'status':'Stop latched' if stop else 'Holding to settlement','ticker':ticker,'quantity':str(quantity),'bid':str(prices[side+'_bid']),'cutoff':close}
    if active:
        stopped=journal.db.execute('SELECT stop FROM markets WHERE ticker=?',(active['ticker'],)).fetchone()
        if active['action']=='buy' and (not journal.running or not entry_open(active['cutoff'],now) or (stopped and stopped['stop'])):
            step('Canceling stopped pending buy' if stopped and stopped['stop'] else 'Canceling pending buy',ticker=active['ticker'],side=active['side'])
            journal.cancel(broker)
        return {'status':'Maintaining/reconciling existing order','ticker':active['ticker'],'cutoff':active['cutoff']}
    if not journal.running:
        if book_feed:
            visible=market_source() if market_source else open_markets(client,now)
            visible=[m for m in visible if cutoff(m)>now]
            if visible:
                market=min(visible,key=cutoff)
                book_feed.watch(market['ticker'])
                prices=book_feed.quote(market['ticker'])
                return {'status':'Entries paused; protective exits remain enabled','ticker':market['ticker'],'cutoff':cutoff(market),
                        'bid':str(prices['yes_bid']) if prices else None}
        return {'status':'Entries paused; protective exits remain enabled'}
    exchange=client.get('/exchange/status')
    if exchange.get('trading_active') is not True:
        return {'status':'Exchange is not trading'}
    markets=market_source() if market_source else open_markets(client,now)
    if use_wall_clock:now=time.time()
    visible={'status':'No open Bitcoin 15-minute market'}
    if markets:
        current=markets[0]
        visible={'status':f'Waiting for the final {ENTRY_WINDOW_SECONDS//60}-minute entry window' if cutoff(current)-now>ENTRY_WINDOW_SECONDS else f'No qualifying {policy().buy_min_cents}–99¢ entry',
                 'ticker':current['ticker'],'cutoff':cutoff(current)}
        if 0<cutoff(current)-now<=ENTRY_CUTOFF_SECONDS:visible['status']='New purchases blocked during final 30 seconds'
        if book_feed:
            book_feed.watch(current['ticker'])
            quote=book_feed.quote(current['ticker'])
            visible['bid']=str(quote['yes_bid']) if quote else None
            visible.update({s+'_ask':str(quote[s+'_ask']) if quote and quote[s+'_ask'] is not None else None for s in ('yes','no')})
    for market in sorted(markets,key=cutoff):
        ticker=market['ticker']
        close=cutoff(market)
        step('Checking executable prices',ticker=ticker,cutoff=close)
        if market.get('market_type')!='binary' or not entry_open(close,now):
            continue
        if book_feed:
            book_feed.watch(ticker)
            frame=book_feed.frame(ticker) if isinstance(book_feed,BookHub) else None
            prices=frame['prices'] if frame else book_feed.quote(ticker)
            if frame:step('Checking book freshness',ticker=ticker,book_status=frame['status'],sequence=frame['sequence'],verified_age=frame['verified_age'])
            if prices is None: return {'status':'Waiting for a fresh sequenced order-book snapshot','ticker':ticker,'cutoff':close}
        else: prices=book_prices(client.get('/markets/'+ticker+'/orderbook',{'depth':10}))
        visible.update({s+'_ask':str(prices[s+'_ask']) if prices[s+'_ask'] is not None else None for s in ('yes','no')})
        visible['eligible_sides']=eligible_sides(prices)
        prior=journal.db.execute("SELECT side FROM orders WHERE ticker=? AND action='buy' AND counted=1 ORDER BY rowid DESC LIMIT 1",(ticker,)).fetchone()
        for side in ordered_candidates(prices,ticker,prior['side'] if prior else None):
            ask=Decimal(str(prices[side+'_ask']))
            # Sub-cent asks round UP to one of the five allowed submitted limits.
            price=entry_limit(ask)
            step('Checking balance and fees',ticker=ticker,side=side,ask=str(ask))
            scope=market_scope(market)
            cash=read_cash(market)
            reserve=fee_reserve
            if book_feed or reserve is None:
                from .fees import entry_reserve
                reserve=max(entry_reserve(client,market),fee_reserve or Decimal(0))
            quantity=affordable(cash,price,reserve)
            if not quantity:
                return {**visible,'status':'Contract target is zero or available cash cannot cover one contract plus fees','side':side,
                        'limit':str(price),'cash_cents':cash,'fee_reserve_dollars':str(reserve)}
            # Re-read official cutoff/status immediately before new risk.
            fresh=client.get('/markets/'+ticker)['market']
            if market_scope(fresh)!=scope:
                return {**visible,'status':'Bitcoin cash scope changed during checks; waiting for fresh market data'}
            fresh_now=time.time() if use_wall_clock else now
            fresh_close=min(close,cutoff(fresh))
            if quantity and fresh.get('status') in ('open','active') and entry_open(fresh_close,fresh_now):
                final_ask=ask
                if book_feed:
                    frame=book_feed.frame(ticker) if isinstance(book_feed,BookHub) else None
                    latest=frame['prices'] if frame else book_feed.quote(ticker)
                    if frame:step('Final price validation',ticker=ticker,side=side,book_status=frame['status'],sequence=frame['sequence'],
                                  ask=str(latest[side+'_ask']) if latest and latest[side+'_ask'] is not None else None)
                    if side not in eligible_sides(latest):
                        return {**visible,'status':'Buy price changed during checks; waiting for the next qualifying quote','ticker':ticker,'cutoff':fresh_close,
                                'side':side,'limit':str(price),'latest_ask':str(latest[side+'_ask']) if latest else None}
                    # A move from 95c to 97c still qualifies. Reprice and resize
                    # against the current quote instead of skipping that entry.
                    price=entry_limit(latest[side+'_ask'])
                    final_ask=Decimal(str(latest[side+'_ask']))
                    quantity=affordable(cash,price,reserve)
                send_now=time.time() if use_wall_clock else now
                if not quantity:
                    return {**visible,'status':'Available cash cannot cover the contract target plus fees','side':side,'limit':str(price),'cash_cents':cash}
                if not entry_open(fresh_close,send_now):
                    return {**visible,'status':'Official trading cutoff reached during checks'}
                step('Submitting buy',ticker=ticker,side=side,ask=str(final_ask),limit=str(price),quantity=quantity)
                journal.submit(broker,ticker,'buy',side,price,quantity,fresh_close,send_now)
                return {'status':'Buy submitted; awaiting confirmed fills','ticker':ticker,'side':side,'executable_ask':str(final_ask),'limit':str(price),'quantity':quantity,'cutoff':fresh_close}
            return {**visible,'status':'Official trading cutoff reached or market is no longer active'}
    return visible


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--environment',choices=['demo','production'],default='demo')
    p.add_argument('--credentials',default='/credentials')
    p.add_argument('--socket',default='/gateway/gateway.sock')
    p.add_argument('--db',default='/state/trading.sqlite3')
    p.add_argument('--preflight',action='store_true')
    p.add_argument('--connect',action='store_true')
    p.add_argument('--trade',action='store_true',help='Explicitly enable this invocation to submit orders')
    p.add_argument('--authorize-live-orders',action='store_true',help='Separate explicit authorization for production money')
    p.add_argument('--fee-reserve',type=Decimal,help='Verified maximum dollars of fees per contract, including fragmented cheaper fills')
    p.add_argument('--interval',type=float,default=3)
    p.add_argument('--headless',action='store_true')
    a=p.parse_args()
    if not a.preflight and not (a.connect and a.trade):
        p.error('No connection attempted; use --preflight, or --connect --trade for an authorized trading session')
    if a.environment=='production' and not a.preflight and not a.authorize_live_orders:
        p.error('Production requires separate --authorize-live-orders; no connection attempted')
    if not a.preflight and (a.fee_reserve is None or not a.fee_reserve.is_finite() or a.fee_reserve<=0 or a.interval<1):
        p.error('A verified positive --fee-reserve and interval >=1 are required')
    check_isolation()
    resource.setrlimit(resource.RLIMIT_AS,(500*1024*1024,500*1024*1024))
    signer=load(a.credentials,a.environment,signer_class=TradingSigner)
    if a.preflight:
        print('Offline trading preflight passed. No connection or order submitted.')
        return
    journal=Journal(a.db,a.environment+':'+signer.fingerprint)
    client=Client(signer,GatewayTransport(a.socket))
    broker=OrderBroker(signer,a.socket,enabled=True)
    from .streams import BookFeed
    feed=BookFeed(signer,a.socket,a.db)
    # New entries require s in the terminal; --headless explicitly starts them.
    journal.running=a.headless
    exiting=False
    def stop_signal(*_):
        nonlocal exiting
        journal.running=False
        exiting=True
    signal.signal(signal.SIGTERM,stop_signal)
    signal.signal(signal.SIGINT,stop_signal)
    try:
        while not exiting:
            try:
                state=cycle(journal,client,broker,a.fee_reserve,book_feed=feed)
            except (APIError,SchemaError,RuntimeError,ValueError,KeyError) as exc:
                journal.ready=False
                journal.log('Trading cycle blocked: '+type(exc).__name__+((' HTTP '+str(exc.status)) if isinstance(exc,APIError) else ''))
                state={'status':'Reconciliation/network error; new orders blocked. Pending orders and stops retained.'}
            state['active_order']=journal.active()
            state['buys_used']={r['ticker']:r['buys'] for r in journal.db.execute('SELECT ticker,buys FROM markets WHERE settled=0')}
            state['entries_enabled']=journal.running
            print(json.dumps(state),flush=True)
            if a.headless:
                time.sleep(a.interval)
                continue
            print('s=start entries | p=pause/cancel residual buys | q=exit (processing stops)',flush=True)
            ready,_,_=select.select([sys.stdin],[],[],a.interval)
            if ready:
                command=sys.stdin.readline().strip().lower()
                if command=='s': journal.running=True
                elif command=='p': journal.running=False
                elif command=='q': exiting=True
    finally:
        journal.running=False
        active=journal.active()
        if active and active['action']=='buy' and active['exchange_id']:
            try:
                journal.cancel(broker)
            except (APIError,ValueError,RuntimeError):
                journal.log('Shutdown buy cancellation unconfirmed; reconcile on restart; exchange expiry remains in force')
        feed.close()
        journal.close()


if __name__=='__main__':
    main()
