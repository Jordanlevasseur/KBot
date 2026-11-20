import base64
import json
from pathlib import Path
import tempfile
import time
import unittest
from decimal import Decimal as D
from unittest.mock import Mock,patch
from kalshi_bot.clocks import ReplayClock,Countdown
from kalshi_bot.streams import Book,persist_crossing,ws_headers,BookFeed
from kalshi_bot.fees import entry_reserve
from kalshi_bot.api import SchemaError


class ClockTests(unittest.TestCase):
    def test_database_latency_does_not_slow_replay_clock(self):
        now=[100.0]
        clock=ReplayClock(780,clock=lambda:now[0])
        now[0]+=2.75
        self.assertEqual(clock.now(),782.75)
        clock.set_rate(5)
        now[0]+=2
        self.assertEqual(clock.now(),792.75)

    def test_ui_updates_between_worker_snapshots_and_includes_pipe_delay(self):
        now=[100.0]
        clock=Countdown(clock=lambda:now[0])
        clock.sync({'remaining':120,'clock_sample_mono':98,'clock_rate':1})
        self.assertEqual(clock.text(),'01:58')
        now[0]+=10
        self.assertEqual(clock.text(),'01:48')

    def test_accelerated_clock_is_explicit_and_expires_at_zero(self):
        now=[100.0]; clock=Countdown(clock=lambda:now[0])
        clock.sync({'remaining':30,'clock_rate':5})
        now[0]+=2
        self.assertEqual(clock.seconds(),20)
        now[0]+=100
        self.assertEqual(clock.text(),'00:00')
        clock.sync({}); self.assertEqual(clock.text(),'—')


class StreamTests(unittest.TestCase):
    def snapshot(self,price='.95',seq=2):
        return {'type':'orderbook_snapshot','sid':1,'seq':seq,'msg':{'market_ticker':'KXBTC15M-T','yes_dollars_fp':[[price,'10']],'no_dollars_fp':[['.03','10']]}}

    def test_snapshot_delta_and_complementary_asks(self):
        book=Book('KXBTC15M-T'); book.consume(self.snapshot())
        self.assertEqual(book.prices()['yes_ask'],D('.97'))
        book.consume({'type':'orderbook_delta','sid':1,'seq':3,'msg':{'market_ticker':'KXBTC15M-T','side':'yes','price_dollars':'.95','delta_fp':'-10'}})
        self.assertIsNone(book.prices()['yes_bid'])

    def test_gap_invalidates_book_until_new_snapshot(self):
        book=Book('KXBTC15M-T'); book.consume(self.snapshot())
        with self.assertRaises(SchemaError):
            book.consume({'type':'orderbook_delta','sid':1,'seq':9,'msg':{'market_ticker':'KXBTC15M-T','side':'yes','price_dollars':'.95','delta_fp':'-1'}})
        self.assertIsNone(book.prices())
        book.consume(self.snapshot(seq=20)); self.assertIsNotNone(book.prices())

    def test_crossing_is_durable_even_when_price_rebounds(self):
        from kalshi_bot.execution import Journal
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db'; journal=Journal(path,'generated-test')
            journal.ready=True; journal.running=True
            broker=Mock()
            broker.create.side_effect=lambda p:{'order_id':'remote','client_order_id':p['client_order_id']}
            journal.submit(broker,'KXBTC15M-T','buy','yes',D('.96'),1,900,780)
            persist_crossing(path,'KXBTC15M-T','yes',D('.86'))
            persist_crossing(path,'KXBTC15M-T','yes',D('.95'))
            self.assertEqual(journal.db.execute('SELECT stop FROM markets').fetchone()[0],1)
            journal.close()
            journal=Journal(path,'generated-test')
            self.assertEqual(journal.db.execute('SELECT stop FROM markets').fetchone()[0],1)
            journal.close()

    def test_ws_signature_uses_official_ws_path(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from kalshi_bot.api import Signer
        key=ed25519.Ed25519PrivateKey.generate()
        signer=Signer('test-key',key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        headers=ws_headers(signer)
        key.public_key().verify(base64.b64decode(headers['KALSHI-ACCESS-SIGNATURE']),(headers['KALSHI-ACCESS-TIMESTAMP']+'GET/trade-api/ws/v2').encode())

    def test_stale_stream_blocks_quote(self):
        feed=BookFeed(Mock(),'/not-used','/not-used')
        feed.ticker='KXBTC15M-T'; feed.book=Book(feed.ticker); feed.book.consume(self.snapshot())
        feed.heartbeat=time.monotonic()-6
        self.assertIsNone(feed.quote(feed.ticker))


class FeeTests(unittest.TestCase):
    def client(self,multiplier=1,changes=()):
        client=Mock()
        client.get.return_value={'series':{'ticker':'KXBTC15M','fee_type':'quadratic','fee_multiplier':multiplier}}
        client.pages.return_value=list(changes)
        return client

    def test_reserve_covers_price_improvement_and_fragmentation(self):
        self.assertEqual(entry_reserve(self.client(),{'event_ticker':'KXBTC15M-E'}),D('.03'))

    def test_event_multiplier_cannot_silently_reduce_fee_reserve(self):
        changes=[{'event_ticker':'KXBTC15M-E','fee_type_override':'quadratic','fee_multiplier_override':2}]
        self.assertEqual(entry_reserve(self.client(changes=changes),{'event_ticker':'KXBTC15M-E'}),D('.05'))

    def test_unknown_fee_model_fails_closed(self):
        client=self.client(); client.get.return_value['series']['fee_type']='unknown'
        with self.assertRaises(SchemaError): entry_reserve(client,{'event_ticker':'KXBTC15M-E'})

class OnlineGateTests(unittest.TestCase):
    def test_production_worker_cannot_activate_without_authorization(self):
        from kalshi_bot.desktop_worker import run_trading
        with patch('kalshi_bot.credentials.load') as load:
            with self.assertRaises(ValueError):
                run_trading(Path('/not-used'),None,False,'production',False)
            load.assert_not_called()

    def test_gateways_require_correct_stream_host_and_ip_policy(self):
        from kalshi_bot.gateway import validate_config
        config={'environment':'production','host':'external-api.kalshi.com','uid':999,'client_uid':1000,
                'addresses':['8.8.8.8'],'ws_addresses':['8.8.8.8'],'ws_host':'evil.example'}
        with self.assertRaises(ValueError): validate_config(config)
        config['ws_host']='external-api-ws.kalshi.com'
        self.assertIs(validate_config(config),config)

    def test_health_check_never_forwards_account_data(self):
        import os,threading
        from kalshi_bot.gateway import Server,Handler
        from kalshi_bot.api import UnixHTTP
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'socket')
            server=Server(path,Handler)
            server.config={'environment':'production','client_uid':os.getuid(),'allow_trading':True,'ws_host':'external-api-ws.kalshi.com'}
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                with patch('kalshi_bot.gateway.forward') as upstream:
                    connection=UnixHTTP(path); connection.request('GET','/internal/health')
                    response=connection.getresponse(); value=json.loads(response.read())
                    connection.close()
                    self.assertEqual(value,{'environment':'production','trading_enabled':True,'streaming_enabled':True,'stream_protocol':2,'entry_window_seconds':240,'market_balance_scope':1,'strategy_policy_version':1})
                    upstream.assert_not_called()
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__=='__main__': unittest.main()
