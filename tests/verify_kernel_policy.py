"""Run only in a disposable user+network namespace; never touches host nft rules.

unshare --user --map-current-user --keep-caps --net /usr/bin/python3 tests/verify_kernel_policy.py
"""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from kalshi_bot.gateway import nft_policy,verify_firewall

uid=os.getuid()
if len(socket.if_nameindex())!=1 or socket.if_nameindex()[0][1]!='lo':
    raise SystemExit('Refusing to run outside a disposable network namespace')
if uid==0:
    raise SystemExit('Use --map-current-user --keep-caps, not --map-root-user')
subprocess.run(['/usr/sbin/ip','link','set','lo','up'],check=True)
config={'environment':'demo','host':'external-api.demo.kalshi.co','uid':uid,
        'client_uid':uid,'addresses':['8.8.8.8']}
with socket.socket() as listener:
    listener.bind(('127.0.0.1',0))
    listener.listen(2)
    address=listener.getsockname()
    with socket.create_connection(address,timeout=.5):
        connection,_=listener.accept()
        connection.close()
    subprocess.run(['/usr/sbin/nft','-f','-'],input=nft_policy(config),text=True,check=True)
    verify_firewall(config)
    with socket.socket() as blocked:
        blocked.settimeout(.5)
        try:
            blocked.connect(address)
        except OSError:
            pass
        else:
            raise AssertionError('Kernel egress reject did not block loopback')
    raw=subprocess.run(['/usr/sbin/nft','-j','list','table','inet','kalshi_demo'],capture_output=True,text=True,check=True)
    rules=[x['rule'] for x in json.loads(raw.stdout)['nftables'] if 'rule' in x]
    counters=[e['counter']['packets'] for e in rules[-1]['expr'] if 'counter' in e]
    assert counters and counters[0]>0,'Reject counter did not record the blocked connection'
print('PASS: generated policy installed in disposable namespace, exact kernel structure verified, blocked connection counted')
