"""Explicitly enabled trading transport; the original account connector stays GET-only."""
import base64
import http.client
import json
import re
import time
from decimal import Decimal
from urllib.parse import urlencode, urlsplit
from .api import APIError, MAX_BODY, ROOT, SchemaError, Signer, TICKER, UnixHTTP, allowed_path, decimal
from .strategy import ENTRY_WINDOW_SECONDS,ENTRY_CUTOFF_SECONDS
from .strategy import StrategyPolicy

CREATE=ROOT+'/portfolio/events/orders'
ID=re.compile(r'[A-Za-z0-9-]{1,80}\Z')

class OrderNotSent(RuntimeError):
    """Local preflight failed before any HTTP write was attempted."""


def validate_write(method,target,body=None,now=None):
    now=time.time() if now is None else now
    parsed=urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise ValueError('Invalid trading target')
    if method=='DELETE' and parsed.path.startswith(CREATE+'/'):
        from urllib.parse import parse_qsl
        pairs=parse_qsl(parsed.query,strict_parsing=True)
        if not ID.fullmatch(parsed.path[len(CREATE)+1:]) or len(pairs)!=1 or pairs[0][0]!='market_ticker' or not TICKER.fullmatch(pairs[0][1]):
            raise ValueError('Invalid cancellation')
        if body is not None:
            raise ValueError('Cancellation has no body')
        return parsed.path
    if method!='POST' or target!=CREATE or not isinstance(body,dict):
        raise ValueError('Trading endpoint denied')
    keys={'ticker','client_order_id','side','price','count','time_in_force','expiration_time','self_trade_prevention_type','cancel_order_on_pause','subaccount','strategy'}
    if set(body)!=keys or not TICKER.fullmatch(body['ticker']) or not ID.fullmatch(body['client_order_id']):
        raise ValueError('Unexpected order fields')
    p,n=decimal(body['price'],'price'),decimal(body['count'],'quantity')
    strategy=body.get('strategy')
    if not isinstance(strategy,dict) or set(strategy)!={'buy_min_cents','sell_cents','entry_window_seconds','entry_cutoff_seconds'}:
        raise ValueError('Missing strategy policy')
    configured=StrategyPolicy(strategy['buy_min_cents'],strategy['sell_cents'],strategy['entry_window_seconds'],strategy['entry_cutoff_seconds'])
    minimum=Decimal(configured.buy_min_cents)/100;stop=Decimal(configured.sell_cents)/100
    buy=(body['side']=='bid' and minimum<=p<=Decimal('.99')) or (body['side']=='ask' and Decimal('.01')<=p<=1-minimum)
    sell=(body['side']=='ask' and p==stop) or (body['side']=='bid' and p==1-stop)
    if not(buy or sell) or n<=0 or n*100!=int(n*100) or (buy and n!=n.to_integral_value()):
        raise ValueError('Order price/quantity violates strategy')
    if body['time_in_force']!='good_till_canceled' or body['self_trade_prevention_type']!='taker_at_cross' or body['cancel_order_on_pause'] is not True or type(body['subaccount']) is not int or body['subaccount']!=0:
        raise ValueError('Unexpected order execution parameters')
    if type(body['expiration_time']) is not int or not 0<body['expiration_time']-now<=(ENTRY_WINDOW_SECONDS-ENTRY_CUTOFF_SECONDS if buy else 900):
        raise ValueError('Order outside allowed expiry window')
    return parsed.path


class TradingSigner(Signer):
    """Same official signing scheme, with a separate explicit write policy."""
    def write_headers(self,method,target,body=None):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ed25519,padding
        path=validate_write(method,target,body)
        stamp=str(int(time.time()*1000))
        message=(stamp+method+path).encode('ascii')
        if isinstance(self._key,ed25519.Ed25519PrivateKey):
            signature=self._key.sign(message)
        else:
            signature=self._key.sign(message,padding.PSS(mgf=padding.MGF1(hashes.SHA256()),salt_length=32),hashes.SHA256())
        return {'KALSHI-ACCESS-KEY':self.key_id,'KALSHI-ACCESS-TIMESTAMP':stamp,'KALSHI-ACCESS-SIGNATURE':base64.b64encode(signature).decode()}


class OrderBroker:
    def __init__(self,signer,socket_path,enabled=False):
        self.signer,self.socket_path,self.enabled=signer,socket_path,enabled
        self.next_write=0.0

    def request(self,method,target,body=None):
        if not self.enabled:
            raise OrderNotSent('Trading has not been explicitly enabled')
        if time.monotonic()<self.next_write:
            raise APIError(429)
        try:
            validate_write(method,target,body)
            headers=self.signer.write_headers(method,target,body)
            encoded=json.dumps(body).encode() if body is not None else None
        except (ValueError,SchemaError):
            raise OrderNotSent('Order preflight failed before sending') from None
        headers['Content-Type']='application/json'
        conn=UnixHTTP(self.socket_path)
        try:
            if getattr(self,'before_wire',None):self.before_wire(body)
            conn.request(method,target,body=encoded,headers=headers)
            response=conn.getresponse()
            raw=response.read(MAX_BODY+1)
            if response.status==429:
                try:
                    delay=float(response.getheader('Retry-After','30'))
                    if not 0<=delay<=3600: delay=3600
                except (ValueError,TypeError):
                    delay=30
                self.next_write=time.monotonic()+max(delay,1)
            if response.status not in (200,201) or len(raw)>MAX_BODY:
                from .exchange_errors import public_code
                raise APIError(response.status,public_code(raw) if len(raw)<=MAX_BODY else None)
            value=json.loads(raw)
            if not isinstance(value,dict):
                raise SchemaError('Invalid order acknowledgment')
            return value
        except (OSError,http.client.HTTPException):
            raise APIError() from None
        finally:
            conn.close()

    def create(self,payload):
        return self.request('POST',CREATE,payload)

    def cancel(self,order_id,ticker):
        return self.request('DELETE',CREATE+'/'+order_id+'?'+urlencode({'market_ticker':ticker}))
