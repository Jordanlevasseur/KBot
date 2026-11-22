"""Prepared-state fast path: temporary journals and synthetic exchange only."""
import copy
from decimal import Decimal as D
import sqlite3
import tempfile
import threading
import unittest
from strategy_fixture import configure_policy

def setUpModule():
    configure_policy(92,71)

from pathlib import Path
from unittest.mock import patch
from kalshi_bot.api import APIError
from kalshi_bot.execution import Journal
from kalshi_bot.prepared import Inputs,PreparedEngine,CASH_TTL,ACCOUNT_TTL,MARKET_TTL,FEES_TTL
from kalshi_bot.trade_api import OrderNotSent
from test_protective_sells import ProtectiveVenue,Quotes,TICKER


class Clock:
    def __init__(self):self.now=100.
    def __call__(self):return self.now


class Fixture:
    def __init__(self,directory,cash=600):
        self.clock=Clock();self.inputs=Inputs(self.clock);self.venue=ProtectiveVenue();self.feed=Quotes('yes')
        self.feed.prices={'yes_bid':D('.49'),'no_bid':D('.49'),'yes_ask':D('.51'),'no_ask':D('.51')}
        self.market=self.venue.market();self.cash=cash
        self.journal=Journal(Path(directory)/'db','prepared-offline');self.journal.running=True
        self.engine=PreparedEngine(self.journal,self.inputs,self.feed,self.venue,clock=self.clock)
        self.inputs.set_meta('market',self.market,self.clock())
        self.inputs.set_meta('exchange',{'trading_active':True},self.clock())
        self.inputs.set_meta('fees',((TICKER,TICKER),D('.03')),self.clock())
        self.publish()
    def publish(self,cash=None):
        if cash is not None:self.cash=cash
        self.inputs.set_cash(self.inputs.epoch(),self.market,self.cash,self.clock())
        data=[self.venue.pages(path) for path in ('/portfolio/orders','/portfolio/fills','/portfolio/positions','/portfolio/settlements')]
        self.inputs.set_account(self.inputs.epoch(),data,self.clock());self.engine.apply()
        self.inputs.set_cash(self.inputs.epoch(),self.market,self.cash,self.clock())
        # A changed cash value requires another asynchronously confirmed account cohort.
        if self.inputs.view()['confirmed'] is None:
            self.inputs.set_account(self.inputs.epoch(),data,self.clock());self.engine.apply()
        self.engine.build(self.market)
    def qualify(self,side='yes',ask='.98'):
        other='no' if side=='yes' else 'yes';p=D(ask)
        self.feed.prices={side+'_ask':p,side+'_bid':p-D('.01'),other+'_ask':1-p+D('.01'),other+'_bid':1-p}
    def execute(self):return self.engine.execute(self.market,now=self.venue.now)
    def close(self):self.journal.close()


class PreparedTests(unittest.TestCase):
    def each(self,fn,cash=600):
        with tempfile.TemporaryDirectory() as directory:
            f=Fixture(directory,cash)
            try:fn(f)
            finally:f.close()
    def test_quantities_and_all_ten_templates_exist_before_eligible_price(self):
        def run(f):
            self.assertEqual(len(f.engine.plans),16)
            for plan in f.engine.plans.values():
                self.assertEqual(plan.quantity,3);self.assertLessEqual(plan.reserve_cents,600)
                self.assertEqual(plan.generation,f.inputs.epoch())
            self.assertTrue(f.engine.status(f.market)['ready'])
            self.assertEqual(f.journal.db.execute("SELECT status FROM prepared_entry").fetchone()[0],'ready')
            self.assertEqual(f.venue.attempts,[])
        self.each(run)
    def test_both_sides_decimal_asks_use_prepared_count_without_get_reconcile_or_sizing(self):
        for side in ('yes','no'):
            for ask,limit in (('.92','.92'),('.924','.93'),('.93','.93'),('.94','.94'),('.95','.95'),('.96','.96'),('.97','.97'),('.98','.98'),('.984','.99'),('.99','.99')):
                with self.subTest(side=side,ask=ask):
                    def run(f):
                        f.qualify(side,ask)
                        with patch('kalshi_bot.prepared.affordable',side_effect=AssertionError('Sizing on critical path')),                             patch.object(f.venue,'get',side_effect=AssertionError('GET on critical path')),                             patch.object(f.venue,'pages',side_effect=AssertionError('Portfolio on critical path')),                             patch.object(f.journal,'reconcile',side_effect=AssertionError('Reconcile on critical path')):
                            r=f.execute()
                        self.assertIn('submitted',r['status']);self.assertEqual(D(r['quantity']),3);self.assertEqual(D(r['limit']),D(limit))
                        self.assertEqual(len(f.venue.orders),1)
                    self.each(run)
    def test_general_cash_formula_and_fee_caps_precomputed(self):
        for cash,count in ((0,0),(199,0),(200,1),(600,3),(2687,13),(5340,26),(10000,50)):
            with self.subTest(cash=cash):
                def run(f):
                    self.assertEqual({p.quantity for p in f.engine.plans.values()},{D(count)} if count else set())
                self.each(run,cash)
    def test_cash_change_invalidates_old_quantity_until_async_refresh(self):
        def run(f):
            f.inputs.set_cash(f.inputs.epoch(),f.market,200,f.clock());f.qualify()
            f.execute();self.assertEqual(f.venue.attempts,[])
            f.publish(cash=200);f.execute();self.assertEqual(D(f.venue.orders[0]['initial_count_fp']),1)
        self.each(run)
    def test_stale_cash_account_market_and_fees_block_without_network_wait(self):
        for kind,age in (('cash',CASH_TTL),('confirmed',ACCOUNT_TTL),('market',MARKET_TTL),('fees',FEES_TTL),('exchange',MARKET_TTL)):
            with self.subTest(kind=kind):
                def run(f):
                    value=getattr(f.inputs,'account_confirmed' if kind=='confirmed' else kind)
                    if kind=='cash':value=(*value[:3],f.clock()-age-.01)
                    else:value=(*value[:-1],f.clock()-age-.01)
                    setattr(f.inputs,'account_confirmed' if kind=='confirmed' else kind,value)
                    f.qualify();f.execute();self.assertEqual(f.venue.attempts,[])
                self.each(run)
    def test_pending_order_cannot_reuse_reserved_cash_or_precomputed_id(self):
        def run(f):
            f.qualify();f.execute();first=f.venue.attempts[0]['client_order_id']
            for _ in range(10):f.execute()
            self.assertEqual(len(f.venue.attempts),1)
            row=f.journal.db.execute('SELECT client_id,cents,status FROM entry_reservations').fetchone()
            self.assertEqual(row['client_id'],first);self.assertEqual(row['cents'],303)
            self.assertEqual(row['status'],'pending_reconciliation')
        self.each(run)
    def test_reservation_is_committed_before_exchange_request(self):
        def run(f):
            create=f.venue.create
            def observed(body):
                path=Path(f.journal.db.execute('PRAGMA database_list').fetchone()[2]);db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
                try:
                    self.assertEqual(db.execute('SELECT cents FROM entry_reservations WHERE client_id=?',(body['client_order_id'],)).fetchone()[0],303)
                    self.assertEqual(db.execute('SELECT status FROM orders WHERE client_id=?',(body['client_order_id'],)).fetchone()[0],'pending')
                finally:db.close()
                return create(body)
            f.venue.create=observed;f.qualify();f.execute()
        self.each(run)
    def test_partial_fill_updates_reservation_and_protective_exit_uses_all_fills(self):
        for side in ('yes','no'):
            def run(f):
                f.qualify(side);f.execute();buy=f.venue.orders[0];f.venue.fill(buy,'1.25');f.publish()
                self.assertEqual(f.journal.db.execute('SELECT cents FROM entry_reservations').fetchone()[0],177)
                f.feed.prices[side+'_bid']=D('.70');f.execute();self.assertEqual(f.venue.sells(),[])
                f.venue.fill(buy,'.25');f.venue.confirm_cancel(buy);f.publish()
                f.execute();sell=f.venue.sells()[0];self.assertEqual(D(sell['initial_count_fp']),D('1.50'))
                f.venue.fill(sell,'1.50');f.publish();self.assertEqual(f.journal.position(),{})
            with self.subTest(side=side):self.each(run)
    def test_sell_lease_does_not_need_cash_refresh_and_latches_gap(self):
        def run(f):
            f.qualify();f.execute();f.venue.fill(f.venue.orders[0],3);f.publish()
            f.inputs.cash_uncertain();f.engine.build(f.market);f.feed.prices['yes_bid']=D('.70')
            f.execute();self.assertEqual(D(f.venue.sells()[0]['initial_count_fp']),3)
            self.assertEqual(D(f.venue.sells()[0]['yes_price_dollars']),D('.01'))
        self.each(run)
    def test_price_departure_pause_and_cutoff_do_not_send(self):
        for case in ('price','stale','pause','cutoff'):
            def run(f):
                f.qualify()
                if case=='price':f.qualify(ask='.919')
                elif case=='stale':f.feed.fresh=False
                elif case=='pause':f.journal.running=False
                else:f.venue.now=f.venue.close
                f.execute();self.assertEqual(f.venue.attempts,[])
            with self.subTest(case=case):self.each(run)
    def test_final_transmission_guard_detects_change_after_durable_intent(self):
        for case in ('price','generation','pause','fee','expiry'):
            def run(f):
                f.qualify()
                def change(ticker,side):
                    if case=='price':f.qualify(ask='.91')
                    elif case=='generation':f.inputs.invalidate('Asynchronous account change')
                    elif case=='fee':f.inputs.set_meta('fees',((TICKER,TICKER),D('.04')),f.clock())
                    elif case=='expiry':f.clock.now+=CASH_TTL+.1
                    else:f.journal.running=False
                f.engine.previous_intent=change
                with self.assertRaises(OrderNotSent):f.execute()
                self.assertEqual(f.venue.attempts,[]);self.assertEqual(f.journal.db.execute('SELECT status FROM orders').fetchone()[0],'rejected')
            with self.subTest(case=case):self.each(run)
    def test_rejection_refreshes_then_uses_new_id_without_consuming_buy(self):
        def run(f):
            f.qualify();f.venue.next_error='reject'
            with self.assertRaises(APIError):f.execute()
            cid=f.venue.attempts[0]['client_order_id'];f.publish();f.execute()
            self.assertNotEqual(cid,f.venue.attempts[-1]['client_order_id'])
            self.assertEqual(f.journal.db.execute('SELECT buys FROM markets').fetchone()[0],0)
        self.each(run)
    def test_uncertain_acceptance_absent_from_snapshot_never_reposts(self):
        def run(f):
            f.qualify();f.venue.next_error='timeout_after'
            with self.assertRaises(APIError):f.execute()
            f.venue.hide.add(f.venue.orders[0]['order_id'])
            for _ in range(3):
                with self.assertRaises(RuntimeError):f.publish()
                f.execute()
            self.assertEqual(len(f.venue.attempts),1)
        self.each(run)
    def test_market_scope_and_rollover_invalidate_old_templates(self):
        for case in ('ticker','scope','close'):
            def run(f):
                f.qualify();market=dict(f.market)
                if case=='ticker':market['ticker']='KXBTC15M-NEXT'
                elif case=='scope':market['exchange_index']=2
                else:market['close_time']='2020-01-01T00:00:00Z'
                f.engine.execute(market,now=f.venue.now);self.assertEqual(f.venue.attempts,[])
            with self.subTest(case=case):self.each(run)
    def test_quote_only_market_metadata_changes_do_not_invalidate_lease(self):
        def run(f):
            epoch=f.inputs.epoch();updated={**f.market,'last_price_dollars':'.984','volume_fp':'100'}
            f.inputs.set_meta('market',updated,f.clock());self.assertEqual(f.inputs.epoch(),epoch)
            f.qualify();f.execute();self.assertEqual(len(f.venue.orders),1)
        self.each(run)
    def test_single_use_claim_is_atomic_under_racing_consumers(self):
        def run(f):
            plan=f.engine.plans[('yes',D('.98'))];results=[]
            def claim():
                try:f.inputs.claim(plan);results.append(True)
                except RuntimeError:results.append(False)
            threads=[threading.Thread(target=claim) for _ in range(8)]
            for t in threads:t.start()
            for t in threads:t.join(timeout=1)
            self.assertEqual(results.count(True),1)
        self.each(run)
    def test_repeated_buys_require_complete_sale_and_third_is_prepared(self):
        def run(f):
            for side in ('yes','no'):
                f.qualify(side);f.execute();buy=f.venue.orders[-1];f.venue.fill(buy,3);f.publish()
                f.feed.prices[side+'_bid']=D('.70');f.execute();sell=f.venue.sells()[-1]
                f.venue.fill(sell,1);f.publish();f.qualify('no' if side=='yes' else 'yes');f.execute()
                self.assertEqual(len(f.venue.sells()),1 if side=='yes' else 2)
                f.venue.fill(sell,2);f.publish()
                self.assertEqual(f.journal.position(),{})
            f.qualify();f.execute()
            self.assertEqual(len(f.venue.attempts),5)
            self.assertEqual(f.venue.attempts[-1]['side'],'bid')
            self.assertEqual(f.journal.db.execute('SELECT buys FROM markets').fetchone()[0],2)
            f.venue.fill(f.venue.orders[-1],3);f.publish()
            self.assertEqual(f.journal.db.execute('SELECT buys FROM markets').fetchone()[0],3)
        self.each(run)
    def test_prepared_execution_is_standard_for_demo_and_live(self):
        from kalshi_bot.desktop_runtime import sandbox_command
        from kalshi_bot.desktop_worker import run_trading
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);credentials=path/'credentials';credentials.mkdir()
            for environment in ('demo','production'):
                command=sandbox_command(path,path,environment+'-trading',credentials)
                self.assertIn('MALLOC_ARENA_MAX',command)
                self.assertNotIn('--prepared-entry',command)
                with patch('kalshi_bot.prepared_runtime.run') as prepared:
                    run_trading(path,None,False,environment,environment=='production')
                    prepared.assert_called_once_with(path,None,environment,environment=='production',__import__('kalshi_bot.desktop_worker',fromlist=['emit']).emit)
            for mode in ('simulation','demo-monitor','production-monitor'):
                command=sandbox_command(path,path,mode,credentials)
                self.assertNotIn('MALLOC_ARENA_MAX',command)
            with patch('kalshi_bot.prepared_runtime.run') as prepared,patch('kalshi_bot.credentials.load'),patch('kalshi_bot.desktop_worker.emit'):
                run_trading(path,None,True,'production',False)
                prepared.assert_not_called()

    def test_repeated_stop_during_unknown_buy_does_not_starve_reconciliation(self):
        def run(f):
            f.qualify();f.venue.next_error='timeout_after'
            with self.assertRaises(APIError):f.execute()
            f.feed.prices['yes_bid']=D('.70')
            with f.journal.db:f.journal.db.execute('UPDATE markets SET stop=1 WHERE ticker=?',(TICKER,)) # Synthetic stream latch for a pending buy.
            epoch=f.inputs.epoch()
            for _ in range(30):f.execute()
            self.assertEqual(f.inputs.epoch(),epoch);self.assertEqual(f.venue.cancellations,[])
            f.publish();f.execute()
            self.assertEqual(len(f.venue.cancellations),1)
        self.each(run)
    def test_cancel_ack_waits_for_new_cohort_instead_of_repeating_on_every_tick(self):
        def run(f):
            f.qualify();f.execute();f.venue.fill(f.venue.orders[0],1);f.publish()
            f.feed.prices['yes_bid']=D('.70');f.execute();epoch=f.inputs.epoch()
            for _ in range(30):f.execute()
            self.assertEqual(len(f.venue.cancellations),1);self.assertEqual(f.inputs.epoch(),epoch)
            f.clock.now+=.1;f.publish();f.execute()
            self.assertEqual(len(f.venue.cancellations),2)
        self.each(run)

    def test_cancel_404_keeps_order_reserved_until_new_account_evidence(self):
        def run(f):
            f.qualify();f.execute();f.venue.fill(f.venue.orders[0],1);f.publish()
            f.feed.prices['yes_bid']=D('.70')
            with patch.object(f.venue,'cancel',side_effect=APIError(404)) as cancel:
                f.execute();epoch=f.inputs.epoch()
                for _ in range(30):f.execute()
                self.assertEqual(cancel.call_count,1)
                self.assertEqual(f.inputs.epoch(),epoch)
                self.assertIsNotNone(f.journal.active())
                self.assertEqual(len(f.venue.orders),1)
        self.each(run)

    def test_slow_account_reads_do_not_queue_cash_or_metadata(self):
        from kalshi_bot.prepared import Refreshers
        readers=Refreshers(Inputs(),lambda:None,lambda:[],lambda:None)
        release=threading.Event()
        started=[threading.Event() for _ in range(4)]
        def block(event):
            event.set();release.wait(3)
        try:
            for event in started:readers.pool.submit(block,event)
            self.assertTrue(all(event.wait(1) for event in started))
            self.assertEqual(readers.cash_pool.submit(lambda:'cash').result(timeout=1),'cash')
            self.assertEqual(readers.meta_pool.submit(lambda:'market').result(timeout=1),'market')
        finally:
            release.set();readers.close()
    def test_fee_change_requires_rebuild_without_blocking_prepared_protective_exit(self):
        def run(f):
            f.inputs.set_meta('fees',((TICKER,TICKER),D('.04')),f.clock());f.qualify()
            f.execute();self.assertEqual(f.venue.attempts,[])
            f.engine.build(f.market);f.execute();f.venue.fill(f.venue.orders[0],3);f.publish()
            f.inputs.fees_uncertain();f.engine.build(f.market);f.feed.prices['yes_bid']=D('.70')
            f.execute();self.assertEqual(D(f.venue.sells()[0]['initial_count_fp']),3)
        self.each(run)

    def test_queued_pause_is_checked_again_at_actual_transmission(self):
        def run(f):
            f.qualify();calls=[]
            def controls():
                calls.append(True)
                if len(calls)==3:f.journal.running=False
            f.journal.before_submit=controls
            with self.assertRaises(OrderNotSent):f.execute()
            self.assertEqual(len(calls),3);self.assertEqual(f.venue.attempts,[])
        self.each(run)

    def test_freshness_counts_entire_slow_request_and_pagination_duration(self):
        from kalshi_bot.prepared import Refreshers
        clock=Clock();inputs=Inputs(clock)
        class Slow:
            def pages(self,path):clock.now+=6;return []
            def get(self,path,params=None):clock.now+=6;return {'balance':600}
        readers=Refreshers(inputs,Slow,lambda:[],lambda:None)
        try:
            before=clock();_,at=readers._read_pages('/portfolio/orders')
            self.assertEqual(at,before);self.assertGreater(clock()-at,ACCOUNT_TTL)
            before=clock();_,at=readers._get('/portfolio/balance')
            self.assertEqual(at,before);self.assertGreater(clock()-at,CASH_TTL)
            def fee(*args):clock.now+=31;return D('.03')
            before=clock()
            with patch('kalshi_bot.prepared.entry_reserve',side_effect=fee):_,at=readers._read_fees({})
            self.assertEqual(at,before);self.assertGreater(clock()-at,FEES_TTL)
        finally:readers.close()

    def test_repeated_market_scope_mismatch_does_not_starve_background_recovery(self):
        def run(f):
            changed={**f.market,'exchange_index':1}
            epoch=f.inputs.epoch()
            for _ in range(100):f.engine.execute(changed,now=f.venue.now)
            self.assertEqual(f.inputs.epoch(),epoch+1)
            self.assertEqual(f.venue.attempts,[])
            f.inputs.set_meta('market',changed,f.clock())
            f.market=changed;f.publish();f.qualify();f.execute()
            self.assertEqual(len(f.venue.attempts),1)
        self.each(run)

    def test_expired_account_refresh_reports_actual_duration_without_extending_lease(self):
        def run(f):
            f.inputs.invalidate('Synthetic slow refresh')
            data=[f.venue.pages(path) for path in ('/portfolio/orders','/portfolio/fills','/portfolio/positions','/portfolio/settlements')]
            f.inputs.set_account(f.inputs.epoch(),data,f.clock()-7.36)
            self.assertFalse(f.engine.apply());f.engine.build(f.market)
            status=f.engine.status(f.market)
            self.assertFalse(status['ready']);self.assertIn('refresh too slow',status['reason'])
            self.assertAlmostEqual(status['refresh_timings']['account_seconds'],7.36)
            f.qualify();f.execute();self.assertEqual(f.venue.attempts,[])
        self.each(run)

    def test_slow_connection_does_not_stop_fresh_collection_refreshes(self):
        import time
        from kalshi_bot.prepared import Refreshers
        inputs=Inputs();published=threading.Event();readers=Refreshers(inputs,lambda:None,lambda:[],published.set);counts={}
        def read(path):
            counts[path]=counts.get(path,0)+1
            time.sleep(.35 if path.endswith('settlements') else .01)
            # Simulate trusted post-TLS request time, not connection setup age.
            return [],time.monotonic()-.005
        readers._read_pages=read
        with patch('kalshi_bot.prepared.ACCOUNT_TTL',.18):
            worker=threading.Thread(target=readers._accounts);worker.start()
            try:
                self.assertTrue(published.wait(2))
                account=inputs.view()['account'];self.assertIsNotNone(account)
                self.assertLess(time.monotonic()-account[2],.18)
                self.assertGreater(counts['/portfolio/orders'],1)
            finally:readers.close();worker.join(timeout=2)

    def test_independent_refresh_never_hides_a_genuinely_stale_collection(self):
        import time
        from kalshi_bot.prepared import Refreshers
        inputs=Inputs();stale_read=threading.Event();readers=Refreshers(inputs,lambda:None,lambda:[],lambda:None)
        def read(path):
            time.sleep(.01)
            if path.endswith('settlements'):stale_read.set();return [],time.monotonic()-ACCOUNT_TTL-1
            return [],time.monotonic()
        readers._read_pages=read;worker=threading.Thread(target=readers._accounts);worker.start()
        try:
            self.assertTrue(stale_read.wait(2));time.sleep(.05)
            self.assertIsNone(inputs.view()['account'])
        finally:readers.close();worker.join(timeout=2)

    def test_restart_disarms_persisted_cache_and_retains_order_reservation(self):
        def run(f):
            f.qualify();f.execute();path=Path(f.journal.db.execute('PRAGMA database_list').fetchone()[2]);f.journal.close()
            f.journal=Journal(path,'prepared-offline');f.engine=PreparedEngine(f.journal,Inputs(f.clock),f.feed,f.venue,clock=f.clock)
            self.assertEqual(f.journal.db.execute('SELECT status FROM prepared_entry').fetchone()[0],'unarmed_after_restart')
            f.journal.running=True;f.execute();self.assertEqual(len(f.venue.attempts),1)
            self.assertEqual(f.journal.db.execute('SELECT cents FROM entry_reservations').fetchone()[0],303)
        self.each(run)


if __name__=='__main__':unittest.main()
