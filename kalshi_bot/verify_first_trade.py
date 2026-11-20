"""One specifically authorized live buy; manage it through exit/settlement.

No key is printed. No trade may be forced outside the configured entry strategy.
The existing production order journal is held exclusively throughout this process.
"""
import argparse
import json
import os
from pathlib import Path
import time
from decimal import Decimal as D
from .api import Client,GatewayTransport,APIError
from .credentials import load
from .execution import Journal,TERMINAL
from .trade_api import TradingSigner,OrderBroker
from .streams import BookFeed
from .trader import cycle
from .security import check_isolation


def emit(**fields): print(json.dumps(fields,default=str),flush=True)


class SingleBuyBroker:
    def __init__(self,journal,broker):
        self.journal,self.broker=journal,broker

    def create(self,payload):
        j=self.journal
        current=j.db.execute('SELECT action FROM orders WHERE client_id=?',(payload['client_order_id'],)).fetchone()
        if not current: raise RuntimeError('Missing durable order intent')
        if current['action']=='buy':
            prior=j.db.execute("SELECT status,filled FROM orders WHERE action='buy' AND client_id!=?",(payload['client_order_id'],)).fetchall()
            if any(D(r['filled'])>0 or r['status'] not in TERMINAL for r in prior):
                raise RuntimeError('One-buy authorization is already used or unresolved')
            with j.db:
                j.db.execute("UPDATE first_trade_verification SET client_id=?,status='buy_pending' WHERE id=1",(payload['client_order_id'],))
        return self.broker.create(payload)

    def cancel(self,order_id,ticker): return self.broker.cancel(order_id,ticker)


def summary(journal,client):
    rows=journal.db.execute("SELECT * FROM orders WHERE action='buy' AND CAST(filled AS REAL)>0").fetchall()
    buys=[]
    for order in rows:
        fills=[json.loads(r['payload']) for r in journal.db.execute('SELECT payload FROM fills WHERE order_id=?',(order['exchange_id'],))]
        quantity=sum((D(f['count_fp']) for f in fills),D(0))
        cost=sum((D(f['count_fp'])*D(f[order['side']+'_price_dollars']) for f in fills),D(0))
        fees=sum((D(f.get('fee_cost','0')) for f in fills),D(0))
        buys.append({'market':order['ticker'],'side':order['side'],'order_id':order['exchange_id'],
                     'limit_dollars':order['price'],'filled_contracts':str(quantity),
                     'average_fill_dollars':str(cost/quantity) if quantity else None,
                     'actual_cost_dollars':str(cost),'actual_fees_dollars':str(fees)})
    return {'buys':buys,'positions':{t+':'+s:str(n) for (t,s),n in journal.position().items()},
            'active_order':journal.active(),'cash_cents':client.get('/portfolio/balance')['balance'],
            'settlements':[json.loads(r['payload']) for r in journal.db.execute('SELECT payload FROM settlements')]}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--authorize-one-live-buy',action='store_true')
    p.add_argument('--max-wait',type=int,default=1200)
    a=p.parse_args()
    if not a.authorize_one_live_buy: p.error('Explicit one-live-buy authorization required; no connection attempted')
    if not 1<=a.max_wait<=1800: p.error('Invalid verification timeout')
    check_isolation()
    signer=load('/credentials','production',signer_class=TradingSigner)
    path=Path('/state/desktop-trading.sqlite3')
    journal=Journal(path,'production:'+signer.fingerprint)
    client=Client(signer,GatewayTransport('/gateway/gateway.sock'))
    feed=BookFeed(signer,'/gateway/gateway.sock',path)
    broker=SingleBuyBroker(journal,OrderBroker(signer,'/gateway/gateway.sock',True))
    journal.db.execute('CREATE TABLE IF NOT EXISTS first_trade_verification(id INTEGER PRIMARY KEY CHECK(id=1),status TEXT,client_id TEXT,report TEXT)')
    journal.db.commit()
    existing=journal.db.execute('SELECT status FROM first_trade_verification WHERE id=1').fetchone()
    if existing and existing['status']=='completed':
        emit(status='already_completed',report=summary(journal,client)); feed.close(); journal.close(); return
    with journal.db:
        journal.db.execute("INSERT OR IGNORE INTO first_trade_verification(id,status) VALUES(1,'authorized')")
    # No fresh buy is allowed if any prior managed buy has a confirmed fill.
    has_fill=journal.db.execute("SELECT 1 FROM orders WHERE action='buy' AND CAST(filled AS REAL)>0").fetchone() is not None
    journal.running=not has_fill
    started=time.monotonic(); next_status=0; errors=0
    try:
        while True:
            try:
                state=cycle(journal,client,broker,None,book_feed=feed)
                errors=0
            except Exception as exc:
                journal.ready=False; errors+=1
                emit(status='waiting_for_consistent_state',kind=type(exc).__name__,http=getattr(exc,'status',None))
                if errors>=10: raise RuntimeError('Repeated preflight/execution errors; no new buy will be authorized') from None
                time.sleep(2); continue
            filled=journal.db.execute("SELECT 1 FROM orders WHERE action='buy' AND CAST(filled AS REAL)>0").fetchone() is not None
            active=journal.active()
            if filled and (active is None or active['action']!='buy'):
                journal.running=False
            if filled and not has_fill:
                has_fill=True
                emit(status='first_buy_fill_confirmed',report=summary(journal,client))
            if filled and not journal.position() and active is None:
                report=summary(journal,client)
                with journal.db:
                    journal.db.execute("UPDATE first_trade_verification SET status='completed',report=? WHERE id=1",(json.dumps(report),))
                emit(status='verification_completed',report=report)
                return
            if not filled and time.monotonic()-started>=a.max_wait:
                journal.running=False
                if active and active['action']=='buy': journal.cancel(broker)
                if not active:
                    emit(status='no_qualifying_fill',report=summary(journal,client)); return
            if time.monotonic()>=next_status:
                emit(status=state['status'],market=state.get('ticker'),cutoff=state.get('cutoff'),
                     new_entries_enabled=journal.running,has_confirmed_buy=filled,active_order=active['action'] if active else None)
                next_status=time.monotonic()+20
            time.sleep(2)
    finally:
        journal.running=False
        active=journal.active()
        if active and active['action']=='buy' and active['exchange_id']:
            try: journal.cancel(broker)
            except Exception: journal.log('Verification stopped with unresolved cancellation; recover before further entries')
        feed.close(); journal.close()


if __name__=='__main__': main()
