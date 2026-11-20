"""Independent strategy assessment cadence and preserved execution boundary."""
import copy
import sqlite3
import tempfile
import threading
import time
import unittest
from strategy_fixture import configure_policy

def setUpModule():
    configure_policy(92,71)

from decimal import Decimal as D
from pathlib import Path
from kalshi_bot.decisions import assess,read_context,DecisionLoop
from kalshi_bot.execution import Journal
from test_trader import Venue
from test_missed_no import FreshFeed
from test_protective_sells import Fixture,TICKER


def context(now):
    return {'held':{},'closes':{},'active':[],'markets':{},'account_at':now,'previous_side':None}


def quote(side,ask):
    other='no' if side=='yes' else 'yes';p=D(ask)
    return {side+'_ask':p,side+'_bid':p-D('.01'),other+'_ask':1-p+D('.01'),other+'_bid':1-p}


class DecisionTests(unittest.TestCase):
    def setUp(self):self.market=Venue().market();self.now=Venue().close-90;self.ctx=context(self.now)
    def decide(self,prices=None,**kw):
        return assess(kw.get('context',self.ctx),kw.get('market',self.market),prices,kw.get('risk',prices),
                      kw.get('running',True),kw.get('phase','Checking executable prices'),kw.get('now',self.now))
    def test_equal_yes_no_eligibility_all_limits_and_decimals(self):
        for side in ('yes','no'):
            for ask,limit in (('.92','.92'),('.924','.93'),('.93','.93'),('.94','.94'),('.95','.95'),('.96','.96'),('.97','.97'),('.98','.98'),('.984','.99'),('.99','.99')):
                with self.subTest(side=side,ask=ask):
                    r=self.decide(quote(side,ask));self.assertEqual(r['action'],'entry_candidate')
                    self.assertEqual(r['side'],side);self.assertEqual(D(r['limit']),D(limit))
                    self.assertTrue(r['requires_fresh_execution_checks'])
    def test_outside_prices_stale_and_missing_market_block_candidates(self):
        for side in ('yes','no'):
            for p in ('.9199','.9901','1'):
                self.assertNotIn(self.decide(quote(side,p))['action'],('entry_candidate','protective_exit_required'))
        self.assertEqual(self.decide(None)['action'],'stale')
        self.assertEqual(self.decide(quote('no','.98'),market=None)['action'],'no_market')
    def test_entry_window_cutoff_and_pause(self):
        p=quote('no','.98');close=Venue().close
        self.assertEqual(self.decide(p,now=close-240)['action'],'await_account')
        self.assertEqual(self.decide(p,now=close-241)['action'],'outside_window')
        self.assertEqual(self.decide(p,now=close)['action'],'closed')
        self.assertEqual(self.decide(p,running=False)['action'],'paused')
    def test_unlimited_buys_pending_orders_and_opposing_positions(self):
        p=quote('no','.98');ctx=copy.deepcopy(self.ctx);ctx['markets'][self.market['ticker']]={'buys':2,'stop':False}
        for count in (2,3,100):
            ctx['markets'][self.market['ticker']]['buys']=count
            self.assertEqual(self.decide(p,context=ctx)['action'],'entry_candidate')
        ctx=copy.deepcopy(self.ctx);ctx['active']=[{'ticker':self.market['ticker'],'action':'buy','side':'yes','status':'pending','cutoff':Venue().close}]
        self.assertEqual(self.decide(p,context=ctx,risk=quote('yes','.98'))['action'],'pending_order')
        ctx['held']={(self.market['ticker'],'yes'):D(13)}
        self.assertEqual(self.decide(p,context=ctx,risk=quote('yes','.98'))['action'],'hold')
        ctx['held'][(self.market['ticker'],'no')]=D(1)
        self.assertEqual(self.decide(p,context=ctx)['action'],'blocked')
    def test_stop_priority_all_shares_gap_and_latched_rebound_both_sides(self):
        for side in ('yes','no'):
            ctx=copy.deepcopy(self.ctx);t=self.market['ticker'];ctx['held']={(t,side):D('9.75')};ctx['closes'][t]=Venue().close
            for bid in ('.71','.7099','.60'):
                prices=quote(side,'.98');prices[side+'_bid']=D(bid)
                r=self.decide(prices,context=ctx,running=False)
                self.assertEqual(r['action'],'protective_exit_required');self.assertEqual(r['side'],side)
                self.assertEqual(D(r['sell_quantity']),D('9.75'))
            prices=quote(side,'.98');prices[side+'_bid']=D('.7101')
            self.assertEqual(self.decide(prices,context=ctx)['action'],'hold')
            ctx['markets'][t]={'buys':1,'stop':True};prices[side+'_bid']=D('.92')
            self.assertEqual(self.decide(prices,context=ctx)['action'],'protective_exit_required')
            ctx['active']=[{'ticker':t,'action':'buy','side':side,'cutoff':Venue().close}]
            self.assertEqual(self.decide(prices,context=ctx)['action'],'cancel_buy_required')
            ctx['active'][0]['action']='sell'
            self.assertEqual(self.decide(None,context=ctx)['action'],'maintain_exit')
    def test_stale_account_or_execution_error_never_authorizes_submission(self):
        p=quote('no','.98')
        for at in (None,self.now-2,self.now+1):
            ctx=copy.deepcopy(self.ctx);ctx['account_at']=at
            self.assertEqual(self.decide(p,context=ctx)['action'],'await_account')
        self.assertEqual(self.decide(p,phase='Blocked: API unavailable')['action'],'await_account')
    def test_context_reads_committed_partial_fills_without_write_access(self):
        for side in ('yes','no'):
            with tempfile.TemporaryDirectory() as directory:
                f=Fixture(directory,side,fill='13',count='13')
                try:
                    f.cycle('.70');sell=f.venue.sells()[0];f.venue.fill(sell,'3.25');f.reconcile()
                    db=sqlite3.connect(f.path.as_uri()+'?mode=ro',uri=True);db.execute('PRAGMA query_only=ON')
                    try:
                        v=read_context(db,TICKER);self.assertEqual(v['held'],{(TICKER,side):D('9.75')})
                        self.assertTrue(v['markets'][TICKER]['stop']);self.assertEqual(len(v['active']),1)
                        self.assertFalse(db.in_transaction)
                        with self.assertRaises(sqlite3.OperationalError):db.execute('DELETE FROM orders')
                        db.rollback()
                    finally:db.close()
                finally:f.close()
    def test_quarter_second_checks_continue_during_slow_io_and_wake_executor(self):
        with tempfile.TemporaryDirectory() as directory:
            j=Journal(Path(directory)/'db','offline');j.reconcile([],[],[])
            market=Venue().market();market['close_time']=__import__('datetime').datetime.fromtimestamp(time.time()+100,__import__('datetime').timezone.utc).isoformat()
            observed=[];release=threading.Event();blocked=threading.Event()
            def slow():blocked.set();release.wait(3)
            thread=threading.Thread(target=slow);thread.start()
            loop=None
            def updated():observed.append(loop.snapshot())
            loop=DecisionLoop(Path(directory)/'db',lambda:(market,True,'Reconciling account'),FreshFeed(),updated)
            try:
                self.assertTrue(blocked.wait(.5));loop.start();time.sleep(1.6)
                self.assertGreaterEqual(len(observed),6)
                self.assertTrue(all(not r['execution_authorized'] for r in observed))
                self.assertTrue(all(r['action'] in ('entry_candidate','await_account') for r in observed))
                self.assertLess(max(r['max_interval_ms'] for r in observed),500)
                self.assertFalse(release.is_set())
            finally:loop.close();release.set();thread.join(timeout=2);j.close()
    def test_missing_journal_fails_closed_and_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'missing';loop=DecisionLoop(path,lambda:(None,False,''),FreshFeed(),lambda:None)
            try:
                loop.start();time.sleep(.3)
                self.assertEqual(loop.snapshot()['action'],'unavailable');self.assertFalse(path.exists())
            finally:loop.close()


if __name__=='__main__':unittest.main()
