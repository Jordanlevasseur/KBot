"""Repeatable warm-entry receipt-to-wire benchmark: isolated worker/mock gateway.

Generated demo keys, fake cash and disposable journals only. No production access.
"""
import argparse
import json
import os
from pathlib import Path
import queue
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from decimal import Decimal as D
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from demo_pipeline import Exchange
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519,rsa
from kalshi_bot.credentials import configure
from kalshi_bot.desktop_runtime import sandbox_command
from kalshi_bot.gateway import Server,TradingHandler

ROOT=Path(__file__).resolve().parent.parent
BOOTSTRAP="""
import threading,time
from pathlib import Path
from kalshi_bot import desktop_worker as worker,prepared,api,execution,streams
local=threading.local();execute=prepared.PreparedEngine.execute
opportunity=threading.Event();sent=threading.Event()
forbidden={'balance_or_other_get':0,'sizing':0,'reconciliation':0}
def critical(self,*args,**kwargs):
    local.executing=True
    try:return execute(self,*args,**kwargs)
    finally:local.executing=False
prepared.PreparedEngine.execute=critical
def guard(kind,fn):
    def check(*args,**kwargs):
        if getattr(local,'executing',False) or (threading.current_thread() is threading.main_thread() and opportunity.is_set() and not sent.is_set()):
            forbidden[kind]+=1;raise AssertionError('Forbidden critical-path operation: '+kind)
        return fn(*args,**kwargs)
    return check
api.Client.get=guard('balance_or_other_get',api.Client.get)
prepared.affordable=guard('sizing',prepared.affordable)
execution.Journal.reconcile=guard('reconciliation',execution.Journal.reconcile)
consume=streams.Book.consume
def receipt(self,message):
    at=time.monotonic();changed=consume(self,message);prices=self.prices()
    if changed and prices and any(prices[s+'_ask'] is not None and prepared.D('.95')<=prices[s+'_ask']<=prepared.D('.99') for s in ('yes','no')):
        opportunity.set()
        worker.emit('measurement',event='receipt',mono=at)
    return changed
streams.Book.consume=receipt
request=api.UnixHTTP.request
def wire(self,method,*args,**kwargs):
    if method=='POST':
        sent.set()
        status=Path('/proc/self/status').read_text().splitlines()
        rss=int(next(v for v in status if v.startswith('VmRSS:')).split()[1])/1024
        worker.emit('measurement',event='wire',mono=time.monotonic(),forbidden=forbidden,rss_mib=rss,cpu_seconds=time.process_time())
    return request(self,method,*args,**kwargs)
api.UnixHTTP.request=wire
from kalshi_bot import prepared_runtime
run=prepared_runtime.run
def debug_run(*args,**kwargs):
    try:return run(*args,**kwargs)
    except Exception:
        import traceback
        traceback.print_exc()
        raise
prepared_runtime.run=debug_run
worker.main()
"""


def percentile(values,q):
    values=sorted(values);i=(len(values)-1)*q;lo=int(i);hi=min(lo+1,len(values)-1)
    return values[lo]+(values[hi]-values[lo])*(i-lo)


def sample(index,key_type):
    side='yes' if index%2==0 else 'no'
    with tempfile.TemporaryDirectory(prefix='kalshi-prepared-bench-') as temporary:
        base=Path(temporary);state=base/'state';state.mkdir();gateway=base/'gateway';gateway.mkdir()
        source=base/'generated.pem';key=rsa.generate_private_key(public_exponent=65537,key_size=2048) if key_type=='rsa' else ed25519.Ed25519PrivateKey.generate();source.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));source.chmod(0o600)
        credentials=base/'demo';configure(credentials,'demo','generated-prepared-benchmark',source)
        exchange=Exchange();exchange.slow=False;exchange.cash=600;exchange.yes=exchange.no=D('.49')
        server=Server(str(gateway/'gateway.sock'),TradingHandler);server.config={'environment':'demo','allow_trading':True,'client_uid':os.getuid(),'ws_host':'external-api-ws.demo.kalshi.co'}
        forward=exchange.forward;events=queue.Queue();measurements=[];states=[];market_errors=[];gateway_receipts=[]
        def delayed(*args,**kwargs):
            if kwargs.get('method',args[3] if len(args)>3 else 'GET')=='POST':gateway_receipts.append(time.monotonic())
            time.sleep(.02)
            return forward(*args,**kwargs)
        with patch('kalshi_bot.gateway.forward',side_effect=delayed),patch('kalshi_bot.streams.relay',side_effect=exchange.relay):
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            command=sandbox_command(ROOT,state,'demo-trading',credentials);mount=command.index('/gateway')
            if command[mount-1]=='--dir':command[mount-1:mount+1]=['--ro-bind',str(gateway),'/gateway']
            else:command[mount-1]=str(gateway)
            marker=command.index('/usr/bin/python3');command=command[:marker]+['/usr/bin/python3','-B','-c',BOOTSTRAP,'--mode','demo-trading']
            started=time.monotonic();process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
            def read():
                for line in process.stdout:
                    try:events.put(json.loads(line))
                    except ValueError:pass
            reader=threading.Thread(target=read,daemon=True);reader.start()
            def receive(predicate,timeout=35):
                end=time.monotonic()+timeout
                while time.monotonic()<end:
                    try:m=events.get(timeout=.1)
                    except queue.Empty:
                        if process.poll() is not None:raise AssertionError('Worker exited: '+process.stderr.read()[-1000:])
                        continue
                    if m['type']=='error':
                        try:process.wait(timeout=3)
                        except subprocess.TimeoutExpired:pass
                        detail=process.stderr.read()[-2500:] if process.poll() is not None else ''
                        raise AssertionError(m['message']+' '+detail)
                    if m['type']=='measurement':measurements.append(m)
                    if m['type']=='state':states.append(m['state'])
                    if m['type']=='market' and m.get('market_error'):market_errors.append(m['market_error'])
                    if predicate(m):return m
                raise AssertionError('Prepared timeout; latest statuses: '+str([(s.get('status'),s.get('prepared_entry',{})) for s in states[-3:]])+'; stream errors: '+str(market_errors[-3:]))
            send=lambda command:(process.stdin.write(json.dumps({'command':command})+'\n'),process.stdin.flush())
            try:
                receive(lambda m:m['type']=='ready')
                receive(lambda m:m['type']=='state' and not m['state'].get('running') and m['state'].get('prepared_entry',{}).get('ready'))
                with exchange.lock:connections_before_start=exchange.connections
                assert connections_before_start>0
                send('start')
                ready=receive(lambda m:m['type']=='state' and m['state'].get('running') and m['state'].get('prepared_entry',{}).get('ready'))['state']
                warm=time.monotonic()-started
                assert ready['prepared_entry']['cash_cents']==600
                with exchange.lock:assert exchange.connections==connections_before_start,'Start recreated the established book stream'
                assert all(n=='3' for sides in ready['prepared_entry']['quantities'].values() for n in sides.values())
                with exchange.lock:
                    exchange.yes=D('.97') if side=='yes' else D('.016');exchange.no=D('.02') if side=='yes' else D('.974');exchange.version+=1
                wire=receive(lambda m:m['type']=='measurement' and m['event']=='wire')
                receipt=next(m for m in measurements if m['event']=='receipt')
                self_latency=(wire['mono']-receipt['mono'])*1000
                assert self_latency>=0 and all(n==0 for n in wire['forbidden'].values())
                held=receive(lambda m:m['type']=='state' and bool(m['state']['position']))['state']
                assert held['position']['side']==side and D(held['position']['qty'])==3
                with exchange.lock:assert exchange.buys==1 and exchange.sells==0 and len(exchange.orders)==1
                send('pause');send('quit');process.wait(timeout=20);assert process.returncode==0
                result={'sample':index+1,'side':side,'key_type':key_type,'receipt_to_http_send_ms':round(self_latency,4),'receipt_to_gateway_ms':round((gateway_receipts[0]-receipt['mono'])*1000,4),'warmup_seconds':round(warm,3),
                        'precalculated_contracts':3,'limit_cents':98 if side=='yes' else 99,'critical_path_forbidden_calls':wire['forbidden'],
                        'worker_rss_mib':round(wire['rss_mib'],3),'real_orders':0,'production_credentials_used':False,
                        'connected_before_start':True,'stream_preserved_on_start':True}
                print(json.dumps(result),flush=True);return result
            finally:
                if process.poll() is None:process.terminate();process.wait(timeout=8)
                for stream in (process.stdin,process.stdout,process.stderr):stream.close()
                server.shutdown();server.server_close();thread.join(timeout=2)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--samples',type=int,default=24);p.add_argument('--key-type',choices=('rsa','ed25519'),default='rsa');p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    assert 2<=a.samples<=100
    results=[sample(i,a.key_type) for i in range(a.samples)];values=[r['receipt_to_gateway_ms'] for r in results]
    report={'conditions':'warm prepared state; real isolated demo worker, signing and Unix gateway; synthetic exchange with 20ms responses; no Internet or production account',
            'signing':('generated RSA-2048 PSS/SHA256' if a.key_type=='rsa' else 'generated Ed25519'),'boundary':'canonical sequenced book receipt to secure gateway receiving/validating full POST; excludes Internet/TLS and exchange acknowledgment/fill',
            'samples':results,'median_ms':statistics.median(values),'p95_ms':percentile(values,.95),'worst_ms':max(values),
            'critical_path_balance_or_other_get_calls':0,'critical_path_sizing_calls':0,'critical_path_reconciliation_calls':0}
    a.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='samples'},indent=2))


if __name__=='__main__':main()
