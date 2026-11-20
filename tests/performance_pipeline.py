"""Offline receipt-to-submit and withdrawn Tk dashboard benchmark.

Real isolated worker and local gateway; generated demo keys and synthetic exchange.
No production socket, credentials or Internet. Run before/after on the same laptop.
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
import tkinter as tk
from decimal import Decimal as D
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from demo_pipeline import Exchange
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from kalshi_bot.credentials import configure
from kalshi_bot.desktop_runtime import sandbox_command
from kalshi_bot.gateway import Server,TradingHandler
from kalshi_bot.desktop import Application
ROOT=Path(__file__).resolve().parent.parent

# Instrument timing boundaries only. Production validation and request pacing run unchanged.
BOOTSTRAP='''
import time, resource
from kalshi_bot import desktop_worker as worker
from kalshi_bot import streams, market_data, trade_api
consume=streams.Book.consume
def timed_consume(self,message):
    stamp=time.monotonic()
    changed=consume(self,message)
    if changed:worker.emit('measurement',event='receipt',mono=stamp,prices=self.prices())
    return changed
streams.Book.consume=timed_consume
execution=market_data.MarketMonitor.set_execution
def timed_execution(self,running,phase,details=None):
    if phase in ('Checking balance and fees','Submitting buy'):
        worker.emit('measurement',event='eligible' if phase=='Checking balance and fees' else 'decision',mono=time.monotonic())
    return execution(self,running,phase,details)
market_data.MarketMonitor.set_execution=timed_execution
create=trade_api.OrderBroker.create
def timed_create(self,payload):
    usage=resource.getrusage(resource.RUSAGE_SELF)
    worker.emit('measurement',event='submission',mono=time.monotonic(),cpu=usage.ru_utime+usage.ru_stime,rss_kib=usage.ru_maxrss)
    return create(self,payload)
trade_api.OrderBroker.create=timed_create
worker.main()
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True);parser.add_argument('--samples',type=int,default=4)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();assert 2<=args.samples<=20
    results=[]
    with tempfile.TemporaryDirectory(prefix='kalshi-perf-') as tmp:
        base=Path(tmp);os.environ['XDG_STATE_HOME']=str(base/'ui');os.environ['XDG_CONFIG_HOME']=str(base/'config')
        root=tk.Tk();root.withdraw();app=Application(root,autostart=False)
        original_update=app.update_market
        rendered=[]
        def update(message):
            original_update(message)
            rendered.append((time.monotonic(),dict(message)))
        app.update_market=update
        source=base/'generated.pem';source.write_bytes(ed25519.Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));source.chmod(0o600)
        credentials=base/'demo';configure(credentials,'demo','generated-benchmark-key',source)
        for index in range(args.samples):
            side='yes' if index%2==0 else 'no'
            exchange=Exchange();exchange.slow=False;exchange.yes=D('.49');exchange.no=D('.49')
            directory=base/str(index);directory.mkdir();gateway=directory/'gateway';gateway.mkdir();state=directory/'state';state.mkdir()
            server=Server(str(gateway/'gateway.sock'),TradingHandler);server.config={'environment':'demo','allow_trading':True,'client_uid':os.getuid(),'ws_host':'external-api-ws.demo.kalshi.co'}
            requests=[]
            forward=exchange.forward
            def measured_forward(*values,**kw):
                requests.append(time.monotonic())
                # Small deterministic synthetic response latency, not an Internet estimate.
                time.sleep(.02)
                method=kw.get('method',values[3] if len(values)>3 else 'GET')
                if method=='DELETE':return 200,{},b'{}'  # Normal test shutdown cancellation.
                return forward(*values,**kw)
            events=queue.Queue();measurements=[];states=[];process=None
            with patch('kalshi_bot.gateway.forward',side_effect=measured_forward),patch('kalshi_bot.streams.relay',side_effect=exchange.relay):
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                command=sandbox_command(ROOT,state,'demo-trading',credentials)
                mount=command.index('/gateway')
                if command[mount-1]=='--dir':command[mount-1:mount+1]=['--ro-bind',str(gateway),'/gateway']
                else:command[mount-1]=str(gateway)
                marker=command.index('/usr/bin/python3');command=command[:marker]+['/usr/bin/python3','-B','-c',BOOTSTRAP,'--mode','demo-trading']
                started=time.monotonic();process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
                def read():
                    for line in process.stdout:
                        events.put(json.loads(line))
                reader=threading.Thread(target=read,daemon=True);reader.start()
                def receive(predicate,timeout=40):
                    end=time.monotonic()+timeout
                    while time.monotonic()<end:
                        root.update()
                        try:message=events.get(timeout=.005)
                        except queue.Empty:continue
                        kind=message.get('type')
                        if kind=='error':raise AssertionError(message['message'])
                        if kind=='measurement':measurements.append(message)
                        if kind=='state':states.append(message['state'])
                        if kind=='market':app.events.put((app.worker.generation,message))
                        if predicate(message):return message
                    raise AssertionError('Benchmark timed out')
                send=lambda command:(process.stdin.write(json.dumps({'command':command})+'\n'),process.stdin.flush())
                try:
                    receive(lambda m:m.get('type')=='ready');send('start')
                    receive(lambda m:m.get('type')=='state' and m['state']['running'] and m['state']['status']=='No qualifying 95–99¢ entry')
                    marker_count=len(requests);rendered.clear()
                    # Deliberately arrive during idle waiting, with non-aligned timing.
                    time.sleep(.075+index*.037)
                    with exchange.lock:
                        exchange.yes=D('.97') if side=='yes' else D('.016')
                        exchange.no=D('.03') if side=='yes' else D('.974')
                        exchange.version+=1
                    submission=receive(lambda m:m.get('type')=='measurement' and m['event']=='submission')
                    deadline=time.monotonic()+2
                    while not any(m.get(side+'_ask_cents')=='98.400' if side=='no' else m.get(side+'_ask_cents')=='97.00' for _,m in rendered) and time.monotonic()<deadline:
                        root.update()
                        try:message=events.get(timeout=.005)
                        except queue.Empty:continue
                        if message.get('type')=='market':app.events.put((app.worker.generation,message))
                    receipt=next(m['mono'] for m in measurements if m['event']=='receipt' and D(m['prices'][side+'_ask'])>=D('.95'))
                    eligible=next(m['mono'] for m in measurements if m['event']=='eligible')
                    decision=next(m['mono'] for m in measurements if m['event']=='decision')
                    display=next(at for at,m in rendered if m.get(side+'_ask_cents') and D(m[side+'_ask_cents'])>=95)
                    results.append({'side':side,'receipt_to_eligibility_ms':(eligible-receipt)*1000,
                        'receipt_to_decision_ms':(decision-receipt)*1000,'decision_to_submission_ms':(submission['mono']-decision)*1000,
                        'receipt_to_submission_ms':(submission['mono']-receipt)*1000,'dashboard_ms':(display-receipt)*1000,
                        'pre_submit_requests':len(requests)-marker_count,'worker_cpu_seconds':submission['cpu'],
                        'worker_cpu_percent_one_core':100*submission['cpu']/(submission['mono']-started),'worker_peak_rss_mib':submission['rss_kib']/1024})
                    print(json.dumps({'sample':index+1,**results[-1]}),flush=True)
                finally:
                    if process.poll() is None:
                        send('pause');send('quit')
                        try:process.wait(timeout=15)
                        except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                    for stream in (process.stdin,process.stdout,process.stderr):stream.close()
                    server.shutdown();server.server_close();thread.join(timeout=2);reader.join(timeout=2)
        root.destroy()
    metrics={key:{'median':statistics.median(r[key] for r in results),'max':max(r[key] for r in results)} for key in results[0] if key!='side'}
    report={'label':args.label,'samples':results,'summary':metrics,'conditions':'withdrawn Tk; real isolated demo worker/local gateway; mock exchange 20ms responses; unchanged REST rate pacing; no Internet/production account'}
    args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report['summary'],indent=2))


if __name__=='__main__':main()
