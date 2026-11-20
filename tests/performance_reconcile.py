"""Profile full-history reconciliation using generated completed order/fill records."""
import argparse
import cProfile
import io
import json
from pathlib import Path
import pstats
import statistics
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from kalshi_bot.execution import Journal,order_payload
from decimal import Decimal as D


def fixture(journal,count):
    orders=[];fills=[];settlements=[]
    with journal.db:
        for n in range(count):
            ticker='KXBTC15M-PERF-'+str(n)
            journal.db.execute('INSERT INTO markets VALUES(?,1,1,1)',(ticker,));settlements.append({'ticker':ticker})
            for action,price in (('buy',D('.98')),('sell',D('.86'))):
                oid=action+str(n);payload=order_payload(ticker,action,'yes',price,1,900,oid)
                journal.db.execute('INSERT INTO orders(client_id,exchange_id,ticker,action,side,price,quantity,cutoff,status,filled,counted,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(oid,oid,ticker,action,'yes',str(price),'1',900,'executed','1',1,json.dumps(payload)))
                orders.append({'client_order_id':oid,'order_id':oid,'ticker':ticker,'type':'limit','initial_count_fp':'1','yes_price_dollars':str(price),'fill_count_fp':'1','remaining_count_fp':'0','status':'executed'})
                fills.append({'fill_id':'f'+oid,'order_id':oid,'count_fp':'1','yes_price_dollars':str(price),'no_price_dollars':str(1-price)})
        for n in range(count*2):
            fills.append({'fill_id':'unmanaged'+str(n),'order_id':'other'+str(n),'count_fp':'1','yes_price_dollars':'.5','no_price_dollars':'.5'})
    return orders,fills,[],settlements


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--label',required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        journal=Journal(Path(directory)/'db','offline-performance');data=fixture(journal,1000)
        # Warm cache + initial fills; compare repeated full checks on identical data.
        journal.reconcile(*data)
        measurements=[]
        for _ in range(5):
            wall=time.monotonic();cpu=time.process_time();journal.reconcile(*data)
            measurements.append({'wall_ms':(time.monotonic()-wall)*1000,'cpu_ms':(time.process_time()-cpu)*1000})
        profiler=cProfile.Profile();profiler.runcall(journal.reconcile,*data)
        report=io.StringIO();pstats.Stats(profiler,stream=report).sort_stats('cumulative').print_stats(12)
        queries=[];journal.db.set_trace_callback(queries.append);journal.reconcile(*data);journal.db.set_trace_callback(None)
        journal.close()
    value={'label':args.label,'generated_owned_orders':2000,'generated_remote_fills':4000,'samples':measurements,
           'median_wall_ms':statistics.median(m['wall_ms'] for m in measurements),'median_cpu_ms':statistics.median(m['cpu_ms'] for m in measurements),'sql_statements':len(queries),'profile':report.getvalue()}
    args.output.write_text(json.dumps(value,indent=2)+'\n');print(json.dumps(value,indent=2))


if __name__=='__main__':main()
