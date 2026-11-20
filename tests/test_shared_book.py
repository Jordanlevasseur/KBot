import json
import sqlite3
import tempfile
import time
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import Mock
from kalshi_bot.streams import BookHub,BookFeed,Book,persist_crossing
from unittest.mock import patch
from kalshi_bot.market_data import MarketMonitor
from kalshi_bot.diagnostics import Diagnostics,failure_reason
from kalshi_bot.eligibility import eligible_sides,ordered_candidates
from kalshi_bot.execution import Journal
from test_refresh import market


class Feed:
    def __init__(self,*args):
        self.closed=False
        self.prices={'yes_bid':D('.02'),'yes_ask':D('.03'),'no_bid':D('.97'),'no_ask':D('.98')}
    def watch(self,ticker):self.ticker=ticker
    def frame(self,ticker):return {'prices':None if self.closed else dict(self.prices),'status':'connected','last_update':100,
                                  'error':None,'sequence':7,'subscription':2,'verified_age':.01}
    def close(self):self.closed=True
    def arm(self,side):pass
    def clear_risk(self):pass


class SharedBookTests(unittest.TestCase):
    def test_display_and_trader_share_the_same_market_sequence_and_prices(self):
        created=[]
        def factory(*args):created.append(Feed());return created[-1]
        hub=BookHub(None,'/unused',None,factory=factory);out=[]
        monitor=MarketMonitor(Mock(),None,'/unused',lambda kind,**v:out.append(v),feed=hub,strategy_feed=hub)
        monitor.market=market('KXBTC15M-T',0,900);hub.watch('KXBTC15M-T');hub.watch('KXBTC15M-T')
        monitor.set_execution(True,'Reconciling account');monitor.publish_once(now=810)
        self.assertEqual(len(created),1)
        self.assertEqual(D(out[-1]['no_ask_cents'])/100,hub.quote('KXBTC15M-T')['no_ask'])
        self.assertEqual(out[-1]['eligible_sides'],['no']);self.assertTrue(out[-1]['entry_window_open'])
        self.assertEqual(out[-1]['execution_stage'],'Reconciling account')
        created[0].closed=True
        monitor.publish_once(now=811)
        self.assertIsNone(out[-1]['no_ask_cents']);self.assertIsNone(hub.quote('KXBTC15M-T'))
        hub.close()

    def test_rollover_retains_old_protection_until_position_closes(self):
        hub=BookHub(None,'/unused',None,factory=Feed)
        hub.watch('KXBTC15M-OLD');hub.watch('KXBTC15M-NEXT')
        old=hub.feeds['KXBTC15M-OLD']
        hub.retain('KXBTC15M-NEXT','KXBTC15M-OLD')
        self.assertFalse(old.closed)
        hub.retain('KXBTC15M-NEXT');self.assertTrue(old.closed)
        self.assertEqual(len(hub.feeds),1);hub.close()

    def test_exact_ties_have_no_fixed_yes_priority_and_alternate_after_sale(self):
        prices={'yes_ask':D('.98'),'no_ask':D('.98')}
        self.assertEqual(set(eligible_sides(prices)),{'yes','no'})
        self.assertEqual(ordered_candidates(prices,'KXBTC15M-T','yes')[0],'no')
        self.assertEqual(ordered_candidates(prices,'KXBTC15M-T','no')[0],'yes')
        choices={ordered_candidates(prices,'KXBTC15M-'+str(n))[0] for n in range(50)}
        self.assertEqual(choices,{'yes','no'})

    def test_market_observation_is_timestamped_without_blocking_the_publisher(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db';journal=Journal(path,'offline');diagnostics=Diagnostics(path)
            hub=BookHub(None,'/unused',path,factory=Feed);hub.watch('KXBTC15M-T')
            monitor=MarketMonitor(Mock(),None,'/unused',lambda *a,**k:None,feed=hub,strategy_feed=hub,diagnostics=diagnostics)
            monitor.market=market('KXBTC15M-T',0,900);monitor.set_execution(True,'Reconciling account')
            try:
                monitor.publish_once(now=810);monitor.publish_once(now=811)
                diagnostics.close()
                rows=journal.db.execute('SELECT at,payload FROM execution_events WHERE kind=?',('market_observation',)).fetchall()
                # Remaining time changes each observation; the research log
                # deliberately preserves both timestamped entry decisions.
                self.assertEqual(len(rows),2)
                self.assertGreater(rows[0][0],0)
                data=json.loads(rows[0][1]);self.assertEqual(data['eligible_sides'],['no'])
                self.assertEqual(data['sequence'],7);self.assertEqual(data['no_ask'],'0.98')
                self.assertEqual(data['stage'],'Reconciling account')
            finally:hub.close();diagnostics.close();journal.close()

    def test_unexpected_exception_messages_are_not_logged_as_credentials(self):
        self.assertEqual(failure_reason(RuntimeError('PRIVATE-KEY-CONTENT')),'RuntimeError')

    def test_flat_stream_does_not_wait_on_stop_database_for_unheld_sides(self):
        feed=BookFeed(Mock(),'/unused','/unused')
        feed.risk_sides=set();feed.ticker='KXBTC15M-T';feed.generation=1;feed.book=Book(feed.ticker)
        observed=[];count=[0]
        def line(_):
            count[0]+=1
            if count[0]==1:
                return json.dumps({'type':'orderbook_snapshot','sid':1,'seq':1,
                      'msg':{'market_ticker':feed.ticker,'yes_dollars_fp':[['.02','10']],'no_dollars_fp':[['.97','10']]}}).encode()
            observed.append(feed.quote(feed.ticker));feed.closed=True;return b''
        response=Mock(status=200);response.readline.side_effect=line
        connection=Mock();connection.getresponse.return_value=response
        with patch('kalshi_bot.streams.UnixHTTP',return_value=connection),patch('kalshi_bot.streams.ws_headers',return_value={}),\
             patch('kalshi_bot.streams.sqlite3.connect',side_effect=AssertionError('Flat book must not query stop database')) as connect:
            feed._run(feed.ticker,1)
            connect.assert_not_called()
        self.assertEqual(observed[0]['no_ask'],D('.98'))

    def test_observation_before_current_intent_cannot_latch_new_position_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db';journal=Journal(path,'offline');journal.ready=True;journal.running=True
            broker=Mock();broker.create.side_effect=lambda payload:{'order_id':'test','client_order_id':payload['client_order_id']}
            try:
                old=time.time()-10
                journal.submit(broker,'KXBTC15M-T','buy','no',D('.98'),1,900,810)
                persist_crossing(path,'KXBTC15M-T','no',D('.85'),observed_at=old)
                self.assertEqual(journal.db.execute('SELECT stop FROM markets').fetchone()[0],0)
                persist_crossing(path,'KXBTC15M-T','no',D('.85'),observed_at=time.time())
                self.assertEqual(journal.db.execute('SELECT stop FROM markets').fetchone()[0],1)
            finally:journal.close()

    def test_stop_tracking_is_armed_after_intent_commit_before_network_send(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db';journal=Journal(path,'offline');journal.ready=True;journal.running=True
            events=[]
            def arm(ticker,side):
                row=journal.active();self.assertEqual(row['status'],'pending');events.append(('armed',side))
            def send(payload):
                self.assertEqual(events,[('armed','no')]);events.append(('sent','no'))
                return {'order_id':'test','client_order_id':payload['client_order_id']}
            broker=Mock();broker.create.side_effect=send;journal.on_intent=arm
            try:
                journal.submit(broker,'KXBTC15M-T','buy','no',D('.98'),1,900,810)
                self.assertEqual(events,[('armed','no'),('sent','no')])
            finally:journal.close()


if __name__=='__main__':unittest.main()
