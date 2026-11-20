"""Verify the offline launcher's observable isolation; never install host rules."""
import os
import socket
from pathlib import Path


def check_isolation():
    problems=[]
    try:
        interfaces = socket.if_nameindex()
    except OSError:
        raise RuntimeError('Isolation verification failed: interface inspection unavailable') from None
    if any(name != 'lo' for _,name in interfaces):
        problems.append('non-loopback network interface visible')
    for path in ('/home','/root','/run/user','/mnt','/media'):
        if Path(path).exists():
            problems.append('host path visible: '+path)
    if os.environ.get('HOME') != '/tmp':
        problems.append('unexpected HOME')
    cap_line=next(line for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('CapEff:'))
    if int(cap_line.split()[1],16):
        problems.append('effective capabilities are not empty')
    if problems:
        raise RuntimeError('Isolation verification failed: '+ '; '.join(problems))
    # A network namespace with only loopback has no route to external services.
    with socket.socket() as sock:
        sock.settimeout(.2)
        try:
            sock.connect(('192.0.2.1',443))  # Reserved documentation address.
        except OSError:
            pass
        else:
            raise RuntimeError('External networking unexpectedly available')
    return {'network':'isolated loopback only','personal_files':'not mounted','capabilities':'none'}
