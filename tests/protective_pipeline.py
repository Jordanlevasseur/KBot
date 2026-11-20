"""Protective exits through the real isolated demo worker, Unix gateway and hidden UI.

Generated credentials, synthetic exchange only. Never uses the production socket.
"""
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from decimal import Decimal as D
from urllib.parse import urlsplit
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from kalshi_bot.api import APIError
from kalshi_bot.credentials import configure
from kalshi_bot.desktop import Application
from kalshi_bot.desktop_runtime import sandbox_command
from kalshi_bot.gateway import Server,TradingHandler
from test_protective_sells import ProtectiveVenue,TICKER
ROOT=Path(__file__).resolve().parent.parent


class Exchange(ProtectiveVenue):
    def __init__(self,side):
        super().__init__();self.side=side;self.now=time.time();self.close=int(self.now)+230
        self.lock=threading.RLock();self.bid=D('.97');self.version=0;self.epoch=0;self.connections=0
        self.trip_pending=False;self.trip_rebounded=False;self.hidden_reads=0;self.sell_failed=False
    def forward(self,config,target,headers,method='GET',body=None):
        path=urlsplit(target).path.removeprefix('/trade-api/v2')
        with self.lock:
            self.now=time.time()
            if method=='POST':
                # Initial sell is accepted by the fake exchange, but its response
                # is lost. One order snapshot lags, requiring read-only reconciliation.
                protective=D(body['price']) in (D('.86'),D('.14'))
                if protective and not self.sell_failed:self.next_error='timeout_after';self.sell_failed=True
                try:
                    value=self.create(body)
                    order=next(o for o in self.orders if o['order_id']==value['order_id'])
                    if self.actions[order['order_id']][0]=='buy' and not D(order['fill_count_fp']):self.fill(order,order['initial_count_fp'])
                except APIError:
                    self.hidden_reads=1
                    return 503,{},b'{}'
            elif method=='DELETE':raise AssertionError('Protective sell must not be canceled on shutdown')
            elif path=='/portfolio/orders':
                orders=copy.deepcopy(self.orders)
                if self.hidden_reads:
                    orders=[o for o in orders if self.actions[o['order_id']][0]!='sell'];self.hidden_reads-=1
                value={'orders':orders,'cursor':''}
            elif path=='/portfolio/fills':value={'fills':self.pages(path),'cursor':''}
            elif path=='/portfolio/positions':value={'market_positions':self.pages(path),'cursor':''}
            elif path=='/portfolio/settlements':value={'settlements':[],'cursor':''}
            elif path=='/events/fee_changes':value={'event_fee_changes':[],'cursor':''}
            elif path=='/markets':value={'markets':[self.market()],'cursor':''}
            else:value=self.get(path)
            return 200,{},json.dumps(value).encode()
    async def relay(self,config,ticker,headers,write):
        with self.lock:self.connections+=1;epoch=self.epoch
        seq=0;last=-1
        while True:
            with self.lock:
                if epoch!=self.epoch:return
                if self.trip_pending:
                    self.bid=D('.85');self.version+=1;self.trip_pending=False;self.trip_rebounded=True
                elif self.trip_rebounded:
                    self.bid=D('.92');self.version+=1;self.trip_rebounded=False
                if self.version!=last:
                    seq+=1;last=self.version
                    other='no' if self.side=='yes' else 'yes'
                    bids={self.side:self.bid,other:1-min(D('.99'),self.bid+D('.01'))}
                    frame={'type':'orderbook_snapshot','sid':1,'seq':seq,'sending_ts_ms':int(time.time()*1000),
                           'msg':{'market_ticker':ticker,**{s+'_dollars_fp':[[str(p),'100']] for s,p in bids.items()}}}
                else:frame={'type':'heartbeat','verified':True}
                fast=self.trip_rebounded
            write(frame)
            await asyncio.sleep(.005 if fast else .1)


def run(side,base,root,app):
    directory=base/side;directory.mkdir();state=directory/'state';state.mkdir();gateway=directory/'gateway';gateway.mkdir()
    source=directory/'generated.pem';source.write_bytes(ed25519.Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));source.chmod(0o600)
    credentials=directory/'demo';configure(credentials,'demo','synthetic-protective-key',source)
    exchange=Exchange(side);events=queue.Queue();seen=[];process=None;readers=[];server=None;server_thread=None
    config={'environment':'demo','allow_trading':True,'client_uid':os.getuid(),'ws_host':'external-api-ws.demo.kalshi.co'}
    def start_gateway():
        nonlocal server,server_thread
        server=Server(str(gateway/'gateway.sock'),TradingHandler);server.config=config
        server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
    def stop_gateway():
        nonlocal server
        if server:
            with exchange.lock:exchange.epoch+=1
            server.shutdown();server.server_close();server_thread.join(timeout=2);server=None
            (gateway/'gateway.sock').unlink(missing_ok=True)
    def launch():
        nonlocal process
        command=sandbox_command(ROOT,state,'demo-trading',credentials);mount=command.index('/gateway')
        if command[mount-1]=='--dir':command[mount-1:mount+1]=['--ro-bind',str(gateway),'/gateway']
        else:command[mount-1]=str(gateway)
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
        def read(proc):
            for line in proc.stdout:events.put(json.loads(line))
        thread=threading.Thread(target=read,args=(process,),daemon=True);thread.start();readers.append(thread)
    def send(command):process.stdin.write(json.dumps({'command':command})+'\n');process.stdin.flush()
    def receive(predicate,timeout=65):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            root.update()
            try:event=events.get(timeout=.01)
            except queue.Empty:
                if process.poll() is not None:raise AssertionError('Synthetic worker exited: '+process.stderr.read()[-1000:])
                continue
            seen.append(event)
            app.events.put((app.worker.generation,event))
            if predicate(event):return event
        raise AssertionError('Timed out waiting for synthetic protective state')
    def stop_worker():
        nonlocal process
        if process and process.poll() is None:
            send('quit')
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
        if process:
            assert process.returncode==0
            for stream in (process.stdin,process.stdout,process.stderr):stream.close()
            process=None
    with patch('kalshi_bot.gateway.forward',side_effect=exchange.forward),patch('kalshi_bot.streams.relay',side_effect=exchange.relay):
        try:
            start_gateway();launch();receive(lambda e:e['type']=='ready');send('start')
            held=receive(lambda e:e['type']=='state' and bool(e['state']['position']))['state']
            assert held['position']['side']==side
            count=D(held['position']['qty']);send('pause')
            receive(lambda e:e['type']=='state' and not e['state']['running'])
            with exchange.lock:exchange.trip_pending=True
            uncertain=receive(lambda e:e['type']=='state' and e['state'].get('protective_exit',{}).get('state')=='submission_uncertain')['state']
            assert uncertain['position'] and not uncertain['protective_exit']['completed']
            receive(lambda e:e['type']=='state' and e['state'].get('protective_exit',{}).get('state')=='accepted')
            with exchange.lock:
                assert len(exchange.sells())==1
                sell=exchange.sells()[0];assert D(sell['initial_count_fp'])==count
                assert D(sell['yes_price_dollars'])==(D('.86') if side=='yes' else D('.14'))
                exchange.fill(sell,'3.25')
            partial=receive(lambda e:e['type']=='state' and e['state'].get('protective_exit',{}).get('state')=='partially_filled')['state']
            assert D(partial['position']['qty'])==count-D('3.25')
            with exchange.lock:before_connections=exchange.connections
            stop_gateway();start_gateway()
            receive(lambda e:e['type']=='market' and e.get('market_health')=='connected' and exchange.connections>before_connections)
            receive(lambda e:e['type']=='state' and e['state'].get('protective_exit',{}).get('state')=='partially_filled')
            with exchange.lock:assert exchange.connections>before_connections
            stop_worker();launch();receive(lambda e:e['type']=='ready')
            restored=receive(lambda e:e['type']=='state' and e['state'].get('protective_exit',{}).get('state')=='partially_filled')['state']
            assert restored['stop'] and not restored['running'] and D(restored['position']['qty'])==count-D('3.25')
            with exchange.lock:exchange.bid=D('.80');exchange.version+=1
            receive(lambda e:e['type']=='market' and D(e.get(side+'_bid_cents') or 0)==80)
            for _ in range(2):
                unfilled=receive(lambda e:e['type']=='state' and e['state'].get('protective_exit',{}).get('state')=='partially_filled')['state']
                assert not unfilled['protective_exit']['completed']
            with exchange.lock:
                assert exchange.sold==D('3.25') and len(exchange.sells())==1
                exchange.bid=D('.92');exchange.version+=1
                exchange.fill(exchange.sells()[0],exchange.position,'.92')
            final=receive(lambda e:e['type']=='state' and e['state'].get('protective_exit',{}).get('completed'))['state']
            assert not final['position'] and not final['active_orders']
            deadline=time.monotonic()+.15
            while time.monotonic()<deadline:root.update();time.sleep(.005)
            assert 'Exit completed' in app.variables['stop'].get()
            with exchange.lock:
                assert exchange.position==0 and exchange.sold==exchange.bought and len(exchange.sells())==1
                assert len({p['client_order_id'] for p in exchange.attempts})==2
                assert len(exchange.attempts)==2 # Initial buy + initial sell; no recovery POST.
            send('pause');stop_worker()
            result={'side':side,'result':'passed','buy_contracts':str(count),'sold_contracts':str(exchange.sold),
                    'distinct_protective_orders':len(exchange.sells()),'stop_latched_through_rebound':True,
                    'partial_fill_verified':True,'unfilled_below_limit_verified':True,'read_only_uncertain_recovery_verified':True,'recovery_reposts':0,
                    'gateway_restart_verified':True,'worker_restart_paused_verified':True,'hidden_dashboard_verified':True,
                    'exchange_fill_and_position_confirmation_required':True,'stream_connections':exchange.connections,
                    'real_money_orders':0,'user_credentials_used':False}
            print(json.dumps(result),flush=True);return result
        finally:
            if process and process.poll() is None:
                process.terminate();process.wait(timeout=10)
                for stream in (process.stdin,process.stdout,process.stderr):stream.close()
            stop_gateway()
            for thread in readers:thread.join(timeout=2)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='kalshi-protective-demo-') as temporary:
        base=Path(temporary);os.environ['XDG_STATE_HOME']=str(base/'ui-state');os.environ['XDG_CONFIG_HOME']=str(base/'ui-config')
        root=tk.Tk();root.withdraw();app=Application(root,autostart=False)
        try:results=[run(side,base,root,app) for side in ('yes','no')]
        finally:root.destroy()
    args.output.write_text(json.dumps({'mode':'real isolated demo worker/local gateway; synthetic exchange','results':results},indent=2)+'\n')


if __name__=='__main__':main()
