"""Real isolated demo worker + real Unix gateway, synthetic exchange only.

No Internet connections, user keys, production socket or real-money orders.
"""
import asyncio
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from decimal import Decimal as D
from urllib.parse import urlsplit
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from kalshi_bot.credentials import configure
from kalshi_bot.desktop_runtime import sandbox_command
from kalshi_bot.gateway import Server,TradingHandler

ROOT=Path(__file__).resolve().parent.parent


class Exchange:
    def __init__(self):
        self.lock=threading.RLock();self.orders=[];self.fills=[];self.settlements=[]
        self.cash=10000;self.position=D(0);self.buys=0;self.sells=0;self.connections=0
        self.version=0;self.epoch=0;self.stale=0;self.slow=True
        self.yes=D('.95');self.no=D('.04');self.closed=False
        self.ticker='KXBTC15M-DEMO-A';self.next='KXBTC15M-DEMO-B';self.close=time.time()+235
        self.entry_remaining=[]

    def market(self,ticker):
        stamp=lambda value:dt.datetime.fromtimestamp(value,dt.timezone.utc).isoformat()
        return {'ticker':ticker,'event_ticker':ticker,'market_type':'binary','exchange_index':0,
                'status':'closed' if self.closed and ticker==self.ticker else 'active',
                'open_time':stamp(self.close-900),'close_time':stamp(time.time()-1 if self.closed and ticker==self.ticker else self.close)}

    def fill(self,order,action,side,price):
        count=D(order['initial_count_fp'])
        if order['status']=='executed': return
        assert (self.position==0 if action=='buy' else abs(self.position)==count)
        sign=1 if side=='yes' else -1
        self.position+=sign*count*(1 if action=='buy' else -1)
        cost=int(count*price*100)
        self.cash+=(-cost-1) if action=='buy' else cost-1
        order.update(status='executed',fill_count_fp=str(count),remaining_count_fp='0')
        yes=price if side=='yes' else 1-price
        self.fills.append({'fill_id':'fill-'+order['order_id'],'order_id':order['order_id'],'count_fp':str(count),
                           'yes_price_dollars':str(yes),'no_price_dollars':str(1-yes),'fee_cost':'.01'})

    def forward(self,config,target,headers,method='GET',body=None):
        path=urlsplit(target).path.removeprefix('/trade-api/v2')
        if path=='/portfolio/positions' and self.slow:
            self.slow=False;time.sleep(2)  # Price publishing must continue here.
        with self.lock:
            if method=='POST':
                existing=next((o for o in self.orders if o['client_order_id']==body['client_order_id']),None)
                if existing:return 200,{},json.dumps({'order_id':existing['order_id'],'client_order_id':existing['client_order_id']}).encode()
                p=D(body['price'])
                if body['side']=='bid' and p>=D('.95'):action,side='buy','yes'
                elif body['side']=='ask' and p<=D('.05'):action,side='buy','no'
                elif body['side']=='ask' and p==D('.86'):action,side='sell','yes'
                elif body['side']=='bid' and p==D('.14'):action,side='sell','no'
                else:raise AssertionError('Invalid order')
                order={'order_id':'demo-'+str(len(self.orders)+1),'client_order_id':body['client_order_id'],
                       'ticker':body['ticker'],'type':'limit','status':'resting','initial_count_fp':body['count'],
                       'yes_price_dollars':body['price'],'fill_count_fp':'0','remaining_count_fp':body['count']}
                self.orders.append(order)
                if action=='buy':
                    assert self.position==0 and self.buys<2
                    assert body['expiration_time']==int(self.close)-30
                    self.entry_remaining.append(self.close-time.time())
                    self.buys+=1
                    self.fill(order,action,side,D('.95') if side=='yes' else 1-self.yes)
                else:
                    self.sells+=1
                    assert side=='yes' and self.position>0
                value={'order_id':order['order_id'],'client_order_id':order['client_order_id']}
            elif method=='DELETE':raise AssertionError('Unexpected cancellation')
            elif path=='/portfolio/orders':value={'orders':self.orders,'cursor':''}
            elif path=='/portfolio/fills':value={'fills':self.fills,'cursor':''}
            elif path=='/portfolio/positions':value={'market_positions':[{'ticker':self.ticker,'position_fp':str(self.position)}] if self.position else [],'cursor':''}
            elif path=='/portfolio/settlements':value={'settlements':self.settlements,'cursor':''}
            elif path=='/portfolio/balance':value={'balance':self.cash}
            elif path=='/exchange/status':value={'trading_active':True}
            elif path=='/series/KXBTC15M':value={'series':{'ticker':'KXBTC15M','fee_type':'quadratic','fee_multiplier':1}}
            elif path=='/events/fee_changes':value={'event_fee_changes':[],'cursor':''}
            elif path=='/markets':value={'markets':[self.market(self.next if self.closed else self.ticker)],'cursor':''}
            elif path.startswith('/markets/'):value={'market':self.market(path.split('/')[2])}
            else:raise AssertionError(path)
            return 200,{},json.dumps(value).encode()

    async def relay(self,config,ticker,headers,write,*,reference=False):
        if reference:
            # Reference traffic is not an executable book. Keep its lifecycle
            # realistic without inventing a benchmark Bitcoin reference price.
            while True:
                write({'type':'heartbeat','verified':True})
                await asyncio.sleep(.15)
        with self.lock:self.connections+=1;epoch=self.epoch
        seq=0;last=-1
        while True:
            with self.lock:
                if self.epoch!=epoch:return
                if last!=self.version:
                    seq+=1;last=self.version
                    yes,no=(D('.49'),D('.49')) if ticker==self.next else (self.yes,self.no)
                    stale=self.stale>0
                    if stale:self.stale-=1
                    frame={'type':'orderbook_snapshot','seq':seq,'sid':1,
                           'sending_ts_ms':int((time.time()-(20 if stale else 0))*1000),
                           'msg':{'market_ticker':ticker,'yes_dollars_fp':[[str(yes),'100']], 'no_dollars_fp':[[str(no),'100']]}}
                else:frame={'type':'heartbeat','verified':True}
            write(frame)
            await asyncio.sleep(.15)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-ask',choices=('.98','.984'),default='.98')
    args=parser.parse_args();ask=D(args.no_ask)
    exchange=Exchange();messages=queue.Queue();received=[]
    with tempfile.TemporaryDirectory(prefix='kalshi-demo-test-') as temporary:
        base=Path(temporary);gateway=base/'gateway';gateway.mkdir()
        state=base/'state';state.mkdir()
        source=base/'generated.pem'
        source.write_bytes(ed25519.Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        source.chmod(0o600);credentials=base/'demo'
        configure(credentials,'demo','generated-test-key',source)
        server=Server(str(gateway/'gateway.sock'),TradingHandler)
        server.config={'environment':'demo','allow_trading':True,'client_uid':os.getuid(),'ws_host':'external-api-ws.demo.kalshi.co'}
        with patch('kalshi_bot.gateway.forward',side_effect=exchange.forward),patch('kalshi_bot.streams.relay',side_effect=exchange.relay):
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            command=sandbox_command(ROOT,state,'demo-trading',credentials)
            mount=command.index('/gateway')
            if command[mount-1]=='--dir':command[mount-1:mount+1]=['--ro-bind',str(gateway),'/gateway']
            else:command[mount-1]=str(gateway)
            process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
            def read():
                for line in process.stdout:
                    try:messages.put(json.loads(line))
                    except ValueError:pass
            reader=threading.Thread(target=read,daemon=True);reader.start()
            def receive(predicate,timeout=35):
                deadline=time.monotonic()+timeout
                while time.monotonic()<deadline:
                    try:value=messages.get(timeout=max(.01,deadline-time.monotonic()))
                    except queue.Empty:break
                    received.append(value)
                    if value.get('type')=='error':raise AssertionError(value['message'])
                    if predicate(value):return value
                raise AssertionError('Timed out: '+str(process.poll()))
            send=lambda value:(process.stdin.write(json.dumps({'command':value})+'\n'),process.stdin.flush())
            try:
                receive(lambda m:m.get('type')=='ready');send('start')
                held=receive(lambda m:m.get('type')=='state' and bool(m['state']['position']))['state']
                assert held['position']['side']=='yes' and held['buys']==1
                assert 180<exchange.entry_remaining[0]<=240
                assert len([m for m in received if m.get('type')=='market' and m.get('yes_ask_cents')])>=3
                with exchange.lock:exchange.yes=D('.85');exchange.version+=1
                receive(lambda m:m.get('type')=='state' and m['state']['stop'] and bool(m['state']['active_orders']))
                with exchange.lock:assert exchange.sells==1
                # Rebound closes the first position; switch qualifying side to NO.
                with exchange.lock:
                    exchange.fill(exchange.orders[-1],'sell','yes',D('.88'))
                    exchange.yes=1-ask;exchange.no=ask-D('.01');exchange.version+=1
                held=receive(lambda m:m.get('type')=='state' and (m['state']['position'] or {}).get('side')=='no',timeout=40)['state']
                assert held['buys']==2 and held['selected_side']=='no'
                limit=ask.quantize(D('.01'),rounding='ROUND_CEILING')
                with exchange.lock:assert exchange.orders[-1]['yes_price_dollars']==format(1-limit,'.4f')
                with exchange.lock:exchange.epoch+=1;before=exchange.connections
                receive(lambda m:m.get('type')=='market' and m.get('market_health')=='reconnecting')
                receive(lambda m:m.get('type')=='market' and m.get('market_health')=='connected')
                with exchange.lock:
                    assert exchange.connections>before
                    exchange.stale=2;exchange.version+=1
                receive(lambda m:m.get('type')=='market' and m.get('market_health')=='reconnecting')
                receive(lambda m:m.get('type')=='market' and m.get('market_health')=='connected')
                # Hold second position to settlement, then discover the next market.
                with exchange.lock:
                    exchange.cash+=int(abs(exchange.position)*100);exchange.position=D(0);exchange.closed=True
                    exchange.settlements=[{'ticker':exchange.ticker,'market_result':'no','revenue':300}]
                    exchange.version+=1
                receive(lambda m:m.get('type')=='market' and m.get('market')==exchange.next,timeout=20)
                receive(lambda m:m.get('type')=='state' and m['state']['market']==exchange.next and not m['state']['position'],timeout=30)
                with exchange.lock:assert exchange.buys==2 and exchange.sells==1
                send('pause');send('quit');process.wait(timeout=15)
                assert process.returncode==0
                print(json.dumps({'result':'passed','mode':'demo with mocked exchange; real isolated worker and Unix gateway',
                    'checks':['YES buy','cheaper fills','stream updates during slow account reads','latched 86c sell','complete sale before NO buy',
                              'two sequential buys in this scripted scenario','no opposing holdings','network reconnect','stale timestamp rejection','settlement','automatic market rollover'],
                    'buys':exchange.buys,'entry_window_seconds':240,'first_buy_seconds_remaining':round(exchange.entry_remaining[0],2),
                    'no_ask_cents':float(ask*100),'no_limit_cents':int(limit*100),
                    'protective_sells':exchange.sells,'market_events':sum(m.get('type')=='market' for m in received)},indent=2))
            finally:
                if process.poll() is None:process.terminate();process.wait(timeout=10)
                for stream in (process.stdin,process.stdout,process.stderr):stream.close()
                server.shutdown();server.server_close();thread.join(timeout=2)


if __name__=='__main__':main()
