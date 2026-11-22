"""Configurable thresholds use the same entry/exit guards on both sides."""
from dataclasses import asdict
from decimal import Decimal as D
import tempfile
import unittest
from kalshi_bot.strategy import policy,set_policy
from test_prepared import Fixture


class StrategyMatrixTests(unittest.TestCase):
    def test_thresholds_exact_trigger_and_full_quantity_across_both_sides(self):
        previous=asdict(policy())
        self.addCleanup(lambda:set_policy(**previous))
        for buy in range(92,100):
            for stop in (63,71,86,92):
                if stop>=buy:continue
                for side in ('yes','no'):
                    with self.subTest(buy=buy,stop=stop,side=side),tempfile.TemporaryDirectory() as directory:
                        set_policy(buy,stop)
                        f=Fixture(directory)
                        try:
                            f.qualify(side,str((D(buy)-D('.1'))/100))
                            f.execute();self.assertEqual(f.venue.attempts,[])
                            f.qualify(side,str(D(buy)/100));f.execute()
                            order=f.venue.orders[-1]
                            f.venue.fill(order,3,price=str(D(buy)/100));f.publish()
                            f.feed.prices[side+'_bid']=D(stop)/100+D('.0001')
                            f.execute();self.assertEqual(f.venue.sells(),[])
                            f.feed.prices[side+'_bid']=D(stop)/100
                            f.execute();sell=f.venue.sells()[-1]
                            self.assertEqual(f.venue.actions[sell['order_id']],('sell',side))
                            self.assertEqual(D(sell['initial_count_fp']),3)
                            price=D(sell['yes_price_dollars'])
                            self.assertEqual(price if side=='yes' else 1-price,D('.01'))
                            f.feed.prices[side+'_bid']=D('.99')
                            for _ in range(5):f.execute()
                            self.assertEqual(len(f.venue.sells()),1)
                            f.venue.fill(sell,3);f.publish()
                            self.assertEqual(f.journal.position(),{})
                        finally:f.close()
