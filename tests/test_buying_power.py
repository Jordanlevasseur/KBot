"""Sizing must use spendable market cash, not money on another exchange."""
import tempfile
import unittest
from pathlib import Path
from decimal import Decimal as D
from unittest.mock import Mock,patch
from kalshi_bot.api import ROOT,SchemaError,allowed_path
from kalshi_bot.buying_power import market_cash,market_scope
from kalshi_bot.execution import Journal
from kalshi_bot.trader import cycle
from test_trader import Venue
from test_missed_no import FreshFeed


class BuyingPowerTests(unittest.TestCase):
    def test_aggregate_cash_cannot_size_yes_or_no_on_another_exchange(self):
        for side in ('yes','no'):
            with self.subTest(side=side),tempfile.TemporaryDirectory() as directory:
                journal=Journal(Path(directory)/'db','offline');journal.running=True;venue=Venue();feed=FreshFeed()
                feed.prices={'no_bid':D('.96'),'no_ask':D('.97'),'yes_bid':D('.03'),'yes_ask':D('.04')}
                if side=='yes':feed.prices={k.replace('yes','TEMP').replace('no','yes').replace('TEMP','no'):v for k,v in feed.prices.items()}
                market=venue.market;venue.market=lambda:{**market(),'exchange_index':2};get=venue.get;reads=[]
                def read(path,params=None):
                    if path=='/portfolio/balance':
                        reads.append(params)
                        return {'balance':620,'balance_dollars':'6.2000'} if params=={'subaccount':0,'exchange_index':2} else {'balance':2700}
                    return get(path,params)
                venue.get=read
                try:
                    with patch('kalshi_bot.fees.entry_reserve',return_value=D('.03')):
                        result=cycle(journal,venue,venue,None,now=venue.close-90,book_feed=feed)
                    self.assertEqual(result['side'],side);self.assertEqual(result['quantity'],3)
                    self.assertEqual(reads,[{'subaccount':0,'exchange_index':2}])
                    self.assertLessEqual(D(result['quantity'])*D('1.00'),D('3.10'))
                finally:journal.close()
    def test_zero_market_cash_never_falls_back_to_aggregate_cash(self):
        with tempfile.TemporaryDirectory() as directory:
            j=Journal(Path(directory)/'db','offline');j.running=True;venue=Venue();get=venue.get
            def read(path,params=None):
                if path=='/portfolio/balance':return {'balance':0} if params else {'balance':2700}
                return get(path,params)
            venue.get=read
            try:
                with patch('kalshi_bot.fees.entry_reserve',return_value=D('.03')):
                    result=cycle(j,venue,venue,None,now=venue.close-90,book_feed=FreshFeed())
                self.assertEqual(venue.orders,[]);self.assertEqual(result['cash_cents'],0)
            finally:j.close()
    def test_unknown_market_exchange_fails_closed(self):
        for value in (None,-1,True,'2',64):
            with self.subTest(value=value),self.assertRaises(SchemaError):market_scope({'ticker':'KXBTC15M-T','exchange_index':value})
    def test_precise_cash_is_rounded_down(self):
        client=Mock();client.get.return_value={'balance':200,'balance_dollars':'1.9999'}
        self.assertEqual(market_cash(client,{'ticker':'KXBTC15M-T','exchange_index':2}),199)
        client.get.assert_called_once_with('/portfolio/balance',{'subaccount':0,'exchange_index':2})
    def test_invalid_cash_cannot_authorize_an_order(self):
        for value in ({'balance':True},{'balance':-1},{'balance':100,'balance_dollars':'NaN'},{'balance':100,'balance_dollars':'-1'}):
            client=Mock();client.get.return_value=value
            with self.subTest(value=value),self.assertRaises(SchemaError):market_cash(client,{'ticker':'KXBTC15M-T','exchange_index':0})
    def test_only_scoped_primary_balance_query_is_permitted(self):
        self.assertEqual(allowed_path(ROOT+'/portfolio/balance?subaccount=0&exchange_index=2'),ROOT+'/portfolio/balance')
        for path in ('/portfolio/balance?subaccount=1&exchange_index=2','/portfolio/balance?exchange_index=2',
                     '/portfolio/balance?subaccount=0&exchange_index=-1','/portfolio/balance?subaccount=0&exchange_index=64',
                     '/portfolio/balance?subaccount=0&exchange_index=02','/portfolio/orders?subaccount=0&exchange_index=2',
                     '/markets?subaccount=0&exchange_index=2','/portfolio/balance?subaccount=0&exchange_index=2&cursor=extra'):
            with self.subTest(path=path),self.assertRaises(ValueError):allowed_path(ROOT+path)
    def test_market_exchange_change_during_checks_blocks_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            j=Journal(Path(directory)/'db','offline');j.running=True;venue=Venue();get=venue.get;calls=[0]
            def market():
                calls[0]+=1
                return {**Venue.market(venue),'exchange_index':2 if calls[0]==1 else 3}
            venue.market=market
            try:
                with patch('kalshi_bot.fees.entry_reserve',return_value=D('.03')):
                    result=cycle(j,venue,venue,None,now=venue.close-90,book_feed=FreshFeed())
                self.assertEqual(venue.orders,[]);self.assertIn('cash scope changed',result['status'])
            finally:j.close()


if __name__=='__main__':unittest.main()
