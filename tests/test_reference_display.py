import asyncio
import json
import unittest
from unittest.mock import patch
from kalshi_bot.reference_display import ReferenceFeed,reference_text
from kalshi_bot.streams import relay


def tick(value='68000.12',stamp=100000,index='BRTI'):
    return {'type':'cfbenchmarks_value','msg':{'index_id':index,'data':json.dumps(
        {'type':'value','id':index,'time':stamp,'value':value})}}


class ReferenceTests(unittest.TestCase):
    def test_source_age_replay_wrong_index_and_invalid_values(self):
        feed=ReferenceFeed(None,'/unused')
        self.assertTrue(feed.consume(tick(),wall=101,mono=10))
        saved=feed.snapshot()
        for value in ('NaN','Infinity','-1','0',True,{},'1e100'):
            self.assertFalse(feed.consume(tick(value,101000),wall=102,mono=11))
        for message in (tick(stamp=100000),tick(stamp=99000),tick(index='ETHUSD_RTI'),
                        tick(stamp=90000),tick(stamp=110000),{'type':'heartbeat','verified':True},
                        {'type':'cfbenchmarks_value','msg':{'index_id':'BRTI','data':'{'}},None):
            self.assertFalse(feed.consume(message,wall=102,mono=11))
        self.assertEqual(feed.snapshot(),saved)

    def test_stale_and_disconnected_values_do_not_show_difference(self):
        feed=ReferenceFeed(None,'/unused');feed.consume(tick(),wall=101,mono=10)
        message={'market':'KXBTC15M-T','btc_target':'67900','btc_reference':feed.snapshot()}
        self.assertIn('+100.12 USD',reference_text(message,mono=10))
        self.assertIn('STALE',reference_text(message,mono=15))
        self.assertIn('Difference: —',reference_text(message,mono=15))
        feed.consume({'type':'error'})
        message['btc_reference']=feed.snapshot()
        self.assertIn('Difference: —',reference_text(message,mono=10))
        message['market']=None
        self.assertIn('BTC target: Unavailable • BRTI: Unavailable',reference_text(message,mono=10))

    def test_new_target_and_missing_target(self):
        feed=ReferenceFeed(None,'/unused');feed.consume(tick(),wall=101,mono=10)
        msg={'market':'KXBTC15M-NEW','btc_target':'68100','btc_reference':feed.snapshot()}
        self.assertIn('-99.88 USD',reference_text(msg,mono=10))
        msg['btc_target']=None
        self.assertIn('Difference: —',reference_text(msg,mono=10))

    def test_reference_relay_uses_fixed_channel_and_tls(self):
        sent=[];options={}
        class End(Exception):pass
        class WS:
            async def send(self,raw):sent.append(json.loads(raw))
            async def recv(self):raise End()
        class Context:
            async def __aenter__(self):return WS()
            async def __aexit__(self,*args):pass
        def connect(url,**kwargs):
            self.assertEqual(url,'wss://external-api-ws.kalshi.com/trade-api/ws/v2')
            options.update(kwargs);return Context()
        from unittest.mock import Mock
        sock=Mock()
        with patch('socket.create_connection',return_value=sock),patch('websockets.connect',side_effect=connect):
            with self.assertRaises(End):
                asyncio.run(relay({'environment':'production','ws_host':'external-api-ws.kalshi.com',
                                  'ws_addresses':['192.0.2.1']},'BRTI',{},lambda x:None,reference=True))
        self.assertEqual(sent[0]['params'],{'channels':['cfbenchmarks_value'],'index_ids':['BRTI']})
        self.assertTrue(options['ssl'].check_hostname)
        self.assertEqual(options['server_hostname'],'external-api-ws.kalshi.com')

    def test_reference_rejects_arbitrary_ticker_and_host(self):
        for host,ticker in [('evil.example','BRTI'),('external-api-ws.kalshi.com','ETHUSD_RTI')]:
            with self.assertRaises(ValueError):
                asyncio.run(relay({'environment':'production','ws_host':host},ticker,{},lambda x:None,reference=True))
