import tempfile
import unittest
from decimal import Decimal as D
from kalshi_bot.entry_guard import entry_verdict,reference_evidence,EXIT_CUSHION
from kalshi_bot.strategy import VolatilityTracker,set_policy
from kalshi_bot.api import SchemaError
from test_prepared import Fixture


class EntryGuardTests(unittest.TestCase):
    def setUp(self):
        set_policy(95,89,240,30,30,25,20)
        self.wall=1000.;self.mono=500.
        self.market={'ticker':'KXBTC15M-TEST','floor_strike':60000}
        self.reference={'value':'60060','source_ms':1000000,'sample_mono':500.,'status':'Live'}
        self.tracker=VolatilityTracker()
        for i in range(61):self.tracker.add(940+i,{'yes_ask':D('.97')})
    def verdict(self,side='yes'):
        return entry_verdict(self.market,self.reference,side,self.tracker,wall=self.wall,mono=self.mono)
    def test_yes_and_no_require_favorable_50_dollars(self):
        for side,price in [('yes','60050'),('no','59950')]:
            self.reference['value']=price;self.assertIsNone(self.verdict(side)[0])
        for side,price in [('yes','60049.99'),('no','59950.01'),('yes','59940'),('no','60060')]:
            self.reference['value']=price;self.assertIsNotNone(self.verdict(side)[0])
    def test_reference_loss_staleness_future_and_missing_target_block(self):
        for key,value in [('status','Unavailable'),('source_ms',990000),('source_ms',1001000),('sample_mono',490.)]:
            old=self.reference[key];self.reference[key]=value
            self.assertIsNotNone(self.verdict()[0]);self.reference[key]=old
        self.market.pop('floor_strike');self.assertIsNotNone(self.verdict()[0])
    def test_custom_dollar_buffers_apply_to_both_sides_and_evidence(self):
        set_policy(95,89,target_buy_usd=75.5,target_exit_usd=40.25)
        for side,price in [('yes','60075.5'),('no','59924.5')]:
            self.reference['value']=price
            reason,evidence=self.verdict(side)
            self.assertIsNone(reason)
            self.assertEqual(D(evidence['buy_cushion_usd']),D('75.5'))
            self.assertEqual(D(evidence['exit_cushion_usd']),D('40.25'))
        self.reference['value']='60075.49';self.assertIsNotNone(self.verdict()[0])
    def test_invalid_target_settings_rejected(self):
        for buy,exit in [(35,35),(20,35),(0,0),(50,-1),(float('nan'),20),(50,float('inf'))]:
            with self.assertRaises(ValueError):set_policy(95,89,target_buy_usd=buy,target_exit_usd=exit)
    def test_missing_sparse_or_stale_volatility_blocks(self):
        for points in [[],[(999,.97)],[(x,.97) for x in range(940,990)]]:
            self.tracker.samples.clear();self.tracker.samples.extend(points)
            self.assertIsNotNone(self.verdict()[0])
    def test_one_minute_shock_uses_actual_range_and_equality_blocks(self):
        self.tracker.samples[0]=(940,.77)
        self.assertIn('volatility',self.verdict()[0])
    def test_both_average_limits_block(self):
        for limits in [(30,.1,100),(.1,100,100)]:
            set_policy(95,89,240,30,*limits)
            self.tracker.samples[10]=(950,.95)
            self.assertIsNotNone(self.verdict()[0])
    def test_exact_35_and_crossed_target_exit_for_each_side(self):
        for side,price in [('yes','60035'),('yes','59990'),('no','59965'),('no','60010')]:
            self.reference['value']=price
            evidence=reference_evidence(self.market,self.reference,side,wall=self.wall,mono=self.mono)
            self.assertLessEqual(D(evidence['held_side_cushion_usd']),EXIT_CUSHION)
        for side,price in [('yes','60035.01'),('no','59964.99')]:
            self.reference['value']=price
            evidence=reference_evidence(self.market,self.reference,side,wall=self.wall,mono=self.mono)
            self.assertGreater(D(evidence['held_side_cushion_usd']),EXIT_CUSHION)
    def test_final_wire_rechecks_guard_after_order_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            f=Fixture(directory)
            try:
                f.qualify();f.engine.entry_guard=lambda ticker,side:None
                f.journal.before_transmit=lambda payload:None
                original=f.engine._record
                def record(payload):
                    original(payload)
                    f.engine.entry_guard=lambda ticker,side:'Entry blocked: changed reference'
                f.journal.on_recorded=record
                from kalshi_bot.trade_api import OrderNotSent
                # The broker oracle does not implement the wire hook; invoke
                # the real final validation at its create boundary.
                from unittest.mock import patch
                with patch.object(f.venue,'create',side_effect=lambda p:f.engine._transmit(p)):
                    with self.assertRaises(OrderNotSent):f.execute()
                self.assertEqual(f.venue.attempts,[])
            finally:f.close()
