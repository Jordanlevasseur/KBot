import asyncio
import io
import json
import os
import queue
import tempfile
import threading
import time
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import Mock,patch

from kalshi_bot.api import ROOT,RateGate,SchemaError,allowed_path
from kalshi_bot.control import CommandPipe
from kalshi_bot.market_data import MarketMonitor
from kalshi_bot.streams import Book,BookFeed,relay
from test_refresh import market


def snapshot(ticker='KXBTC15M-T',seq=1):
    return {'type':'orderbook_snapshot','sid':1,'seq':seq,'msg':{'market_ticker':ticker,
            'yes_dollars_fp':[['.018','10']],'no_dollars_fp':[['.98','10']]}}


class MonitorTests(unittest.TestCase):
    def monitor(self):
        client=Mock(); feed=Mock(); strategy=Mock(); messages=[]
        feed.health.return_value={'status':'connected','last_update':123,'error':None}
        feed.quote.return_value={'yes_bid':D('.018'),'no_bid':D('.98'),'yes_ask':D('.02'),'no_ask':D('.982')}
        strategy.quote.return_value={'yes_bid':D('.01'),'no_bid':D('.99'),'yes_ask':D('.01'),'no_ask':D('.99')}
        monitor=MarketMonitor(client,None,'/unused',lambda kind,**v:messages.append((kind,v)),feed=feed,strategy_feed=strategy)
        return monitor,client,feed,strategy,messages

    def test_rollover_keeps_old_position_protection_and_new_market_prices_separate(self):
        monitor,client,feed,strategy,messages=self.monitor()
        monitor.set_position({'ticker':'KXBTC15M-OLD','side':'no'})
        client.pages.return_value=[market('KXBTC15M-NEW',900,1800)]
        monitor.discover_once(now=900); monitor.publish_once(now=901)
        result=messages[-1][1]
        self.assertEqual(result['market'],'KXBTC15M-NEW')
        self.assertEqual(D(result['no_bid_cents']),D(98))
        self.assertEqual(D(result['position_bid_cents']),D(99))
        self.assertEqual(result['position_ticker'],'KXBTC15M-OLD')
        strategy.watch.assert_not_called()

    def test_expired_market_never_publishes_old_quotes(self):
        monitor,client,feed,strategy,messages=self.monitor()
        monitor.market=market('KXBTC15M-OLD',0,900)
        monitor.publish_once(now=900)
        self.assertIsNone(messages[-1][1]['market'])
        self.assertIsNone(messages[-1][1]['yes_ask_cents'])

    def test_discovery_recovers_after_network_error(self):
        monitor,client,feed,strategy,messages=self.monitor()
        client.pages.side_effect=[OSError(),[market('KXBTC15M-T',0,900)]]
        monitor.discover_once(now=100)
        self.assertEqual(monitor.discovery_error,'OSError')
        monitor.discover_once(now=101);monitor.publish_once(now=101)
        self.assertEqual(messages[-1][1]['market'],'KXBTC15M-T')
        self.assertIsNone(monitor.discovery_error)

    def test_publisher_continues_during_blocked_discovery_or_portfolio_io(self):
        monitor,client,feed,strategy,messages=self.monitor()
        monitor.market=market('KXBTC15M-T',time.time()-10,time.time()+100)
        release=threading.Event(); blocked=threading.Event()
        def slow(*args,**kwargs): blocked.set();release.wait(3);return [monitor.market]
        client.pages.side_effect=slow
        try:
            monitor.start(); self.assertTrue(blocked.wait(1))
            deadline=time.monotonic()+2
            while len(messages)<3 and time.monotonic()<deadline:time.sleep(.02)
            self.assertGreaterEqual(len(messages),3)
            self.assertTrue(all(D(m[1]['no_ask_cents'])==D('98.2') for m in messages))
        finally: release.set();monitor.close()


class ControlTests(unittest.TestCase):
    def test_batched_start_pause_cannot_leave_a_buffered_pause_unread(self):
        read,write=os.pipe()
        try:
            with os.fdopen(read,'r') as stream:
                controls=CommandPipe(stream)
                os.write(write,b'{"command":"start"}\n{"command":"pause"}\n')
                self.assertEqual([m['command'] for m in controls.poll(.1)],['start','pause'])
                self.assertEqual(controls.poll(0),[])
        finally: os.close(write)

    def test_fragmented_json_and_oversized_input(self):
        read,write=os.pipe()
        try:
            with os.fdopen(read,'r') as stream:
                controls=CommandPipe(stream)
                os.write(write,b'{"command":"pa')
                self.assertEqual(controls.poll(.1),[])
                os.write(write,b'use"}\n')
                self.assertEqual(controls.poll(.1),[{'command':'pause'}])
        finally: os.close(write)

    def test_shared_rate_gate_preserves_balance_and_general_pacing(self):
        now=[100.]
        gate=RateGate(clock=lambda:now[0],sleep=lambda n:now.__setitem__(0,now[0]+n))
        gate.wait('/portfolio/balance');gate.wait('/markets')
        self.assertEqual(now[0],100.)  # Balance uses an independent bucket.
        gate.wait('/markets');self.assertAlmostEqual(now[0],100.2)
        gate.wait('/portfolio/balance');self.assertAlmostEqual(now[0],101.05)


class GatewayHeartbeatTests(unittest.TestCase):
    def test_busy_control_messages_still_require_verified_peer_heartbeat(self):
        class WS:
            async def send(self,value):pass
            async def recv(self):return json.dumps({'type':'subscribed'})
            async def ping(self):
                pong=asyncio.get_running_loop().create_future();pong.set_result(None);return pong
        class Connection:
            async def __aenter__(self):return WS()
            async def __aexit__(self,*args):return False
        frames=[]
        def capture(frame):
            frames.append(frame)
            if frame.get('type')=='heartbeat':raise RuntimeError('test complete')
        config={'environment':'demo','ws_host':'external-api-ws.demo.kalshi.co','ws_addresses':['8.8.8.8']}
        with patch('kalshi_bot.streams.socket.create_connection',return_value=Mock()),patch('websockets.connect',return_value=Connection()),patch('kalshi_bot.streams.time',Mock(monotonic=Mock(side_effect=[10,12]))):
            with self.assertRaisesRegex(RuntimeError,'test complete'):
                asyncio.run(relay(config,'KXBTC15M-T',{},capture))
        self.assertEqual(frames[-1],{'type':'heartbeat','verified':True})

    def test_only_verified_heartbeats_refresh_an_idle_book(self):
        for verified in (False,True):
            with self.subTest(verified=verified):
                feed=BookFeed(Mock(),'/unused',None)
                feed.ticker='KXBTC15M-T';feed.generation=1;feed.book=Book(feed.ticker)
                observed=[];calls=[0];original_update=[]
                def line(_):
                    calls[0]+=1
                    if calls[0]==1:return json.dumps(snapshot()).encode()
                    if calls[0]==2:
                        original_update.append(feed.updated_at)
                        feed.heartbeat=time.monotonic()-6
                        return json.dumps({'type':'heartbeat','verified':verified}).encode()
                    observed.append(feed.quote(feed.ticker))
                    observed.append(feed.updated_at)
                    feed.closed=True
                    return b''
                response=Mock(status=200);response.readline.side_effect=line
                connection=Mock();connection.getresponse.return_value=response
                with patch('kalshi_bot.streams.UnixHTTP',return_value=connection),patch('kalshi_bot.streams.ws_headers',return_value={}):
                    feed._run(feed.ticker,1)
                self.assertEqual(observed[0] is not None,verified)
                self.assertEqual(observed[1],original_update[0])

    def test_idle_heartbeat_requires_actual_upstream_pong(self):
        class WS:
            async def send(self,value):pass
            async def recv(self):raise asyncio.TimeoutError()
            async def ping(self):
                pong=asyncio.get_running_loop().create_future();pong.set_result(None);return pong
        class Connection:
            async def __aenter__(self):return WS()
            async def __aexit__(self,*args):return False
        frames=[]
        def capture(frame):frames.append(frame);raise RuntimeError('test complete')
        config={'environment':'demo','ws_host':'external-api-ws.demo.kalshi.co','ws_addresses':['8.8.8.8']}
        with patch('kalshi_bot.streams.socket.create_connection',return_value=Mock()),patch('websockets.connect',return_value=Connection()):
            with self.assertRaisesRegex(RuntimeError,'test complete'):
                asyncio.run(relay(config,'KXBTC15M-T',{},capture))
        self.assertEqual(frames,[{'type':'heartbeat','verified':True}])

    def test_failed_upstream_pong_never_emits_a_fresh_heartbeat(self):
        class WS:
            async def send(self,value):pass
            async def recv(self):raise asyncio.TimeoutError()
            async def ping(self):raise OSError('offline')
        class Connection:
            async def __aenter__(self):return WS()
            async def __aexit__(self,*args):return False
        frames=[]
        config={'environment':'demo','ws_host':'external-api-ws.demo.kalshi.co','ws_addresses':['8.8.8.8']}
        with patch('kalshi_bot.streams.socket.create_connection',return_value=Mock()),patch('websockets.connect',return_value=Connection()):
            with self.assertRaises(OSError):asyncio.run(relay(config,'KXBTC15M-T',{},frames.append))
        self.assertEqual(frames,[])

    def test_time_filters_remain_narrow_and_compatible_with_official_api(self):
        target=ROOT+'/markets?series_ticker=KXBTC15M&min_close_ts=100&max_close_ts=1900'
        self.assertEqual(allowed_path(target),ROOT+'/markets')
        for wrong in (target+'&status=open',target.replace('1900','99999'),target.replace('KXBTC15M','OTHER'),target.replace('markets?','portfolio/orders?')):
            with self.assertRaises(ValueError):allowed_path(wrong)

    def test_book_replay_and_malformed_snapshot_invalidate_old_data(self):
        book=Book('KXBTC15M-T');book.consume(snapshot())
        with self.assertRaises(SchemaError):book.consume(snapshot())
        self.assertIsNone(book.prices())
        book.consume(snapshot(seq=3))
        with self.assertRaises(SchemaError):book.consume({'type':'orderbook_snapshot','sid':1,'seq':4,'msg':{}})
        self.assertIsNone(book.prices())


if __name__=='__main__':unittest.main()
