"""Display-only BRTI cache. No strategy, order, or database dependencies."""
import json
import threading
import time
from decimal import Decimal, InvalidOperation
from .api import MAX_BODY, UnixHTTP
from .streams import REFERENCE_PATH, ws_headers, BookFeed

FRESH_SECONDS=5


def number(value):
    if isinstance(value,bool) or not isinstance(value,(str,int,float,Decimal)):
        return None
    try:
        result=Decimal(str(value))
        return result if result.is_finite() and 0<result<Decimal('1e12') else None
    except InvalidOperation:
        return None


class ReferenceFeed:
    """One bounded background connection, independent of all order-book feeds."""
    def __init__(self,signer,socket_path):
        self.signer,self.socket_path=signer,socket_path
        self.lock=threading.Lock();self.stop=threading.Event()
        self.connection=None;self.thread=None
        self.value=None;self.source_ms=0;self.sample_mono=None;self.initial_age=None
        self.status='Unavailable'

    def consume(self,message,*,wall=None,mono=None):
        wall=time.time() if wall is None else wall
        mono=time.monotonic() if mono is None else mono
        if not isinstance(message,dict):return False
        if message.get('type')=='error':
            with self.lock:self.status='Unavailable'
            return False
        if message.get('type')!='cfbenchmarks_value':return False
        try:
            body=message['msg']
            if body.get('index_id')!='BRTI':return False
            raw=body['data']
            if not isinstance(raw,str) or len(raw)>16384:return False
            data=json.loads(raw)
            if not isinstance(data,dict) or data.get('type')!='value' or data.get('id')!='BRTI':return False
            value=number(data.get('value'));stamp=data.get('time')
            if value is None or type(stamp) not in (int,float):return False
            age=wall-stamp/1000
            if not 0<=age<=FRESH_SECONDS:return False
            with self.lock:
                if stamp<=self.source_ms:return False
                self.value=str(value);self.source_ms=stamp
                self.sample_mono=mono;self.initial_age=age;self.status='Live'
            return True
        except (KeyError,TypeError,ValueError,AttributeError,OverflowError):return False

    def snapshot(self):
        with self.lock:
            return dict(value=self.value,source_ms=self.source_ms,sample_mono=self.sample_mono,
                        initial_age=self.initial_age,status=self.status)

    def start(self):
        if self.thread is not None:return
        self.thread=threading.Thread(target=self._run,daemon=True,name='brti-display')
        self.thread.start()

    def _run(self):
        delay=1
        while not self.stop.is_set():
            connection=UnixHTTP(self.socket_path);response=None
            try:
                with self.lock:self.connection=connection
                connection.request('GET',REFERENCE_PATH,headers=ws_headers(self.signer))
                connection.stream_socket=connection.sock
                response=connection.getresponse()
                if response.status!=200:raise OSError('Reference unavailable')
                while not self.stop.is_set():
                    raw=response.readline(MAX_BODY+1)
                    if not raw or len(raw)>MAX_BODY:raise OSError('Reference closed')
                    if self.consume(json.loads(raw)):delay=1
            except Exception:
                # Never retain upstream bodies, signed headers, or exception text.
                with self.lock:self.status='Unavailable'
            finally:
                if response is not None:response.close()
                connection.close()
                with self.lock:self.connection=None
            self.stop.wait(delay);delay=min(30,delay*2)

    def close(self):
        self.stop.set()
        with self.lock:connection=self.connection;self.status='Unavailable'
        if connection is not None:BookFeed._disconnect(connection)
        if self.thread is not None:self.thread.join(timeout=.3)


def reference_text(message,mono=None):
    mono=time.monotonic() if mono is None else mono
    active=bool(message.get('market'))
    target=number(message.get('btc_target')) if active else None
    ref=message.get('btc_reference') or {}
    value=number(ref.get('value')) if active else None
    sample=ref.get('sample_mono');age=ref.get('initial_age')
    age=(age+max(0,mono-sample)) if isinstance(age,(int,float)) and isinstance(sample,(int,float)) else None
    live=value is not None and age is not None and 0<=age<=FRESH_SECONDS and ref.get('status')=='Live'
    target_text=f'${target:,.2f}' if target is not None else 'Unavailable'
    if value is None:price='Unavailable'
    elif live:price=f'${value:,.2f} (Live, {age:.1f}s old)'
    else:price=f'${value:,.2f} (STALE / unavailable'+(f', {age:.0f}s old)' if age is not None else ')')
    difference=f'{value-target:+,.2f} USD' if live and target is not None else '—'
    return f'BTC target: {target_text} • BRTI: {price} • Difference: {difference}'
