"""Verified GET observation timing and bounded read-only TLS reuse."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
import json
import time
import unittest
from unittest.mock import Mock,patch
from kalshi_bot import gateway
from kalshi_bot.api import Client,SchemaError,ROOT,READ_STARTED_HEADER,APIError
from kalshi_bot.execution import order_payload
from kalshi_bot.trade_api import CREATE


class Clock:
    def __init__(self):self.now=100.
    def __call__(self):return self.now


class ReadFreshnessTests(unittest.TestCase):
    def connection(self):
        connection=Mock();response=Mock(status=200,will_close=False)
        response.read.return_value=b'{}';response.getheaders.return_value=[]
        connection.getresponse.return_value=response
        return connection
    def config(self):return {'host':'external-api.demo.kalshi.co','addresses':['8.8.8.8','1.1.1.1'],'allow_trading':True,'_read_pool':gateway.ReadConnections()}

    def test_reuses_verified_get_connection_with_new_auth_headers(self):
        config=self.config();connection=self.connection()
        try:
            with patch.object(gateway,'PinnedHTTPS',return_value=connection) as factory:
                gateway.forward(config,ROOT+'/portfolio/balance',{'auth':'first'})
                gateway.forward(config,ROOT+'/portfolio/balance',{'auth':'second'})
                factory.assert_called_once_with(config['host'],'8.8.8.8');connection.connect.assert_called_once()
                self.assertEqual([c.kwargs['headers']['auth'] for c in connection.request.call_args_list],['first','second'])
                connection.close.assert_not_called()
        finally:config['_read_pool'].close()
        connection.close.assert_called_once()

    def test_broken_reused_get_recovers_once_without_replaying_write(self):
        config=self.config();first=self.connection();second=self.connection()
        try:
            with patch.object(gateway,'PinnedHTTPS',side_effect=[first,second]) as factory:
                gateway.forward(config,ROOT+'/portfolio/balance',{})
                first.request.side_effect=OSError('closed keepalive')
                gateway.forward(config,ROOT+'/portfolio/balance',{})
                self.assertEqual(factory.call_count,2);first.close.assert_called_once();second.request.assert_called_once()
        finally:config['_read_pool'].close()

    def test_write_uses_fresh_connection_and_is_never_replayed(self):
        config=self.config();reader=self.connection();writer=self.connection()
        body=order_payload('KXBTC15M-T','buy','no',D('.98'),1,int(time.time())+90,'synthetic')
        try:
            with patch.object(gateway,'PinnedHTTPS',side_effect=[reader,writer]) as factory:
                gateway.forward(config,ROOT+'/portfolio/balance',{})
                writer.request.side_effect=OSError('uncertain write')
                with self.assertRaises(APIError):gateway.forward(config,CREATE,{},'POST',body)
                self.assertEqual(factory.call_count,2);writer.request.assert_called_once();reader.request.assert_called_once()
        finally:config['_read_pool'].close()

    def test_pool_expires_bounds_and_never_shares_one_connection_concurrently(self):
        clock=Clock();pool=gateway.ReadConnections(clock=clock,max_idle=1);first=self.connection();second=self.connection()
        pool.put('official','8.8.8.8',first);pool.put('official','8.8.8.8',second);second.close.assert_called_once()
        self.assertIsNone(pool.take('different','8.8.8.8'));self.assertIsNone(pool.take('official','1.1.1.1'))
        with ThreadPoolExecutor(max_workers=2) as workers:results=list(workers.map(lambda _:pool.take('official','8.8.8.8'),range(2)))
        self.assertEqual(sum(c is first for c in results),1)
        pool.put('official','8.8.8.8',first);clock.now+=16
        self.assertIsNone(pool.take('official','8.8.8.8'));first.close.assert_called_once();pool.close()

    def test_gateway_timestamps_after_connect_and_replaces_upstream_spoof(self):
        clock=Clock();connection=self.connection()
        connection.connect.side_effect=lambda:setattr(clock,'now',110.)
        connection.getresponse.return_value.getheaders.return_value=[(READ_STARTED_HEADER,'999999999999999999')]
        with patch.object(gateway,'PinnedHTTPS',return_value=connection),patch.object(gateway.time,'monotonic_ns',side_effect=lambda:int(clock()*1e9)):
            _,headers,_=gateway.forward(self.config()|{'_read_pool':None},ROOT+'/portfolio/balance',{})
            self.assertEqual(headers[READ_STARTED_HEADER],str(110_000_000_000))

    def test_client_uses_successful_get_start_instead_of_tls_setup_time(self):
        clock=Clock()
        def transport(*args):
            clock.now+=10
            return 200,{READ_STARTED_HEADER:str(int((clock()-1)*1e9))},b'{}'
        client=Client(Mock(),transport,clock=clock)
        client.get('/portfolio/balance')
        self.assertEqual(client.observed_at,109.);self.assertEqual(clock()-client.observed_at,1.)

    def test_pagination_keeps_oldest_successful_page_age(self):
        clock=Clock();calls=[]
        def transport(*args):
            calls.append(1);clock.now+=10
            return 200,{READ_STARTED_HEADER:str(int((clock()-1)*1e9))},json.dumps({'orders':[],'cursor':'next' if len(calls)==1 else ''}).encode()
        client=Client(Mock(),transport,clock=clock)
        client.pages('/portfolio/orders')
        self.assertEqual(client.observed_at,109.);self.assertEqual(clock()-client.observed_at,11.)

    def test_invalid_old_future_or_duplicate_gateway_timestamp_fails_closed(self):
        for value in ('garbage','-1','1','99999999999999999999','1.0','١٢٣'):
            with self.subTest(value=value):
                clock=Clock();client=Client(Mock(),lambda *a:(200,{READ_STARTED_HEADER:value},b'{}'),clock=clock)
                with self.assertRaises(SchemaError):client.get('/portfolio/balance')
                self.assertIsNone(client.observed_at)
        client=Client(Mock(),lambda *a:(200,{READ_STARTED_HEADER:'100000000000',READ_STARTED_HEADER.upper():'100000000000'},b'{}'),clock=Clock())
        with self.assertRaises(SchemaError):client.get('/portfolio/balance')

    def test_trusted_observation_roundtrip_through_real_unix_gateway(self):
        import os,tempfile,threading
        from pathlib import Path
        from kalshi_bot.api import GatewayTransport
        with tempfile.TemporaryDirectory() as directory:
            server=gateway.Server(str(Path(directory)/'gateway.sock'),gateway.Handler)
            server.config={**self.config(),'client_uid':os.getuid(),'environment':'demo'}
            connection=self.connection();connection.connect.side_effect=lambda:time.sleep(.1)
            connection.getresponse.return_value.read.return_value=b'{"balance":600}'
            signer=Mock();signer.headers.return_value={'KALSHI-ACCESS-KEY':'generated-test','KALSHI-ACCESS-TIMESTAMP':'123','KALSHI-ACCESS-SIGNATURE':'synthetic'}
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            try:
                with patch.object(gateway,'PinnedHTTPS',return_value=connection) as factory:
                    thread.start();client=Client(signer,GatewayTransport(str(Path(directory)/'gateway.sock')))
                    before=time.monotonic();self.assertEqual(client.get('/portfolio/balance')['balance'],600)
                    self.assertGreaterEqual(client.observed_at-before,.09)
                    self.assertLessEqual(client.observed_at,time.monotonic())
                    client.get('/portfolio/balance');factory.assert_called_once();connection.connect.assert_called_once()
            finally:server.shutdown();server.server_close();thread.join(timeout=2)

    def test_gateway_requests_compression_and_reports_sizes_without_payload(self):
        import gzip
        raw=b'{"synthetic":"'+b'x'*10000+b'"}';compressed=gzip.compress(raw);connection=self.connection()
        connection.getresponse.return_value.read.return_value=compressed
        connection.getresponse.return_value.getheaders.return_value=[('Content-Encoding','gzip'),('Content-Type','application/json')]
        with patch.object(gateway,'PinnedHTTPS',return_value=connection):
            _,headers,data=gateway.forward(self.config()|{'_read_pool':None},ROOT+'/portfolio/balance',{})
            self.assertEqual(data,raw);self.assertEqual(connection.request.call_args.kwargs['headers']['Accept-Encoding'],'gzip')
            self.assertEqual(int(headers['x-kalshi-bot-wire-bytes']),len(compressed))
            self.assertEqual(int(headers['x-kalshi-bot-decoded-bytes']),len(raw))
            self.assertNotIn('Content-Encoding',headers)

    def test_older_gateway_without_header_preserves_conservative_fallback(self):
        clock=Clock()
        def transport(*args):clock.now+=10;return 200,{},b'{}'
        client=Client(Mock(),transport,clock=clock);client.get('/portfolio/balance')
        self.assertEqual(client.observed_at,100.)
