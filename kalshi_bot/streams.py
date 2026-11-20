"""Sequenced order-book streaming through the existing isolated Unix gateway."""
import asyncio
import base64
import json
import logging
import socket
import sqlite3
import ssl
import threading
import time
from decimal import Decimal as D
from .strategy import policy
from .api import MAX_BODY,TICKER,SchemaError,UnixHTTP,decimal

WS_PATH='/trade-api/ws/v2'
STREAM_PATH='/internal/orderbook/'
REFERENCE_PATH='/internal/reference/BRTI'
WS_HOSTS={'demo':'external-api-ws.demo.kalshi.co','production':'external-api-ws.kalshi.com'}
STREAM_PROTOCOL=2
# Socket recovery must not tear down a still-fresh book at four seconds.
# Quotes independently expire after five seconds; allow one further second to
# receive a verified heartbeat before rebuilding the entire TLS subscription.
STREAM_IDLE_TIMEOUT=6.0

_FAILURE_LOCK=threading.Lock()
_FAILURE_NEXT={}


def safe_stream_failure(phase,exc):
    """Bounded metadata only: never log signed headers or exception bodies."""
    status=getattr(exc,'status_code',None)
    if type(status) is not int or not 100<=status<=599:status=None
    now=time.monotonic()
    with _FAILURE_LOCK:
        if now<_FAILURE_NEXT.get(phase,0):return
        _FAILURE_NEXT[phase]=now+30
    logging.getLogger('kalshi.connection').warning(
        'Stream failure: phase=%s error=%s http_status=%s',phase,type(exc).__name__,status)


def ws_headers(signer):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ed25519,padding
    stamp=str(int(time.time()*1000))
    message=(stamp+'GET'+WS_PATH).encode()
    signature=signer._key.sign(message) if isinstance(signer._key,ed25519.Ed25519PrivateKey) else signer._key.sign(message,padding.PSS(mgf=padding.MGF1(hashes.SHA256()),salt_length=32),hashes.SHA256())
    return {'KALSHI-ACCESS-KEY':signer.key_id,'KALSHI-ACCESS-TIMESTAMP':stamp,'KALSHI-ACCESS-SIGNATURE':base64.b64encode(signature).decode()}


async def relay(config,ticker,headers,write,*,reference=False):
    """Gateway side: fixed official WS host and pinned IPs, no client commands."""
    import websockets
    host=WS_HOSTS[config['environment']]
    if config.get('ws_host')!=host or (reference and ticker!='BRTI') or (not reference and not TICKER.fullmatch(ticker)):
        raise ValueError('Streaming gateway is not provisioned')
    logger=logging.getLogger('kalshi.websocket')
    logger.disabled=True  # Never dump signed handshake headers via debug logging.
    for address in config.get('ws_addresses',[])[:4]:
        sock=None
        phase='connect'
        try:
            sock=socket.create_connection((address,443),timeout=5)
            sock.setblocking(False)
            phase='websocket-handshake'
            async with websockets.connect('wss://'+host+WS_PATH,sock=sock,ssl=ssl.create_default_context(),
                                          server_hostname=host,extra_headers=headers,compression=None,
                                          open_timeout=8,ping_interval=5,ping_timeout=5,close_timeout=2,
                                          max_size=MAX_BODY,max_queue=32,logger=logger) as ws:
                params=({'channels':['cfbenchmarks_value'],'index_ids':['BRTI']} if reference else
                        {'channels':['orderbook_delta'],'market_ticker':ticker})
                phase='subscription'
                await ws.send(json.dumps({'id':1,'cmd':'subscribe','params':params}))
                verified_at=time.monotonic()
                while True:
                    try:
                        phase='receive'
                        raw=await asyncio.wait_for(ws.recv(),timeout=1)
                    except asyncio.TimeoutError:
                        # Only recv inactivity enters this path. A failed Pong
                        # or local write must not initiate a second verification.
                        phase='peer-verification';pong=await ws.ping()
                        await asyncio.wait_for(pong,timeout=2)
                        phase='local-write';write({'type':'heartbeat','verified':True})
                        verified_at=time.monotonic()
                    else:
                        body=json.loads(raw)
                        if not isinstance(body,dict): raise SchemaError('Invalid stream message')
                        phase='local-write';write(body)
                        if body.get('type') in ('orderbook_snapshot','orderbook_delta'):
                            verified_at=time.monotonic()
                        elif time.monotonic()-verified_at>=1:
                            # Repeated subscription/control traffic must not
                            # postpone peer verification indefinitely.
                            phase='peer-verification';pong=await ws.ping()
                            await asyncio.wait_for(pong,timeout=2)
                            phase='local-write'
                            write({'type':'heartbeat','verified':True})
                            verified_at=time.monotonic()
            return
        except (OSError,asyncio.TimeoutError,websockets.exceptions.WebSocketException) as exc:
            if not isinstance(exc,(BrokenPipeError,ConnectionResetError)):
                safe_stream_failure(phase,exc)
            continue
        finally:
            if sock:sock.close()
    raise OSError('Official stream unavailable')


class Book:
    def __init__(self,ticker):
        self.ticker=ticker
        self.valid=False
        self.seq=None
        self.sid=None
        self.levels={'yes':{},'no':{}}

    def consume(self,message):
        if not isinstance(message,dict):
            self.valid=False; raise SchemaError('Invalid stream envelope')
        kind=message.get('type')
        if kind in ('heartbeat','subscribed'): return False
        if kind not in ('orderbook_snapshot','orderbook_delta'):
            if kind=='error': self.valid=False; raise SchemaError('Exchange stream error')
            return False
        # Any malformed update must invalidate the previous book too.
        was_valid=self.valid
        self.valid=False
        body=message.get('msg')
        if not isinstance(body,dict):raise SchemaError('Invalid stream message body')
        if body.get('market_ticker')!=self.ticker:
            self.valid=False; raise SchemaError('Unexpected stream market')
        seq,sid=message.get('seq'),message.get('sid')
        if type(seq) is not int or type(sid) is not int:
            self.valid=False; raise SchemaError('Invalid stream sequence')
        if kind=='orderbook_snapshot':
            if was_valid and sid==self.sid and seq<=self.seq:
                raise SchemaError('Replayed order-book snapshot')
            levels={'yes':{},'no':{}}
            empty=not any(side+'_dollars_fp' in body for side in levels)
            if empty and any(k in body for k in ('yes','no','bids','asks')):raise SchemaError('Unsupported order-book price format')
            for side in levels:
                key=side+'_dollars_fp'
                if key not in body and not empty:raise SchemaError('Incomplete order-book snapshot')
                rows=body.get(key,[]) or []
                if not isinstance(rows,list):raise SchemaError('Invalid snapshot levels')
                for level in rows:
                    if not isinstance(level,(tuple,list)) or len(level)!=2:raise SchemaError('Invalid snapshot level')
                    price,quantity=level
                    p,n=decimal(price,'stream price'),decimal(quantity,'stream quantity')
                    if not 0<=p<=1 or n<0: raise SchemaError('Invalid snapshot level')
                    if n: levels[side][p]=n
            self.levels=levels
            self.seq,self.sid,self.valid=seq,sid,True
        else:
            if not was_valid or sid!=self.sid or seq!=self.seq+1:
                self.valid=False; raise SchemaError('Order-book sequence gap; resnapshot required')
            side=body.get('side')
            p,delta=decimal(body.get('price_dollars'),'stream price'),decimal(body.get('delta_fp'),'stream delta')
            if side not in self.levels or not 0<=p<=1: raise SchemaError('Invalid delta')
            n=self.levels[side].get(p,D(0))+delta
            if n<0: self.valid=False; raise SchemaError('Negative order-book level')
            if n: self.levels[side][p]=n
            else: self.levels[side].pop(p,None)
            self.seq=seq
            self.valid=True
        return True

    def prices(self):
        if not self.valid: return None
        yes=max(self.levels['yes'],default=None)
        no=max(self.levels['no'],default=None)
        return {'yes_bid':yes,'no_bid':no,'yes_ask':1-no if no is not None else None,'no_ask':1-yes if yes is not None else None}


def persist_crossing(path,ticker,side,bid,db=None,observed_at=None,threshold=None):
    """A separate SQLite connection preserves observed stops during REST requests."""
    stop=D(policy().sell_cents)/100 if threshold is None else D(threshold)
    if bid is None or bid>stop: return
    owned=db is None
    if owned: db=sqlite3.connect(path,timeout=10)
    try:
        row=db.execute('SELECT stop FROM markets WHERE ticker=? AND settled=0',(ticker,)).fetchone()
        if row and row[0]: return  # Already durable; never rewrite a latched stop.
        with db:
            if observed_at is not None:
                latest=db.execute("SELECT max(intent_at) FROM orders WHERE ticker=? AND side=? AND action='buy'",(ticker,side)).fetchone()[0]
                if latest and latest>observed_at:return
            # Include a pending buy: a fill may be visible on the exchange before
            # its portfolio projection arrives. Conservatively protect that risk.
            rows=db.execute('SELECT action,filled,status FROM orders WHERE ticker=? AND side=?',(ticker,side)).fetchall()
            quantity=sum((D(r[1])*(1 if r[0]=='buy' else -1) for r in rows),D(0))
            pending=any(r[0]=='buy' and r[2] in ('pending','resting') for r in rows)
            if quantity>0 or pending:
                db.execute("UPDATE markets SET stop=1 WHERE ticker=? AND settled=0 AND (? IS NULL OR (SELECT coalesce(max(intent_at),0) FROM orders WHERE ticker=? AND side=? AND action='buy')<=?)",
                           (ticker,observed_at,ticker,side,observed_at))
    finally:
        if owned: db.close()


class BookFeed:
    def __init__(self,signer,socket_path,journal_path):
        self.signer,self.socket_path,self.journal_path=signer,socket_path,journal_path
        self.lock=threading.Lock()
        self.ticker=None
        self.book=None
        self.connection=None
        self.heartbeat=0
        self.closed=False
        self.generation=0
        self.persistence_ok=True
        self.updated_at=None
        self.error=None
        self.connected=False
        self.threads=[]
        self.risk_sides=None  # Standalone CLI retains conservative all-side tracking.
        self.risk_epoch=0
        self.on_update=None
        self.stopping=threading.Event()

    def _notify(self):
        if self.on_update:self.on_update()

    def arm(self,side):
        crossing=None
        with self.lock:
            if self.risk_sides is not None:
                fresh=side not in self.risk_sides
                self.risk_sides.add(side)
                if fresh:self.risk_epoch+=1
                prices=self.book.prices() if self.book else None
                live=not self.closed and self.connected and time.monotonic()-self.heartbeat<=5
                if live and prices and prices[side+'_bid'] is not None and prices[side+'_bid']<=D(policy().sell_cents)/100 and self.journal_path is not None:
                    if fresh:self.persistence_ok=False
                    crossing=(self.ticker,prices[side+'_bid'],time.time())
        if crossing:
            # arm() follows the committed buy intent and precedes its HTTP write.
            # Persist the currently executable crossing now: a rebound may be the
            # very next stream message. Failure blocks that new buy before send.
            db=None
            try:
                db=sqlite3.connect(self.journal_path,timeout=.05)
                persist_crossing(self.journal_path,crossing[0],side,crossing[1],db=db,observed_at=crossing[2])
            except sqlite3.Error:
                with self.lock:self.persistence_ok=False
                raise
            finally:
                if db is not None:db.close()
            self._notify()

    def clear_risk(self):
        with self.lock:
            self.risk_sides=set()
            self.persistence_ok=True
            self.risk_epoch+=1

    def watch(self,ticker):
        if not TICKER.fullmatch(ticker): raise ValueError('Invalid stream ticker')
        with self.lock:
            if ticker==self.ticker: return
            self.generation+=1
            generation=self.generation
            previous=self.connection
            self.ticker=ticker; self.book=Book(ticker); self.heartbeat=0
            self.updated_at=None; self.error=None; self.connected=False; self.persistence_ok=True
        self._disconnect(previous)
        thread=threading.Thread(target=self._run,args=(ticker,generation),daemon=True)
        self.threads=[t for t in self.threads if t.is_alive()]+[thread]
        thread.start()

    @staticmethod
    def _disconnect(connection):
        if not connection: return
        try:
            sock=getattr(connection,'stream_socket',None) or connection.sock
            if sock:sock.shutdown(socket.SHUT_RDWR)
        except (OSError,AttributeError): pass
        connection.close()

    def health(self):
        with self.lock:
            fresh=not self.closed and self.connected and self.book and self.book.valid and time.monotonic()-self.heartbeat<=5
            status='connected' if fresh and self.persistence_ok else ('saving stop' if fresh else 'reconnecting')
            return {'status':status,'last_update':self.updated_at,'error':self.error,
                    'verified_age':round(max(0,time.monotonic()-self.heartbeat),3) if self.heartbeat else None}

    def frame(self,ticker):
        """Prices and sequence metadata sampled under the same lock."""
        with self.lock:
            fresh=not self.closed and ticker==self.ticker and self.book and self.book.valid and time.monotonic()-self.heartbeat<=5
            valid=bool(fresh and self.persistence_ok)
            return {'prices':self.book.prices() if valid else None,
                    'status':'connected' if valid else 'saving stop' if fresh else 'reconnecting',
                    'last_update':self.updated_at,'error':self.error,
                    'sequence':self.book.seq if self.book else None,'subscription':self.book.sid if self.book else None,
                    'verified_age':round(max(0,time.monotonic()-self.heartbeat),3) if self.heartbeat else None}

    def quote(self,ticker):
        with self.lock:
            if self.closed or ticker!=self.ticker or not self.persistence_ok or time.monotonic()-self.heartbeat>5: return None
            return self.book.prices() if self.book else None

    def _run(self,ticker,generation):
        pending={}
        pending_epoch=None
        retry_delay=.25
        while not self.closed and generation==self.generation:
            connection=UnixHTTP(self.socket_path)
            response=None;stop_db=None
            try:
                connection.request('GET',STREAM_PATH+ticker,headers=ws_headers(self.signer))
                stream_socket=getattr(connection,'sock',None)
                connection.stream_socket=stream_socket
                response=connection.getresponse()
                if response.status!=200: raise OSError('Stream gateway rejected request')
                with self.lock:
                    if generation!=self.generation: return
                    self.connection=connection
                    self.book=Book(ticker)
                    self.connected=True
                while not self.closed and generation==self.generation:
                    raw=response.readline(MAX_BODY+1)
                    if not raw or len(raw)>MAX_BODY: raise OSError('Stream closed or oversized')
                    message=json.loads(raw)
                    had_pending=bool(pending)
                    sent=message.get('sending_ts_ms') if message.get('type') in ('orderbook_snapshot','orderbook_delta') else None
                    if sent is not None and (type(sent) not in (int,float) or not -5<=time.time()-sent/1000<=5):
                        raise SchemaError('Delayed stream; resnapshot required')
                    with self.lock:
                        # A blocked read from the old contract may finish after
                        # watch() switches markets. It must not touch the new book.
                        if self.closed or generation!=self.generation: return
                        changed=self.book.consume(message)
                        prices=self.book.prices()
                        if changed or (message.get('type')=='heartbeat' and message.get('verified') is True and prices):
                            self.heartbeat=time.monotonic()
                            self.error=None
                        if changed:
                            self.updated_at=time.time()
                            retry_delay=.25
                            if stream_socket is not None:stream_socket.settimeout(STREAM_IDLE_TIMEOUT)
                        if self.book.valid and self.heartbeat and time.monotonic()-self.heartbeat>5:
                            raise SchemaError('Stream verification expired; resnapshot required')
                        risk=tuple(self.risk_sides) if self.risk_sides is not None else ('yes','no')
                        risk_epoch=self.risk_epoch
                        if prices and risk and self.journal_path is not None:self.persistence_ok=False
                    if prices and risk and self.journal_path is not None:
                        if pending_epoch!=risk_epoch:pending.clear();pending_epoch=risk_epoch
                        # Retry the latest coherent crossing on heartbeats too if
                        # another writer briefly held SQLite's transaction lock.
                        for side in risk:
                            bid=prices[side+'_bid']
                            if bid is not None and bid<=D(policy().sell_cents)/100:
                                if side not in pending:pending[side]=(bid,time.time())
                                elif bid<pending[side][0]:pending[side]=(bid,pending[side][1])
                        persistence_ok=True
                        for side,(bid,observed_at) in list(pending.items()):
                            try:
                                if stop_db is None:
                                    stop_db=sqlite3.connect(self.journal_path,timeout=.05)
                                persist_crossing(self.journal_path,ticker,side,bid,db=stop_db,observed_at=observed_at)
                                del pending[side]
                            except sqlite3.OperationalError:
                                persistence_ok=False
                        with self.lock:
                            if generation==self.generation and risk_epoch==self.risk_epoch:self.persistence_ok=persistence_ok
                    # Publish only after any stop crossing has been made durable.
                    # Subscribers receive a bounded wake-up, never do work here.
                    if changed or had_pending or pending:self._notify()
            except Exception as exc:
                with self.lock:
                    if generation==self.generation:
                        if self.book: self.book.valid=False
                        self.connected=False
                        self.error=str(exc)[:200] if isinstance(exc,SchemaError) else type(exc).__name__
                        # SchemaError messages are local constants; never retain API bodies/headers.
                self._notify()
                self._disconnect(connection)
                if response is not None:response.close()
                if not self.closed and generation==self.generation:
                    self.stopping.wait(retry_delay)
                    retry_delay=min(4.0,retry_delay*2)
            finally:
                if stop_db is not None: stop_db.close()
                if response is not None:response.close()
                connection.close()

    def close(self):
        with self.lock:
            self.closed=True
            self.generation+=1
            self.connected=False
            connection=self.connection
        self.stopping.set()
        self._notify()
        self._disconnect(connection)
        for thread in self.threads:
            if thread is not threading.current_thread(): thread.join(timeout=.2)


class BookHub:
    """One canonical sequenced feed per market shared by display and execution."""
    def __init__(self,signer,socket_path,journal_path,*,factory=None):
        self.signer,self.socket_path,self.journal_path=signer,socket_path,journal_path
        self.factory=factory or BookFeed
        self.lock=threading.Lock();self.feeds={};self.closed=False
        self.listeners=set()

    def subscribe(self,wakeup):
        with self.lock:self.listeners.add(wakeup)

    def unsubscribe(self,wakeup):
        with self.lock:self.listeners.discard(wakeup)

    def _notify(self):
        with self.lock:listeners=tuple(self.listeners)
        for wakeup in listeners:wakeup.notify()

    def watch(self,ticker):
        if not TICKER.fullmatch(ticker): raise ValueError('Invalid stream ticker')
        with self.lock:
            if self.closed:return
            if ticker not in self.feeds:
                feed=self.factory(self.signer,self.socket_path,self.journal_path)
                feed.risk_sides=set()
                feed.on_update=self._notify
                self.feeds[ticker]=feed
                feed.watch(ticker)

    def frame(self,ticker):
        with self.lock:feed=self.feeds.get(ticker)
        return feed.frame(ticker) if feed else {'prices':None,'status':'waiting for snapshot','last_update':None,
                                             'error':None,'sequence':None,'subscription':None,'verified_age':None}

    def quote(self,ticker):return self.frame(ticker)['prices']
    def health(self,ticker):return self.frame(ticker)

    def arm(self,ticker,side):
        self.watch(ticker)
        with self.lock:feed=self.feeds.get(ticker)
        if feed is None:raise RuntimeError('Price stream unavailable before submission')
        feed.arm(side)

    def clear_flat_risk(self):
        with self.lock:feeds=list(self.feeds.values())
        for feed in feeds:feed.clear_risk()

    def retain(self,*tickers):
        needed=set(t for t in tickers if t)
        with self.lock:
            old=[self.feeds.pop(t) for t in list(self.feeds) if t not in needed]
        for feed in old:feed.close()

    def close(self):
        with self.lock:
            self.closed=True;old=list(self.feeds.values());self.feeds.clear()
        for feed in old:feed.close()
