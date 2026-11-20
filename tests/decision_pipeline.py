"""Real isolated worker: 4 Hz strategy checks during a blocked account request.

Generated demo credentials, local gateway and synthetic exchange only.
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
from urllib.parse import urlsplit
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from demo_pipeline import Exchange
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from kalshi_bot.credentials import configure
from kalshi_bot.desktop_runtime import sandbox_command
from kalshi_bot.gateway import Server,TradingHandler

ROOT=Path(__file__).resolve().parent.parent
BOOTSTRAP="""
import threading,time
from pathlib import Path
from kalshi_bot import desktop_worker as worker
def resources():
    while True:
        status=Path('/proc/self/status').read_text().splitlines()
        rss=int(next(v for v in status if v.startswith('VmRSS:')).split()[1])/1024
        worker.emit('resource',mono=time.monotonic(),cpu=time.process_time(),rss_mib=rss)
        time.sleep(1)
threading.Thread(target=resources,daemon=True).start()
worker.main()
"""


def run(side):
    with tempfile.TemporaryDirectory(prefix='kalshi-decisions-') as temporary:
        base=Path(temporary);gateway=base/'gateway';gateway.mkdir();state=base/'state';state.mkdir()
        source=base/'generated.pem';source.write_bytes(ed25519.Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));source.chmod(0o600)
        credentials=base/'demo';configure(credentials,'demo','generated-cadence-test-key',source)
        exchange=Exchange();exchange.slow=False;exchange.yes=exchange.no=D('.49')
        gate_started=threading.Event();block_next=[False];blocked_at=[None];ended_at=[None]
        original=exchange.forward
        def forward(config,target,headers,method='GET',body=None):
            if method=='GET' and urlsplit(target).path.endswith('/portfolio/positions') and block_next[0]:
                block_next[0]=False;blocked_at[0]=time.monotonic();gate_started.set()
                time.sleep(14) # Less than the unchanged 15-second Unix HTTP timeout.
                ended_at[0]=time.monotonic()
            return original(config,target,headers,method,body)
        server=Server(str(gateway/'gateway.sock'),TradingHandler)
        server.config={'environment':'demo','allow_trading':True,'client_uid':os.getuid(),'ws_host':'external-api-ws.demo.kalshi.co'}
        events=queue.Queue();samples=[];resources=[];process=None
        with patch('kalshi_bot.gateway.forward',side_effect=forward),patch('kalshi_bot.streams.relay',side_effect=exchange.relay):
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            command=sandbox_command(ROOT,state,'demo-trading',credentials);mount=command.index('/gateway')
            if command[mount-1]=='--dir':command[mount-1:mount+1]=['--ro-bind',str(gateway),'/gateway']
            else:command[mount-1]=str(gateway)
            marker=command.index('/usr/bin/python3');command=command[:marker]+['/usr/bin/python3','-B','-c',BOOTSTRAP,'--mode','demo-trading']
            process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
            def read():
                for line in process.stdout:events.put(json.loads(line))
            reader=threading.Thread(target=read,daemon=True);reader.start()
            def receive(predicate,timeout=40,observe=None):
                end=time.monotonic()+timeout
                while time.monotonic()<end:
                    try:message=events.get(timeout=.1)
                    except queue.Empty:
                        if process.poll() is not None:raise AssertionError('Synthetic worker exited')
                        continue
                    if message['type']=='error':raise AssertionError(message['message'])
                    if message['type']=='resource':resources.append(message)
                    if message['type']=='market' and message.get('strategy_decision'):samples.append(message['strategy_decision'])
                    if observe:observe(message)
                    if predicate(message):return message
                raise AssertionError('Cadence pipeline timeout')
            send=lambda command:(process.stdin.write(json.dumps({'command':command})+'\n'),process.stdin.flush())
            def eligible():
                with exchange.lock:
                    exchange.yes=D('.97') if side=='yes' else D('.016')
                    exchange.no=D('.02') if side=='yes' else D('.974');exchange.version+=1
            changed=[False,False]
            def during(message):
                if not gate_started.is_set() or ended_at[0] is not None:return
                elapsed=time.monotonic()-blocked_at[0]
                assert exchange.buys==0,'Order submitted before account reconciliation completed'
                if elapsed>4 and not changed[0]:
                    with exchange.lock:exchange.yes=D('.93');exchange.no=D('.06');exchange.version+=1
                    changed[0]=True
                if elapsed>6 and not changed[1]:eligible();changed[1]=True
            try:
                receive(lambda m:m['type']=='ready');send('start')
                receive(lambda m:m['type']=='state' and m['state']['running'] and m['state']['status']=='No qualifying 95–99¢ entry')
                block_next[0]=True;eligible()
                held=receive(lambda m:m['type']=='state' and bool(m['state']['position']),observe=during)['state']
                assert held['position']['side']==side and exchange.buys==1 and exchange.sells==0
                assert gate_started.is_set() and ended_at[0] is not None
                unique={s['decision_count']:s for s in samples if blocked_at[0]<=s['decision_mono']<=ended_at[0]}
                checks=sorted(unique.values(),key=lambda s:s['decision_mono']);assert len(checks)>=28
                gaps=[b['decision_mono']-a['decision_mono'] for a,b in zip(checks,checks[1:])]
                assert max(gaps)<.5,'Strategy gap exceeded 500 ms'
                assert all(s['execution_authorized'] is False for s in checks)
                assert any(s['action']=='no_entry' for s in checks),'Prices were not reevaluated during network stall'
                assert any(s.get('side')==side and s['action'] in ('entry_candidate','await_account') for s in checks)
                timed=[r for r in resources if blocked_at[0]+1<=r['mono']<=ended_at[0]]
                duration=timed[-1]['mono']-timed[0]['mono'];cpu=100*(timed[-1]['cpu']-timed[0]['cpu'])/duration
                send('pause');send('quit');process.wait(timeout=15);assert process.returncode==0
                result={'side':side,'result':'passed','blocked_account_seconds':round(ended_at[0]-blocked_at[0],3),
                        'strategy_checks_during_block':len(checks),'median_interval_ms':round(statistics.median(gaps)*1000,3),
                        'max_interval_ms':round(max(gaps)*1000,3),'observed_checks_per_second':round((len(checks)-1)/(checks[-1]['decision_mono']-checks[0]['decision_mono']),3),
                        'changed_prices_reevaluated_during_block':True,'orders_before_required_checks_completed':0,
                        'buys_after_validation':exchange.buys,'no_duplicate_orders':True,
                        'worker_cpu_percent_one_core_during_block':round(cpu,3),'worker_rss_mib':round(max(r['rss_mib'] for r in timed),3),
                        'real_account_requests':0,'real_orders':0,'production_credentials_used':False}
                print(json.dumps(result),flush=True);return result
            finally:
                if process.poll() is None:process.terminate();process.wait(timeout=5)
                for stream in (process.stdin,process.stdout,process.stderr):stream.close()
                server.shutdown();server.server_close();thread.join(timeout=2)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    results=[run(side) for side in ('yes','no')]
    args.output.write_text(json.dumps({'mode':'isolated demo worker; synthetic slow exchange','results':results},indent=2)+'\n')


if __name__=='__main__':main()
