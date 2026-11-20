"""Buy decisions, stream pressure, and entry controls with generated data only."""
import io
import json
import sqlite3
import tempfile
import time
import unittest
from strategy_fixture import configure_policy

def setUpModule():
    configure_policy(92,86)

from decimal import Decimal as D
from pathlib import Path
from unittest.mock import Mock,patch

from kalshi_bot.api import Client
from kalshi_bot.execution import Journal,affordable
from kalshi_bot.streams import Book,BookFeed,persist_crossing
from kalshi_bot.trader import cycle
from test_trader import Venue


def prices(side,ask):
    asks={side:D(ask),('no' if side=='yes' else 'yes'):D('.05')}
    return {'yes_ask':asks['yes'],'no_ask':asks['no'],'yes_bid':1-asks['no'],'no_bid':1-asks['yes']}


class EntryTests(unittest.TestCase):
    def test_current_qualifying_price_is_used_and_resized_for_both_sides(self):
        for side in ('yes','no'):
            for price in ('.95','.96','.97','.98','.99'):
                with self.subTest(side=side,price=price),tempfile.TemporaryDirectory() as directory:
                    journal=Journal(Path(directory)/'db','offline'); journal.running=True
                    venue=Venue(); feed=Mock()
                    feed.quote.side_effect=[prices(side,'.95'),prices(side,'.95'),prices(side,price)]
                    try:
                        with patch('kalshi_bot.fees.entry_reserve',return_value=D('.03')):
                            result=cycle(journal,venue,venue,None,now=venue.close-100,book_feed=feed)
                        self.assertEqual(result['limit'],str(D(price).quantize(D('.01'))))
                        self.assertEqual(result['quantity'],affordable(10000,D(price),D('.03')))
                        self.assertEqual(venue.orders[0]['yes_price_dollars'],format(D(price) if side=='yes' else 1-D(price),'.4f'))
                        self.assertEqual(len(venue.orders),1)
                    finally: journal.close()

    def test_price_leaving_allowed_range_or_stale_stream_does_not_buy(self):
        for latest in (prices('yes','.91'),prices('yes','1.00'),None):
            with tempfile.TemporaryDirectory() as directory:
                journal=Journal(Path(directory)/'db','offline'); journal.running=True
                venue=Venue(); feed=Mock(); feed.quote.side_effect=[prices('yes','.95'),prices('yes','.95'),latest]
                try:
                    with patch('kalshi_bot.fees.entry_reserve',return_value=D('.03')):
                        result=cycle(journal,venue,venue,None,now=venue.close-100,book_feed=feed)
                    self.assertEqual(venue.orders,[])
                    self.assertIn('changed',result['status'])
                finally: journal.close()

    def test_zero_contract_target_has_persistent_explanation(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'db','offline'); journal.running=True
            venue=Venue(); original=venue.get
            venue.get=lambda path,params=None:{'balance':100} if path=='/portfolio/balance' else original(path,params)
            try:
                result=cycle(journal,venue,venue,D('.03'),now=venue.close-100)
                self.assertEqual(venue.orders,[])
                self.assertIn('cannot cover',result['status'])
                log=journal.db.execute("SELECT event FROM audit WHERE event LIKE 'Decision:%'").fetchone()[0]
                self.assertIn('cannot cover',log)
                self.assertIn('cash_cents',log)
            finally: journal.close()

    def test_full_account_reconciliation_uses_large_pages_and_keeps_pagination(self):
        signer=Mock(); signer.headers.return_value={}
        bodies=[{'fills':[{'fill_id':str(i)} for i in range(1000)],'cursor':'next'},
                {'fills':[{'fill_id':str(i)} for i in range(1000,1082)]}]
        transport=Mock(side_effect=[(200,{},json.dumps(body).encode()) for body in bodies])
        client=Client(signer,transport,sleep=lambda _:None,clock=lambda:100)
        self.assertEqual(len(client.pages('/portfolio/fills')),1082)
        self.assertEqual(transport.call_count,2)
        self.assertIn('limit=1000',transport.call_args_list[0].args[0])
        self.assertIn('cursor=next',transport.call_args_list[1].args[0])

    def test_unrelated_history_is_checked_without_repeated_large_snapshot_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'db','offline')
            unrelated=[{'order_id':str(i),'status':'executed'} for i in range(1000)]
            try:
                journal.reconcile(unrelated,[],[],[{'ticker':'KXBTC15M-OTHER'}])
                snapshot=json.loads(journal.db.execute('SELECT payload FROM snapshots').fetchone()[0])
                self.assertEqual(snapshot['orders'],[])
                with self.assertRaises(RuntimeError):
                    journal.reconcile([{'order_id':'unmanaged','status':'resting'}],[],[])
            finally: journal.close()

    def test_existing_stream_connection_does_not_rewrite_latched_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db'; journal=Journal(path,'offline')
            try:
                with journal.db:
                    journal.db.execute("INSERT INTO markets(ticker,stop) VALUES('KXBTC15M-T',1)")
                db=sqlite3.connect(path)
                try:
                    for _ in range(1000): persist_crossing(path,'KXBTC15M-T','yes',D('.80'),db=db)
                    self.assertEqual(db.total_changes,0)
                    self.assertEqual(db.execute('SELECT stop FROM markets').fetchone()[0],1)
                finally: db.close()
            finally: journal.close()

    def test_pause_is_processed_even_while_a_qualifying_quote_is_present(self):
        from kalshi_bot.desktop_worker import _run_legacy_trading as run_trading
        observed=[];waits=[]
        def ready(readers,writers,errors,timeout):
            waits.append(timeout)
            return readers[:1],[],[]
        class FakeClient:
            def __init__(self,*a,**kw): pass
        def check(journal,*args,**kwargs):
            observed.append(journal.running)
            return {'status':'Synthetic check','ticker':'KXBTC15M-T','cutoff':time.time()+100}
        with tempfile.TemporaryDirectory() as directory,\
             patch('kalshi_bot.api.Client',FakeClient),\
             patch('kalshi_bot.credentials.load') as load,\
             patch('kalshi_bot.trade_api.OrderBroker') as broker,\
             patch('kalshi_bot.streams.BookFeed') as feed,\
             patch('kalshi_bot.trader.cycle',side_effect=check),\
             patch('kalshi_bot.desktop_worker.emit'),\
             patch('kalshi_bot.desktop_worker.MarketMonitor'),\
             patch('kalshi_bot.desktop_worker.sys.stdin',io.StringIO(''.join(json.dumps({'command':c})+'\n' for c in ('start','pause','quit')))),\
             patch('kalshi_bot.desktop_worker.select.select',side_effect=ready):
            load.return_value.fingerprint='offline'
            feed.return_value.quote.return_value=prices('yes','.96')
            run_trading(Path(directory),None,False,'production',True)
            broker.return_value.create.assert_not_called()
        self.assertEqual(observed,[False,True,False])
        self.assertTrue(waits);self.assertTrue(all(timeout==0 for timeout in waits))

    def test_delayed_exchange_messages_cannot_look_like_fresh_quotes(self):
        feed=BookFeed(Mock(),'/unused','/unused')
        feed.ticker='KXBTC15M-T'; feed.generation=1; feed.book=Book(feed.ticker)
        message={'type':'orderbook_snapshot','sending_ts_ms':int((time.time()-20)*1000),'sid':1,'seq':1,
                 'msg':{'market_ticker':feed.ticker,'yes_dollars_fp':[['.96','1']],'no_dollars_fp':[['.03','1']]}}
        response=Mock(status=200); response.readline.return_value=json.dumps(message).encode()
        connection=Mock(); connection.getresponse.return_value=response
        with patch('kalshi_bot.streams.UnixHTTP',return_value=connection),\
             patch('kalshi_bot.streams.ws_headers',return_value={}),\
             patch.object(feed.stopping,'wait',side_effect=lambda _:setattr(feed,'closed',True)):
            feed._run(feed.ticker,1)
        self.assertIsNone(feed.quote(feed.ticker))

    def test_unpersisted_stop_crossing_survives_reconnect_and_rebound(self):
        with tempfile.TemporaryDirectory() as directory:
            feed=BookFeed(Mock(),'/unused',Path(directory)/'db')
            feed.ticker='KXBTC15M-T'; feed.generation=1; feed.book=Book(feed.ticker)
            def snapshot(bid):
                return json.dumps({'type':'orderbook_snapshot','sid':1,'seq':1,
                    'msg':{'market_ticker':feed.ticker,'yes_dollars_fp':[[bid,'1']],'no_dollars_fp':[['.03','1']]}}).encode()
            first=Mock(status=200); first.readline.side_effect=[snapshot('.85'),b'']
            second=Mock(status=200); second.readline.return_value=snapshot('.95')
            connections=[Mock(),Mock()]
            connections[0].getresponse.return_value=first; connections[1].getresponse.return_value=second
            attempts=[]
            def persist(path,ticker,side,bid,db=None,**kwargs):
                if side!='yes': return
                attempts.append(bid)
                if len(attempts)==1: raise sqlite3.OperationalError('database is locked')
                feed.closed=True
            with patch('kalshi_bot.streams.UnixHTTP',side_effect=connections),\
                 patch('kalshi_bot.streams.ws_headers',return_value={}),\
                 patch('kalshi_bot.streams.persist_crossing',side_effect=persist),\
                 patch.object(feed.stopping,'wait'):
                feed._run(feed.ticker,1)
            self.assertEqual(attempts,[D('.85'),D('.85')])


if __name__=='__main__': unittest.main()
