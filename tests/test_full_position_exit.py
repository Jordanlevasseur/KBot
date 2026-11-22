"""A protective exit always targets every owned share, never the buy allocation."""
import tempfile
import unittest
from strategy_fixture import configure_policy

def setUpModule():
    configure_policy(92,71)

from decimal import Decimal as D
from kalshi_bot.api import APIError
from kalshi_bot.protective import exit_state
from test_protective_sells import Fixture,TICKER


class FullPositionExitTests(unittest.TestCase):
    def assert_order(self,f,quantity):
        order=f.venue.sells()[-1]
        self.assertEqual(f.venue.actions[order['order_id']],('sell',f.side))
        self.assertEqual(D(order['initial_count_fp']),D(quantity))
        self.assertEqual(D(order['yes_price_dollars']),D('.01') if f.side=='yes' else D('.99'))
        self.assertLessEqual(f.venue.max_live_sells,1)
        return order

    def test_entire_position_for_both_sides_and_multiple_sizes_through_restart(self):
        for side in ('yes','no'):
            for count in ('1','13','26','50','137'):
                with self.subTest(side=side,count=count),tempfile.TemporaryDirectory() as directory:
                    f=Fixture(directory,side,fill=count,count=count)
                    try:
                        f.cycle('.71');order=self.assert_order(f,count)
                        part=min(D('3.25'),D(count)/2)
                        f.venue.fill(order,part);f.cycle('.92')
                        state=exit_state(f.journal,TICKER)
                        self.assertFalse(state['completed']);self.assertEqual(D(state['unsold_qty']),D(count)-part)
                        f.restart();f.cycle('.92')
                        self.assertEqual(len(f.venue.sells()),1)
                        self.assertEqual(D(f.journal.active()['quantity'])-D(f.journal.active()['filled']),D(count)-part)
                        f.venue.fill(order,D(count)-part);f.cycle('.92')
                        self.assertEqual(f.journal.position(),{});self.assertIsNone(f.journal.active())
                        state=exit_state(f.journal,TICKER)
                        self.assertTrue(state['completed']);self.assertEqual(D(state['unsold_qty']),0)
                        self.assertEqual(f.venue.sold,D(count))
                    finally:f.close()

    def test_sell_ignores_buy_allocation_and_rejects_under_or_oversized_quantity(self):
        for side in ('yes','no'):
            with self.subTest(side=side),tempfile.TemporaryDirectory() as directory:
                f=Fixture(directory,side,fill='13',count='13')
                get=f.venue.get
                f.venue.get=lambda path,params=None:{'balance':0} if path=='/portfolio/balance' else get(path,params)
                try:
                    f.journal.latch_stop(TICKER,side,D('.71'));attempts=len(f.venue.attempts)
                    for n in ('.01','1','6','12','13.01','26'):
                        with self.assertRaises(RuntimeError):
                            f.journal.submit(f.venue,TICKER,'sell',side,D('.71'),D(n),f.venue.close,f.venue.now)
                    self.assertEqual(len(f.venue.attempts),attempts)
                    f.cycle('.71');self.assert_order(f,'13')
                    self.assertEqual(f.venue.sold,0) # Submitted is not sold.
                finally:f.close()

    def test_partial_buy_and_fills_during_cancel_are_all_included_in_exit(self):
        for side in ('yes','no'):
            with self.subTest(side=side),tempfile.TemporaryDirectory() as directory:
                f=Fixture(directory,side,fill='2.75',count='13')
                try:
                    buy=f.venue.orders[0]
                    f.cycle('.71');self.assertEqual(f.venue.sells(),[])
                    f.venue.fill(buy,'1.25');f.cycle('.92')
                    self.assertEqual(f.venue.sells(),[]) # Wait for definitive cancel.
                    f.venue.confirm_cancel(buy);f.cycle('.92')
                    order=self.assert_order(f,'4')
                    f.venue.fill(order,'4');f.cycle('.92')
                    self.assertEqual(f.venue.sold,4);self.assertEqual(f.journal.position(),{})
                    self.assertTrue(exit_state(f.journal,TICKER)['completed'])
                finally:f.close()

    def test_partial_sell_cancel_rejection_restart_and_retry_exit_every_remainder(self):
        for side in ('yes','no'):
            with self.subTest(side=side),tempfile.TemporaryDirectory() as directory:
                f=Fixture(directory,side,fill='13',count='13')
                try:
                    f.cycle('.71');first=self.assert_order(f,'13')
                    f.venue.fill(first,'3.25');f.venue.confirm_cancel(first)
                    f.venue.next_error='reject'
                    with self.assertRaises(APIError):f.cycle('.92')
                    self.assertEqual(f.venue.position,D('9.75'));self.assertIsNone(f.journal.active())
                    f.cycle('.92');second=self.assert_order(f,'9.75')
                    f.venue.fill(second,'.50');f.venue.confirm_cancel(second)
                    f.restart();f.cycle('.92');third=self.assert_order(f,'9.25')
                    f.venue.fill(third,'9.25');f.cycle('.92')
                    self.assertEqual(f.venue.sold,13);self.assertEqual(f.venue.position,0)
                    self.assertEqual(f.journal.position(),{});self.assertEqual(len(f.venue.sells()),3)
                    self.assertTrue(exit_state(f.journal,TICKER)['completed'])
                finally:f.close()


if __name__=='__main__':unittest.main()
