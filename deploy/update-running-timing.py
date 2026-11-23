#!/usr/bin/python3
"""Update only the expiry validator while a flat worker is outside entry time.

No exchange requests, settings changes, journal writes, or worker restarts.
The production gateway briefly restarts; its systemd socket stays active.
"""
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

SOURCE = Path(__file__).resolve().parent.parent
UNIT = 'kalshi-production-gateway.service'
GATEWAY = Path('/opt/kalshi-bot/kalshi_bot/trade_api.py')


def trusted(path):
    info = path.lstat()
    if path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('Untrusted protected service path')


def readonly(path):
    db = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=2)
    db.execute('PRAGMA query_only=ON')
    return db


def safe_window(home):
    state = home/'.local/state/kalshi-bitcoin-bot/production-trading'
    with readonly(state/'desktop-trading.sqlite3') as db:
        if db.execute("SELECT count(*) FROM orders WHERE status NOT IN "
                      "('executed','rejected','canceled','expired')").fetchone()[0]:
            raise RuntimeError('An unresolved order prevents the update')
        row = db.execute('SELECT at,payload FROM snapshots ORDER BY id DESC LIMIT 1').fetchone()
        if not row or not 0 <= time.time()-row[0] <= 45:
            raise RuntimeError('A recent account snapshot is required')
        snapshot = json.loads(row[1])
        if not isinstance(snapshot.get('positions'), list) or not isinstance(snapshot.get('orders'), list):
            raise RuntimeError('Account snapshot is incomplete')
        for position in snapshot['positions']:
            quantity = position.get('position_fp', position.get('position'))
            if quantity is None or float(quantity) != 0:
                raise RuntimeError('An account position prevents the update')
        if any(order.get('status') not in ('executed','rejected','canceled','expired')
               for order in snapshot['orders']):
            raise RuntimeError('An active account order prevents the update')
    with readonly(state/'volatility-diagnostics.sqlite3') as db:
        row = db.execute("SELECT at,payload FROM research_log WHERE kind='strategy_decision' "
                         'ORDER BY at DESC LIMIT 1').fetchone()
        if not row or not 0 <= time.time()-row[0] <= 5:
            raise RuntimeError('Fresh worker decisions are required')
        decision = json.loads(row[1])
        paused = decision.get('entries_enabled') is False and decision.get('action') == 'paused'
        if not paused and decision.get('action') != 'outside_window':
            raise RuntimeError('Worker must be paused or outside the entry window')
        row = db.execute('SELECT at,seconds_remaining FROM volatility_history WHERE ticker=? '
                         'ORDER BY at DESC LIMIT 1', (decision['ticker'],)).fetchone()
        # The gateway restart normally completes in under a second. Keep at
        # least 15 seconds before the four-minute entry window so the worker
        # can reconnect and refresh without missing an eligible interval.
        if (not paused and
                (not row or not 0 <= time.time()-row[0] <= 5 or row[1] is None or row[1] <= 255)):
            raise RuntimeError('Insufficient time before the entry window')


def atomic(path, data, uid, gid, mode):
    fd, temporary = tempfile.mkstemp(prefix='.kbot-timing-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chown(temporary, uid, gid)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=25)


def firewall():
    run('/usr/bin/python3', '-B', '-m', 'kalshi_bot.gateway', '--verify',
        '--config', '/etc/kalshi-bot/production-gateway.json',
        '--socket', '/run/kalshi-production/gateway.sock')


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Linux administrator authentication is required')
    user = pwd.getpwnam('mint')
    home = Path(user.pw_dir)
    app = home/'.local/share/kalshi-bitcoin-bot/kalshi_bot/trade_api.py'
    for path in (GATEWAY.parent.parent, GATEWAY.parent, GATEWAY):
        trusted(path)
    if app.is_symlink() or app.parent.is_symlink() or app.stat().st_uid != user.pw_uid:
        raise RuntimeError('Unexpected installed application path')
    source = (SOURCE/'kalshi_bot/trade_api.py').read_bytes()
    compile(source, 'trade_api.py', 'exec')
    # Restrict this running-session update to the two reviewed timing edits.
    old = GATEWAY.read_bytes()
    expected = old.replace(b'from .strategy import ENTRY_WINDOW_SECONDS,ENTRY_CUTOFF_SECONDS\n', b'')
    expected = expected.replace(b'ENTRY_WINDOW_SECONDS-ENTRY_CUTOFF_SECONDS if buy else 900',
                                b'configured.entry_window_seconds-configured.entry_cutoff_seconds if buy else 900')
    if source != expected or old == source:
        raise RuntimeError('Installed source differs from the reviewed timing-only patch')
    if app.read_bytes() != old:
        raise RuntimeError('Application and gateway validators differ; use a stopped deployment')
    os.chdir('/opt/kalshi-bot')
    run('/usr/bin/systemctl', 'is-active', '--quiet', UNIT)
    run('/usr/bin/systemctl', 'is-active', '--quiet', 'kalshi-production-gateway.socket')
    firewall()
    safe_window(home)
    backup = Path(tempfile.mkdtemp(prefix='kbot-running-timing-', dir='/var/tmp'))
    shutil.copy2(GATEWAY, backup/'gateway-trade_api.py')
    shutil.copy2(app, backup/'app-trade_api.py')
    started = time.monotonic()
    try:
        safe_window(home)
        atomic(GATEWAY, source, 0, 0, 0o644)
        atomic(app, source, user.pw_uid, user.pw_gid, 0o600)
        run('/usr/bin/systemctl', 'restart', UNIT)
        run('/usr/bin/systemctl', 'is-active', '--quiet', UNIT)
        firewall()
        if GATEWAY.read_bytes() != source or app.read_bytes() != source:
            raise RuntimeError('Installed checksum mismatch')
    except Exception:
        atomic(GATEWAY, old, 0, 0, 0o644)
        atomic(app, old, user.pw_uid, user.pw_gid, 0o600)
        run('/usr/bin/systemctl', 'restart', UNIT)
        raise RuntimeError('Update failed; previous validator restored') from None
    print(json.dumps({'result':'timing-only update installed', 'backup':str(backup),
                      'service_restart_seconds':round(time.monotonic()-started, 3),
                      'sha256':hashlib.sha256(source).hexdigest(),
                      'firewall':'verified', 'worker_restarted':False, 'test_orders':0}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(type(error).__name__+': '+str(error), file=sys.stderr)
        raise SystemExit(1)
