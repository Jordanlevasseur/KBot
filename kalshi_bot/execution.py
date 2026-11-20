"""Durable external order journal. Exchange quantities/prices use Decimal.

A pending order is recorded BEFORE the network write. Ambiguous results retain
that same client_order_id and block all subsequent orders until reconciliation.
This module never treats a POST/DELETE response as confirmation of a fill/cancel.
"""
import fcntl
import json
import os
import sqlite3
import time
import uuid
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from .api import APIError, SchemaError, TICKER, decimal
from .trade_api import OrderNotSent
from .strategy import entry_open,contract_target,ENTRY_CUTOFF_SECONDS,policy

D=Decimal
TERMINAL={'executed','canceled','rejected','expired'}


def order_payload(ticker,action,side,price,quantity,cutoff,client_id):
    if not TICKER.fullmatch(ticker) or action not in ('buy','sell') or side not in ('yes','no'):
        raise ValueError('Invalid order identity')
    price,quantity=decimal(price,'limit'),decimal(quantity,'quantity')
    current=policy();minimum=D(current.buy_min_cents)/100;stop=D(current.sell_cents)/100
    if action=='buy' and (price<minimum or price>D('.99') or price*100!=int(price*100) or quantity!=quantity.to_integral_value()):
        raise ValueError('Buy orders require whole contracts and a limit at or above the configured threshold')
    if action=='sell' and price!=stop:
        raise ValueError('Protective sell limit must match the configured trigger')
    if quantity<=0 or quantity*100!=int(quantity*100):
        raise ValueError('Invalid contract quantity')
    # V2 represents everything in YES-leg prices and bid/ask direction.
    yes_price=price if side=='yes' else 1-price
    book_side='bid' if (action=='buy')==(side=='yes') else 'ask'
    return {'ticker':ticker,'client_order_id':client_id,'side':book_side,
            'price':format(yes_price,'.4f'),'count':format(quantity,'f'),
            'time_in_force':'good_till_canceled','expiration_time':int(cutoff)-ENTRY_CUTOFF_SECONDS if action=='buy' else int(cutoff),
            'self_trade_prevention_type':'taker_at_cross','cancel_order_on_pause':True,
            'subaccount':0,'strategy':{'buy_min_cents':current.buy_min_cents,'sell_cents':current.sell_cents,'entry_window_seconds':current.entry_window_seconds,'entry_cutoff_seconds':current.entry_cutoff_seconds}}


def affordable(cash_cents,price,fee_ceiling):
    """Target floor(cash dollars / 2) contracts, capped by cash including fees.

    Fees are reserved from the remaining available cash, not a half-cash dollar
    budget. fee_ceiling must cover fragmentation and cheaper fills, not just p.
    """
    target=contract_target(cash_cents)
    price=decimal(price,'price')
    reserve=decimal(fee_ceiling,'fee reserve')
    if price<D(policy().buy_min_cents)/100 or price>D('.99') or price*100!=int(price*100) or reserve<0:
        raise ValueError('Invalid sizing parameters')
    unit=(price+reserve).quantize(D('.0001'),rounding=ROUND_CEILING)
    return min(target,int((D(cash_cents)/100)//unit))


class Journal:
    def __init__(self,path,identity):
        os.umask(0o077)
        path=Path(path)
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if path.is_symlink():
            raise ValueError('Refusing symlink state')
        self.lock=open(str(path)+'.lock','a')
        try:
            fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError('Another trader or verification process owns this state; close it before reconnecting') from None
        self.db=sqlite3.connect(path)
        os.chmod(path,0o600)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA cache_size=-8192;
        CREATE TABLE IF NOT EXISTS identity(value TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS orders(client_id TEXT PRIMARY KEY,exchange_id TEXT UNIQUE,ticker TEXT,action TEXT,side TEXT,price TEXT,quantity TEXT,cutoff INTEGER,status TEXT,filled TEXT DEFAULT '0',counted INTEGER DEFAULT 0,payload TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS one_unresolved ON orders((1)) WHERE status NOT IN ('executed','canceled','rejected','expired');
        CREATE TABLE IF NOT EXISTS markets(ticker TEXT PRIMARY KEY,buys INTEGER DEFAULT 0 CHECK(buys>=0),stop INTEGER DEFAULT 0,settled INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS fills(id TEXT PRIMARY KEY,order_id TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY,at REAL,payload TEXT);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at REAL,event TEXT);
        CREATE TABLE IF NOT EXISTS settlements(ticker TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS historical_order_verifications(
            client_id TEXT NOT NULL, exchange_id TEXT NOT NULL, ticker TEXT NOT NULL,
            checked_at REAL NOT NULL, evidence TEXT NOT NULL,
            PRIMARY KEY(client_id,checked_at));
        CREATE INDEX IF NOT EXISTS orders_market_side_action ON orders(ticker,side,action);
        CREATE INDEX IF NOT EXISTS fills_order ON fills(order_id);
        ''')
        if 'intent_at' not in {r[1] for r in self.db.execute('PRAGMA table_info(orders)')}:
            self.db.execute('ALTER TABLE orders ADD COLUMN intent_at REAL NOT NULL DEFAULT 0')
        saved=self.db.execute('SELECT value FROM identity').fetchone()
        if saved and saved[0]!=identity:
            self.close()
            raise ValueError('State belongs to a different account/environment')
        if not saved:
            self.db.execute('INSERT INTO identity VALUES(?)',(identity,))
            self.db.commit()
        from .schema import remove_purchase_cap
        try:remove_purchase_cap(self.db)
        except Exception:
            self.close()
            raise
        self.running=False
        self.ready=False
        self._last_snapshot_at=0.
        self._last_reconciliation_evidence=None
        self._last_reconciliation_log_at=0.
        # Optional, non-blocking evidence sink installed by the runtime. It
        # must never influence order decisions or network writes.
        self.research_event=None
        if self.db.execute('PRAGMA quick_check').fetchone()[0]!='ok':
            self.close()
            raise RuntimeError('Corrupt journal')

    def close(self):
        self.db.close()
        self.lock.close()

    def log(self,event):
        try:
            with self.db:
                self.db.execute('INSERT INTO audit(at,event) VALUES(?,?)',(time.time(),event))
        except sqlite3.OperationalError:
            # Diagnostics/audit persistence must never terminate the trading
            # worker. Reconciliation remains the source of truth.
            return

    def research(self, kind, **values):
        """Record structured execution evidence when a sink is configured."""
        sink=self.research_event
        if sink:
            try: sink(kind, values)
            except Exception: pass

    def active(self):
        row=self.db.execute("SELECT * FROM orders WHERE status NOT IN ('executed','canceled','rejected','expired')").fetchone()
        return dict(row) if row else None

    def position(self):
        balances={}
        for row in self.db.execute('SELECT orders.* FROM orders JOIN markets USING(ticker) WHERE markets.settled=0 AND (counted=1 OR action=\'sell\')'):
            key=(row['ticker'],row['side'])
            balances[key]=balances.get(key,D(0))+D(row['filled'])*(1 if row['action']=='buy' else -1)
        return {key:n for key,n in balances.items() if n}

    def reconcile(self,orders,fills,positions,settlements=(),missing_proofs=None):
        """Only complete and independently verified exchange evidence permits trading."""
        self.ready=False
        indexed={o['client_order_id']:o for o in orders if o.get('client_order_id')}
        proofs=missing_proofs or {}
        verified=[]
        prior_changes=self.db.total_changes
        with self.db:
            local_orders=self.db.execute('SELECT * FROM orders').fetchall()
            for row in local_orders:
                remote=indexed.get(row['client_id'])
                if not remote and row['status']=='rejected' and D(row['filled'])==0:
                    continue
                if not remote and row['client_id'] in proofs:
                    from .missing_order import verified_missing_order
                    proof=proofs[row['client_id']]
                    remote=verified_missing_order(row,proof,local_orders,orders,fills,positions,settlements)
                    verified.append((row['client_id'],row['exchange_id'],row['ticker'],
                                     proof['verified_at'],
                                     'direct' if remote else 'settled_zero_fill'))
                    if not remote:
                        continue
                if not remote:
                    raise RuntimeError(
    'Known order absent: ' + str(row['client_id']) +
    ' ticker=' + str(row['ticker']) +
    ' status=' + str(row['status']) +
    '; reconcile before retrying'
)
                if remote.get('ticker')!=row['ticker'] or remote.get('type')!='limit':
                    raise SchemaError('Remote order identity mismatch')
                if decimal(remote['initial_count_fp'],'initial count')!=D(row['quantity']) or decimal(remote['yes_price_dollars'],'order price')!=D(json.loads(row['payload'])['price']):
                    raise SchemaError('Remote price or quantity differs from submitted limit')
                filled=decimal(remote['fill_count_fp'],'filled')
                remaining=decimal(remote['remaining_count_fp'],'remaining')
                status=remote['status']
                if status not in TERMINAL|{'resting','pending'} or filled<D(row['filled']) or filled>D(row['quantity']) or remaining<0:
                    raise SchemaError('Invalid remote order state')
                if status=='executed' and remaining!=0:
                    raise SchemaError('Executed order still has remaining quantity')
                if remote['order_id']!=row['exchange_id'] and row['exchange_id'] is not None:
                    raise SchemaError('Exchange order ID changed')
                if filled and row['action']=='buy' and not row['counted']:
                    self.db.execute('UPDATE markets SET buys=buys+1 WHERE ticker=?',(row['ticker'],))
                counted=int(bool(filled) or row['counted'])
                if (row['exchange_id'],D(row['filled']),row['status'],row['counted'])!=(remote['order_id'],filled,status,counted):
                    self.db.execute('UPDATE orders SET exchange_id=?,filled=?,status=?,counted=? WHERE client_id=?',
                                    (remote['order_id'],str(filled),status,counted,row['client_id']))
            # Index the complete snapshot once, without omitting any records or
            # trusting a cache from an earlier cycle. Preserve duplicate counts
            # so repeated/incomplete fill feeds still block reconciliation.
            owned={row['exchange_id']:row for row in self.db.execute('SELECT * FROM orders')}
            fill_counts={}
            for fill in fills:
                own=owned.get(fill['order_id'])
                if own:
                    price=decimal(fill[own['side']+'_price_dollars'],'fill price')
                    if not 0<=price<=1 or (own['action']=='buy' and price>D(own['price'])) or (own['action']=='sell' and price<D(own['price'])):
                        raise SchemaError('Fill violates order price')
                    inserted=self.db.execute('INSERT OR IGNORE INTO fills VALUES(?,?,?)',(fill['fill_id'],fill['order_id'],json.dumps(fill))).rowcount
                    if inserted:
                        self.research('fill', fill_id=fill.get('fill_id'), order_id=fill.get('order_id'),
                                      ticker=own['ticker'], action=own['action'], side=own['side'],
                                      quantity=fill.get('count_fp'), price=fill.get(own['side']+'_price_dollars'),
                                      fee=fill.get('fee_cost'))
                    fill_counts[fill['order_id']]=fill_counts.get(fill['order_id'],D(0))+decimal(fill['count_fp'],'fill count')
            known=set(owned)
            if any(o.get('status') in ('resting','pending') and o['order_id'] not in known for o in orders):
                raise RuntimeError('Unmanaged open order; manual reconciliation required')
            owned_tickers={r[0] for r in self.db.execute('SELECT ticker FROM markets')}
            for settlement in settlements:
                ticker=settlement.get('ticker')
                if ticker not in owned_tickers: continue
                pending=self.db.execute("SELECT 1 FROM orders WHERE ticker=? AND status NOT IN ('executed','canceled','rejected','expired')",(ticker,)).fetchone()
                if not pending and ticker is not None:
                    changed=self.db.execute('INSERT INTO settlements VALUES(?,?) ON CONFLICT(ticker) DO UPDATE SET payload=excluded.payload WHERE settlements.payload!=excluded.payload',(ticker,json.dumps(settlement))).rowcount
                    self.db.execute('UPDATE markets SET settled=1 WHERE ticker=? AND settled=0',(ticker,))
                    if changed:
                        self.research('settlement', ticker=ticker, settlement=settlement)
            expected=self.position()
            actual={}
            for pos in positions:
                n=decimal(pos['position_fp'],'position')
                if n:
                    actual[(pos['ticker'],'yes' if n>0 else 'no')]=abs(n)
            if expected!=actual or len(expected)>1 or any(n<0 for n in expected.values()):
                raise RuntimeError('Account positions do not match owned fills')
            for row in owned.values():
                if fill_counts.get(row['exchange_id'],D(0))!=D(row['filled']):
                    raise RuntimeError('Fill feed incomplete; wait for reconciliation')
            # All remote records above still participate in reconciliation.
            # Avoid rewriting unrelated, completed account history on every tick.
            relevant=[o for o in orders if o.get('order_id') in known or o.get('status') in ('resting','pending')]
            # An identical account needs periodic freshness, not a new WAL
            # record on every snapshot. Real order/fill changes write promptly.
            clock=time.monotonic()
            if self.db.total_changes!=prior_changes or clock-self._last_snapshot_at>=5:
                self.db.execute('INSERT INTO snapshots(at,payload) VALUES(?,?)',
                                (time.time(),json.dumps({'positions':positions,'orders':relevant})))
                self._last_snapshot_at=clock
            for item in verified:
                self.db.execute('''INSERT OR IGNORE INTO historical_order_verifications
                    (client_id,exchange_id,ticker,checked_at,evidence) VALUES(?,?,?,?,?)''',item)
        self.ready=True
        evidence=(len(orders),len(fills),json.dumps(positions,sort_keys=True,default=str),len(settlements))
        now=time.monotonic()
        if evidence!=self._last_reconciliation_evidence or now-self._last_reconciliation_log_at>=30:
            self.research('reconciliation',orders_seen=len(orders),fills_seen=len(fills),
                          positions=positions,settlements=len(settlements),ready=True)
            self._last_reconciliation_evidence=evidence
            self._last_reconciliation_log_at=now

    def submit(self,broker,ticker,action,side,price,quantity,cutoff,now,*,prepared_payload=None):
        if getattr(self,'before_submit',None): self.before_submit()
        if not self.ready or self.active():
            raise RuntimeError('Account not reconciled or an order is unresolved')
        held=self.position()
        if action=='buy':
            if not self.running or held or not entry_open(cutoff,now):
                raise RuntimeError('Entry not eligible')
        else:
            row=self.db.execute('SELECT * FROM markets WHERE ticker=?',(ticker,)).fetchone()
            if not row or not row['stop'] or held!={(ticker,side):D(str(quantity))} or now>=cutoff:
                raise RuntimeError('Protective exit not eligible')
        cid=prepared_payload['client_order_id'] if prepared_payload is not None else str(uuid.uuid4())
        payload=order_payload(ticker,action,side,price,quantity,cutoff,cid)
        if prepared_payload is not None and dict(prepared_payload)!=payload:
            raise ValueError('Prepared order does not match validated intent')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO markets(ticker) VALUES(?)',(ticker,))
            if action=='buy':
                self.db.execute('UPDATE markets SET stop=0 WHERE ticker=?',(ticker,))
            self.db.execute('INSERT INTO orders(client_id,ticker,action,side,price,quantity,cutoff,status,payload,intent_at) VALUES(?,?,?,?,?,?,?,\'pending\',?,?)',
                            (cid,ticker,action,side,str(price),str(quantity),cutoff,json.dumps(payload),time.time()))
            if getattr(self,'on_recorded',None):self.on_recorded(payload)
        self.research('order_intent', ticker=ticker, action=action, side=side,
                      price=str(price), quantity=str(quantity), cutoff=cutoff,
                      client_order_id=cid)
        self.ready=False
        if action=='buy' and getattr(self,'on_intent',None):
            try:self.on_intent(ticker,side)
            except Exception:
                # This hook runs BEFORE the HTTP attempt, after the durable intent.
                with self.db:self.db.execute("UPDATE orders SET status='rejected' WHERE client_id=?",(cid,))
                raise
        # Deliberately no retry/new UUID here. Any error leaves durable pending.
        try:
            if getattr(self,'before_transmit',None):self.before_transmit(payload)
            response=broker.create(payload)
        except OrderNotSent:
            with self.db:
                self.db.execute("UPDATE orders SET status='rejected' WHERE client_id=?",(cid,))
            raise
        except APIError as exc:
            if exc.status in (400,401,403,422,429):
                with self.db:
                    self.db.execute("UPDATE orders SET status='rejected' WHERE client_id=?",(cid,))
            raise
        if response.get('client_order_id')!=cid or not response.get('order_id'):
            raise SchemaError('Order acknowledgment mismatch')
        with self.db:
            self.db.execute('UPDATE orders SET exchange_id=? WHERE client_id=?',(response['order_id'],cid))
        self.research('order_ack', ticker=ticker, action=action, side=side,
                      client_order_id=cid, exchange_order_id=response['order_id'])
        return cid

    def recover_pending(self,broker,orders,now,progress=None):
        order=self.active()
        if not order or order['exchange_id'] or any(o.get('client_order_id')==order['client_id'] for o in orders):
            return
        if getattr(self,'before_submit',None): self.before_submit()
        # The exchange reserves client IDs only while an order is open. An
        # accepted order can fill and disappear from a delayed snapshot, making
        # even a same-ID replay a NEW order. Absence never proves nonacceptance.
        if now>=order['cutoff']:
            raise RuntimeError('Expired ambiguous submission; manual reconciliation required')
        if order['action']=='buy' and not self.running:
            raise RuntimeError('Unconfirmed buy while entries paused; awaiting exchange reconciliation')
        if progress:progress('Awaiting uncertain order reconciliation',ticker=order['ticker'],side=order['side'],action=order['action'])
        self.ready=False
        raise RuntimeError('Unconfirmed order absent from exchange snapshot; automatic resubmission blocked until reconciliation')

    def latch_stop(self,ticker,side,bid):
        held=self.position().get((ticker,side),D(0))
        if held and bid is not None and decimal(bid,'bid')<=D(policy().sell_cents)/100:
            with self.db:
                self.db.execute('UPDATE markets SET stop=1 WHERE ticker=?',(ticker,))
        row=self.db.execute('SELECT stop FROM markets WHERE ticker=?',(ticker,)).fetchone()
        return bool(row and row[0])

    def cancel(self,broker):
        order=self.active()
        if not order or not order['exchange_id']:
            raise RuntimeError('Resolve order identity before canceling')
        self.ready=False
        with self.db:
            self.db.execute("UPDATE orders SET status='pending' WHERE client_id=?",(order['client_id'],))
        broker.cancel(order['exchange_id'],order['ticker'])
        # DELETE acknowledgment is not proof of cancellation or absence of fills.
