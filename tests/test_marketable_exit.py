import tempfile
import time
import unittest
from decimal import Decimal as D
from unittest.mock import patch
from kalshi_bot.exit_price import protective_limit
from kalshi_bot.execution import order_payload
from kalshi_bot.api import SchemaError
from kalshi_bot.trade_api import validate_write,CREATE
from test_prepared import Fixture
from strategy_fixture import configure_policy


class MarketableExitTests(unittest.TestCase):
    def setUp(self):configure_policy(95,89)

    def test_grid_and_complementary_prices(self):
        for step in ('.01','.001','.0001'):
            market={'price_ranges':[{'start':'0','end':'1','step':step}]}
            for side in ('yes','no'):
                self.assertEqual(protective_limit(market,side),D(step))

    def test_missing_grid_blocks_instead_of_inventing_price(self):
        with self.assertRaises(SchemaError):protective_limit({},'yes')

    def test_fractional_remaining_quantity_is_valid_for_both_sides(self):
        for side in ('yes','no'):
            p=order_payload('KXBTC15M-TEST','sell',side,D('.001'),D('3.67'),int(time.time())+600,'synthetic-exit')
            validate_write('POST',CREATE,p)
            self.assertEqual(p['price'],'0.0010' if side=='yes' else '0.9990')

    def test_gap_below_89_sweeps_remaining_position_and_stays_latched(self):
        for side in ('yes','no'):
            with self.subTest(side=side),tempfile.TemporaryDirectory() as directory:
                f=Fixture(directory)
                try:
                    f.qualify(side);f.execute();f.venue.fill(f.venue.orders[0],3);f.publish()
                    f.feed.prices[side+'_bid']=D('.11');f.execute()
                    order=f.venue.sells()[0]
                    self.assertEqual(f.venue.actions[order['order_id']],('sell',side))
                    self.assertEqual(D(order['initial_count_fp']),3)
                    f.venue.fill(order,1,price=D('.11'));f.publish()
                    f.feed.prices[side+'_bid']=D('.05')
                    for _ in range(20):f.execute()
                    self.assertEqual(len(f.venue.sells()),1)
                    f.venue.fill(order,2,price=D('.05'));f.publish()
                    self.assertEqual(f.venue.position,0)
                    self.assertEqual(f.venue.sold,3)
                finally:f.close()
