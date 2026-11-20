"""Whole-contract allocation; generated venues/temp journals only, never live."""
import tempfile
import unittest
from strategy_fixture import configure_policy

def setUpModule():
    configure_policy(92,86)

from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
from kalshi_bot.api import SchemaError
from kalshi_bot.strategy import contract_target
from kalshi_bot.core import size
from kalshi_bot.execution import Journal,affordable
from kalshi_bot.trader import cycle
from test_trader import Venue
from test_missed_no import FreshFeed


class ContractSizingTests(unittest.TestCase):
    def test_cash_examples_and_whole_contract_boundaries(self):
        for cash,expected in ((0,0),(1,0),(100,0),(199,0),(200,1),(201,1),
                              (2599,12),(2600,13),(2687,13),(2699,13),(2799,13),(2800,14),(10000,50)):
            with self.subTest(cash=cash):
                self.assertEqual(contract_target(cash),expected)
                for cents in range(92,100):
                    self.assertEqual(affordable(cash,D(cents)/100,D('.03')),expected)
                    self.assertEqual(size(cash,cents),expected)

    def test_general_formula_for_every_cent_through_1000_and_large_balances(self):
        # No special case for $26: all 100,001 balances, all five allowed limits.
        prices=[D(cents)/100 for cents in range(92,100)]
        for cash in range(100001):
            expected=cash//200
            self.assertEqual(contract_target(cash),expected)
            for price in prices:
                self.assertEqual(affordable(cash,price,D('.03')),expected)
        for cash in (5340,1000001,123456789,2**31-1,2**63-1):
            for price in prices:
                self.assertEqual(affordable(cash,price,D('.03')),cash//200)

    def test_all_cash_cents_26_to_27_99_target_13_at_every_limit(self):
        for cash in range(2600,2800):
            for cents in range(92,100):
                with self.subTest(cash=cash,limit=cents):
                    n=affordable(cash,D(cents)/100,D('.03'))
                    self.assertEqual(n,13)
                    self.assertLessEqual(D(n)*(D(cents)/100+D('.03')),D(cash)/100)

    def test_fee_reserve_comes_from_total_cash_and_cannot_overspend(self):
        # 13 contracts at 99c plus 3c fee reserve costs $13.26, not a $13 budget.
        self.assertEqual(affordable(2600,D('.99'),D('.03')),13)
        self.assertGreater(D(13)*D('1.02'),D('13'))
        for cash in (0,199,200,2687,10000):
            for reserve in ('.00','.03','1.01','4.00001'):
                with self.subTest(cash=cash,reserve=reserve):
                    n=affordable(cash,D('.99'),D(reserve))
                    self.assertLessEqual(n,contract_target(cash))
                    self.assertLessEqual(D(n)*(D('.99')+D(reserve)),D(cash)/100)
        self.assertEqual(affordable(2687,D('.99'),D('4')),5)
        self.assertEqual(affordable(200,D('.99'),D('4')),0)

    def test_invalid_inputs_cannot_authorize_contracts(self):
        for cash in (-1,True,False,26.87,D('2687'),'2687',None):
            with self.subTest(cash=cash):
                with self.assertRaises(ValueError):contract_target(cash)
                with self.assertRaises(ValueError):affordable(cash,D('.98'),D('.03'))
                with self.assertRaises(ValueError):size(cash,98)
        for price,reserve in (('.91','.03'),('1','.03'),('.984','.03'),('.98','-.01'),('.98','NaN'),('.98','Infinity')):
            with self.subTest(price=price,reserve=reserve),self.assertRaises((ValueError,SchemaError)):
                affordable(2687,D(price),D(reserve))

    def test_yes_and_no_submit_13_contracts_with_valid_cent_or_fractional_ask(self):
        for side in ('yes','no'):
            for ask,limit in (('.92','.92'),('.924','.93'),('.93','.93'),('.94','.94'),('.95','.95'),('.96','.96'),('.97','.97'),('.98','.98'),('.984','.99'),('.99','.99')):
                with self.subTest(side=side,ask=ask),tempfile.TemporaryDirectory() as directory:
                    j=Journal(Path(directory)/'db','offline');j.running=True;venue=Venue();feed=FreshFeed()
                    feed.prices={side+'_ask':D(ask),side+'_bid':D(ask)-D('.01'),
                                 ('no' if side=='yes' else 'yes')+'_ask':1-D(ask)+D('.01'),
                                 ('no' if side=='yes' else 'yes')+'_bid':1-D(ask)}
                    get=venue.get;balances=[]
                    def read(path,params=None):
                        if path=='/portfolio/balance':
                            balances.append(params)
                            return {'balance':2687} if params=={'subaccount':0,'exchange_index':0} else {'balance':100000}
                        return get(path,params)
                    venue.get=read
                    try:
                        with patch('kalshi_bot.fees.entry_reserve',return_value=D('.03')):
                            result=cycle(j,venue,venue,None,now=venue.close-90,book_feed=feed)
                        self.assertEqual(result['side'],side);self.assertEqual(result['quantity'],13)
                        self.assertEqual(D(result['limit']),D(limit));self.assertEqual(len(venue.orders),1)
                        self.assertEqual(D(venue.orders[0]['initial_count_fp']),13)
                        self.assertEqual(D(venue.orders[0]['yes_price_dollars']),D(limit) if side=='yes' else 1-D(limit))
                        self.assertEqual(balances,[{'subaccount':0,'exchange_index':0}])
                        # The unresolved first order still prevents another submission.
                        cycle(j,venue,venue,D('.03'),now=venue.close-89)
                        self.assertEqual(len(venue.orders),1)
                    finally:j.close()

    def test_second_purchase_recalculates_count_only_after_completed_sale(self):
        with tempfile.TemporaryDirectory() as directory:
            j=Journal(Path(directory)/'db','offline');j.running=True;venue=Venue();get=venue.get;cash=[2687]
            venue.get=lambda path,params=None:{'balance':cash[0]} if path=='/portfolio/balance' else get(path,params)
            try:
                first=cycle(j,venue,venue,D('.03'),now=venue.close-90)
                self.assertEqual(first['quantity'],13)
                venue.fill();venue.bid=D('.85');venue.ask=D('.87')
                cycle(j,venue,venue,D('.03'),now=venue.close-89)
                self.assertEqual(len(venue.orders),2)
                self.assertEqual(D(venue.orders[-1]['initial_count_fp']),13)
                cash[0]=2003
                cycle(j,venue,venue,D('.03'),now=venue.close-88)
                self.assertEqual(len(venue.orders),2)
                venue.fill(buy=False);venue.bid=D('.97');venue.ask=D('.98')
                second=cycle(j,venue,venue,D('.03'),now=venue.close-87)
                self.assertEqual(second['quantity'],10)
                self.assertEqual(len(venue.orders),3)
            finally:j.close()


if __name__=='__main__':unittest.main()
