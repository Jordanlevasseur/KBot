"""Credential-free, GET-only TLS gateway. Install behind the supplied nft policy.

Listens only on a Unix socket. Destinations are immutable IPs resolved during
administrator provisioning; TLS verifies the official hostname, never a caller URL.
"""
import argparse
import errno
import http.client
import ipaddress
import json
import logging
import os
import socket
import socketserver
import threading
import time
import asyncio
import ssl
import zlib
import stat
import subprocess
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from .api import APIError, HOSTS, MAX_BODY, ROOT, allowed_path, READ_STARTED_HEADER

AUTH = {'kalshi-access-key','kalshi-access-timestamp','kalshi-access-signature'}


def validate_config(config):
    if config.get('environment') not in HOSTS or config.get('host')!=HOSTS[config['environment']]:
        raise ValueError('Invalid official gateway host')
    if not isinstance(config.get('uid'),int) or config['uid']<=0:
        raise ValueError('Gateway must run as a dedicated unprivileged user')
    if not isinstance(config.get('client_uid'),int) or config['client_uid']<=0:
        raise ValueError('An unprivileged client UID is required')
    addresses=config.get('addresses',[])
    if not addresses or len(addresses)>100:
        raise ValueError('Invalid destination set')
    if 'rest_addresses' in config and (not config['rest_addresses'] or not set(config['rest_addresses'])<=set(addresses)):
        raise ValueError('Invalid REST destination set')
    if 'ws_host' in config:
        from .streams import WS_HOSTS
        if config['ws_host']!=WS_HOSTS[config['environment']] or not config.get('ws_addresses') or not set(config['ws_addresses'])<=set(addresses):
            raise ValueError('Invalid official stream destination')
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise ValueError('Destination must be a public official service address')
    return config


def nft_policy(config):
    validate_config(config)
    uid=config['uid']
    v4=', '.join(a for a in config['addresses'] if ipaddress.ip_address(a).version==4)
    v6=', '.join(a for a in config['addresses'] if ipaddress.ip_address(a).version==6)
    table='kalshi_'+config['environment']
    rules=[f'table inet {table} {{', ' chain output { type filter hook output priority 0; policy accept;']
    if v4:
        rules.append(f'  meta skuid {uid} ip daddr {{ {v4} }} tcp dport 443 counter accept')
    if v6:
        rules.append(f'  meta skuid {uid} ip6 daddr {{ {v6} }} tcp dport 443 counter accept')
    rules.extend([f'  meta skuid {uid} counter reject',' }','}'])
    return '\n'.join(rules)+'\n'


def verify_firewall(config):
    """Verify exact installed kernel rules, including the terminal UID reject."""
    result=subprocess.run(['/usr/sbin/nft','-j','list','table','inet','kalshi_'+config['environment']],capture_output=True,text=True,check=True)
    data=json.loads(result.stdout)['nftables']
    chains=[x['chain'] for x in data if 'chain' in x]
    rules=[x['rule'] for x in data if 'rule' in x]
    if len(chains)!=1 or chains[0].get('hook')!='output' or chains[0].get('prio')!=0 or chains[0].get('policy')!='accept':
        raise RuntimeError('Unexpected gateway firewall chain')
    expected=[]
    for version in (4,6):
        addresses=[a for a in config['addresses'] if ipaddress.ip_address(a).version==version]
        if addresses:
            expected.append((version,set(addresses)))
    if len(rules)!=len(expected)+1:
        raise RuntimeError('Unexpected gateway firewall rules')
    for rule,(version,addresses) in zip(rules,expected):
        expr=[e for e in rule['expr'] if 'counter' not in e]
        if len(expr)!=4 or expr[-1]!={'accept':None}:
            raise RuntimeError('Unexpected gateway allow rule')
        matches=[e.get('match') for e in expr[:-1]]
        uid_match={'op':'==','left':{'meta':{'key':'skuid'}},'right':config['uid']}
        port_match={'op':'==','left':{'payload':{'protocol':'tcp','field':'dport'}},'right':443}
        if matches[0]!=uid_match or matches[2]!=port_match:
            raise RuntimeError('Firewall user or port mismatch')
        dest=matches[1]
        if dest.get('op')!='==' or dest.get('left')!={'payload':{'protocol':'ip' if version==4 else 'ip6','field':'daddr'}}:
            raise RuntimeError('Firewall destination mismatch')
        right=dest.get('right')
        actual=set(right['set']) if isinstance(right,dict) and 'set' in right else {right}
        if actual!=addresses:
            raise RuntimeError('Firewall address set mismatch')
    tail=[e for e in rules[-1]['expr'] if 'counter' not in e]
    if len(tail)!=2 or tail[0]!={'match':{'op':'==','left':{'meta':{'key':'skuid'}},'right':config['uid']}} or 'reject' not in tail[1]:
        raise RuntimeError('Gateway egress is not denied by default')


def verify_bind_denial():
    """Require the service's OS socket-bind denial before serving any request."""
    # systemd SocketBindDeny rules apply to ports 1..65535; port 0 is
    # dynamic allocation and is not a valid proof of missing enforcement.
    for family,address in ((socket.AF_INET,('127.0.0.1',65529)),(socket.AF_INET6,('::1',65529))):
        try:
            sock=socket.socket(family,socket.SOCK_STREAM)
        except OSError as exc:
            if exc.errno in (errno.EAFNOSUPPORT,errno.EPERM,errno.EACCES):
                continue
            raise
        with sock:
            try:
                sock.bind(address)
            except OSError as exc:
                if exc.errno in (errno.EPERM,errno.EACCES):
                    continue
                raise RuntimeError('Cannot verify gateway socket-bind isolation') from None
            raise RuntimeError('OS socket-bind denial is unavailable; refusing gateway startup')


_TLS_CONTEXT=None
_TLS_CONTEXT_LOCK=threading.Lock()


def tls_context():
    """One verified, immutable trust store per gateway process, shared by readers.

    Loading the system CA bundle for every request consumes the old service's
    entire CPU quota under continuous preparation. TLS peer/hostname validation
    and the pinned destination checks remain enabled for every connection.
    """
    global _TLS_CONTEXT
    with _TLS_CONTEXT_LOCK:
        if _TLS_CONTEXT is None:_TLS_CONTEXT=ssl.create_default_context()
        return _TLS_CONTEXT


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,host,address):
        super().__init__(host,443,timeout=10,context=tls_context())
        self.address=address

    def connect(self):
        raw=socket.create_connection((self.address,443),timeout=self.timeout)
        try:
            self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def decoded_response(data,headers):
    """Bound both compressed input and decompressed JSON; reject malformed gzip."""
    if len(data)>MAX_BODY:raise APIError(502)
    encodings=[v.strip().lower() for k,v in headers if k.lower()=='content-encoding']
    if not encodings or encodings==['identity']:return data
    if encodings!=['gzip']:raise APIError(502)
    try:
        decoder=zlib.decompressobj(16+zlib.MAX_WBITS)
        result=decoder.decompress(data,MAX_BODY+1)
        if len(result)>MAX_BODY or decoder.unconsumed_tail or not decoder.eof or decoder.unused_data:
            raise APIError(502)
        return result
    except zlib.error:raise APIError(502) from None


class ReadConnections:
    """Bounded idle TLS sessions for GET only; never replay a write."""
    def __init__(self,clock=time.monotonic,max_idle=4,idle_seconds=15):
        self.clock=clock;self.max_idle=max_idle;self.idle_seconds=idle_seconds
        self.lock=threading.Lock();self.idle=[];self.closed=False
    def take(self,host,address):
        key=(host,address)
        with self.lock:
            now=self.clock();live=[];selected=None
            for saved,connection,at in self.idle:
                if now-at>self.idle_seconds:connection.close()
                elif saved==key and selected is None:selected=connection
                else:live.append((saved,connection,at))
            self.idle=live
            return selected
    def put(self,host,address,connection):
        with self.lock:
            if self.closed or len(self.idle)>=self.max_idle:connection.close()
            else:self.idle.append(((host,address),connection,self.clock()))
    def close(self):
        with self.lock:
            self.closed=True
            for _,connection,_ in self.idle:connection.close()
            self.idle=[]


def forward(config,target,headers,method="GET",body=None):
    if method=="GET":allowed_path(target)
    else:
        from .trade_api import validate_write
        if config.get("allow_trading") is not True:raise ValueError("Gateway trading is disabled")
        validate_write(method,target,body)
    pool=config.get('_read_pool') if method=='GET' else None
    if not isinstance(pool,ReadConnections):pool=None
    for address in config.get('rest_addresses',config['addresses'])[:4]:
        connection=pool.take(config['host'],address) if pool else None
        reused=connection is not None
        # GET may recover a closed keepalive session once on the same pinned IP.
        # Writes use a new connection, with no retry after an uncertain result.
        for attempt in range(2 if reused else 1):
            conn=connection if attempt==0 and reused else PinnedHTTPS(config['host'],address)
            retained=False
            try:
                observed=None
                if method=='GET':
                    if not reused or attempt:conn.connect()
                    observed=time.monotonic_ns()  # After TLS, before authenticated GET.
                upstream={key:value for key,value in body.items() if key!='strategy'} if body is not None and method=='POST' else body
                conn.request(method,target,body=json.dumps(upstream).encode() if upstream is not None else None,
                             headers={**headers,**({'Accept-Encoding':'gzip'} if method=='GET' else {}),
                                      **({'Content-Type':'application/json'} if body is not None else {})})
                response=conn.getresponse();wire_data=response.read(MAX_BODY+1);returned=response.getheaders()
                data=decoded_response(wire_data,returned)
                safe={k:v for k,v in returned if k.lower() in ('content-type','retry-after')}
                if observed is not None:
                    safe[READ_STARTED_HEADER]=str(observed)
                    safe['x-kalshi-bot-wire-bytes']=str(len(wire_data))
                    safe['x-kalshi-bot-decoded-bytes']=str(len(data))
                if pool and response.status==200 and response.will_close is False and conn.sock is not None:
                    pool.put(config['host'],address,conn);retained=True
                return response.status,safe,data
            except (OSError,http.client.HTTPException):
                if method!='GET':raise APIError(502) from None
            finally:
                if not retained:conn.close()
    raise APIError(502)


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.0'
    server_version='KalshiReadOnlyGateway'
    sys_version=''

    def handle(self):
        # A client timeout/normal reconnect is already closed. Attempting to
        # send a second 502 to that same socket creates misleading tracebacks.
        try:super().handle()
        except (BrokenPipeError,ConnectionResetError):self.close_connection=True

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self,*args):
        pass  # Never log signed request targets/headers or response bodies.

    def do_GET(self):
        if self.path=='/internal/health':
            from .streams import STREAM_PROTOCOL
            from .strategy import ENTRY_WINDOW_SECONDS
            body=json.dumps({'environment':self.server.config['environment'],
                             'trading_enabled':self.server.config.get('allow_trading') is True,
                             'streaming_enabled':bool(self.server.config.get('ws_host')),
                             'stream_protocol':STREAM_PROTOCOL,'entry_window_seconds':ENTRY_WINDOW_SECONDS,'market_balance_scope':1,
                             'strategy_policy_version':1}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        from .streams import STREAM_PATH,REFERENCE_PATH
        if self.path==REFERENCE_PATH or self.path.startswith(STREAM_PATH):
            return self._stream()
        try:
            if len(self.path)>8192 or self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length') not in (None,'0'):
                raise ValueError('Request body not permitted')
            allowed_path(self.path)
            headers={}
            for name in AUTH:
                values=self.headers.get_all(name,[])
                if len(values)!=1 or not values[0] or len(values[0])>2000:
                    raise ValueError('Missing/duplicate authentication header')
                headers[name]=values[0]
            status,returned,body=forward(self.server.config,self.path,headers)
            self.send_response(status)
            for k,v in returned.items():
                self.send_header(k,v)
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except ValueError:
            self.send_error(403,'Read-only request policy rejected')
        except (BrokenPipeError,ConnectionResetError):
            self.close_connection=True
        except (APIError,OSError):
            self.send_error(502,'Official service unavailable')

    def _stream(self):
        from .streams import STREAM_PATH,REFERENCE_PATH,relay
        from .api import TICKER
        try:
            reference=self.path==REFERENCE_PATH
            ticker='BRTI' if reference else self.path[len(STREAM_PATH):]
            if (not reference and not TICKER.fullmatch(ticker)) or not self.server.config.get('ws_host'):
                raise ValueError('Invalid stream request')
            if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length') not in (None,'0'):
                raise ValueError('Unexpected stream body')
            headers={}
            for name in AUTH:
                values=self.headers.get_all(name,[])
                if len(values)!=1 or not values[0] or len(values[0])>2000: raise ValueError('Invalid auth')
                headers[name]=values[0]
            self.send_response(200)
            self.send_header('Content-Type','application/x-ndjson')
            self.end_headers()
            def write(value):
                self.wfile.write(json.dumps(value).encode()+b'\n')
                self.wfile.flush()
            asyncio.run(relay(self.server.config,ticker,headers,write,reference=True) if reference else
                        relay(self.server.config,ticker,headers,write))
        except (ValueError,OSError):
            self.close_connection=True
        except Exception as exc:
            from .streams import safe_stream_failure
            safe_stream_failure('handler',exc)
            self.close_connection=True


class TradingHandler(Handler):
    def _write(self,method):
        try:
            from .trade_api import validate_write
            if self.server.config.get('allow_trading') is not True:
                raise ValueError('Gateway trading is disabled')
            if self.headers.get('Transfer-Encoding'):
                raise ValueError('Chunked requests denied')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<=length<=4096 or len(self.headers.get_all('Content-Length',[]))>1:
                raise ValueError('Invalid body size')
            body=json.loads(self.rfile.read(length)) if length else None
            validate_write(method,self.path,body)
            headers={}
            for name in AUTH:
                values=self.headers.get_all(name,[])
                if len(values)!=1 or not values[0] or len(values[0])>2000:
                    raise ValueError('Invalid authentication header')
                headers[name]=values[0]
            # Strategy metadata is validated locally by both client and gateway,
            # but is private to this gateway and must never reach Kalshi.
            status,returned,data=forward(self.server.config,self.path,headers,method,body)
            self.send_response(status)
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (ValueError,TypeError,KeyError):
            self.send_error(403,'Trading request policy rejected')
        except (APIError,OSError):
            self.send_error(502,'Order result unknown; reconcile before retry')

    def do_POST(self):
        self._write('POST')

    def do_DELETE(self):
        self._write('DELETE')


class Server(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    allow_reuse_address=False
    daemon_threads=True

    def __init__(self,*args,**kwargs):
        self.slots=threading.BoundedSemaphore(8)
        super().__init__(*args,**kwargs)

    @property
    def config(self):return self._config

    @config.setter
    def config(self,value):
        previous=getattr(self,'_config',{}).get('_read_pool')
        if previous:previous.close()
        self._config={**value,'_read_pool':ReadConnections()}

    def server_close(self):
        pool=getattr(self,'_config',{}).get('_read_pool')
        if pool:pool.close()
        super().server_close()

    def process_request(self,request,client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try: super().process_request(request,client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self,request,client_address):
        try: super().process_request_thread(request,client_address)
        finally: self.slots.release()

    def verify_request(self,request,client_address):
        import struct
        _,uid,_=struct.unpack('3i',request.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        return uid==self.config['client_uid']


def load_config(path):
    p=Path(path)
    info=p.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
        raise RuntimeError('Gateway configuration must be root-owned and not writable by other users')
    return validate_config(json.loads(p.read_text()))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True)
    p.add_argument('--socket',required=True)
    p.add_argument('--verify',action='store_true',help='Root firewall preflight only; no network')
    a=p.parse_args()
    config=load_config(a.config)
    if a.verify:
        verify_firewall(config)
        print('Kernel gateway policy verified')
        return
    if os.getuid()!=config['uid']:
        raise RuntimeError('Dedicated gateway UID mismatch')
    verify_bind_denial()
    os.umask(0o007)
    # systemd creates the sole AF_UNIX listener before dropping privileges.
    # The gateway itself is denied bind/listen by the kernel seccomp policy.
    if os.environ.get('LISTEN_PID')!=str(os.getpid()) or os.environ.get('LISTEN_FDS')!='1':
        raise RuntimeError('A systemd-activated Unix socket is required')
    activated=socket.socket(fileno=3)
    if activated.family!=socket.AF_UNIX or activated.type&socket.SOCK_STREAM!=socket.SOCK_STREAM:
        activated.close()
        raise RuntimeError('Unexpected activated socket type')
    handler=TradingHandler if config.get('allow_trading') is True else Handler
    with Server(a.socket,handler,bind_and_activate=False) as server:
        server.socket.close()
        server.socket=activated
        server.config=config
        server.serve_forever(poll_interval=.5)


if __name__=='__main__':
    main()
