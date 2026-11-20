"""The reported NO-at-98c opportunity, with no exchange connections."""
import tempfile
import unittest
from strategy_fixture import configure_policy

def setUpModule():
    configure_policy(95,86)

from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch
from kalshi_bot.execution import Journal
from kalshi_bot.trader import cycle
from kalshi_bot.api import APIError
import json
from test_trader import Venue


class FreshFeed:
    def __init__(self):
        self.prices={'yes_bid':D('.02'),'yes_ask':D('.03'),'no_bid':D('.97'),'no_ask':D('.98')}
    def watch(self,ticker):pass
    def quote(self,ticker):return dict(self.prices) if self.prices is not None else None


class MissedNoTests(unittest.TestCase):
    def test_decimal_asks_round_up_to_allowed_limits_and_decimal_fills_are_accepted(self):
        for side in ('yes','no'):
            for ask,limit in (('.951','.96'),('.964','.97'),('.984','.99'),('.989','.99'),('.99','.99')):
                with self.subTest(side=side,ask=ask),tempfile.TemporaryDirectory() as directory:
                    journal=Journal(Path(directory)/'db','offline');journal.running=True;venue=Venue();feed=FreshFeed()
                    other='no' if side=='yes' else 'yes';p=D(ask)
                    feed.prices={side+'_ask':p,side+'_bid':p-D('.01'),other+'_bid':1-p,other+'_ask':1-p+D('.01')}
                    try:
                        with patch('kalshi_bot.fees.entry_reserve',return_value=D('.03')):
                            result=cycle(journal,venue,venue,None,now=venue.close-90,book_feed=feed)
                        self.assertEqual(D(result['limit']),D(limit))
                        self.assertEqual(D(result['executable_ask']),p)
                        self.assertLessEqual(D(result['limit']),D('.99'))
                        self.assertEqual(result['quantity'],50)
                        self.assertLessEqual(result['quantity']*(D(result['limit'])+D('.03')),D(100))
                        order=venue.orders[0];count=D(order['initial_count_fp'])
                        order.update(status='executed',fill_count_fp=str(count),remaining_count_fp='0')
                        yes=p if side=='yes' else 1-p
                        fills=[{'order_id':order['order_id'],'fill_id':'decimal-fill','count_fp':str(count),
                                'yes_price_dollars':str(yes),'no_price_dollars':str(1-yes),'fee_cost':'.01'}]
                        journal.reconcile(venue.orders,fills,[{'ticker':'KXBTC15M-T','position_fp':str(count if side=='yes' else -count)}])
                        self.assertEqual(journal.position(),{('KXBTC15M-T',side):count})
                    finally:journal.close()

    def test_ask_above_99_cannot_be_rounded_down_to_bypass_the_limit_cap(self):
        for side in ('yes','no'):
            with tempfile.TemporaryDirectory() as directory:
                journal=Journal(Path(directory)/'db','offline');journal.running=True;venue=Venue();feed=FreshFeed()
                other='no' if side=='yes' else 'yes'
                feed.prices={side+'_ask':D('.994'),side+'_bid':D('.98'),other+'_bid':D('.006'),other+'_ask':D('.02')}
                try:
                    result=cycle(journal,venue,venue,D('.03'),now=venue.close-90,book_feed=feed)
                    self.assertEqual(venue.orders,[])
                finally:journal.close()

    def test_exact_90_seconds_no_98_yes_3_submits_no_limit_98(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'db','offline-reproduction')
            venue=Venue();venue.bid=D('.02');venue.ask=D('.03');journal.running=True
            try:
                with patch('kalshi_bot.fees.entry_reserve',return_value=D('.03')):
                    result=cycle(journal,venue,venue,None,now=venue.close-90,book_feed=FreshFeed())
                self.assertEqual(result['side'],'no')
                self.assertEqual(result['limit'],'0.98')
                self.assertEqual(len(venue.orders),1)
                self.assertEqual(venue.orders[0]['yes_price_dollars'],'0.0200')
                own=journal.db.execute('SELECT side,price FROM orders').fetchone()
                self.assertEqual(tuple(own),('no','0.98'))
            finally:journal.close()

    def test_both_sides_and_blocking_conditions_are_symmetric(self):
        for side in ('yes','no'):
            for case in ('eligible','paused','stale','cash','pending','two_buys','outside_window','below','above'):
                with self.subTest(side=side,case=case),tempfile.TemporaryDirectory() as directory:
                    journal=Journal(Path(directory)/'db','offline');venue=Venue();feed=FreshFeed()
                    if side=='yes':feed.prices={('no'+k[3:] if k.startswith('yes') else 'yes'+k[2:]):v for k,v in feed.prices.items()}
                    journal.running=case!='paused'
                    try:
                        if case=='stale':feed.prices=None
                        if case in ('below','above'):feed.prices[side+'_ask']=D('.94') if case=='below' else D('1.00')
                        if case=='cash':
                            original=venue.get
                            venue.get=lambda path,params=None:{'balance':100} if path=='/portfolio/balance' else original(path,params)
                        if case=='pending':
                            journal.reconcile([],[],[])
                            journal.submit(venue,'KXBTC15M-T','buy',side,D('.98'),1,venue.close,venue.close-100)
                        if case=='two_buys':
                            with journal.db:journal.db.execute("INSERT INTO markets(ticker,buys) VALUES('KXBTC15M-T',2)")
                        before=len(venue.orders)
                        with patch('kalshi_bot.fees.entry_reserve',return_value=D('.03')):
                            result=cycle(journal,venue,venue,None,now=venue.close-(241 if case=='outside_window' else 90),book_feed=feed)
                        self.assertEqual(len(venue.orders)-before,1 if case in ('eligible','two_buys') else 0)
                        if case=='eligible':self.assertEqual(result['side'],side)
                    finally:journal.close()

    def test_exchange_rejection_records_stage_side_http_and_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'db','offline');journal.running=True;venue=Venue()
            venue.create=lambda payload:(_ for _ in ()).throw(APIError(403))
            try:
                with patch('kalshi_bot.fees.entry_reserve',return_value=D('.03')):
                    with self.assertRaises(APIError):cycle(journal,venue,venue,None,now=venue.close-90,book_feed=FreshFeed())
                event=journal.db.execute("SELECT event FROM audit WHERE event LIKE 'Decision failure:%'").fetchone()[0]
                detail=json.loads(event[len('Decision failure: '):])
                self.assertEqual(detail['side'],'no');self.assertEqual(detail['http_status'],403)
                self.assertEqual(detail['outcome'],'rejected');self.assertEqual(detail['stage'],'Submitting buy')
            finally:journal.close()

    def test_reconciliation_failure_preserves_exact_reason_before_any_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'db','offline');journal.running=True;venue=Venue();venue.quantity=D(1)
            try:
                with self.assertRaises(RuntimeError):cycle(journal,venue,venue,None,now=venue.close-90,book_feed=FreshFeed())
                event=journal.db.execute("SELECT event FROM audit WHERE event LIKE 'Decision failure:%'").fetchone()[0]
                detail=json.loads(event[len('Decision failure: '):])
                self.assertEqual(detail['reason'],'Account positions do not match owned fills')
                self.assertEqual(detail['outcome'],'blocked before submission');self.assertEqual(venue.orders,[])
            finally:journal.close()


if __name__=='__main__':unittest.main()
