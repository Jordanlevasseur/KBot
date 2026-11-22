#!/usr/bin/python3
"""Coordinated stopped-app deployment; no credentials, accounts or order calls."""
import hashlib
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tempfile

SOURCE=Path(__file__).resolve().parent.parent
FILES=('core.py','desktop.py','execution.py','prepared.py','prepared_runtime.py',
       'trade_api.py','trader.py','exit_price.py','entry_guard.py','market_data.py','decisions.py')


def main():
    if os.geteuid()!=0:raise SystemExit('Run with sudo to update the gateway and application together.')
    from importlib.machinery import SourceFileLoader
    helper=SourceFileLoader('deployment_checks',str(SOURCE/'deploy/repair-gateway-performance.py')).load_module()
    user=pwd.getpwnam('mint');home=Path(user.pw_dir)
    helper.ensure_closed(user.pw_uid,home)
    app=home/'.local/share/kalshi-bitcoin-bot/kalshi_bot'
    gateway=Path('/opt/kalshi-bot/kalshi_bot/trade_api.py')
    helper.trusted(gateway.parent);helper.trusted(gateway)
    if not app.is_dir() or app.is_symlink():raise RuntimeError('Unexpected application location')
    sources={name:(SOURCE/'kalshi_bot'/name).read_bytes() for name in FILES}
    for name,data in sources.items():compile(data,name,'exec')
    backup=Path(tempfile.mkdtemp(prefix='kbot-target-guards-',dir='/var/tmp'))
    originals={}
    for name in FILES:
        target=app/name
        if target.is_symlink():raise RuntimeError('Refusing application symlink')
        originals[target]=target.read_bytes() if target.exists() else None
        if target.exists():shutil.copy2(target,backup/name)
    old_gateway=gateway.read_bytes();(backup/'gateway-trade_api.py').write_bytes(old_gateway)
    def write_app(target,data):
        fd,temp=tempfile.mkstemp(prefix='.kbot-update-',dir=app)
        try:
            with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
            os.chmod(temp,0o600);os.chown(temp,user.pw_uid,user.pw_gid);os.replace(temp,target)
        finally:
            if os.path.exists(temp):os.unlink(temp)
    units=['kalshi-production-gateway.service','kalshi-demo-gateway.service']
    active=[u for u in units if subprocess.run(['systemctl','is-active','--quiet',u]).returncode==0]
    helper.ensure_closed(user.pw_uid,home)
    try:
        helper.atomic(gateway,sources['trade_api.py'])
        for name,data in sources.items():write_app(app/name,data)
        for unit in active:subprocess.run(['systemctl','restart',unit],check=True,timeout=30)
        for unit in active:subprocess.run(['systemctl','is-active','--quiet',unit],check=True,timeout=10)
        for name,data in sources.items():
            if (app/name).read_bytes()!=data:raise RuntimeError('Installed source mismatch')
    except Exception:
        helper.atomic(gateway,old_gateway)
        for target,data in originals.items():
            if data is not None:write_app(target,data)
            elif target.exists():target.unlink()
        for unit in active:subprocess.run(['systemctl','restart',unit],timeout=30)
        raise
    print('Installed target/volatility/marketable-exit repair. Bot remains closed. Backup:',backup)
    print('Installed guard SHA256:',hashlib.sha256(sources['entry_guard.py']).hexdigest())


if __name__=='__main__':main()
