#!/usr/bin/python3
"""Install durable missing-order reconciliation without restarting a flat bot.

The running legacy worker receives a reversible status bridge only for exact
orders already proven as settled and zero-fill. The fixed Journal restores the
accurate canceled status automatically on its next restart.
"""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
from decimal import Decimal as D

SOURCE=Path(__file__).resolve().parent.parent
APP=Path.home()/'.local/share/kalshi-bitcoin-bot/kalshi_bot'
STATE=Path.home()/'.local/state/kalshi-bitcoin-bot/production-trading'
FILES=('execution.py','missing_order.py')


def atomic(path,data):
    if path.is_symlink() or not path.is_file() or path.stat().st_uid!=os.getuid():
        raise RuntimeError('Unexpected installed application file')
    fd,name=tempfile.mkstemp(prefix='.kbot-reconcile-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as out:
            out.write(data);out.flush();os.fsync(out.fileno())
        os.chmod(name,0o600);os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def live_account_flat():
    sys.path.insert(0,str(SOURCE))
    from kalshi_bot.api import Client,GatewayTransport,RateGate,decimal
    from kalshi_bot.credentials import load
    signer=load(Path.home()/'.config/kalshi-bitcoin-bot/production','production')
    client=Client(signer,GatewayTransport('/run/kalshi-production/gateway.sock'),gate=RateGate())
    orders=client.pages('/portfolio/orders')
    positions=client.pages('/portfolio/positions')
    active=[o for o in orders if o.get('status') in ('resting','pending')]
    exposed=[p for p in positions if decimal(p.get('position_fp'),'position')!=0]
    if active or exposed:
        raise RuntimeError('Current account has an active order or position')
    return len(orders),len(positions)


def outside_window():
    path=STATE/'volatility-diagnostics.sqlite3'
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2);db.row_factory=sqlite3.Row
    try:
        row=db.execute("SELECT at,payload FROM research_log WHERE kind='strategy_decision' ORDER BY at DESC LIMIT 1").fetchone()
        if not row or time.time()-row['at']>5:raise RuntimeError('Live strategy decision is stale')
        decision=json.loads(row['payload'])
        if decision.get('action')!='outside_window':raise RuntimeError('Wait until outside the entry window')
        ticker=decision.get('ticker')
        sample=db.execute('SELECT at,seconds_remaining FROM volatility_history WHERE ticker=? ORDER BY at DESC LIMIT 1',(ticker,)).fetchone()
        if not sample or time.time()-sample['at']>5 or sample['seconds_remaining'] is None or sample['seconds_remaining']<300:
            raise RuntimeError('Need at least five minutes before market close')
    finally:db.close()


def main():
    if os.geteuid()==0:raise RuntimeError('Run as the desktop user')
    marker=APP.parent/'installation.json'
    info=json.loads(marker.read_text())
    if info.get('application')!='kalshi-bitcoin-bot' or info.get('owner')!=os.getuid():
        raise RuntimeError('Unrecognized application installation')
    sources={name:(SOURCE/'kalshi_bot'/name).read_bytes() for name in FILES}
    for name,data in sources.items():compile(data,name,'exec')
    outside_window()
    account_orders,account_positions=live_account_flat()
    outside_window()
    backup=Path(tempfile.mkdtemp(prefix='kbot-reconciliation-backup-',dir=Path.home()))
    originals={}
    for name in FILES:
        target=APP/name;originals[name]=target.read_bytes();shutil.copy2(target,backup/name)
    db=sqlite3.connect(STATE/'desktop-trading.sqlite3',timeout=20);db.row_factory=sqlite3.Row
    migrated=[]
    try:
        if db.execute("SELECT count(*) FROM orders WHERE status NOT IN ('executed','rejected','canceled','expired')").fetchone()[0]:
            raise RuntimeError('Local journal has an unresolved order')
        for name,data in sources.items():atomic(APP/name,data)
        now=time.time()
        with db:
            db.execute('''CREATE TABLE IF NOT EXISTS reconciliation_status_migrations(
                client_id TEXT PRIMARY KEY,original_status TEXT NOT NULL,
                migrated_at REAL NOT NULL,reason TEXT NOT NULL)''')
            rows=db.execute("""SELECT o.client_id FROM orders o
                WHERE o.status='canceled' AND CAST(o.filled AS REAL)=0
                  AND o.exchange_id IS NOT NULL AND ?>o.cutoff+86400
                  AND EXISTS(SELECT 1 FROM historical_order_verifications h
                    WHERE h.client_id=o.client_id AND h.exchange_id=o.exchange_id
                      AND h.ticker=o.ticker AND h.evidence='settled_zero_fill')""",(now,)).fetchall()
            for row in rows:
                cid=row['client_id']
                db.execute('INSERT OR IGNORE INTO reconciliation_status_migrations VALUES(?,?,?,?)',
                           (cid,'canceled',now,'durable settled zero-fill live bridge'))
                if db.execute("UPDATE orders SET status='rejected' WHERE client_id=? AND status='canceled' AND CAST(filled AS REAL)=0",(cid,)).rowcount:
                    migrated.append(cid)
            db.execute('INSERT INTO audit(at,event) VALUES(?,?)',
                       (now,f'Installed durable missing-order repair; temporarily bridged {len(migrated)} proven zero-fill records'))
    except Exception:
        for name,data in originals.items():atomic(APP/name,data)
        raise
    finally:db.close()
    print(json.dumps({'result':'installed','migrated_records':len(migrated),
                      'account_orders_read':account_orders,'account_positions_read':account_positions,
                      'account_writes':0,'test_orders':0,'worker_restarted':False,
                      'backup':str(backup)}))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(type(exc).__name__+': '+str(exc),file=sys.stderr);raise SystemExit(1)
