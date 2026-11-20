"""Read-only local trade-history audit. Never imports credentials or trading runtime.

Outputs aggregates only: no key IDs, order IDs, market tickers or account balance.
"""
import argparse
import datetime as dt
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo


def digest(path):
    if not path.exists():return None
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def audit(path):
    before={name:digest(Path(str(path)+suffix)) for name,suffix in (('database',''),('wal','-wal'))}
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
    try:
        orders=db.execute('SELECT * FROM orders ORDER BY rowid').fetchall();rows=[];issues=[];cashflows={};revenues={}
        for number,order in enumerate(orders,1):
            fills=[json.loads(r[0]) for r in db.execute('SELECT payload FROM fills WHERE order_id=?',(order['exchange_id'],))]
            quantity=sum((D(fill['count_fp']) for fill in fills),D(0))
            if quantity!=D(order['filled']):issues.append('Fill quantity mismatch for record '+str(number))
            cost=sum((D(fill['count_fp'])*D(fill[order['side']+'_price_dollars']) for fill in fills),D(0))
            fees=sum((D(fill.get('fee_cost','0')) for fill in fills),D(0))
            settlement=db.execute('SELECT payload FROM settlements WHERE ticker=?',(order['ticker'],)).fetchone()
            settled=json.loads(settlement[0]) if settlement else None
            market=db.execute('SELECT buys,stop,settled FROM markets WHERE ticker=?',(order['ticker'],)).fetchone()
            if market and market['settled'] and settled:
                # Count each market's settlement once, including all owned
                # buys/sells and their fees. Revenue is cents; fills are dollars.
                ticker=order['ticker']
                cashflows[ticker]=cashflows.get(ticker,D(0))+(cost if order['action']=='sell' else -cost)-fees
                revenues[ticker]=D(settled['revenue'])/100
            rows.append({'record':number,'action':order['action'],'side':order['side'],'status':order['status'],
                         'fills_match_quantity':quantity==D(order['filled']),'settlement_recorded':bool(settled),
                         'market_marked_settled':bool(market and market['settled']),
                         'stop_latched':bool(market and market['stop'])})
        snapshot=db.execute('SELECT at,payload FROM snapshots ORDER BY id DESC LIMIT 1').fetchone()
        latest=json.loads(snapshot['payload']) if snapshot else None
        contains_all=bool(latest and all(any(o.get('order_id')==r['exchange_id'] for o in latest['orders']) for r in orders))
        if not contains_all:issues.append('Latest saved snapshot does not include every owned order')
        if latest:
            indexed={o['order_id']:o for o in latest['orders']}
            for order in orders:
                remote=indexed.get(order['exchange_id'])
                if remote and (remote.get('status')!=order['status'] or D(remote['fill_count_fp'])!=D(order['filled'])):
                    issues.append('Saved exchange projection differs from local order')
        net=sum((cashflows[ticker]+revenue for ticker,revenue in revenues.items()),D(0))
        value={'source':'local SQLite journal; mode=ro; query_only=ON; consistent read transaction',
               'snapshot_at_pacific':dt.datetime.fromtimestamp(snapshot['at'],ZoneInfo('America/Los_Angeles')).isoformat() if snapshot else None,
               'completed_buys':sum(r['action']=='buy' and r['status']=='executed' for r in orders),
               'yes_buys':sum(r['action']=='buy' and r['side']=='yes' for r in orders),
               'no_buys':sum(r['action']=='buy' and r['side']=='no' for r in orders),
               'protective_sell_records':sum(r['action']=='sell' for r in orders),
               'recorded_net_profit_dollars':str(net),'recorded_net_profit_rounded_dollars':str(net.quantize(D('.01'))),
               'saved_nonzero_positions':sum(D(p['position_fp'])!=0 for p in latest['positions']) if latest else None,
               'saved_open_orders':sum(o.get('status') in ('pending','resting') for o in latest['orders']) if latest else None,
               'snapshot_contains_all_own_orders':contains_all,'records':rows,'issues':issues,
               'account_requests_sent':0,'credentials_read':False,'live_protective_execution_verified':False}
    finally:db.rollback();db.close()
    after={name:digest(Path(str(path)+suffix)) for name,suffix in (('database',''),('wal','-wal'))}
    value['database_and_wal_unchanged']=before==after
    if before!=after:value['issues'].append('Source files changed during audit; another writer may have been active')
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('database',type=Path);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=audit(args.database.resolve());args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='records'},indent=2))


if __name__=='__main__':main()
