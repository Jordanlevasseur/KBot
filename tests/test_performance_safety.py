"""Event-driven performance changes must preserve write safety and full reconciliation."""
import copy
import io
import json
import os
from pathlib import Path
import select
import tempfile
import threading
import time
import unittest
from decimal import Decimal as D
from unittest.mock import Mock,patch
from kalshi_bot.activity import Wakeup
from kalshi_bot.control import CommandPipe
from kalshi_bot.execution import Journal
from kalshi_bot.trader import cycle
from kalshi_bot.api import APIError,SchemaError
from kalshi_bot.streams import BookHub,BookFeed,Book
from kalshi_bot.market_data import MarketMonitor
from test_shared_book import Feed
from test_trader import Venue
from test_missed_no import FreshFeed
from test_refresh import market


class PerformanceSafetyTests(unittest.TestCase):
    def test_notifications_are_bounded_coalesced_and_rearm_after_drain(self):
        wake=Wakeup()
        try:
            for _ in range(10000):wake.notify()
            self.assertTrue(wake.wait(0));self.assertFalse(wake.wait(0))
            wake.notify();self.assertTrue(wake.wait(0));self.assertFalse(wake.wait(0))
        finally:wake.close()
        wake.notify();wake.close()  # Shutdown callbacks cannot write a reused FD.

    def test_independent_display_and_trader_receive_same_update(self):
        hub=BookHub(None,'/unused',None,factory=Feed)
        a,b=Wakeup(),Wakeup()
        try:
            hub.subscribe(a);hub.subscribe(b);hub.watch('KXBTC15M-T')
            hub.feeds['KXBTC15M-T'].on_update()
            self.assertTrue(a.wait(0));self.assertTrue(b.wait(0))
            self.assertEqual(hub.quote('KXBTC15M-T')['no_ask'],D('.98'))
            hub.unsubscribe(a);hub.feeds['KXBTC15M-T'].on_update()
            self.assertFalse(a.wait(0));self.assertTrue(b.wait(0))
        finally:hub.close();a.close();b.close()

    def test_price_wakeup_does_not_consume_pause_and_batched_quit(self):
        read,write=os.pipe();wake=Wakeup()
        try:
            with os.fdopen(read,'r') as stream:
                controls=CommandPipe(stream);wake.notify()
                self.assertEqual(controls.poll(.1,wakeup=wake),[])
                os.write(write,b'{"command":"pause"}\n{"command":"quit"}\n');wake.notify()
                self.assertEqual(controls.poll(.1,wakeup=wake),[{'command':'pause'},{'command':'quit'}])
        finally:os.close(write);wake.close()

    def test_event_publisher_wakes_while_discovery_is_blocked(self):
        hub=BookHub(None,'/unused',None,factory=Feed);hub.watch('KXBTC15M-T')
        client=Mock();blocked=threading.Event();release=threading.Event();frames=[]
        monitor=MarketMonitor(client,None,'/unused',lambda kind,**v:frames.append(v),feed=hub)
        monitor.market=market('KXBTC15M-T',time.time()-10,time.time()+200)
        def slow(*a,**kw):blocked.set();release.wait(2);return [monitor.market]
        client.pages.side_effect=slow
        try:
            monitor.start();self.assertTrue(blocked.wait(.5))
            deadline=time.monotonic()+.5
            while not frames and time.monotonic()<deadline:time.sleep(.005)
            hub.feeds['KXBTC15M-T'].prices['no_ask']=D('.984');hub.feeds['KXBTC15M-T'].on_update()
            deadline=time.monotonic()+.3
            while not any(f.get('no_ask_cents')=='98.400' for f in frames) and time.monotonic()<deadline:time.sleep(.005)
            self.assertTrue(any(f.get('no_ask_cents')=='98.400' for f in frames))
        finally:release.set();monitor.close()

    def test_stop_is_committed_before_notifying_subscribers(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db';journal=Journal(path,'offline');journal.ready=True;journal.running=True
            broker=Mock();broker.create.side_effect=lambda p:{'client_order_id':p['client_order_id'],'order_id':'synthetic'}
            journal.submit(broker,'KXBTC15M-T','buy','yes',D('.98'),1,900,800)
            feed=BookFeed(Mock(),'/unused',path);feed.risk_sides={'yes'};feed.ticker='KXBTC15M-T';feed.generation=1
            frame={'type':'orderbook_snapshot','sid':1,'seq':1,'msg':{'market_ticker':feed.ticker,'yes_dollars_fp':[['.85','1']],'no_dollars_fp':[['.02','1']]}}
            response=Mock(status=200);response.readline.side_effect=[json.dumps(frame).encode(),b'']
            connection=Mock();connection.getresponse.return_value=response
            observed=[]
            def notified():
                observed.append(journal.db.execute('SELECT stop FROM markets').fetchone()[0]);feed.closed=True
            feed.on_update=notified
            try:
                with patch('kalshi_bot.streams.UnixHTTP',return_value=connection),patch('kalshi_bot.streams.ws_headers',return_value={}):feed._run(feed.ticker,1)
                self.assertEqual(observed,[1])
            finally:feed.close();journal.close()

    def test_one_fresh_cash_read_for_sizing_and_refresh_when_paused(self):
        for side in ('yes','no'):
            for cash in (10000,200,199):
                with self.subTest(side=side,cash=cash),tempfile.TemporaryDirectory() as directory:
                    journal=Journal(Path(directory)/'db','offline');journal.running=True;venue=Venue();feed=FreshFeed();reads=[]
                    if side=='yes':feed.prices={k.replace('yes','TEMP').replace('no','yes').replace('TEMP','no'):v for k,v in feed.prices.items()}
                    get=venue.get
                    def read(path,params=None):
                        reads.append(path)
                        return {'balance':cash} if path=='/portfolio/balance' else get(path,params)
                    venue.get=read
                    try:
                        with patch('kalshi_bot.fees.entry_reserve',return_value=D('.03')):
                            result=cycle(journal,venue,venue,None,now=venue.close-90,book_feed=feed)
                        self.assertEqual(reads.count('/portfolio/balance'),1)
                        self.assertEqual(len(venue.orders),int(cash>=200))
                        if cash>=200:
                            self.assertEqual(result['quantity'],cash//200)
                            self.assertLessEqual(D(result['quantity'])*D('1.01'),D(cash)/100)
                        else:self.assertIn('cannot cover',result['status'])
                        journal.running=False;reads.clear();cycle(journal,venue,venue,None,now=venue.close-89,book_feed=feed)
                        self.assertEqual(reads.count('/portfolio/balance'),1)
                    finally:journal.close()

    def test_historical_corruption_and_duplicate_or_missing_fills_still_block(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'db','offline');journal.running=True;venue=Venue()
            try:
                cycle(journal,venue,venue,D('.03'),now=venue.close-90);venue.fill()
                positions=venue.pages('/portfolio/positions');journal.reconcile(venue.orders,venue.fills,positions)
                with self.assertRaises(RuntimeError):journal.reconcile(venue.orders,venue.fills*2,positions)
                with self.assertRaises(RuntimeError):journal.reconcile(venue.orders,[],positions)
                changed=copy.deepcopy(venue.orders);changed[0]['yes_price_dollars']='.97'
                with self.assertRaises(SchemaError):journal.reconcile(changed,venue.fills,positions)
                self.assertFalse(journal.ready)
            finally:journal.close()

    def test_unmodified_historical_orders_are_validated_without_rewriting(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'db','offline');journal.running=True;venue=Venue()
            try:
                cycle(journal,venue,venue,D('.03'),now=venue.close-90);venue.fill()
                positions=venue.pages('/portfolio/positions');journal.reconcile(venue.orders,venue.fills,positions)
                queries=[];journal.db.set_trace_callback(queries.append)
                journal.reconcile(venue.orders,venue.fills,positions);journal.db.set_trace_callback(None)
                self.assertFalse(any(q.startswith('UPDATE orders') for q in queries));self.assertTrue(journal.ready)
                self.assertEqual(journal.db.execute('PRAGMA synchronous').fetchone()[0],2)
            finally:journal.close()


if __name__=='__main__':unittest.main()
