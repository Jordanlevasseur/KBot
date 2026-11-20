import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from strategy_fixture import configure_policy

def setUpModule():
    configure_policy(92,71)

from decimal import Decimal as D
from pathlib import Path
from kalshi_bot.execution import Journal
from kalshi_bot.trader import cycle


class Venue:
    def __init__(self):
        self.orders=[]; self.fills=[]; self.quantity=D(0)
        self.bid=D('.94'); self.ask=D('.96'); self.cancellations=0
        self.close=2000000110

    def create(self,payload):
        order={'order_id':'exchange-'+payload['client_order_id'],'client_order_id':payload['client_order_id'],
               'ticker':payload['ticker'],'type':'limit','status':'resting','yes_price_dollars':payload['price'],
               'initial_count_fp':payload['count'],'fill_count_fp':'0','remaining_count_fp':payload['count']}
        self.orders.append(order)
        return {'client_order_id':order['client_order_id'],'order_id':order['order_id']}

    def cancel(self,oid,ticker):
        self.cancellations+=1
        # Cancellation acknowledgment intentionally leaves order unresolved.
        return {'order_id':oid,'reduced_by':'0'}

    def fill(self,buy=True):
        order=self.orders[-1]
        n=D(order['remaining_count_fp'])
        price=D('.94') if buy else D('.88')
        order.update(status='executed',fill_count_fp=order['initial_count_fp'],remaining_count_fp='0')
        self.quantity+=n if buy else -n
        self.fills.append({'fill_id':'fill-'+order['order_id'],'order_id':order['order_id'],'count_fp':str(n),
                           'yes_price_dollars':str(price),'no_price_dollars':str(1-price),'fee_cost':'0.01'})

    def pages(self,path,params=None):
        if path=='/portfolio/orders': return self.orders
        if path=='/portfolio/fills': return self.fills
        if path=='/portfolio/positions': return [{'ticker':'KXBTC15M-T','position_fp':str(self.quantity),'netting_enabled':True}] if self.quantity else []
        if path=='/portfolio/settlements': return []
        if path=='/markets': return [self.market()]
        raise AssertionError(path)

    def market(self):
        return {'ticker':'KXBTC15M-T','market_type':'binary','exchange_index':0,'status':'active',
                'open_time':dt.datetime.fromtimestamp(self.close-900,dt.timezone.utc).isoformat(),'close_time':dt.datetime.fromtimestamp(self.close,dt.timezone.utc).isoformat()}

    def get(self,path,params=None):
        if path=='/portfolio/balance': return {'balance':10000}
        if path=='/exchange/status': return {'trading_active':True}
        if path.endswith('orderbook'):
            return {'orderbook_fp':{'yes_dollars':[[str(self.bid),'1000']], 'no_dollars':[[str(1-self.ask),'1000']]}}
        if path=='/markets/KXBTC15M-T': return {'market':self.market()}
        raise AssertionError(path)


class TraderTests(unittest.TestCase):
    def test_end_to_end_repeated_buys_stops_rebound_and_third(self):
        with tempfile.TemporaryDirectory() as d:
            j=Journal(Path(d)/'db','demo-generated')
            venue=Venue(); j.running=True
            try:
                cycle(j,venue,venue,D('.02'),now=2000000000)
                self.assertEqual(len(venue.orders),1)
                self.assertEqual(venue.orders[-1]['yes_price_dollars'],'0.9600')
                venue.fill()
                cycle(j,venue,venue,D('.02'),now=2000000001)
                venue.bid=D('.70'); venue.ask=D('.87')
                cycle(j,venue,venue,D('.02'),now=2000000002)
                self.assertEqual(len(venue.orders),2)
                self.assertEqual(venue.orders[-1]['yes_price_dollars'],'0.7100')
                cycle(j,venue,venue,D('.02'),now=2000000003)
                self.assertEqual(len(venue.orders),2)
                venue.bid=D('.94'); venue.ask=D('.96'); venue.fill(buy=False)
                cycle(j,venue,venue,D('.02'),now=2000000004)
                self.assertEqual(len(venue.orders),3)
                venue.fill()
                cycle(j,venue,venue,D('.02'),now=2000000005)
                venue.bid=D('.70'); venue.ask=D('.87')
                cycle(j,venue,venue,D('.02'),now=2000000006)
                self.assertEqual(len(venue.orders),4)
                venue.fill(buy=False); venue.bid=D('.94'); venue.ask=D('.96')
                cycle(j,venue,venue,D('.02'),now=2000000007)
                self.assertEqual(len(venue.orders),5)
                self.assertEqual(j.active()['action'],'buy')
                self.assertEqual(j.position(),{})
            finally: j.close()

    def test_default_cli_and_production_without_authorization_never_connect(self):
        for flags in ([],['--environment','production','--connect','--trade','--fee-reserve','.02']):
            result=subprocess.run([sys.executable,'-m','kalshi_bot.trader',*flags],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('connection attempted',result.stderr)


if __name__=='__main__': unittest.main()
