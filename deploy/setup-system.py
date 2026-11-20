#!/usr/bin/python3
"""Explicit administrator setup for this application's isolated Kalshi gateway.

No private key is read. No account API request or order is submitted. Only the
reviewed Python package, dedicated service configuration, and UID firewall are installed.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import tempfile
import time

SOURCE=Path(__file__).resolve().parent.parent
TARGET=Path('/opt/kalshi-bot')


def write(path,data,mode=0o644):
    if path.is_symlink(): raise RuntimeError('Refusing symlink target')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
    fd,temp=tempfile.mkstemp(prefix='.kalshi-install-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as output:
            output.write(data); output.flush(); os.fsync(output.fileno())
        os.chmod(temp,mode); os.chown(temp,0,0)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--environment',choices=['demo','production'],required=True)
    p.add_argument('--client-user',required=True)
    p.add_argument('--allow-trading',action='store_true')
    a=p.parse_args()
    if os.geteuid()!=0: p.error('Administrator authentication is required')
    client=pwd.getpwnam(a.client_user)
    if client.pw_uid==0: p.error('Client must be an unprivileged laptop user')
    import cryptography,websockets  # Check dependencies before system changes.
    for program in ('/usr/sbin/nft','/usr/sbin/useradd','/usr/sbin/groupadd','/usr/bin/systemctl','/usr/bin/setfacl','/usr/sbin/runuser'):
        if not Path(program).is_file(): raise RuntimeError('Missing dependency: '+program)
    marker=TARGET/'managed-install.json'
    if TARGET.is_symlink(): raise RuntimeError('Untrusted installation directory')
    if TARGET.exists() and not marker.is_file():
        # Earlier manually reviewed deployments have the same exact package/helper.
        if not (TARGET/'kalshi_bot/gateway.py').is_file() or not (TARGET/'deploy/provision-gateway.py').is_file():
            raise RuntimeError('Unmanaged /opt/kalshi-bot; refusing to overwrite it')
    for ancestor in (TARGET.parent,Path('/')):
        info=ancestor.lstat()
        if info.st_uid!=0 or info.st_mode&0o022: raise RuntimeError('Untrusted system installation parent')
    TARGET.mkdir(mode=0o755,exist_ok=True)
    info=TARGET.lstat()
    if info.st_uid!=0 or info.st_mode&0o022: raise RuntimeError('Installation must be root-owned')
    for folder in ('kalshi_bot','deploy'):
        target=TARGET/folder
        if target.is_symlink(): raise RuntimeError('Untrusted package directory')
        target.mkdir(mode=0o755,exist_ok=True)
    for source in (SOURCE/'kalshi_bot').glob('*.py'):
        if source.is_symlink() or not stat.S_ISREG(source.lstat().st_mode): raise RuntimeError('Untrusted source file')
        write(TARGET/'kalshi_bot'/source.name,source.read_bytes())
    helper=SOURCE/'deploy/provision-gateway.py'
    if helper.is_symlink(): raise RuntimeError('Untrusted provisioning helper')
    write(TARGET/'deploy/provision-gateway.py',helper.read_bytes(),0o755)
    write(marker,json.dumps({'application':'kalshi-bitcoin-bot','client_uid':client.pw_uid}).encode())
    command=['/usr/bin/python3','-B',str(TARGET/'deploy/provision-gateway.py'),'--environment',a.environment,'--client-user',a.client_user]
    if a.allow_trading: command+=['--allow-trading']
    subprocess.run(command,check=True)
    service='kalshi-'+a.environment+'-gateway.service'
    socket='kalshi-'+a.environment+'-gateway.socket'
    # Keep the protected local endpoint available after reboot.  Enabling the
    # gateway does not read credentials or submit orders; the desktop worker
    # still has to authenticate and explicitly start trading.  The socket is
    # enabled separately because the gateway is socket-activated.
    subprocess.run(['/usr/bin/systemctl','enable',service,socket],check=True)
    subprocess.run(['/usr/bin/systemctl','start',service],check=True)
    # Type=simple can report active before Python finishes its security checks.
    # Verify the socket as the actual permitted client, without an API key.
    code="from kalshi_bot.desktop_runtime import gateway_health; from kalshi_bot.strategy import ENTRY_WINDOW_SECONDS; import json; value=gateway_health("+repr(a.environment)+"); assert value['environment']=="+repr(a.environment)+"; assert value['trading_enabled']=="+repr(a.allow_trading)+"; assert value.get('stream_protocol',0)>=2; assert value.get('entry_window_seconds')==ENTRY_WINDOW_SECONDS; assert value.get('market_balance_scope')==1; print(json.dumps(value))"
    verified=False
    for attempt in range(40):
        result=subprocess.run(['/usr/sbin/runuser','-u',a.client_user,'--','/usr/bin/python3','-B','-c',code],cwd=TARGET,capture_output=True,text=True)
        if result.returncode==0:
            verified=True
            break
        time.sleep(.5)
    if not verified:
        subprocess.run(['/usr/bin/systemctl','stop',service,'kalshi-'+a.environment+'-gateway.socket'],check=False)
        raise RuntimeError('Gateway failed real client health verification; inspect its system journal. No account connected.')
    print('Secure '+a.environment+' gateway is online. No account authenticated and no order submitted.')


if __name__=='__main__': main()
