"""Final 30-second purchase block, using disposable state and synthetic venues."""
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest
from strategy_fixture import configure_policy

def setUpModule():
    configure_policy(92,86)

from unittest.mock import Mock,patch
from kalshi_bot.core import Engine,Quote
from kalshi_bot.execution import Journal,order_payload
from kalshi_bot.strategy import entry_open
from kalshi_bot.trade_api import CREATE,validate_write,OrderNotSent
from kalshi_bot.trader import cycle
from test_prepared import Fixture
from test_trader import Venue


class ClosingBlackoutTests(unittest.TestCase):
    def test_shared_exact_boundary(self):
        for seconds,allowed in ((241,False),(240,True),(31,True),(30.001,True),(30,False),(29.999,False),(15,False),(1,False),(0,False),(-1,False)):
            with self.subTest(seconds=seconds):self.assertEqual(entry_open(900,900-seconds),allowed)

    def test_wire_buy_expiry_is_30_seconds_early_sell_expiry_is_market_close(self):
        for side in ('yes','no'):
            buy=order_payload('KXBTC15M-T','buy',side,D('.98'),3,900,'test-buy')
            sell=order_payload('KXBTC15M-T','sell',side,D('.86'),3,900,'test-sell')
            self.assertEqual(buy['expiration_time'],870);self.assertEqual(sell['expiration_time'],900)
            for now in (660,869.999):self.assertEqual(validate_write('POST',CREATE,buy,now=now),CREATE)
            for now in (659.999,870,885,900):
                with self.subTest(side=side,now=now),self.assertRaises(ValueError):validate_write('POST',CREATE,buy,now=now)
            self.assertEqual(validate_write('POST',CREATE,sell,now=899),CREATE)

    def test_journal_rejects_late_yes_and_no_before_recording_any_order(self):
        for side in ('yes','no'):
            for seconds in (30,15,1,0):
                with self.subTest(side=side,seconds=seconds),tempfile.TemporaryDirectory() as directory:
                    j=Journal(Path(directory)/'db','offline');broker=Mock();j.running=True;j.reconcile([],[],[])
                    try:
                        with self.assertRaises(RuntimeError):j.submit(broker,'KXBTC15M-T','buy',side,D('.98'),3,900,900-seconds)
                        broker.create.assert_not_called();self.assertEqual(j.db.execute('SELECT count(*) FROM orders').fetchone()[0],0)
                    finally:j.close()

    def test_prepared_both_sides_allow_just_before_then_block_at_30_seconds(self):
        for side in ('yes','no'):
            for seconds in (31,30.001,30,29.999,15,1):
                with self.subTest(side=side,seconds=seconds),tempfile.TemporaryDirectory() as directory:
                    f=Fixture(directory)
                    try:
                        f.venue.now=f.venue.close-seconds;f.qualify(side);result=f.execute()
                        self.assertEqual(len(f.venue.attempts),int(seconds>30))
                        if seconds<=30:self.assertIn('final 30',result['status'])
                        else:self.assertEqual(f.venue.attempts[0]['expiration_time'],f.venue.close-30)
                    finally:f.close()

    def test_crossing_boundary_after_durable_intent_blocks_before_transmit(self):
        for side in ('yes','no'):
            with self.subTest(side=side),tempfile.TemporaryDirectory() as directory:
                f=Fixture(directory)
                try:
                    f.qualify(side);f.venue.now=f.venue.close-31;wall=[f.venue.close-30.01]
                    f.engine.previous_intent=lambda ticker,side:wall.__setitem__(0,f.venue.close-30)
                    with patch('kalshi_bot.prepared.time.time',side_effect=lambda:wall[0]):
                        with self.assertRaises(OrderNotSent):f.execute()
                    self.assertEqual(f.venue.attempts,[])
                    self.assertEqual(f.journal.db.execute('SELECT status FROM orders').fetchone()[0],'rejected')
                    self.assertEqual(f.journal.db.execute('SELECT buys FROM markets').fetchone()[0],0)
                finally:f.close()

    def test_protective_exits_remain_enabled_at_10_and_1_seconds(self):
        for side in ('yes','no'):
            with self.subTest(side=side),tempfile.TemporaryDirectory() as directory:
                f=Fixture(directory)
                try:
                    f.qualify(side);f.execute();f.venue.fill(f.venue.orders[-1],3);f.publish()
                    f.venue.now=f.venue.close-10;f.feed.prices[side+'_bid']=D('.80');f.execute()
                    sell=f.venue.sells()[-1];self.assertEqual(f.venue.attempts[-1]['expiration_time'],f.venue.close)
                    f.venue.fill(sell,1);f.publish();f.venue.now=f.venue.close-1
                    f.feed.prices[side+'_bid']=D('.92');f.execute()
                    self.assertEqual(len(f.venue.sells()),1)
                    f.venue.fill(sell,2);f.publish();f.qualify(side);f.execute()
                    self.assertEqual(len(f.venue.attempts),2);self.assertEqual(f.journal.position(),{})
                finally:f.close()

    def test_residual_entry_cancellation_at_blackout_is_not_duplicated(self):
        for side in ('yes','no'):
            with self.subTest(side=side),tempfile.TemporaryDirectory() as directory:
                f=Fixture(directory)
                try:
                    f.qualify(side);f.execute();buy=f.venue.orders[-1];f.venue.fill(buy,1);f.publish()
                    f.venue.now=f.venue.close-30;f.execute();f.execute()
                    self.assertEqual(len(f.venue.cancellations),1);self.assertEqual(len(f.venue.attempts),1)
                    f.venue.confirm_cancel(buy);f.publish();f.qualify(side);f.execute()
                    self.assertEqual(len(f.venue.attempts),1);self.assertEqual(sum(f.journal.position().values()),1)
                finally:f.close()

    def test_simulation_pending_fills_expire_at_blackout_and_late_stop_still_sells(self):
        for initial_fill in (0,1):
            with self.subTest(initial_fill=initial_fill),tempfile.TemporaryDirectory() as directory:
                e=Engine(Path(directory)/'db',cash=10000);e.start()
                try:
                    e.tick(Quote('KXBTC15M-T',900,869,94,4,96,6,liquidity=initial_fill))
                    e.tick(Quote('KXBTC15M-T',900,870,94,4,96,6))
                    self.assertEqual(e.row('SELECT status FROM orders')['status'],'expired')
                    self.assertEqual(e.db.execute('SELECT coalesce(sum(qty),0) FROM positions').fetchone()[0],initial_fill)
                    e.tick(Quote('KXBTC15M-T',900,890,86,13,87,14))
                    self.assertIsNone(e.row('SELECT * FROM positions WHERE qty>0'))
                    e.tick(Quote('KXBTC15M-T',900,891,94,4,96,6))
                    self.assertEqual(e.row('SELECT buys FROM markets')['buys'],initial_fill)
                finally:e.close()

    def test_simulation_retains_earliest_official_close_for_pending_buy(self):
        with tempfile.TemporaryDirectory() as directory:
            e=Engine(Path(directory)/'db');e.start()
            try:
                e.tick(Quote('KXBTC15M-T',900,869,94,4,96,6,liquidity=0))
                e.tick(Quote('KXBTC15M-T',950,870,94,4,96,6))
                self.assertEqual(e.row('SELECT cutoff FROM markets')['cutoff'],900)
                self.assertEqual(e.row('SELECT status FROM orders')['status'],'expired')
                self.assertEqual(e.db.execute('SELECT count(*) FROM fills').fetchone()[0],0)
            finally:e.close()

    def test_legacy_cycle_blocks_late_buys_on_both_sides(self):
        for side in ('yes','no'):
            with self.subTest(side=side),tempfile.TemporaryDirectory() as directory:
                j=Journal(Path(directory)/'db','offline');venue=Venue();j.running=True
                if side=='yes':venue.bid=D('.97');venue.ask=D('.98')
                else:venue.bid=D('.02');venue.ask=D('.03')
                try:
                    result=cycle(j,venue,venue,D('.03'),now=venue.close-15)
                    self.assertEqual(venue.orders,[]);self.assertIn('final 30',result['status'])
                finally:j.close()


if __name__=='__main__':unittest.main()
