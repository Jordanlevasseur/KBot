"""Steady-state isolated worker resources, generated demo credentials and fake exchange."""
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
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from demo_pipeline import Exchange
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from kalshi_bot.credentials import configure
from kalshi_bot.desktop_runtime import sandbox_command
from kalshi_bot.gateway import Server,TradingHandler
ROOT=Path(__file__).resolve().parent.parent
BOOTSTRAP='''
import threading,time
from pathlib import Path
from kalshi_bot import desktop_worker as worker
def sample():
    while True:
        status=Path('/proc/self/status').read_text().splitlines()
        rss=int(next(v for v in status if v.startswith('VmRSS:')).split()[1])/1024
        worker.emit('resource_sample',mono=time.monotonic(),cpu=time.process_time(),rss_mib=rss)
        time.sleep(1)
threading.Thread(target=sample,daemon=True).start()
worker.main()
'''


def main():
    p=argparse.ArgumentParser();p.add_argument('--source-root',type=Path,default=ROOT);p.add_argument('--label',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    with tempfile.TemporaryDirectory(prefix='kalshi-idle-') as tmp:
        base=Path(tmp);state=base/'state';state.mkdir();gateway=base/'gateway';gateway.mkdir()
        source=base/'generated.pem';source.write_bytes(ed25519.Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));source.chmod(0o600)
        credentials=base/'demo';configure(credentials,'demo','generated-profile-key',source)
        exchange=Exchange();exchange.slow=False
        server=Server(str(gateway/'gateway.sock'),TradingHandler);server.config={'environment':'demo','allow_trading':True,'client_uid':os.getuid(),'ws_host':'external-api-ws.demo.kalshi.co'}
        events=queue.Queue();samples=[];states=0
        with patch('kalshi_bot.gateway.forward',side_effect=exchange.forward),patch('kalshi_bot.streams.relay',side_effect=exchange.relay):
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            command=sandbox_command(a.source_root,state,'demo-trading',credentials);mount=command.index('/gateway')
            if command[mount-1]=='--dir':command[mount-1:mount+1]=['--ro-bind',str(gateway),'/gateway']
            else:command[mount-1]=str(gateway)
            marker=command.index('/usr/bin/python3');command=command[:marker]+['/usr/bin/python3','-B','-c',BOOTSTRAP,'--mode','demo-trading']
            process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
            def read():
                for line in process.stdout:events.put(json.loads(line))
            reader=threading.Thread(target=read,daemon=True);reader.start()
            try:
                deadline=time.monotonic()+25
                while len(samples)<14 and time.monotonic()<deadline:
                    event=events.get(timeout=5)
                    if event['type']=='error':raise AssertionError(event['message'])
                    if event['type']=='state':assert not event['state']['running'];states+=1
                    if event['type']=='resource_sample':samples.append(event)
                assert len(samples)==14 and states>=2
                first,last=samples[3],samples[-1]  # Exclude imports/initial connection.
                duration=last['mono']-first['mono'];cpu=last['cpu']-first['cpu']
                value={'label':a.label,'mode':'paused entries; continuous book/display and full account reconciliation',
                       'sample_seconds':duration,'worker_cpu_seconds':cpu,'worker_cpu_percent_one_core':100*cpu/duration,
                       'worker_rss_mib_median':statistics.median(s['rss_mib'] for s in samples[3:]),
                       'worker_rss_mib_max':max(s['rss_mib'] for s in samples[3:]),'account_cycles':states,
                       'real_credentials_used':False,'orders_sent':exchange.buys+exchange.sells}
                assert value['orders_sent']==0
                a.output.write_text(json.dumps(value,indent=2)+'\n');print(json.dumps(value,indent=2))
            finally:
                if process.poll() is None:
                    process.stdin.write('{"command":"quit"}\n');process.stdin.flush()
                    try:process.wait(timeout=15)
                    except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                for stream in (process.stdin,process.stdout,process.stderr):stream.close()
                server.shutdown();server.server_close();thread.join(timeout=2);reader.join(timeout=2)


if __name__=='__main__':main()
