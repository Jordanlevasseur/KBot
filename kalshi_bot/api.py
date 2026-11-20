"""Read-only official API connector. There is deliberately no order-write method.

The process has no internet route. HTTP travels over one Unix socket to a
separately isolated gateway. No credentials or HTTP response bodies are logged.
"""
import base64
import http.client
import hashlib
from email.utils import parsedate_to_datetime
import json
import re
import socket
import time
import threading
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, urlsplit

ROOT = '/trade-api/v2'
HOSTS = {'demo': 'external-api.demo.kalshi.co', 'production': 'external-api.kalshi.com'}
COLLECTIONS = {
    '/markets': 'markets', '/portfolio/positions': 'market_positions',
    '/portfolio/orders': 'orders', '/portfolio/fills': 'fills',
    '/portfolio/settlements': 'settlements', '/events/fee_changes':'event_fee_changes',
}
EXACT = set(COLLECTIONS) | {'/portfolio/balance', '/exchange/status', '/series/KXBTC15M'}
TICKER = re.compile(r'KXBTC15M-[A-Za-z0-9_-]{1,150}\Z')
PARAMS = {'limit', 'cursor', 'status', 'series_ticker', 'ticker', 'count_filter', 'depth', 'event_ticker','min_close_ts','max_close_ts','subaccount','exchange_index'}
MAX_BODY = 4 * 1024 * 1024
READ_STARTED_HEADER='x-kalshi-bot-read-started-ns'


class APIError(RuntimeError):
    def __init__(self, status=0,code=None):
        from .exchange_errors import REASONS
        self.status = status
        self.code=code if isinstance(code,str) and code in REASONS else None
        self.reason=REASONS.get(self.code)
        message=f'Kalshi request failed (HTTP {status})' if status else 'Kalshi transport unavailable'
        super().__init__(message+(' • '+self.code+': '+self.reason if self.code else ''))


class SchemaError(RuntimeError):
    pass


def allowed_path(target):
    """Narrow GET allowlist shared by both sides of the trust boundary."""
    from urllib.parse import parse_qsl
    if not isinstance(target, str) or len(target) > 8192 or any(ord(c)<32 or ord(c)>126 for c in target):
        raise ValueError('Invalid request target')
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith(ROOT+'/'):
        raise ValueError('Only relative official Trade API paths are permitted')
    path = parsed.path[len(ROOT):]
    if path not in EXACT:
        parts = path.split('/')
        market_read = (
            len(parts) in (3,4)
            and parts[1]=='markets'
            and TICKER.fullmatch(parts[2])
            and (len(parts)==3 or parts[3]=='orderbook')
        )
        order_read = re.fullmatch(
            r'/portfolio/orders/[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}',
            path
        ) is not None
        if not (market_read or order_read):
            raise ValueError('Endpoint is not in the read-only allowlist')
        if order_read and parsed.query:
            raise ValueError('Individual order lookup does not accept query parameters')
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    if len({k for k,_ in pairs}) != len(pairs) or any(k not in PARAMS for k,_ in pairs):
        raise ValueError('Invalid query parameters')
    for k,v in pairs:
        if k=='event_ticker' and not TICKER.fullmatch(v):
            raise ValueError('Only Bitcoin event fees are allowed')
        if k == 'series_ticker' and v != 'KXBTC15M':
            raise ValueError('Only Bitcoin 15-minute series is allowed')
        if k == 'limit' and (not v.isdigit() or not 1 <= int(v) <= 1000):
            raise ValueError('Invalid page size')
    query=dict(pairs)
    if 'subaccount' in query or 'exchange_index' in query:
        index=query.get('exchange_index','')
        if (path!='/portfolio/balance' or set(query)!={'subaccount','exchange_index'} or query['subaccount']!='0'
                or not index.isdigit() or not 0<=int(index)<=63 or str(int(index))!=index):
            raise ValueError('Only primary-account, market-scoped balance reads are permitted')
    if 'min_close_ts' in query or 'max_close_ts' in query:
        low,high=query.get('min_close_ts',''),query.get('max_close_ts','')
        if (path!='/markets' or query.get('series_ticker')!='KXBTC15M' or query.get('status') not in (None,'closed')
                or not low.isdigit() or not high.isdigit() or not 0<int(high)-int(low)<=3600):
            raise ValueError('Invalid Bitcoin discovery time window')
    return parsed.path


def decimal(value, label):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise SchemaError('Invalid '+label) from None
    if not result.is_finite():
        raise SchemaError('Non-finite '+label)
    return result


def book_prices(payload):
    try:
        book = payload['orderbook_fp']
        bids = {}
        for side in ('yes','no'):
            levels = book[side+'_dollars'] or []
            valid = []
            for price, quantity in levels:
                p, n = decimal(price,'price'), decimal(quantity,'quantity')
                if not 0 <= p <= 1 or n < 0:
                    raise SchemaError('Invalid order book')
                if n > 0:
                    valid.append(p)
            bids[side] = max(valid) if valid else None
        return {'yes_bid': bids['yes'], 'no_bid': bids['no'],
                'yes_ask': 1-bids['no'] if bids['no'] is not None else None,
                'no_ask': 1-bids['yes'] if bids['yes'] is not None else None}
    except (KeyError, TypeError, ValueError):
        raise SchemaError('Unexpected order-book schema') from None


class Signer:
    def __init__(self, key_id, pem):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
        if not isinstance(key_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',key_id):
            raise ValueError('Invalid API key identifier')
        try:
            key = serialization.load_pem_private_key(pem, password=None)
        except (ValueError, TypeError):
            raise ValueError('Private key could not be parsed') from None
        if not isinstance(key,(ed25519.Ed25519PrivateKey,rsa.RSAPrivateKey)):
            raise ValueError('Only Ed25519 and RSA API keys are supported')
        if isinstance(key,rsa.RSAPrivateKey) and key.key_size < 2048:
            raise ValueError('RSA key must be at least 2048 bits')
        self.key_id, self._key = key_id, key
        public = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.fingerprint = hashlib.sha256(key_id.encode()+b"\0"+public).hexdigest()

    def headers(self, method, target, timestamp=None):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ed25519, padding
        if method != 'GET':
            raise ValueError('This connector is read-only')
        path = allowed_path(target)
        stamp = str(int(time.time()*1000) if timestamp is None else timestamp)
        message = (stamp + method + path).encode('ascii')
        if isinstance(self._key,ed25519.Ed25519PrivateKey):
            signature = self._key.sign(message)
        else:
            signature = self._key.sign(message,padding.PSS(mgf=padding.MGF1(hashes.SHA256()),salt_length=32),hashes.SHA256())
        return {'KALSHI-ACCESS-KEY':self.key_id,'KALSHI-ACCESS-TIMESTAMP':stamp,
                'KALSHI-ACCESS-SIGNATURE':base64.b64encode(signature).decode('ascii')}


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self,path):
        super().__init__('localhost',timeout=15)
        self.path=path

    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class GatewayTransport:
    def __init__(self,path):
        self.path=path

    def __call__(self,target,headers):
        allowed_path(target)
        conn=UnixHTTP(self.path)
        try:
            conn.request('GET',target,headers=headers)
            response=conn.getresponse()
            data=response.read(MAX_BODY+1)
            if len(data)>MAX_BODY:
                raise SchemaError('Response exceeds safety limit')
            return response.status, dict(response.getheaders()), data
        except (OSError,http.client.HTTPException):
            raise APIError() from None
        finally:
            conn.close()


# Verified against official Basic-tier budgets and the public endpoint-cost list:
# these allowlisted ordinary reads cost 10 tokens; Basic allows 200 tokens/s.
# Use only 50 tokens/s, shared by discovery and trading. Balance has its OWN
# one-request/s bucket. These constants do not increase the order-write rate.
READ_INTERVAL_SECONDS=.2
BALANCE_INTERVAL_SECONDS=1.05


class RateGate:
    """Shared conservative pacing; independent balance/read buckets.

    Sleep outside the lock: a UI balance wait must not block market discovery.
    Server retry backoff is shared with other clients using the affected bucket.
    """
    def __init__(self,sleep=time.sleep,clock=time.monotonic):
        self.sleep,self.clock=sleep,clock
        self.lock=threading.Lock()
        self.last=self.balance=-100.0
        self.read_after=self.balance_after=0.0

    def wait(self,path):
        balance=path=='/portfolio/balance'
        while True:
            with self.lock:
                now=self.clock()
                ready=max(self.balance+BALANCE_INTERVAL_SECONDS,self.balance_after) if balance else max(self.last+READ_INTERVAL_SECONDS,self.read_after)
                delay=max(0,ready-now)
                if not delay:
                    if balance:self.balance=now
                    else:self.last=now
                    return
            self.sleep(delay)

    def defer(self,path,delay):
        # Clients still perform their bounded exponential retry. Sharing its
        # cooldown prevents the discovery thread spending the same empty bucket.
        with self.lock:
            until=self.clock()+delay
            if path=='/portfolio/balance':self.balance_after=max(self.balance_after,until)
            else:self.read_after=max(self.read_after,until)


class Client:
    def __init__(self,signer,transport,*,sleep=time.sleep,clock=time.monotonic,gate=None):
        self.signer,self.transport=signer,transport
        self.sleep,self.clock=sleep,clock
        self.gate=gate
        self._last=-100.0
        self._balance=-100.0
        self.observed_at=None

    def get(self,path,params=None):
        target=ROOT+path+('?' + urlencode(params) if params else '')
        allowed_path(target)
        for attempt in range(4):
            if self.gate:
                self.gate.wait(path)
            else:
                # A standalone client follows the same budget. With a shared
                # gate, do not add a second per-client queue in front of it.
                last=self._balance if path=='/portfolio/balance' else self._last
                interval=BALANCE_INTERVAL_SECONDS if path=='/portfolio/balance' else READ_INTERVAL_SECONDS
                wait=max(0,last+interval-self.clock())
                if wait:self.sleep(wait)
            if path=='/portfolio/balance':self._balance=self.clock()
            else:self._last=self.clock()
            sent_at=self.clock()
            try:
                status,headers,raw=self.transport(target,self.signer.headers('GET',target))
                received_at=self.clock()
            except APIError:
                if attempt==3:
                    raise
                self.sleep(2**attempt)
                continue
            if status==429 or status>=500:
                if attempt==3:
                    raise APIError(status)
                try:
                    delay=float(next((v for k,v in headers.items() if k.lower()=='retry-after'),2**attempt))
                except (TypeError,ValueError):
                    retry=next((v for k,v in headers.items() if k.lower()=='retry-after'),None)
                    try:
                        delay=max(0,parsedate_to_datetime(retry).timestamp()-time.time())
                    except (TypeError,ValueError,AttributeError,OverflowError):
                        delay=2**attempt
                # Excessive retry delays fail instead of retrying prematurely.
                if not 0 <= delay <= 30:
                    raise APIError(status)
                delay=max(delay,2**attempt)
                if self.gate:self.gate.defer(path,delay)
                self.sleep(delay)
                continue
            if status != 200:
                # Never expose response body: it may echo account/request details.
                from .exchange_errors import public_code
                raise APIError(status,public_code(raw))
            try:
                result=json.loads(raw)
            except (ValueError,UnicodeError):
                raise SchemaError('Response is not valid JSON') from None
            if not isinstance(result,dict):
                raise SchemaError('Expected JSON object')
            values=[v for k,v in headers.items() if k.lower()==READ_STARTED_HEADER]
            observed=sent_at
            if values:
                if len(values)!=1 or not isinstance(values[0],str) or not values[0].isascii() or not values[0].isdigit() or len(values[0])>20:
                    raise SchemaError('Invalid trusted-gateway read timestamp')
                observed=int(values[0])/1_000_000_000
                if not sent_at<=observed<=received_at:
                    raise SchemaError('Trusted-gateway read timestamp is outside this request')
            self.observed_at=observed if self.observed_at is None else min(self.observed_at,observed)
            return result
        raise APIError()

    def pages(self,path,params=None):
        if path not in COLLECTIONS:
            raise ValueError('Endpoint is not a supported collection')
        params=dict(params or {})
        # Official portfolio endpoints support 1,000 rows. Re-reading the full
        # history in 200-row batches delayed each entry check on active accounts.
        params.setdefault('limit',1000 if path.startswith('/portfolio/') else 200)
        seen=set()
        records=[]
        for _ in range(500):
            body=self.get(path,params)
            values=body.get(COLLECTIONS[path])
            if not isinstance(values,list) or any(not isinstance(v,dict) for v in values):
                raise SchemaError('Invalid collection response')
            records.extend(values)
            cursor=body.get('cursor','')
            if not cursor:
                return records
            if not isinstance(cursor,str) or cursor in seen:
                raise SchemaError('Repeated or invalid pagination cursor')
            seen.add(cursor)
            params['cursor']=cursor
        raise SchemaError('Pagination limit exceeded; snapshot is incomplete')

    def submit_order(self,*args,**kwargs):
        raise RuntimeError('External orders disabled: strict fill floor is not exchange-enforced')
