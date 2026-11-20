"""Account monitor, kept separate from synthetic balances and simulated orders."""
import argparse
import datetime as dt
import fcntl
import json
import os
import resource
import select
import sqlite3
import sys
import time
from pathlib import Path
from .api import APIError, Client, GatewayTransport, SchemaError, TICKER, book_prices, decimal
from .credentials import load
from .security import check_isolation
from .discovery import open_markets


class AccountStore:
    def __init__(self,path,environment,principal="offline-test"):
        if environment not in ('demo','production'):
            raise ValueError('Unknown account environment')
        os.umask(0o077)
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.lock=open(str(path)+'.lock','a')
        try:
            fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError('Another account monitor owns this database') from None
        if self.path.is_symlink():
            self.lock.close()
            raise ValueError('Database may not be a symlink')
        self.db=sqlite3.connect(path)
        os.chmod(self.path,0o600)
        self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS identity(environment TEXT NOT NULL, principal TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, observed REAL NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, observed REAL NOT NULL, event TEXT NOT NULL);
        ''')
        found=self.db.execute('SELECT environment,principal FROM identity').fetchone()
        if found and (found[0]!=environment or found[1]!=principal):
            self.close()
            raise ValueError('Credential identity or demo/production database mismatch')
        if not found:
            self.db.execute('INSERT INTO identity VALUES(?,?)',(environment,principal))
        self.db.commit()
        if self.db.execute('PRAGMA quick_check').fetchone()[0]!='ok':
            self.close()
            raise RuntimeError('Account database integrity check failed')

    def save(self,payload):
        with self.db:
            self.db.execute('INSERT INTO snapshots(observed,payload) VALUES(?,?)',(time.time(),json.dumps(payload)))
            # Bounded cache, not the sole archive of exchange trade history.
            self.db.execute('DELETE FROM snapshots WHERE id < (SELECT max(id)-99 FROM snapshots)')

    def log(self,event):
        with self.db:
            self.db.execute('INSERT INTO audit(observed,event) VALUES(?,?)',(time.time(),event))
            self.db.execute('DELETE FROM audit WHERE id < (SELECT max(id)-999 FROM audit)')

    def latest(self):
        row=self.db.execute('SELECT observed,payload FROM snapshots ORDER BY id DESC LIMIT 1').fetchone()
        return (row[0],json.loads(row[1])) if row else None

    def close(self):
        self.db.close()
        self.lock.close()


def cutoff(market):
    try:
        stamp=dt.datetime.fromisoformat(market['close_time'].replace('Z','+00:00'))
        if stamp.utcoffset() is None:
            raise ValueError()
        return stamp.timestamp()
    except (KeyError,TypeError,ValueError,AttributeError):
        raise SchemaError('Market has no valid official cutoff') from None


def snapshot(client,now=None):
    start=time.time()
    exchange=client.get('/exchange/status')
    balance=client.get('/portfolio/balance')
    cash=balance.get('balance')
    if type(cash) is not int or cash<0:
        raise SchemaError('Expected nonnegative integer cash balance in cents')
    positions=client.pages('/portfolio/positions')
    orders=client.pages('/portfolio/orders',{'status':'resting'})
    fills=client.pages('/portfolio/fills')
    settlements=client.pages('/portfolio/settlements')
    current=time.time() if now is None else now
    markets=open_markets(client,current)
    eligible=[]
    for market in markets:
        ticker=market.get('ticker','')
        if not isinstance(ticker,str) or not TICKER.fullmatch(ticker):
            raise SchemaError('Discovery returned an unexpected series ticker')
        if market.get('market_type')!='binary':
            raise SchemaError('Discovered market is not binary')
        close=cutoff(market)
        if close>current:
            eligible.append((close,ticker,market))
    market_info=None
    if eligible:
        close,ticker,market=min(eligible,key=lambda m:(m[0],m[1]))
        prices=book_prices(client.get('/markets/'+ticker+'/orderbook',{'depth':10}))
        market_info={'ticker':ticker,'cutoff':close,'seconds_remaining':max(0,close-current),
                     'prices_dollars':{k:str(v) if v is not None else None for k,v in prices.items()}}
    normalized=[]
    for pos in positions:
        quantity=decimal(pos.get('position_fp'),'position quantity')
        if quantity:
            normalized.append({'ticker':pos.get('ticker'),'quantity':str(abs(quantity)),
                               'side':'yes' if quantity>0 else 'no',
                               'realized_pnl_dollars':str(decimal(pos.get('realized_pnl_dollars'),'P/L')),
                               'market_exposure_dollars':str(decimal(pos.get('market_exposure_dollars'),'exposure'))})
    # The endpoint collection is not an atomic exchange snapshot. It is never
    # used to authorize orders or infer a flat position for new entries.
    return {'read_only':True,'entries_blocked':'This account monitor is read-only; use the separate trading command for execution',
            'started_at':start,'finished_at':time.time(),'cash_cents':cash,
            'exchange':exchange,'positions':normalized,'open_orders':orders,
            'fills':fills,'settlements':settlements,'market':market_info,
            'warnings':['Sequential account reads are not an atomic exchange snapshot.',
                        'Existing account positions are observed only; this monitor does not place stop orders.',
                        'P/L belongs to the account positions returned by the API, not simulated bot trades.',
                        'Archived fills/settlements may require historical endpoints and are not included.']}


def safe_text(value):
    return ''.join(c if c.isprintable() else '?' for c in str(value))[:240]


def render(state,environment,status):
    print('\033[2J\033[H',end='')
    print('KALSHI ACCOUNT |',environment.upper(),'| READ ONLY |',safe_text(status))
    if not state:
        print('No complete account snapshot yet.')
    else:
        print('Cash reported by API: $'+format(state['cash_cents']/100,'.2f'))
        print('Read-only account view | Entries and stop execution: DISABLED')
        print('Buy count: not applicable in read-only mode | Entry price: inspect confirmed fills (h)')
        m=state.get('market')
        if m:
            print('Market:',safe_text(m['ticker']),'| Cutoff in:',max(0,int(m['cutoff']-time.time())),'seconds')
            print('Executable prices ($):',safe_text(m['prices_dollars']))
        print('Open orders:',len(state['open_orders']),'| Positions:',len(state['positions']))
        for pos in state['positions'][:8]:
            print(safe_text(pos))
        for warning in state['warnings']:
            print('Warning:',warning)
    print('r=refresh, h=fills, o=open orders, t=settlements, l=logs, p=pause polling, s=resume polling, q=quit')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--environment',choices=['demo','production'],default='demo')
    p.add_argument('--credentials',default='/credentials')
    p.add_argument('--socket',default='/gateway/gateway.sock')
    p.add_argument('--db',default='/state/account.sqlite3')
    p.add_argument('--connect',action='store_true',help='Explicitly authorize read-only connection for this process')
    p.add_argument('--once',action='store_true')
    p.add_argument('--interval',type=float,default=30)
    p.add_argument('--preflight',action='store_true',help='Validate credentials/isolation offline; never connects')
    a=p.parse_args()
    if a.interval<10:
        p.error('Polling interval must be at least 10 seconds')
    if not a.connect and not a.preflight:
        p.error('No connection attempted. Use --preflight offline or explicitly --connect for read-only access.')
    try:
        check_isolation()
        resource.setrlimit(resource.RLIMIT_AS,(500*1024*1024,500*1024*1024))
        signer=load(a.credentials,a.environment)
    except (OSError,ValueError,KeyError,RuntimeError):
        p.exit(1,'Offline preflight failed: check isolation, credentials, environment, and permissions. No connection attempted.\n')
    if a.preflight:
        print('Offline credential/isolation checks passed. No connection attempted. External orders remain blocked.')
        return
    if not a.once and not sys.stdin.isatty():
        p.error('Use --once for headless account checks')
    store=AccountStore(a.db,a.environment,signer.fingerprint)
    client=Client(signer,GatewayTransport(a.socket))
    last=store.latest()
    state=last[1] if last else None
    status='STALE CACHE — not connected'
    paused=False
    due=0
    try:
        while True:
            if not paused and time.monotonic()>=due:
                try:
                    state=snapshot(client)
                    store.save(state)
                    store.log('Complete read-only account snapshot recorded')
                    status='Connected; snapshot completed '+dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
                except (APIError,SchemaError,OSError,ValueError):
                    store.log('Account refresh failed; retaining stale cache; no orders submitted')
                    status='DISCONNECTED / snapshot incomplete; displayed cache is STALE'
                    if a.once:
                        p.exit(1,'Read-only account refresh failed. No orders submitted.\n')
                due=time.monotonic()+a.interval
            if a.once:
                print(json.dumps(state,indent=2))
                return
            render(state,a.environment,status+(' | POLLING PAUSED' if paused else ''))
            ready,_,_=select.select([sys.stdin],[],[],min(1,max(0,due-time.monotonic())) if not paused else 1)
            if ready:
                cmd=sys.stdin.readline().strip().lower()
                if cmd=='q':
                    break
                if cmd=='p':
                    paused=True
                elif cmd=='s':
                    paused=False
                    due=0
                elif cmd=='r':
                    due=0
                elif cmd in ('h','o','t','l'):
                    if cmd=='l':
                        values=[{'time':row[0],'event':row[1]} for row in store.db.execute('SELECT observed,event FROM audit ORDER BY id DESC LIMIT 30')]
                    else:
                        key={'h':'fills','o':'open_orders','t':'settlements'}[cmd]
                        values=state.get(key,[])[:50] if state else []
                    print(json.dumps(values,indent=2))
                    input('Press Enter to return')
    except KeyboardInterrupt:
        pass
    finally:
        store.close()


if __name__=='__main__':
    main()
