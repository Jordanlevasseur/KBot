#!/usr/bin/python3
"""Administrator provisioning. Never run automatically by the bot.

Install only root-reviewed code into /opt/kalshi-bot before invoking this helper.
Creates a dedicated UID, narrow nft table, config and disabled system service.
No account authentication, key access, or gateway startup occurs here.
"""
import argparse
import grp
import json
import os
import pwd
import socket
import stat
import subprocess
import sys
from pathlib import Path

ROOT=Path('/opt/kalshi-bot')
sys.path.insert(0,str(ROOT))
from kalshi_bot.api import HOSTS
from kalshi_bot.streams import WS_HOSTS
from kalshi_bot.gateway import nft_policy, verify_firewall


def run(*cmd):
    return subprocess.run(cmd,check=True,capture_output=True,text=True)


def trusted(path):
    info=path.lstat()
    if info.st_uid!=0 or info.st_mode&0o022 or stat.S_ISLNK(info.st_mode):
        raise RuntimeError('Deployment must be root-owned and not writable by other users: '+str(path))


def atomic(path,data):
    if path.is_symlink():
        raise RuntimeError('Refusing configuration symlink')
    temp=path.with_suffix(path.suffix+'.new')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
    with os.fdopen(fd,'w') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp,path)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--environment',choices=HOSTS,required=True)
    p.add_argument('--client-user',required=True,help='Local owner of the account credentials/monitor')
    p.add_argument('--allow-trading',action='store_true',help='Enable narrowly validated order writes; default remains read-only')
    a=p.parse_args()
    if os.geteuid()!=0:
        p.error('Administrator privileges are required to install the OS network policy')
    for path in [ROOT,*ROOT.parents]:
        trusted(path)
    for path in ROOT.rglob('*'):
        trusted(path)
    client=pwd.getpwnam(a.client_user)
    if client.pw_uid==0:
        p.error('Account monitor must be an unprivileged user')
    environment=a.environment
    user='kalshi-'+environment
    service=user+'-gateway.service'
    group='kalshi-access'
    try:
        grp.getgrnam(group)
    except KeyError:
        run('/usr/sbin/groupadd','--system',group)
    try:
        owner=pwd.getpwnam(user)
        if owner.pw_shell not in ('/usr/sbin/nologin','/sbin/nologin') or owner.pw_dir!='/nonexistent':
            raise RuntimeError('Reserved service username already belongs to another account')
    except KeyError:
        run('/usr/sbin/useradd','--system','--no-create-home','--home-dir','/nonexistent','--shell','/usr/sbin/nologin','--gid',group,user)
        owner=pwd.getpwnam(user)
    # Only administrators resolve DNS. The gateway itself can use pinned addresses
    # only, has no DNS egress, and verifies the fixed official hostname with TLS.
    addresses=sorted({row[4][0] for row in socket.getaddrinfo(HOSTS[environment],443,type=socket.SOCK_STREAM)})
    rest_addresses=list(addresses)
    ws_addresses=sorted({row[4][0] for row in socket.getaddrinfo(WS_HOSTS[environment],443,type=socket.SOCK_STREAM)})
    addresses=sorted(set(addresses+ws_addresses))
    config={'rest_addresses':rest_addresses,'ws_host':WS_HOSTS[environment],'ws_addresses':ws_addresses,'environment':environment,'host':HOSTS[environment],'uid':owner.pw_uid,
            'client_uid':client.pw_uid,'addresses':addresses,'allow_trading':a.allow_trading}
    rules=nft_policy(config)
    folder=Path('/etc/kalshi-bot')
    folder.mkdir(mode=0o755,exist_ok=True)
    trusted(folder)
    config_path=folder/(environment+'-gateway.json')
    socket_service='kalshi-'+environment+'-gateway.socket'
    subprocess.run(['/usr/bin/systemctl','stop',service,socket_service],capture_output=True,check=False)
    table='kalshi_'+environment
    existing=subprocess.run(['/usr/sbin/nft','list','table','inet',table],capture_output=True)
    if existing.returncode==0:
        run('/usr/bin/systemctl','stop',service)
        rules='delete table inet '+table+'\n'+rules
    # One atomic netlink batch; syntax or permission failures abort provisioning.
    subprocess.run(['/usr/sbin/nft','-f','-'],input=rules,text=True,check=True)
    atomic(config_path,json.dumps(config,indent=2)+'\n')
    verify_firewall(config)
    # Keep a boot-restorable policy separate from unrelated host firewall rules.
    atomic(folder/(environment+'.nft'),nft_policy(config))
    unit=f'''[Unit]
Description=Kalshi {environment} isolated TLS gateway
After=network-online.target kalshi-{environment}-policy.service kalshi-{environment}-gateway.socket
Wants=network-online.target
Requires=kalshi-{environment}-policy.service kalshi-{environment}-gateway.socket
[Service]
Type=simple
User={user}
Group={group}
WorkingDirectory=/opt/kalshi-bot
UMask=0007
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 -m kalshi_bot.gateway --config {config_path} --socket /run/kalshi-{environment}/gateway.sock
Restart=on-failure
RestartSec=10
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictNamespaces=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
Sockets=kalshi-{environment}-gateway.socket
SystemCallFilter=~bind listen
SystemCallErrorNumber=EPERM
CapabilityBoundingSet=
MemoryMax=256M
CPUQuota=50%
[Install]
WantedBy=multi-user.target
'''
    policy_unit=f'''[Unit]
Description=Kalshi {environment} dedicated egress policy
Before={service}
[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/kalshi-bot
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 -m kalshi_bot.restore_policy --environment {environment}
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
CapabilityBoundingSet=CAP_NET_ADMIN
RestrictAddressFamilies=AF_NETLINK AF_UNIX
'''
    socket_unit=f'''[Unit]
Description=Kalshi {environment} local client socket
After=kalshi-{environment}-policy.service
Requires=kalshi-{environment}-policy.service
[Socket]
ListenStream=/run/kalshi-{environment}/gateway.sock
SocketUser={client.pw_name}
SocketMode=0600
DirectoryMode=0711
RemoveOnStop=yes
Service={service}
[Install]
WantedBy=sockets.target
'''
    atomic(Path('/etc/systemd/system')/socket_service,socket_unit)
    atomic(Path('/etc/systemd/system')/('kalshi-'+environment+'-policy.service'),policy_unit)
    atomic(Path('/etc/systemd/system')/service,unit)
    run('/usr/bin/systemctl','daemon-reload')
    print('Gateway installed but NOT started or enabled. No account connected.')
    print('After approval: systemctl start '+service)
    print('The dependent policy service restores and verifies the pinned destination policy at startup.')


if __name__=='__main__':
    main()
