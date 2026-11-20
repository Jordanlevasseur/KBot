"""Protective exits: both sides, adverse lifecycle events and randomized sequences.

All exchange data and keys are synthetic. Production journals are never opened.
"""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import sqlite3
import tempfile
import threading
import time
import unittest
from strategy_fixture import configure_policy
from kalshi_bot.strategy import policy

def setUpModule():
    configure_policy(92,86)

from decimal import Decimal as D
from unittest.mock import Mock,patch
from kalshi_bot.api import APIError,SchemaError
from kalshi_bot.execution import Journal
from kalshi_bot.streams import Book,BookFeed,persist_crossing
from kalshi_bot.trader import cycle
from kalshi_bot.trade_api import CREATE,validate_write

TICKER='KXBTC15M-PROTECT'


class Quotes:
    def __init__(self,side):self.side=side;self.fresh=True;self.set('.97')
    def set(self,bid):
        p=D(bid);other='no' if self.side=='yes' else 'yes'
        ask=min(D('.99'),p+D('.01'))
        self.prices={self.side+'_bid':p,self.side+'_ask':ask,other+'_bid':1-ask,other+'_ask':1-p}
    def watch(self,ticker):assert ticker==TICKER
    def quote(self,ticker):return dict(self.prices) if self.fresh else None


class ProtectiveVenue:
    """Exchange-side oracle: only explicit fills change holdings; ID retries deduplicate."""
    def __init__(self):
        self.now=2000000000;self.close=self.now+90;self.orders=[];self.fills=[];self.settlements=[]
        self.actions={};self.position=D(0);self.held_side=None;self.bought=D(0);self.sold=D(0)
        self.attempts=[];self.cancellations=[];self.next_error=None;self.read_error=None
        self.hide=set();self.closed=False;self.before_pages=None;self.max_live_sells=0
    def create(self,p):
        validate_write('POST',CREATE,p,now=self.now)
        self.attempts.append(copy.deepcopy(p))
        existing=next((o for o in self.orders if o['client_order_id']==p['client_order_id']),None)
        if existing:return {'order_id':existing['order_id'],'client_order_id':p['client_order_id']}
        error=self.next_error;self.next_error=None
        if error in ('reject','timeout_before','rate_limit'):
            raise APIError({'reject':400,'timeout_before':0,'rate_limit':429}[error])
        yes=D(p['price']);n=D(p['count'])
        if p['side']=='bid' and yes>=D('.92'):action,side='buy','yes'
        elif p['side']=='ask' and yes<=D('.08'):action,side='buy','no'
        elif p['side']=='ask' and yes==D(policy().sell_cents)/100:action,side='sell','yes'
        elif p['side']=='bid' and yes==1-D(policy().sell_cents)/100:action,side='sell','no'
        else:raise AssertionError('Unexpected synthetic order')
        if action=='sell':
            assert self.held_side==side and 0<n<=self.position,'Attempted oversell or wrong side'
            assert not any(o['status'] in ('pending','resting') for o in self.orders),'Concurrent orders'
        else:assert self.position==0,'Opposing or overlapping buy'
        oid='synthetic-'+str(len(self.orders)+1);self.actions[oid]=(action,side)
        order={'order_id':oid,'client_order_id':p['client_order_id'],'ticker':p['ticker'],'type':'limit','status':'resting',
               'yes_price_dollars':p['price'],'initial_count_fp':p['count'],'fill_count_fp':'0','remaining_count_fp':p['count']}
        self.orders.append(order)
        self.max_live_sells=max(self.max_live_sells,sum(o['status'] in ('resting','pending') and self.actions[o['order_id']][0]=='sell' for o in self.orders))
        if error=='timeout_after':raise APIError()
        return {'order_id':oid,'client_order_id':p['client_order_id']}
    def fill(self,order,quantity,price=None):
        n=D(str(quantity));action,side=self.actions[order['order_id']]
        assert order['status'] in ('resting','pending') and 0<n<=D(order['remaining_count_fp'])
        limit=D(order['yes_price_dollars']) if side=='yes' else 1-D(order['yes_price_dollars'])
        price=D(price) if price is not None else (D('.98') if action=='buy' else limit)
        if action=='buy':
            assert self.held_side in (None,side)
            self.held_side=side;self.position+=n;self.bought+=n
        else:
            assert side==self.held_side and n<=self.position,'Exchange would oversell'
            assert price>=limit;self.position-=n;self.sold+=n
            if not self.position:self.held_side=None
        filled=D(order['fill_count_fp'])+n;remaining=D(order['initial_count_fp'])-filled
        order.update(fill_count_fp=str(filled),remaining_count_fp=str(remaining),status='executed' if not remaining else 'resting')
        yes=price if side=='yes' else 1-price
        self.fills.append({'fill_id':'synthetic-fill-'+str(len(self.fills)+1),'order_id':order['order_id'],'count_fp':str(n),
                           'yes_price_dollars':str(yes),'no_price_dollars':str(1-yes),'fee_cost':'0'})
    def cancel(self,oid,ticker):
        self.cancellations.append(oid)
        return {'order_id':oid} # An acknowledgment is deliberately NOT a cancellation.
    def confirm_cancel(self,order):order['status']='canceled'
    def pages(self,path,params=None):
        if self.before_pages:self.before_pages(path)
        if self.read_error:raise APIError(self.read_error if isinstance(self.read_error,int) else 0)
        if path=='/portfolio/orders':return copy.deepcopy([o for o in self.orders if o['order_id'] not in self.hide])
        if path=='/portfolio/fills':return copy.deepcopy(self.fills)
        if path=='/portfolio/positions':return [{'ticker':TICKER,'position_fp':str(self.position if self.held_side=='yes' else -self.position)}] if self.position else []
        if path=='/portfolio/settlements':return copy.deepcopy(self.settlements)
        if path=='/markets':return [self.market()]
        if path=='/events/fee_changes':return []
        raise AssertionError(path)
    def market(self):
        stamp=lambda t:dt.datetime.fromtimestamp(t,dt.timezone.utc).isoformat()
        return {'ticker':TICKER,'event_ticker':TICKER,'market_type':'binary','exchange_index':0,'status':'closed' if self.closed else 'active',
                'open_time':stamp(self.close-900),'close_time':stamp(self.close)}
    def get(self,path,params=None):
        if self.read_error:raise APIError(self.read_error if isinstance(self.read_error,int) else 0)
        if path=='/portfolio/balance':return {'balance':10000}
        if path=='/exchange/status':return {'trading_active':True}
        if path=='/markets/'+TICKER:return {'market':self.market()}
        if path=='/markets':return {'markets':[] if self.closed else [self.market()]}
        if path=='/series/KXBTC15M':return {'series':{'ticker':'KXBTC15M','fee_type':'quadratic','fee_multiplier':1}}
        raise AssertionError(path)
    def sells(self):return [o for o in self.orders if self.actions.get(o['order_id'],(None,None))[0]=='sell']


class Fixture:
    def __init__(self,directory,side,fill='10',count='10'):
        self.path=Path(directory)/'db';self.side=side;self.venue=ProtectiveVenue();self.feed=Quotes(side)
        self.journal=Journal(self.path,'offline-protective');self.journal.running=True;self.journal.reconcile([],[],[])
        self.journal.submit(self.venue,TICKER,'buy',side,D('.98'),D(count),self.venue.close,self.venue.now)
        if D(fill):self.venue.fill(self.venue.orders[0],fill)
        self.reconcile();self.journal.running=False
    def reconcile(self):
        v=self.venue;self.journal.reconcile(v.pages('/portfolio/orders'),v.pages('/portfolio/fills'),v.pages('/portfolio/positions'),v.pages('/portfolio/settlements'))
    def cycle(self,bid=None,**kw):
        if bid is not None:self.feed.set(bid)
        return cycle(self.journal,self.venue,self.venue,D('.03'),now=self.venue.now,book_feed=kw.get('feed',self.feed))
    def restart(self):self.journal.close();self.journal=Journal(self.path,'offline-protective')
    def stop(self):return bool(self.journal.db.execute('SELECT stop FROM markets WHERE ticker=?',(TICKER,)).fetchone()[0])
    def close(self):self.journal.close()


class ProtectiveSellTests(unittest.TestCase):
    def each_side(self,callback,fill='10'):
        for side in ('yes','no'):
            with self.subTest(side=side),tempfile.TemporaryDirectory() as directory:
                fixture=Fixture(directory,side,fill)
                try:callback(fixture)
                finally:fixture.close()
    def assert_sell(self,f,quantity='10'):
        sell=f.venue.sells()[-1]
        self.assertEqual(f.venue.actions[sell['order_id']],('sell',f.side))
        self.assertEqual(D(sell['initial_count_fp']),D(quantity))
        self.assertEqual(D(sell['yes_price_dollars']),D('.86') if f.side=='yes' else D('.14'))
        self.assertLessEqual(f.venue.max_live_sells,1)

    def test_hold_at_90_and_87_and_just_above_86(self):
        def run(f):
            for bid in ('.90','.87','.8601'):
                f.cycle(bid);self.assertFalse(f.stop());self.assertEqual(f.venue.sells(),[])
                self.assertEqual(f.venue.position,D(10))
        self.each_side(run)
    def test_exact_86_triggers_on_both_sides(self):
        def run(f):
            f.cycle('.8601')
            self.assertFalse(f.stop())
            self.assertEqual(f.venue.sells(),[])
            f.cycle('.86')
            self.assertTrue(f.stop())
            self.assert_sell(f)
            self.assertEqual(f.venue.sold,0)
            self.assertEqual(f.venue.position,D(10))
        self.each_side(run)
    def test_direct_gap_from_95_to_80_keeps_86_limit_unfilled(self):
        def run(f):
            f.cycle('.95');f.cycle('.80');self.assert_sell(f)
            for _ in range(5):f.cycle('.80')
            self.assertEqual(len(f.venue.sells()),1);self.assertEqual(f.venue.sold,0)
        self.each_side(run)
    def test_brief_crossing_then_rebound_is_durable_through_restart(self):
        def run(f):
            persist_crossing(f.path,TICKER,f.side,D('.85'),observed_at=time.time())
            f.restart();f.cycle('.92');self.assertTrue(f.stop());self.assert_sell(f)
        self.each_side(run)
    def test_repeated_crossings_preserve_one_accepted_sell(self):
        def run(f):
            for bid in ('.86','.92','.85','.87','.80','.99')*3:f.cycle(bid)
            self.assertEqual(len(f.venue.sells()),1);self.assertEqual(len(f.venue.attempts),2)
            self.assertEqual(f.venue.cancellations,[])
        self.each_side(run)
    def test_exact_trigger_ten_seconds_before_close(self):
        def run(f):
            f.venue.now=f.venue.close-10;f.cycle('.86');self.assert_sell(f)
        self.each_side(run)
    def test_partial_buy_cancel_ack_and_additional_fill_before_confirmed_cancel(self):
        def run(f):
            f.cycle('.86');self.assertTrue(f.stop());self.assertEqual(f.venue.sells(),[])
            self.assertTrue(f.venue.cancellations)
            f.venue.fill(f.venue.orders[0],'1.25');f.cycle('.92')
            self.assertEqual(f.venue.sells(),[])
            f.venue.confirm_cancel(f.venue.orders[0]);f.cycle('.92');self.assert_sell(f,'5.25')
        self.each_side(run,fill='4')
    def test_no_sell_for_zero_filled_buy_while_cancellation_pending(self):
        def run(f):
            persist_crossing(f.path,TICKER,f.side,D('.86'),observed_at=time.time())
            f.cycle('.86');self.assertEqual(f.venue.sells(),[]);self.assertEqual(f.venue.position,0)
        self.each_side(run,fill='0')
    def test_existing_managed_sell_is_kept_during_stop(self):
        def run(f):
            f.cycle('.86');order=f.venue.sells()[0];f.restart()
            for _ in range(4):f.cycle('.80')
            self.assertEqual(f.journal.active()['exchange_id'],order['order_id']);self.assertEqual(len(f.venue.sells()),1)
            self.assertEqual(f.venue.cancellations,[])
        self.each_side(run)
    def test_unmanaged_pending_order_blocks_writes_instead_of_bypassing_reconciliation(self):
        def run(f):
            f.venue.orders.append({'order_id':'external','client_order_id':'external','status':'resting'})
            with self.assertRaises(RuntimeError):f.cycle('.86')
            self.assertEqual(f.venue.sells(),[])
        self.each_side(run)
    def test_rejected_sell_can_retry_with_stop_preserved(self):
        def run(f):
            f.venue.next_error='reject'
            with self.assertRaises(APIError):f.cycle('.86')
            self.assertTrue(f.stop());self.assertIsNone(f.journal.active());self.assertEqual(f.venue.position,10)
            f.cycle('.92');self.assert_sell(f);self.assertEqual(len(f.venue.sells()),1)
        self.each_side(run)
    def test_unlimited_definitive_rejections_then_success_without_third_buy(self):
        def run(f):
            for _ in range(12):
                f.venue.next_error='reject'
                with self.assertRaises(APIError):f.cycle('.86')
            f.cycle('.92');self.assert_sell(f)
            self.assertEqual(f.journal.db.execute('SELECT buys FROM markets').fetchone()[0],1)
        self.each_side(run)
    def test_partial_sell_fill_never_reports_a_complete_exit_or_resubmits(self):
        def run(f):
            f.cycle('.86');sell=f.venue.sells()[0];f.venue.fill(sell,'2.75');f.cycle('.90')
            self.assertEqual(f.journal.position(),{(TICKER,f.side):D('7.25')});self.assertEqual(len(f.venue.sells()),1)
            self.assertEqual(D(f.journal.active()['filled']),D('2.75'))
        self.each_side(run)
    def test_partial_sell_cancel_then_remaining_quantity_only(self):
        def run(f):
            f.cycle('.86');sell=f.venue.sells()[0];f.venue.fill(sell,'2.75');f.venue.confirm_cancel(sell)
            f.cycle('.92');self.assert_sell(f,'7.25');self.assertEqual(len(f.venue.sells()),2)
            f.venue.fill(f.venue.sells()[-1],'7.25');f.cycle('.92');self.assertEqual(f.journal.position(),{})
            self.assertEqual(f.venue.sold,10)
        self.each_side(run)
    def test_successful_fill_requires_exchange_fill_and_position_confirmation(self):
        def run(f):
            f.cycle('.86');self.assertEqual(f.venue.position,10)
            f.venue.fill(f.venue.sells()[0],10);f.cycle('.92')
            self.assertEqual(f.journal.position(),{});self.assertEqual(f.venue.sold,10)
        self.each_side(run)
    def test_delayed_ack_and_hidden_order_recover_with_same_client_id(self):
        def run(f):
            f.venue.next_error='timeout_after'
            with self.assertRaises(APIError):f.cycle('.86')
            cid=f.journal.active()['client_id'];f.venue.hide.add(f.venue.sells()[0]['order_id'])
            f.restart()
            with self.assertRaises(RuntimeError):f.cycle('.92')
            self.assertEqual(f.journal.active()['client_id'],cid)
            f.venue.hide.clear();f.cycle('.92');self.assertEqual(len(f.venue.sells()),1)
            self.assertEqual(f.venue.attempts[-1]['client_order_id'],cid)
        self.each_side(run)
    def test_timeout_before_receipt_is_blocked_until_authoritative_reconciliation(self):
        def run(f):
            f.venue.next_error='timeout_before'
            with self.assertRaises(APIError):f.cycle('.86')
            cid=f.journal.active()['client_id'];attempts=len(f.venue.attempts)
            for _ in range(3):
                with self.assertRaises(RuntimeError):f.cycle('.92')
            f.restart()
            with self.assertRaises(RuntimeError):f.cycle('.92')
            self.assertEqual(len(f.venue.attempts),attempts);self.assertEqual(f.venue.sells(),[])
            self.assertEqual(f.journal.active()['client_id'],cid);self.assertTrue(f.stop())
        self.each_side(run)
    def test_definitive_rate_limit_rejection_does_not_leave_ambiguous_intent(self):
        def run(f):
            f.venue.next_error='rate_limit'
            with self.assertRaises(APIError):f.cycle('.86')
            old=f.journal.db.execute('SELECT client_id FROM orders ORDER BY rowid DESC LIMIT 1').fetchone()[0]
            self.assertIsNone(f.journal.active());self.assertTrue(f.stop())
            f.cycle('.92');self.assert_sell(f)
            self.assertNotEqual(f.journal.active()['client_id'],old)
        self.each_side(run)
    def test_account_network_loss_preserves_latched_stop_and_retries_after_recovery(self):
        def run(f):
            persist_crossing(f.path,TICKER,f.side,D('.86'),observed_at=time.time())
            for status in ('offline',429,503):
                f.venue.read_error=status
                with self.assertRaises(APIError):f.cycle('.92')
                self.assertTrue(f.stop());self.assertEqual(f.venue.sells(),[])
            f.venue.read_error=None;f.cycle('.92');self.assert_sell(f)
        self.each_side(run)
    def test_latched_stop_submits_during_missing_or_interrupted_book(self):
        def run(f):
            persist_crossing(f.path,TICKER,f.side,D('.86'),observed_at=time.time());f.feed.fresh=False
            f.cycle();self.assert_sell(f)
        self.each_side(run)
    def test_unobserved_crossing_is_not_invented_during_a_disconnection(self):
        def run(f):
            f.feed.set('.80');f.feed.fresh=False;f.cycle()
            self.assertFalse(f.stop());self.assertEqual(f.venue.sells(),[])
            f.feed.fresh=True;f.cycle();self.assert_sell(f)
        self.each_side(run)
    def test_stale_book_cannot_trigger_new_stop_until_resnapshot(self):
        def run(f):
            feed=BookFeed(None,'/unused',None);feed.ticker=TICKER;feed.book=Book(TICKER);feed.connected=True
            bids={'yes':'.86' if f.side=='yes' else '.02','no':'.86' if f.side=='no' else '.02'}
            feed.book.consume({'type':'orderbook_snapshot','sid':1,'seq':1,'msg':{'market_ticker':TICKER,**{s+'_dollars_fp':[[p,'10']] for s,p in bids.items()}}})
            feed.heartbeat=time.monotonic()-6
            f.cycle(feed=feed);self.assertFalse(f.stop());self.assertEqual(f.venue.sells(),[])
            feed.heartbeat=time.monotonic();f.cycle(feed=feed);self.assert_sell(f)
        self.each_side(run)
    def test_stream_crossing_is_persisted_while_account_reconciliation_waits(self):
        def run(f):
            entered=threading.Event();release=threading.Event();errors=[]
            def persist():
                try:
                    if not entered.wait(2):raise AssertionError('Read did not block')
                    persist_crossing(f.path,TICKER,f.side,D('.85'),observed_at=time.time());f.feed.set('.92')
                except Exception as exc:errors.append(exc)
                finally:release.set()
            def delayed(path):
                if path=='/portfolio/orders':entered.set();assert release.wait(2)
            f.venue.before_pages=delayed;thread=threading.Thread(target=persist);thread.start()
            try:f.cycle();self.assert_sell(f);self.assertTrue(f.stop())
            finally:release.set();thread.join(timeout=2)
            self.assertEqual(errors,[])
        self.each_side(run)
    def test_expiration_keeps_unfilled_sell_distinct_from_exit_and_then_settlement(self):
        def run(f):
            f.cycle('.86');f.venue.now=f.venue.close;f.venue.closed=True
            f.cycle('.80');self.assertEqual(f.venue.sold,0);self.assertEqual(f.venue.position,10)
            self.assertEqual(len(f.venue.sells()),1)
            f.venue.sells()[0]['status']='expired';f.venue.position=D(0)
            f.venue.settlements=[{'ticker':TICKER,'market_result':f.side,'revenue':1000}]
            f.cycle('.80');self.assertEqual(f.journal.position(),{});self.assertEqual(f.venue.sold,0)
        self.each_side(run)
    def test_expired_ambiguous_sell_requires_reconciliation_not_replacement(self):
        def run(f):
            f.venue.next_error='timeout_before'
            with self.assertRaises(APIError):f.cycle('.86')
            before=len(f.venue.attempts);f.venue.now=f.venue.close
            with self.assertRaises(RuntimeError):f.cycle('.92')
            self.assertEqual(len(f.venue.attempts),before);self.assertEqual(f.venue.sold,0)
        self.each_side(run)
    def test_protective_exit_precedes_any_qualifying_opposite_entry(self):
        def run(f):
            f.journal.running=True;f.cycle('.02');self.assert_sell(f)
            self.assertEqual(len(f.venue.orders),2)
        self.each_side(run)
    def test_arm_durably_records_current_low_bid_before_an_immediate_rebound(self):
        def run(f):
            feed=BookFeed(None,'/unused',f.path);feed.risk_sides=set();feed.ticker=TICKER;feed.book=Book(TICKER);feed.connected=True
            bids={'yes':'.85' if f.side=='yes' else '.02','no':'.85' if f.side=='no' else '.02'}
            feed.book.consume({'type':'orderbook_snapshot','sid':1,'seq':1,'msg':{'market_ticker':TICKER,**{s+'_dollars_fp':[[p,'10']] for s,p in bids.items()}}});feed.heartbeat=time.monotonic()
            feed.arm(f.side)
            self.assertTrue(f.stop(),'Arming currently only hides the quote; a rebound can lose this crossing')
            f.cycle('.92');self.assert_sell(f)
        self.each_side(run)

    def test_exit_diagnostics_distinguish_submission_acceptance_partial_fill_and_completion(self):
        from kalshi_bot.protective import exit_state
        def run(f):
            self.assertEqual(exit_state(f.journal,TICKER)['state'],'not_triggered')
            persist_crossing(f.path,TICKER,f.side,D('.86'),observed_at=time.time())
            self.assertEqual(exit_state(f.journal,TICKER)['state'],'triggered')
            f.cycle('.92');self.assertEqual(exit_state(f.journal,TICKER)['state'],'acknowledged')
            f.cycle('.92');state=exit_state(f.journal,TICKER)
            self.assertEqual(state['state'],'accepted');self.assertFalse(state['completed'])
            sell=f.venue.sells()[0];f.venue.fill(sell,'2.75');f.cycle('.92')
            state=exit_state(f.journal,TICKER);self.assertEqual(state['state'],'partially_filled')
            self.assertEqual(D(state['unsold_qty']),D('7.25'));self.assertFalse(state['completed'])
            f.venue.fill(sell,'7.25');result=f.cycle('.92')
            self.assertEqual(result['protective_exit']['state'],'completed')
            self.assertTrue(result['protective_exit']['completed'])
        self.each_side(run)
    def test_settlement_does_not_misreport_a_completed_protective_sale(self):
        from kalshi_bot.protective import exit_state
        def run(f):
            f.cycle('.86');f.venue.sells()[0]['status']='expired';f.venue.position=D(0);f.venue.closed=True
            f.venue.now=f.venue.close;f.venue.settlements=[{'ticker':TICKER,'market_result':f.side,'revenue':1000}]
            f.cycle('.80');state=exit_state(f.journal,TICKER)
            self.assertEqual(state['state'],'settled_without_full_exit');self.assertFalse(state['completed'])
            self.assertEqual(D(state['sold_qty']),0)
        self.each_side(run)
    def test_rejection_and_uncertainty_are_distinct_in_diagnostics(self):
        from kalshi_bot.protective import exit_state
        for error,expected in (('reject','rejected'),('timeout_after','submission_uncertain')):
            def run(f):
                f.venue.next_error=error
                with self.assertRaises(APIError):f.cycle('.86')
                self.assertEqual(exit_state(f.journal,TICKER)['state'],expected)
                event=f.journal.db.execute("SELECT event FROM audit WHERE event LIKE 'Decision failure:%' ORDER BY id DESC LIMIT 1").fetchone()[0]
                detail=json.loads(event[len('Decision failure: '):])
                self.assertEqual(detail['stage'],'Submitting protective sell');self.assertEqual(detail['side'],f.side)
                self.assertEqual(detail['outcome'],'rejected' if error=='reject' else 'submission uncertain')
            self.each_side(run)
    def test_uncertain_recovery_reports_waiting_without_a_new_submission(self):
        def run(f):
            f.venue.next_error='timeout_before'
            with self.assertRaises(APIError):f.cycle('.86')
            with self.assertRaises(RuntimeError):f.cycle('.92')
            event=f.journal.db.execute("SELECT event FROM audit WHERE event LIKE 'Decision failure:%' ORDER BY id DESC LIMIT 1").fetchone()[0]
            detail=json.loads(event[len('Decision failure: '):])
            self.assertEqual(detail['stage'],'Awaiting uncertain order reconciliation')
            self.assertEqual(detail['outcome'],'blocked before submission')
        self.each_side(run)
    def test_arm_does_not_treat_stale_prices_as_a_new_crossing(self):
        def run(f):
            feed=BookFeed(None,'/unused',f.path);feed.risk_sides=set();feed.ticker=TICKER;feed.book=Book(TICKER);feed.connected=True
            feed.book.consume({'type':'orderbook_snapshot','sid':1,'seq':1,'msg':{'market_ticker':TICKER,'yes_dollars_fp':[['.80','10']],'no_dollars_fp':[['.80','10']]}})
            feed.heartbeat=time.monotonic()-6;feed.arm(f.side);self.assertFalse(f.stop())
        self.each_side(run)
    def test_failed_arm_persistence_prevents_new_buy_http_submission(self):
        for side in ('yes','no'):
            with self.subTest(side=side),tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'db';journal=Journal(path,'offline');journal.reconcile([],[],[]);journal.running=True
                feed=BookFeed(None,'/unused',path);feed.risk_sides=set();feed.ticker=TICKER;feed.book=Book(TICKER);feed.connected=True
                feed.book.consume({'type':'orderbook_snapshot','sid':1,'seq':1,'msg':{'market_ticker':TICKER,'yes_dollars_fp':[['.80','10']],'no_dollars_fp':[['.80','10']]}});feed.heartbeat=time.monotonic()
                journal.on_intent=lambda ticker,side:feed.arm(side);broker=Mock()
                try:
                    with patch('kalshi_bot.streams.sqlite3.connect',side_effect=sqlite3.OperationalError('synthetic lock')):
                        with self.assertRaises(sqlite3.OperationalError):journal.submit(broker,TICKER,'buy',side,D('.98'),1,900,800)
                    broker.create.assert_not_called();self.assertIsNone(journal.active())
                    self.assertEqual(journal.db.execute('SELECT status FROM orders').fetchone()[0],'rejected')
                finally:journal.close()

    def test_unheld_low_bid_does_not_latch_stream_stop(self):
        def run(f):
            feed=BookFeed(None,'/unused',f.path);feed.risk_sides={f.side};feed.ticker=TICKER;feed.generation=1
            bids={f.side:'.97',('no' if f.side=='yes' else 'yes'):'.02'}
            frame={'type':'orderbook_snapshot','sid':1,'seq':1,'msg':{'market_ticker':TICKER,**{s+'_dollars_fp':[[p,'10']] for s,p in bids.items()}}}
            response=Mock(status=200);response.readline.side_effect=[json.dumps(frame).encode(),b'']
            connection=Mock();connection.getresponse.return_value=response
            feed.on_update=lambda:setattr(feed,'closed',True)
            with patch('kalshi_bot.streams.UnixHTTP',return_value=connection),patch('kalshi_bot.streams.ws_headers',return_value={}):feed._run(TICKER,1)
            self.assertFalse(f.stop());self.assertEqual(f.venue.sells(),[])
        self.each_side(run)
    def test_delayed_sell_ack_keeps_durable_pending_intent_and_one_submission(self):
        def run(f):
            entered=threading.Event();release=threading.Event();errors=[];create=f.venue.create
            def delayed(payload):
                reply=create(payload);entered.set();assert release.wait(2);return reply
            def observe():
                try:
                    assert entered.wait(2)
                    db=sqlite3.connect(f.path.as_uri()+'?mode=ro',uri=True)
                    try:
                        status,oid=db.execute("SELECT status,exchange_id FROM orders WHERE action='sell'").fetchone()
                        assert status=='pending' and oid is None
                        assert len(f.venue.sells())==1
                        f.feed.set('.92')
                    finally:db.close()
                except Exception as exc:errors.append(exc)
                finally:release.set()
            f.venue.create=delayed;thread=threading.Thread(target=observe);thread.start()
            try:f.cycle('.86')
            finally:release.set();thread.join(timeout=2)
            self.assertEqual(errors,[]);f.venue.create=create;f.cycle('.92')
            self.assertEqual(len(f.venue.sells()),1);self.assertEqual(f.venue.sold,0)
        self.each_side(run)
    def test_delayed_position_projection_after_partial_sell_blocks_new_writes(self):
        def run(f):
            f.cycle('.86');f.venue.fill(f.venue.sells()[0],'2.75');pages=f.venue.pages
            def stale(path,params=None):
                if path=='/portfolio/positions':return [{'ticker':TICKER,'position_fp':'10' if f.side=='yes' else '-10'}]
                return pages(path,params)
            f.venue.pages=stale
            with self.assertRaises(RuntimeError):f.cycle('.92')
            self.assertFalse(f.journal.ready);self.assertEqual(len(f.venue.sells()),1)
            f.venue.pages=pages;f.cycle('.92');self.assertEqual(f.journal.position(),{(TICKER,f.side):D('7.25')})
            self.assertEqual(len(f.venue.sells()),1)
        self.each_side(run)
    def test_manual_oversized_or_wrong_side_sell_is_rejected_before_network(self):
        def run(f):
            f.journal.latch_stop(TICKER,f.side,D('.86'));before=len(f.venue.attempts)
            for side,quantity in ((f.side,11),(('no' if f.side=='yes' else 'yes'),10)):
                with self.assertRaises(RuntimeError):f.journal.submit(f.venue,TICKER,'sell',side,D('.86'),quantity,f.venue.close,f.venue.now)
            self.assertEqual(len(f.venue.attempts),before)
        self.each_side(run)
    def test_real_broker_honors_429_backoff_without_a_second_http_attempt(self):
        from kalshi_bot.execution import order_payload
        from kalshi_bot.trade_api import OrderBroker
        signer=Mock();signer.write_headers.return_value={};broker=OrderBroker(signer,'/synthetic-only',True)
        payload=order_payload(TICKER,'sell','no',D('.86'),1,int(time.time())+90,'synthetic-rate-limit')
        response=Mock(status=429);response.read.return_value=b'{}';response.getheader.return_value='2'
        connection=Mock();connection.getresponse.return_value=response
        with patch('kalshi_bot.trade_api.UnixHTTP',return_value=connection) as transport:
            for _ in range(5):
                with self.assertRaises(APIError) as error:broker.create(payload)
                self.assertEqual(error.exception.status,429)
            self.assertEqual(transport.call_count,1)
            self.assertGreater(broker.next_write,time.monotonic()+1)

    def test_actual_process_crash_after_synthetic_acceptance_recovers_without_resubmission(self):
        def run(f):
            remote=f.path.parent/'remote.json';accepted=f.path.parent/'accepted.json'
            remote.write_text(json.dumps([f.venue.pages(p) for p in ('/portfolio/orders','/portfolio/fills','/portfolio/positions')]))
            f.close()
            script="""import json,os,sys
from pathlib import Path
from decimal import Decimal as D
from kalshi_bot.execution import Journal
path,remote,accepted,side,close,now=sys.argv[1:]
from kalshi_bot.strategy import set_policy
set_policy(92,86)
journal=Journal(path,'offline-protective')
journal.reconcile(*json.loads(Path(remote).read_text()))
journal.latch_stop('KXBTC15M-PROTECT',side,D('.86'))
class CrashBroker:
    def create(self,payload):
        with open(accepted,'w') as stream:
            json.dump(payload,stream);stream.flush();os.fsync(stream.fileno())
        os._exit(17)
journal.submit(CrashBroker(),'KXBTC15M-PROTECT','sell',side,D('.86'),10,int(close),int(now))
"""
            process=subprocess.run([sys.executable,'-B','-c',script,str(f.path),str(remote),str(accepted),f.side,str(f.venue.close),str(f.venue.now)],capture_output=True,text=True,timeout=10)
            self.assertEqual(process.returncode,17)
            f.journal=Journal(f.path,'offline-protective');self.assertTrue(f.stop())
            self.assertIsNone(f.journal.active()['exchange_id'])
            payload=json.loads(accepted.read_text());f.venue.create(payload) # Restore synthetic exchange acceptance evidence.
            before=len(f.venue.attempts);f.cycle('.92')
            self.assertEqual(len(f.venue.attempts),before);self.assert_sell(f)
            self.assertEqual(f.journal.active()['client_id'],payload['client_order_id'])
        self.each_side(run)
    def test_cutoff_crossed_during_reconciliation_prevents_a_late_sell(self):
        def run(f):
            wall=time.time();f.venue.close=wall+10;clock=[wall]
            persist_crossing(f.path,TICKER,f.side,D('.86'),observed_at=wall)
            def delay(path):
                if path=='/portfolio/orders':clock[0]=wall+12
            f.venue.before_pages=delay
            with patch('kalshi_bot.trader.time.time',side_effect=lambda:clock[0]):
                result=cycle(f.journal,f.venue,f.venue,D('.03'),book_feed=f.feed)
            self.assertIn('Trading closed',result['status']);self.assertTrue(f.stop())
            self.assertEqual(f.venue.sells(),[]);self.assertEqual(f.venue.position,10)
        self.each_side(run)

    def test_second_same_side_buy_rearms_a_current_crossing_without_clearing_the_feed(self):
        def run(f):
            f.cycle('.86');f.venue.fill(f.venue.sells()[0],10);f.cycle('.92')
            self.assertEqual(f.journal.position(),{})
            feed=BookFeed(None,'/unused',f.path);feed.risk_sides={f.side};feed.ticker=TICKER;feed.book=Book(TICKER);feed.connected=True
            bids={f.side:'.85',('no' if f.side=='yes' else 'yes'):'.02'}
            feed.book.consume({'type':'orderbook_snapshot','sid':1,'seq':1,'msg':{'market_ticker':TICKER,**{s+'_dollars_fp':[[p,'10']] for s,p in bids.items()}}});feed.heartbeat=time.monotonic()
            f.journal.on_intent=lambda ticker,side:feed.arm(side)
            f.journal.running=True;f.journal.submit(f.venue,TICKER,'buy',f.side,D('.98'),10,f.venue.close,f.venue.now)
            self.assertTrue(f.stop());f.venue.fill(f.venue.orders[-1],10);f.journal.running=False
            f.cycle('.92');self.assert_sell(f);self.assertEqual(len(f.venue.sells()),2)
            self.assertEqual(f.journal.db.execute('SELECT buys FROM markets').fetchone()[0],2)
        self.each_side(run)

    def test_stop_cancels_unfilled_pending_buy_even_when_entries_are_enabled(self):
        def run(f):
            f.journal.running=True
            persist_crossing(f.path,TICKER,f.side,D('.86'),observed_at=time.time())
            f.cycle('.86')
            self.assertTrue(f.venue.cancellations,'Latched stop must cancel pending entry risk, including a lagging zero-fill projection')
            self.assertEqual(f.venue.sells(),[]);self.assertEqual(f.venue.position,0)
        self.each_side(run,fill='0')

    def test_worker_wakes_for_latched_stop_on_zero_fill_pending_buy(self):
        from kalshi_bot.desktop_worker import _run_legacy_trading as run_trading
        for side in ('yes','no'):
            with self.subTest(side=side),tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'desktop-trading.sqlite3';journal=Journal(path,'demo:synthetic')
                venue=ProtectiveVenue();journal.reconcile([],[],[]);journal.running=True
                journal.submit(venue,TICKER,'buy',side,D('.98'),10,venue.close,venue.now)
                persist_crossing(path,TICKER,side,D('.86'),observed_at=time.time());journal.close()
                with patch('kalshi_bot.credentials.load') as load,patch('kalshi_bot.trade_api.OrderBroker'),\
                     patch('kalshi_bot.streams.BookHub') as hub,patch('kalshi_bot.desktop_worker.MarketMonitor'),\
                     patch('kalshi_bot.trader.cycle',return_value={'ticker':TICKER,'cutoff':venue.close,'status':'Synthetic pending buy'}),\
                     patch('kalshi_bot.desktop_worker.emit'),\
                     patch('kalshi_bot.desktop_worker.CommandPipe.poll',return_value=[{'command':'quit'}]) as controls:
                    load.return_value.fingerprint='synthetic';hub.return_value.quote.return_value=None
                    run_trading(Path(directory),None,False,'demo')
                    self.assertEqual(controls.call_args.args[0],0,'A persisted stop must bypass the ordinary three-second idle wait')

    def test_randomized_yes_sequences(self):self.randomized('yes')
    def test_randomized_no_sequences(self):self.randomized('no')
    def randomized(self,side):
        for seed in range(int(os.environ.get('KALSHI_TEST_SEED_OFFSET','0')),int(os.environ.get('KALSHI_TEST_SEED_OFFSET','0'))+50):
            with self.subTest(side=side,seed=seed),tempfile.TemporaryDirectory() as directory:
                f=Fixture(directory,side);rng=random.Random(seed)
                try:
                    for _ in range(100):
                        choice=rng.randrange(10);f.feed.fresh=True;f.venue.read_error=None;f.venue.hide.clear()
                        if choice==0:f.restart()
                        elif choice==1:f.feed.fresh=False
                        elif choice==2:f.venue.read_error=rng.choice(['offline',429,503])
                        elif choice==3 and f.journal.active() and f.journal.active()['action']=='sell' and f.venue.sells():
                            f.venue.hide.add(f.venue.sells()[-1]['order_id'])
                        elif choice==4 and not f.journal.active() and f.venue.position:
                            f.venue.next_error=rng.choice(['reject','timeout_before','timeout_after','rate_limit'])
                        elif choice==5 and f.venue.sells() and f.venue.sells()[-1]['status']=='resting':
                            f.venue.confirm_cancel(f.venue.sells()[-1])
                        elif choice==6 and f.venue.sells() and f.venue.sells()[-1]['status']=='resting':
                            order=f.venue.sells()[-1];n=min(f.venue.position,D(order['remaining_count_fp']),D(rng.choice(['.25','1','2.75'])))
                            if n:f.venue.fill(order,n)
                        bid=rng.choice(['.98','.95','.92','.90','.87','.8601','.86','.85','.80'])
                        try:f.cycle(bid)
                        except APIError as exc:self.assertIn(exc.status,(0,400,429,503))
                        except RuntimeError as exc:self.assertTrue(str(exc).startswith(('Known order absent','Unconfirmed order absent')),str(exc))
                        self.assertGreaterEqual(f.venue.position,0);self.assertLessEqual(f.venue.sold,f.venue.bought)
                        self.assertLessEqual(f.venue.max_live_sells,1)
                        self.assertEqual(f.journal.db.execute('SELECT buys FROM markets').fetchone()[0],1)
                    f.venue.read_error=None;f.venue.hide.clear();f.venue.next_error=None;f.feed.fresh=True
                    persist_crossing(f.path,TICKER,side,D('.86'),observed_at=time.time())
                    active=f.journal.active()
                    if active and not active['exchange_id'] and not any(o['client_order_id']==active['client_id'] for o in f.venue.orders):
                        attempts=len(f.venue.attempts)
                        with self.assertRaises(RuntimeError):f.cycle('.92')
                        self.assertEqual(len(f.venue.attempts),attempts)
                        self.assertTrue(f.stop());continue # Absence cannot prove the original request failed.
                    for _ in range(8):
                        try:f.cycle('.92')
                        except (APIError,RuntimeError):continue
                        if not f.venue.position:break
                        if f.venue.sells() and f.venue.sells()[-1]['status']=='resting':f.venue.fill(f.venue.sells()[-1],f.venue.position)
                    self.assertEqual(f.venue.sold,f.venue.bought);self.assertEqual(f.venue.position,0)
                finally:f.close()


if __name__=='__main__':unittest.main()
