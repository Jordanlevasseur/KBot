#!/usr/bin/python3
"""Targeted administrator repair: verified TLS context cache and bounded CPU.

Does not read API keys, contact an account, change a firewall, or send orders.
Run only after the desktop is closed and Bitcoin positions/orders are confirmed
flat. The caller performs the account check; this helper refuses running workers.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile

SOURCE=Path(__file__).resolve().parent.parent
TARGET=Path('/opt/kalshi-bot')
DROPIN='# Managed Kalshi prepared-execution performance repair\n[Service]\nCPUQuota=50%\n'


def trusted(path):
    if path.is_symlink():raise RuntimeError('Refusing a symlink system path')
    if path.exists():
        st=path.stat()
        if st.st_uid!=0 or st.st_mode&0o022:raise RuntimeError('System destination must be root-owned and protected')


def atomic(path,data):
    trusted(path.parent);trusted(path)
    fd,name=tempfile.mkstemp(prefix='.kalshi-performance-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
        os.chmod(name,0o644);os.chown(name,0,0);os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def ensure_closed(uid,home):
    launcher=str(home/'.local/share/kalshi-bitcoin-bot/launch.py')
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():continue
        try:
            if entry.stat().st_uid!=uid:continue
            args=(entry/'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError,ProcessLookupError):continue
        text=[a.decode(errors='replace') for a in args]
        if launcher in text or any(text[i]=='-m' and text[i+1] in ('kalshi_bot.desktop','kalshi_bot.desktop_worker') for i in range(len(text)-1)):
            raise RuntimeError('Close the desktop and trading worker before repair')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment',choices=('demo','production'),required=True)
    parser.add_argument('--expected-sha256',required=True)
    parser.add_argument('--expected-api-sha256',required=True)
    args=parser.parse_args()
    if os.geteuid()!=0:parser.error('Administrator authentication is required')
    sys.path.insert(0,str(SOURCE))
    from kalshi_bot.gateway import validate_config,verify_firewall
    for path in (Path('/opt'),TARGET,TARGET/'kalshi_bot',Path('/etc/systemd/system')):trusted(path)
    source=(SOURCE/'kalshi_bot/gateway.py').read_bytes()
    if hashlib.sha256(source).hexdigest()!=args.expected_sha256:raise RuntimeError('Gateway changed after review; aborting')
    api_source=(SOURCE/'kalshi_bot/api.py').read_bytes()
    if hashlib.sha256(api_source).hexdigest()!=args.expected_api_sha256:raise RuntimeError('API validation changed after review; aborting')
    config_path=Path('/etc/kalshi-bot')/(args.environment+'-gateway.json')
    trusted(config_path);config=validate_config(json.loads(config_path.read_text()))
    if config['environment']!=args.environment:raise RuntimeError('Wrong gateway environment')
    client=pwd.getpwuid(config['client_uid']);ensure_closed(client.pw_uid,Path(client.pw_dir))
    verify_firewall(config)
    unit='kalshi-'+args.environment+'-gateway.service'
    service=Path('/etc/systemd/system')/unit;trusted(service)
    if not service.is_file():raise RuntimeError('Existing managed gateway unit required')
    folder=service.with_name(unit+'.d');trusted(folder);folder.mkdir(mode=0o755,exist_ok=True)
    dropin=folder/'50-kalshi-prepared-performance.conf';trusted(dropin)
    if dropin.exists() and not dropin.read_text().startswith('# Managed Kalshi prepared-execution performance repair'):
        raise RuntimeError('Unmanaged performance override exists')
    target=TARGET/'kalshi_bot/gateway.py';trusted(target)
    api_target=TARGET/'kalshi_bot/api.py';trusted(api_target);old_api=api_target.read_bytes()
    original=target.read_bytes();old_dropin=dropin.read_bytes() if dropin.exists() else None
    def run(*cmd):subprocess.run(cmd,check=True,capture_output=True,text=True,timeout=30)
    ensure_closed(client.pw_uid,Path(client.pw_dir))
    try:
        atomic(api_target,api_source);atomic(target,source);atomic(dropin,DROPIN.encode())
        run('/usr/bin/systemctl','daemon-reload')
        run('/usr/bin/systemctl','restart',unit)
        run('/usr/bin/systemctl','is-active','--quiet',unit)
        verify_firewall(config)
        quota,period=(Path('/sys/fs/cgroup/system.slice')/unit/'cpu.max').read_text().split()
        if quota=='max' or int(quota)*2!=int(period):raise RuntimeError('CPU limit verification failed')
    except Exception:
        atomic(api_target,old_api);atomic(target,original)
        if old_dropin is None:dropin.unlink(missing_ok=True)
        else:atomic(dropin,old_dropin)
        run('/usr/bin/systemctl','daemon-reload');run('/usr/bin/systemctl','restart',unit)
        raise RuntimeError('Repair failed; previous gateway restored') from None
    print(json.dumps({'result':'gateway performance repair installed','cpu_quota':'50% of one CPU core','memory_limit':'unchanged','firewall':'verified unchanged','account_requests':0,'order_requests':0}))


if __name__=='__main__':main()
