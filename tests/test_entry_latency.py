"""Shorter entry latency keeps fresh reads, budgets, pause and order safety."""
import json
import tempfile
import threading
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlsplit,parse_qsl
from kalshi_bot.api import Client,RateGate,ROOT,COLLECTIONS,APIError
from kalshi_bot.execution import Journal
from kalshi_bot.trader import cycle
from test_trader import Venue
from test_missed_no import FreshFeed


class Clock:
    def __init__(self):self.now=100.;self.sleeps=[]
    def __call__(self):return self.now
    def sleep(self,seconds):self.sleeps.append(seconds);self.now+=seconds


def connector(clock,gate=None,transport=None):
    signer=Mock();signer.headers.return_value={}
    return Client(signer,transport or (lambda *args:(200,{},b'{}')),sleep=clock.sleep,clock=clock,gate=gate)


class EntryLatencyTests(unittest.TestCase):
    def test_shared_clients_follow_one_five_reads_per_second_pacer(self):
        clock=Clock();gate=RateGate(sleep=clock.sleep,clock=clock);sent=[]
        def transport(target,headers):sent.append(clock());return 200,{},b'{}'
        a,b=connector(clock,gate,transport),connector(clock,gate,transport)
        for client in (a,b,a,b,a,b):client.get('/exchange/status')
        self.assertEqual(len(sent),6)
        for index,stamp in enumerate(sent):self.assertAlmostEqual(stamp,100+index*.2)
        self.assertAlmostEqual(clock(),101.)

    def test_balance_is_separate_and_keeps_one_second_limit(self):
        for shared in (False,True):
            with self.subTest(shared=shared):
                clock=Clock();gate=RateGate(sleep=clock.sleep,clock=clock) if shared else None
                sent=[]
                def transport(target,headers):sent.append((clock(),target));return 200,{},b'{}'
                client=connector(clock,gate,transport)
                client.get('/exchange/status');client.get('/portfolio/balance')
                self.assertEqual(sent[0][0],sent[1][0])
                client.get('/exchange/status');self.assertAlmostEqual(clock(),100.2)
                client.get('/portfolio/balance');self.assertAlmostEqual(clock(),101.05)
                client.get('/exchange/status');self.assertAlmostEqual(clock(),101.05)

    def test_waiting_for_balance_does_not_hold_the_read_lock(self):
        clock=Clock();entered=threading.Event();release=threading.Event();errors=[]
        def sleep(seconds):
            entered.set()
            if not release.wait(2):raise AssertionError('Balance sleeper not released')
            clock.sleep(seconds)
        gate=RateGate(sleep=sleep,clock=clock);gate.wait('/portfolio/balance')
        def balance():
            try:gate.wait('/portfolio/balance')
            except Exception as exc:errors.append(exc)
        thread=threading.Thread(target=balance);thread.start()
        try:
            self.assertTrue(entered.wait(.5))
            self.assertTrue(gate.lock.acquire(timeout=.1))
            gate.lock.release()
            gate.wait('/exchange/status')  # Dispatches without waiting for cash.
            self.assertEqual(clock(),100.)
        finally:release.set();thread.join(timeout=2)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.assertAlmostEqual(clock(),101.05)

    def test_read_and_balance_cooldowns_are_shared_but_separate(self):
        for balance in (False,True):
            with self.subTest(balance=balance):
                clock=Clock();gate=RateGate(sleep=clock.sleep,clock=clock)
                blocked='/portfolio/balance' if balance else '/exchange/status'
                other='/exchange/status' if balance else '/portfolio/balance'
                gate.defer(blocked,3)
                connector(clock,gate).get(other);self.assertEqual(clock(),100.)
                connector(clock,gate).get(blocked);self.assertAlmostEqual(clock(),103.)

    def test_429_still_has_bounded_backoff_and_shared_cooldown(self):
        clock=Clock();gate=RateGate(sleep=clock.sleep,clock=clock)
        responses=iter([(429,{'Retry-After':'3'},b'{}'),(200,{},b'{}')]);attempts=[]
        def transport(*args):attempts.append(clock());return next(responses)
        connector(clock,gate,transport).get('/exchange/status')
        self.assertEqual(attempts,[100.,103.]);self.assertEqual(gate.read_after,103.)
        clock=Clock();gate=RateGate(sleep=clock.sleep,clock=clock)
        attempts=[]
        def limited(*args):attempts.append(clock());return 429,{},b'{}'
        with self.assertRaises(APIError):connector(clock,gate,limited).get('/exchange/status')
        self.assertEqual(len(attempts),4)
        self.assertGreaterEqual(attempts[-1]-attempts[0],7)

    def test_both_sides_still_read_all_account_fee_cash_and_cutoff_data(self):
        for side in ('yes','no'):
            with self.subTest(side=side),tempfile.TemporaryDirectory() as directory:
                journal=Journal(Path(directory)/'db','offline');journal.running=True;journal.ready=True
                venue=Venue();feed=FreshFeed()
                if side=='yes':feed.prices={k.replace('yes','TEMP').replace('no','yes').replace('TEMP','no'):v for k,v in feed.prices.items()}
                original=venue.market;venue.market=lambda:{**original(),'event_ticker':'KXBTC15M-T'}
                clock=Clock();gate=RateGate(sleep=clock.sleep,clock=clock);reads=[];balance=[2687]
                def transport(target,headers):
                    parsed=urlsplit(target);path=parsed.path.removeprefix(ROOT);params=dict(parse_qsl(parsed.query));reads.append(path)
                    if path in COLLECTIONS:
                        value=[] if path=='/events/fee_changes' else venue.pages(path,params)
                        body={COLLECTIONS[path]:value,'cursor':''}
                    elif path=='/series/KXBTC15M':body={'series':{'ticker':'KXBTC15M','fee_type':'quadratic','fee_multiplier':1}}
                    elif path=='/portfolio/balance':
                        self.assertEqual(params,{'subaccount':'0','exchange_index':'0'})
                        body={'balance':balance[0]}
                    else:body=venue.get(path,params)
                    return 200,{},json.dumps(body).encode()
                client=connector(clock,gate,transport)
                try:
                    result=cycle(journal,client,venue,None,now=venue.close-90,book_feed=feed)
                    self.assertEqual(result['side'],side);self.assertEqual(result['quantity'],13)
                    self.assertEqual(reads[:4],['/portfolio/orders','/portfolio/fills','/portfolio/positions','/portfolio/settlements'])
                    for path in ('/portfolio/balance','/series/KXBTC15M','/events/fee_changes','/exchange/status','/markets/KXBTC15M-T'):
                        self.assertIn(path,reads)
                    self.assertEqual(reads.count('/portfolio/balance'),1)
                    self.assertLess(clock()-100,2)
                finally:journal.close()
