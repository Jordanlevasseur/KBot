"""Authenticated GET/stream audit through the installed gateway; no order broker.

Production journal is mounted read-only. Reconciliation runs on a disposable
SQLite backup, never on production. Prints aggregate health only.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))

INNER=r'''
import json,sqlite3,threading,time,sys
from pathlib import Path
from kalshi_bot.api import Client,GatewayTransport,RateGate
from kalshi_bot.credentials import load
from kalshi_bot.discovery import open_markets
from kalshi_bot.execution import Journal
from kalshi_bot.missing_order import MissingOrderVerifier
from kalshi_bot.security import check_isolation
from kalshi_bot.streams import BookFeed
check_isolation()
signer=load('/credentials','production');gate=RateGate()
def client():return Client(signer,GatewayTransport('/gateway/gateway.sock'),gate=gate)
source=sqlite3.connect('file:/history/desktop-trading.sqlite3?mode=ro',uri=True)
source.execute('PRAGMA query_only=ON')
target=sqlite3.connect('/state/copy.sqlite3');source.backup(target);target.close();source.close()
journal=Journal('/state/copy.sqlite3','production:'+signer.fingerprint)
verifier=MissingOrderVerifier(client);feed=BookFeed(signer,'/gateway/gateway.sock',None)
stop=threading.Event();results={'read_only':True,'production_journal_modified':False,'orders_submitted':0,'account_reads':0,'account_read_errors':0,'reconciled':False,'reconciliation_error':None}
lock=threading.Lock();snapshot=None
def accounts():
    global snapshot
    while not stop.is_set():
        try:
            c=client();data=[c.pages(p) for p in ('/portfolio/orders','/portfolio/fills','/portfolio/positions','/portfolio/settlements')]
            with lock:snapshot=(data,time.monotonic());results['account_reads']+=1
        except Exception as exc:
            with lock:results['account_read_errors']+=1;results['account_error_type']=type(exc).__name__
        stop.wait(10)
try:
    markets=open_markets(client())
    if not markets:raise RuntimeError('No active market discovered')
    feed.watch(markets[0]['ticker'])
    worker=threading.Thread(target=accounts,daemon=True);worker.start()
    start=time.monotonic();samples=healthy=0;transitions=[];previous=None;last_apply=None
    while time.monotonic()-start<float(sys.argv[1]):
        health=feed.health();samples+=1;healthy+=health['status']=='connected'
        signature=(health['status'],health['error'])
        if signature!=previous:
            transitions.append({'seconds':round(time.monotonic()-start,2),'status':signature[0],'error':signature[1]});previous=signature
        with lock:cohort=snapshot
        if cohort:
            data,at=cohort;proofs=verifier.proofs(journal,data[0])
            revision=(at,len(proofs))
            if revision!=last_apply and time.monotonic()-at<15:
                last_apply=revision
                try:
                    journal.reconcile(*data,missing_proofs=proofs)
                    results.update(reconciled=True,reconciliation_error=None,last_reconciled_seconds=round(time.monotonic()-start,2))
                except Exception as exc:
                    results.update(reconciled=False,reconciliation_error=str(exc).split(':')[0] if isinstance(exc,RuntimeError) else type(exc).__name__)
        time.sleep(.2)
    results.update(duration_seconds=round(time.monotonic()-start,2),healthy_sample_percent=round(100*healthy/max(1,samples),2),stream_transitions=transitions,
                   verification_records=journal.db.execute('SELECT count(*) FROM historical_order_verifications').fetchone()[0])
finally:
    stop.set();feed.close();verifier.close();journal.close()
print(json.dumps(results),flush=True)
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=120)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 10<=args.seconds<=300:parser.error('Duration must be 10–300 seconds')
    from kalshi_bot.desktop_runtime import sandbox_command,paths
    state,config=paths()
    with tempfile.TemporaryDirectory(prefix='kalshi-read-only-') as directory:
        command=sandbox_command(ROOT,Path(directory),'production-monitor',config/'production')
        marker=command.index('/usr/bin/python3')
        command=command[:marker]+['--ro-bind',str(state/'production-trading'),'/history',
            '/usr/bin/python3','-B','-c',INNER,str(args.seconds)]
        result=subprocess.run(command,capture_output=True,text=True,timeout=args.seconds+90)
        if result.returncode:
            # Avoid relaying arbitrary stderr from authenticated operations.
            raise SystemExit('Read-only audit failed; no orders were submitted. Exit '+str(result.returncode))
        value=json.loads(result.stdout)
        args.output.write_text(json.dumps(value,indent=2)+'\n')
        print(json.dumps(value,indent=2))


if __name__=='__main__':main()
