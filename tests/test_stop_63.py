"""Current 63-cent protective rule, synthetic exchange only."""
import tempfile
import unittest
from strategy_fixture import configure_policy

def setUpModule():
    configure_policy(92,63)

from decimal import Decimal as D
from test_protective_sells import Fixture
from kalshi_bot.execution import order_payload
from kalshi_bot.trade_api import validate_write, CREATE

class Stop63Tests(unittest.TestCase):
    def test_both_sides_exact_gap_rebound_restart_and_quantity(self):
        for side in ('yes','no'):
            for trigger in ('.63','.50'):
                with self.subTest(side=side,trigger=trigger),tempfile.TemporaryDirectory() as directory:
                    f=Fixture(directory,side,fill='10')
                    try:
                        f.cycle('.64');self.assertFalse(f.stop());self.assertEqual(f.venue.sells(),[])
                        f.cycle(trigger);self.assertTrue(f.stop())
                        f.reconcile();f.cycle(trigger)
                        sells=f.venue.sells();self.assertEqual(len(sells),1)
                        o=sells[0];self.assertEqual(D(o['initial_count_fp']),D('10'))
                        self.assertEqual(f.venue.actions[o['order_id']],('sell',side))
                        self.assertEqual((D(o['yes_price_dollars']) if side=='yes' else 1-D(o['yes_price_dollars'])),D('.63'))
                        f.cycle('.92');f.restart();f.cycle('.92')
                        self.assertTrue(f.stop());self.assertEqual(len(f.venue.sells()),1)
                    finally:f.close()
    def test_wire_guards_and_new_buy_range(self):
        for side in ('yes','no'):
            for cents in range(92,100):
                p=order_payload('KXBTC15M-T','buy',side,D(cents)/100,3,900,'test-buy')
                validate_write('POST',CREATE,p,now=800)
            p=order_payload('KXBTC15M-T','sell',side,D('.63'),3,900,'test-sell')
            validate_write('POST',CREATE,p,now=800)
            for price in ('.62','.64','.71','.86'):
                with self.assertRaises(ValueError):order_payload('KXBTC15M-T','sell',side,D(price),3,900,'test-sell')
