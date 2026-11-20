"""Bounded asynchronous decision evidence. No credentials or raw API bodies."""
import json
import queue
import sqlite3
import threading
import time
from .api import APIError,SchemaError

SCHEMA='''
CREATE TABLE IF NOT EXISTS execution_events(id INTEGER PRIMARY KEY,at REAL NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_log(id INTEGER PRIMARY KEY,at REAL NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS research_log_kind_at ON research_log(kind,at);
CREATE INDEX IF NOT EXISTS research_log_at ON research_log(at);
CREATE TABLE IF NOT EXISTS volatility_history(
 id INTEGER PRIMARY KEY, at REAL NOT NULL, ticker TEXT, seconds_remaining REAL, price_cents REAL,
 market_high_cents REAL, market_low_cents REAL, market_range_cents REAL,
 range_1m_cents REAL, range_5m_cents REAL, range_15m_cents REAL,
 avg_1m_cents REAL, avg_5m_cents REAL, avg_15m_cents REAL,
 sample_count INTEGER, missing_count INTEGER, coverage_seconds REAL,
 status TEXT, source TEXT NOT NULL DEFAULT 'yes_ask');
CREATE INDEX IF NOT EXISTS volatility_history_ticker_at ON volatility_history(ticker,at);
CREATE INDEX IF NOT EXISTS volatility_history_at ON volatility_history(at);
'''


def failure_reason(exc):
    if isinstance(exc,APIError):
        return str(exc) if exc.status else 'Protected connection unavailable'
    if isinstance(exc,SchemaError):return str(exc)[:200]  # Locally generated, sanitized schema explanation.
    allowed=('Account positions do not match','Unmanaged open order','Known order absent','Fill feed incomplete',
             'Account not reconciled','Entry not eligible','Expired ambiguous','Unconfirmed buy while entries paused',
             'Order preflight failed','Order acknowledgment mismatch','Recovery acknowledgment mismatch','Recovery submitted','Unconfirmed order absent', 'Missing order', 'Historical market',
             'Exchange market', 'Final settlement', 'Incomplete independent', 'Historical fill',
             'Open or inconsistent', 'Unmanaged historical', 'Contradictory direct', 'Ambiguous missing',
             'Market settlement', 'Unmanaged order', 'Unmanaged fill', 'Missing or duplicate',
             'Incomplete or active', 'Direct order', 'Invalid historical', 'Historical order')
    message=str(exc)
    return message[:200] if isinstance(exc,(RuntimeError,SchemaError)) and message.startswith(allowed) else type(exc).__name__


class Diagnostics:
    def __init__(self,path):
        self.path=path;self.queue=queue.Queue(maxsize=1024);self.stop=threading.Event()
        self.dropped=0;self.error=None;self.thread=threading.Thread(target=self._run,daemon=True)
        self.thread.start()

    def record(self,kind,values):
        if not self.thread.is_alive() and self.error:
            self.dropped+=1;return
        item=(time.time(),kind,json.dumps(values,default=str,sort_keys=True))
        try:self.queue.put_nowait(item)
        except queue.Full:self.dropped+=1

    def _run(self):
        db=None
        pending=[]
        try:
            db=sqlite3.connect(self.path,timeout=.2)
            db.executescript(SCHEMA);db.commit()
            if 'seconds_remaining' not in {r[1] for r in db.execute('PRAGMA table_info(volatility_history)')}:
                db.execute('ALTER TABLE volatility_history ADD COLUMN seconds_remaining REAL'); db.commit()
            while not self.stop.is_set() or pending or not self.queue.empty():
                if not pending:
                    try:pending.append(self.queue.get(timeout=.1))
                    except queue.Empty:continue
                # Group low-frequency evidence for up to 200 ms to avoid
                # excessive SQLite commits without blocking the caller.
                deadline=time.monotonic()+.2
                while len(pending)<64:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:break
                    try:pending.append(self.queue.get(timeout=remaining))
                    except queue.Empty:break
                try:
                    with db:
                        db.executemany('INSERT INTO execution_events(at,kind,payload) VALUES(?,?,?)',pending)
                        # The research log is an append-only, analysis-friendly
                        # copy of the same sanitized evidence.  Keep the older
                        # execution_events table for compatibility with the UI.
                        db.executemany('INSERT INTO research_log(at,kind,payload) VALUES(?,?,?)',pending)
                        volatility=[]
                        for at,kind,payload in pending:
                            if kind!='volatility_reading': continue
                            try:
                                value=json.loads(payload)
                                volatility.append((at,value.get('ticker'),value.get('seconds_remaining'),value.get('yes_price_cents'),value.get('market_high_cents'),value.get('market_low_cents'),value.get('market_range_cents'),value.get('range_1m_cents'),value.get('range_5m_cents'),value.get('range_15m_cents'),value.get('avg_1m_cents'),value.get('avg_5m_cents'),value.get('avg_15m_cents'),value.get('sample_count'),value.get('missing_count'),value.get('coverage_seconds'),value.get('status')))
                            except (TypeError,ValueError): pass
                        if volatility:
                            db.executemany('INSERT INTO volatility_history(at,ticker,seconds_remaining,price_cents,market_high_cents,market_low_cents,market_range_cents,range_1m_cents,range_5m_cents,range_15m_cents,avg_1m_cents,avg_5m_cents,avg_15m_cents,sample_count,missing_count,coverage_seconds,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',volatility)
                        # No hot-path pruning. Future archival owns retention.
                    pending=[];self.error=None
                except sqlite3.Error:
                    self.error='Diagnostics waiting for database'
                    if self.stop.is_set():self.dropped+=len(pending);break
                    time.sleep(.1)
        except sqlite3.Error:
            self.error='Diagnostic recorder unavailable'
            self.dropped+=len(pending)+self.queue.qsize()
        finally:
            if db is not None:db.close()

    def close(self):
        self.stop.set();self.thread.join(timeout=2)
