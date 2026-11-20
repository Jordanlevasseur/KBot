"""Transactional simulated exchange and strategy; all monetary amounts are cents.

One process owns the database. Each tick is one durable transaction. Simulation
fills at the displayed ask, including price improvement below the entry band.
"""
import fcntl
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from .strategy import entry_open,contract_target,policy


@dataclass(frozen=True)
class Quote:
    ticker: str
    cutoff: int
    now: int
    yes_bid: int | None
    no_bid: int | None
    yes_ask: int | None
    no_ask: int | None
    liquidity: int = 100000
    connected: bool = True
    result: str | None = None
    series: str = 'KXBTC15M'

    def validate(self):
        if self.series != 'KXBTC15M' or not self.ticker.startswith('KXBTC15M-'):
            raise ValueError('Ineligible series')
        if self.result not in (None, 'yes', 'no'):
            raise ValueError('Unknown settlement')
        if self.result and self.now < self.cutoff:
            raise ValueError('Settlement before cutoff')
        if type(self.liquidity) is not int or self.liquidity < 0:
            raise ValueError('Invalid liquidity')
        for price in (self.yes_bid, self.no_bid, self.yes_ask, self.no_ask):
            if price is not None and (type(price) is not int or not 0 <= price <= 100):
                raise ValueError('Invalid cents')


def fee(quantity, price):
    """Simulation fee: conservative per-contract ceil(7% * p * (1-p)).

    Per-contract rounding reserves for fragmented fills. Not a live fee quote.
    """
    return quantity * ((7 * price * (100-price) + 9999) // 10000)


def size(cash, price):
    if type(price) is not int or price not in range(policy().buy_min_cents, 100):
        raise ValueError('Entry must be at or above the configured threshold')
    return min(contract_target(cash),cash//(price+2))


class Engine:
    def __init__(self, path, cash=100000):
        os.umask(0o077)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise RuntimeError('Refusing symlink database')
        self.lock = open(str(self.path) + '.lock', 'a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError('Another bot owns this database') from None
        self.db = sqlite3.connect(self.path, isolation_level=None)
        os.chmod(self.path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS account(id INTEGER PRIMARY KEY CHECK(id=1), cash INTEGER NOT NULL CHECK(cash>=0), pnl INTEGER NOT NULL, running INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS markets(ticker TEXT PRIMARY KEY, buys INTEGER NOT NULL DEFAULT 0 CHECK(buys>=0), cutoff INTEGER NOT NULL, last_tick INTEGER NOT NULL DEFAULT -1, settled INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS positions(ticker TEXT PRIMARY KEY, side TEXT NOT NULL, qty INTEGER NOT NULL CHECK(qty>=0), cost INTEGER NOT NULL, stop INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, action TEXT NOT NULL, side TEXT NOT NULL, price INTEGER NOT NULL, qty INTEGER NOT NULL, filled INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, counted INTEGER NOT NULL DEFAULT 0);
        CREATE UNIQUE INDEX IF NOT EXISTS one_active ON orders((1)) WHERE status='active';
        CREATE TABLE IF NOT EXISTS fills(id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, qty INTEGER NOT NULL, price INTEGER NOT NULL, fee INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, tick INTEGER, kind TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, tick INTEGER, cash INTEGER);
        CREATE TABLE IF NOT EXISTS settlements(ticker TEXT PRIMARY KEY, result TEXT, payout INTEGER);
        ''')
        from .schema import remove_purchase_cap
        try:remove_purchase_cap(self.db)
        except Exception:
            self.close()
            raise
        self.db.execute('INSERT OR IGNORE INTO account VALUES(1,?,0,0)', (cash,))
        # Restart never silently enables new risk; protective selling continues.
        self.db.execute('UPDATE account SET running=0')
        self.reconcile()

    def close(self):
        self.db.close()
        self.lock.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def row(self, sql, args=()):
        r = self.db.execute(sql, args).fetchone()
        return dict(r) if r else None

    def event(self, now, kind, detail):
        self.db.execute('INSERT INTO events(tick,kind,detail) VALUES(?,?,?)', (now, kind, detail))

    def reconcile(self):
        if self.db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Corrupt state; manual recovery required')
        positions = self.db.execute('SELECT * FROM positions WHERE qty>0').fetchall()
        if len(positions) > 1:
            raise RuntimeError('Multiple positions; manual recovery required')
        for o in self.db.execute("SELECT * FROM orders WHERE status='active'"):
            p = self.row('SELECT * FROM positions WHERE ticker=?', (o['ticker'],))
            if o['action'] == 'sell' and (not p or not p['stop'] or o['qty']-o['filled'] != p['qty'] or not 1<=o['price']<=99):
                raise RuntimeError('Order/position mismatch')
        for m in self.db.execute('SELECT * FROM markets'):
            n = self.db.execute("SELECT count(*) FROM orders WHERE ticker=? AND action='buy' AND counted=1", (m['ticker'],)).fetchone()[0]
            if n != m['buys']:
                raise RuntimeError('Buy count mismatch')

    def start(self):
        self.reconcile()
        self.db.execute('UPDATE account SET running=1')

    def stop(self):
        with self.transaction():
            changed=self.db.execute('UPDATE account SET running=0 WHERE running!=0').rowcount
            canceled=self.db.execute("UPDATE orders SET status='canceled' WHERE action='buy' AND status='active'").rowcount
            if changed or canceled:
                self.event(None, 'control', 'Entries stopped; protective exits remain enabled')

    def cancel(self, order_id, rejected=False):
        # Simulator acknowledgment is synchronous. Real exchange requires reconciliation.
        with self.transaction():
            self.db.execute("UPDATE orders SET status=? WHERE id=? AND status='active'", ('rejected' if rejected else 'canceled', order_id))
            self.event(None, 'order', 'Simulated terminal cancellation/rejection acknowledged')

    def tick(self, q):
        q.validate()
        with self.transaction():
            self._tick(q)

    def _tick(self, q):
        if not q.connected:
            self.event(q.now, 'warning', 'Disconnected: no execution; stop state preserved')
            return
        self.db.execute('INSERT OR IGNORE INTO markets(ticker,cutoff) VALUES(?,?)', (q.ticker, q.cutoff))
        m = self.row('SELECT * FROM markets WHERE ticker=?', (q.ticker,))
        if q.now <= m['last_tick']:
            return
        # Earlier authoritative cutoff is accepted; never extend entry eligibility.
        cutoff = min(q.cutoff, m['cutoff'])
        self.db.execute('UPDATE markets SET last_tick=?,cutoff=? WHERE ticker=?', (q.now, cutoff, q.ticker))
        p = self.row('SELECT * FROM positions WHERE ticker=? AND qty>0', (q.ticker,))
        if q.now >= cutoff:
            self.db.execute("UPDATE orders SET status='expired' WHERE ticker=? AND status='active'", (q.ticker,))
            if q.result and not m['settled']:
                payout = p['qty'] * 100 if p and p['side'] == q.result else 0
                cost = p['cost'] if p else 0
                self.db.execute('UPDATE account SET cash=cash+?,pnl=pnl+?', (payout, payout-cost))
                self.db.execute('UPDATE positions SET qty=0,cost=0 WHERE ticker=?', (q.ticker,))
                self.db.execute('UPDATE markets SET settled=1 WHERE ticker=?', (q.ticker,))
                self.db.execute('INSERT INTO settlements VALUES(?,?,?)', (q.ticker, q.result, payout))
                self.event(q.now, 'settlement', json.dumps({'ticker': q.ticker, 'payout': payout}))
            return
        if p:
            bid = getattr(q, p['side'] + '_bid')
            if bid is not None and bid <= policy().sell_cents and not p['stop']:
                self.db.execute('UPDATE positions SET stop=1 WHERE ticker=?', (q.ticker,))
                self.event(q.now, 'stop', str(policy().sell_cents)+'c stop latched')
                p['stop'] = 1
            if p['stop']:
                # Cancel residual entry before creating a protective exit.
                self.db.execute("UPDATE orders SET status='canceled' WHERE action='buy' AND status='active'")
                if not self.row("SELECT * FROM orders WHERE status='active'"):
                    self._order(q.ticker, 'sell', p['side'], policy().sell_cents, p['qty'])
        active = self.row("SELECT * FROM orders WHERE status='active'")
        if active:
            if active['ticker'] == q.ticker:
                self._match(active, q)
            return
        # Global exposure guard, including markets awaiting settlement.
        if p or self.row('SELECT * FROM positions WHERE qty>0'):
            return
        a = self.row('SELECT * FROM account')
        if not a['running'] or not entry_open(cutoff,q.now):
            return
        for side in ('yes', 'no'):
            ask = getattr(q, side + '_ask')
            if type(ask) is int and policy().buy_min_cents <= ask <= 99:
                n = size(a['cash'], ask)
                if n:
                    self._order(q.ticker, 'buy', side, ask, n)
                    self._match(self.row("SELECT * FROM orders WHERE status='active'"), q)
                return

    def _order(self, ticker, action, side, price, qty):
        self.db.execute("INSERT INTO orders(ticker,action,side,price,qty,status) VALUES(?,?,?,?,?,'active')", (ticker, action, side, price, qty))

    def _match(self, o, q):
        if o['action'] == 'buy':
            cutoff=min(q.cutoff,self.row('SELECT cutoff FROM markets WHERE ticker=?',(o['ticker'],))['cutoff'])
            if not entry_open(cutoff,q.now):
                self.db.execute("UPDATE orders SET status='expired' WHERE id=?", (o['id'],))
                return
            price = getattr(q, o['side'] + '_ask')
            # Submitted buy limits stay above the chosen floor; cheaper fills are permitted.
            executable = price is not None and 0 <= price <= o['price'] and policy().buy_min_cents <= o['price'] <= 99
        else:
            price = getattr(q, o['side'] + '_bid')
            executable = price is not None and price >= o['price']
        if not executable:
            return
        n = min(q.liquidity, o['qty']-o['filled'])
        if n <= 0:
            return
        f = fee(n, price)
        p = self.row('SELECT * FROM positions WHERE ticker=?', (q.ticker,))
        if o['action'] == 'buy':
            cost = n*price + f
            self.db.execute('UPDATE account SET cash=cash-?', (cost,))
            if not o['counted']:
                self.db.execute('UPDATE markets SET buys=buys+1 WHERE ticker=?', (q.ticker,))
            if p and p['qty']:
                self.db.execute('UPDATE positions SET qty=qty+?,cost=cost+? WHERE ticker=?', (n, cost, q.ticker))
            else:
                self.db.execute('INSERT OR REPLACE INTO positions VALUES(?,?,?,?,0)', (q.ticker, o['side'], n, cost))
        else:
            if not p or n > p['qty']:
                raise RuntimeError('Sell exceeds holdings')
            basis = p['cost'] if n == p['qty'] else p['cost']*n//p['qty']
            self.db.execute('UPDATE positions SET qty=qty-?,cost=cost-? WHERE ticker=?', (n, basis, q.ticker))
            self.db.execute('UPDATE account SET cash=cash+?,pnl=pnl+?', (n*price-f, n*price-f-basis))
        self.db.execute('INSERT INTO fills(order_id,qty,price,fee) VALUES(?,?,?,?)', (o['id'], n, price, f))
        self.db.execute('UPDATE orders SET filled=filled+?,counted=1,status=? WHERE id=?', (n, 'filled' if n+o['filled'] == o['qty'] else 'active', o['id']))
        self.event(q.now, 'fill', json.dumps({'order': o['id'], 'action': o['action'], 'quantity': n, 'price': price}))
        self.db.execute('INSERT INTO snapshots(tick,cash) SELECT ?,cash FROM account', (q.now,))

    def status(self):
        return { 'mode': 'SIMULATION — external trading disabled',
                 'account': self.row('SELECT * FROM account'),
                 'positions': [dict(r) for r in self.db.execute('SELECT * FROM positions WHERE qty>0')],
                 'markets': [dict(r) for r in self.db.execute('SELECT * FROM markets ORDER BY last_tick DESC LIMIT 5')],
                 'active_orders': [dict(r) for r in self.db.execute("SELECT * FROM orders WHERE status='active'")],
                 'recent_events': [dict(r) for r in self.db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 10')] }
