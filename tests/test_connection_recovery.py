"""Market-stream recovery and exact preservation of customer execution routes."""
import ast
import hashlib
import json
from pathlib import Path
import socket
import time
import unittest
from unittest.mock import Mock,patch
from kalshi_bot.api import SchemaError
from kalshi_bot.streams import Book,BookFeed
from test_market_pipeline import snapshot

ROOT=Path(__file__).resolve().parent.parent

class ConnectionRecoveryTests(unittest.TestCase):
    def test_runtime_refresh_never_notifies_optional_none_wakeup(self):
        source=(ROOT/'kalshi_bot'/'prepared_runtime.py').read_text()
        self.assertIn('runtime_wakeup=wakeup',source)
        self.assertIn("elif name=='refresh':monitor.refresh();runtime_wakeup.notify()",source)
        self.assertNotIn("elif name=='refresh':monitor.refresh();wakeup.notify()",source)

    def test_gateway_setup_persists_socket_and_service_after_reboot(self):
        source=(ROOT/'deploy'/'setup-system.py').read_text()
        self.assertIn("systemctl','enable',service,socket",source)

    def test_unchanged_credential_cash_fee_signing_and_runtime_order_routes(self):
        values=json.loads((ROOT/'docs/connection-route-baseline.json').read_text())
        # Whole-file snapshots from an earlier repair cannot validate files
        # intentionally repaired since then. Keep the unchanged components
        # frozen; execution, policy and relay behavior have dedicated tests.
        for name in ('credentials.py','buying_power.py','fees.py'):
            digest=values[name]
            with self.subTest(module=name):self.assertEqual(hashlib.sha256((ROOT/'kalshi_bot'/name).read_bytes()).hexdigest(),digest)
        frozen=json.loads((ROOT/'docs/connection-execution-invariants.json').read_text())
        tree=ast.parse((ROOT/'kalshi_bot/streams.py').read_text())
        for node in tree.body:
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name=='ws_headers':
                self.assertEqual(hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest(),frozen['stream_route_functions'][node.name])
        tree=ast.parse((ROOT/'kalshi_bot/prepared_runtime.py').read_text());calls=[]
        for node in ast.walk(tree):
            if not isinstance(node,ast.Call):continue
            f=node.func
            if isinstance(f,ast.Name) and f.id=='PreparedEngine':
                self.assertEqual([(k.arg,ast.dump(k.value)) for k in node.keywords],
                                 [('missing_verifier',"Name(id='missing_verifier', ctx=Load())")])
                node.keywords=[]  # Added read-only verifier, not an order route.
            if isinstance(f,ast.Name) and f.id in ('OrderBroker','Journal','PreparedEngine'):calls.append(ast.dump(node,include_attributes=False))
            elif isinstance(f,ast.Attribute) and isinstance(f.value,ast.Name) and f.value.id in ('engine','journal','broker') and f.attr in ('execute','submit','cancel','create','claim','apply','build'):calls.append(ast.dump(node,include_attributes=False))
        self.assertEqual(sorted(calls),frozen['runtime_order_calls'])

    def test_documented_empty_snapshot_replaces_all_old_liquidity(self):
        book=Book('KXBTC15M-T');book.consume(snapshot())
        empty=snapshot(seq=2);empty['msg']={'market_ticker':'KXBTC15M-T'}
        book.consume(empty);self.assertTrue(book.valid)
        self.assertTrue(all(value is None for value in book.prices().values()))
        partial=snapshot(seq=3);partial['msg'].pop('no_dollars_fp')
        with self.assertRaises(SchemaError):book.consume(partial)
        self.assertIsNone(book.prices())

    def test_control_frame_timestamp_does_not_invalidate_current_prices(self):
        feed=BookFeed(Mock(),'/unused',None);feed.ticker='KXBTC15M-T';feed.generation=1;count=[0];observed=[]
        def line(*_):
            count[0]+=1
            if count[0]==1:return json.dumps(snapshot()).encode()
            if count[0]==2:return json.dumps({'type':'subscribed','sending_ts_ms':0}).encode()
            observed.append(feed.quote(feed.ticker));feed.closed=True;return b''
        response=Mock(status=200);response.readline.side_effect=line;connection=Mock();connection.getresponse.return_value=response
        with patch('kalshi_bot.streams.UnixHTTP',return_value=connection),patch('kalshi_bot.streams.ws_headers',return_value={}):feed._run(feed.ticker,1)
        self.assertIsNotNone(observed[0]);connection.request.assert_called_once()

    def test_socket_can_be_timed_out_after_http_reader_takes_ownership(self):
        left,right=socket.socketpair();reader=right.makefile('rb');right.close()
        try:
            right.settimeout(.05);start=time.monotonic()
            with self.assertRaises(TimeoutError):reader.readline()
            self.assertLess(time.monotonic()-start,.5)
        finally:left.close();reader.close()

    def test_disconnect_shuts_underlying_stream_socket_when_http_sock_is_none(self):
        connection=Mock();connection.sock=None;connection.stream_socket=Mock()
        BookFeed._disconnect(connection)
        connection.stream_socket.shutdown.assert_called_once_with(socket.SHUT_RDWR);connection.close.assert_called_once()

    def test_refresh_wakes_discovery_without_changing_entry_authorization(self):
        from kalshi_bot.market_data import MarketMonitor
        monitor=MarketMonitor(Mock(),Mock(),'/unused',Mock(),feed=Mock())
        try:
            monitor.running=True;monitor.refresh();self.assertTrue(monitor.discovery_wakeup.is_set());self.assertTrue(monitor.running)
        finally:monitor.close()

    def test_nonprice_traffic_cannot_keep_an_unverified_book_alive(self):
        feed=BookFeed(Mock(),'/unused',None);feed.ticker='KXBTC15M-T';feed.generation=1;count=[0];errors=[]
        def line(*_):
            count[0]+=1
            if count[0]==1:return json.dumps(snapshot()).encode()
            feed.heartbeat=time.monotonic()-6
            return json.dumps({'type':'subscribed'}).encode()
        def notified():
            if feed.error:
                errors.append((feed.error,feed.quote(feed.ticker)));feed.closed=True
        feed.on_update=notified;response=Mock(status=200);response.readline.side_effect=line;connection=Mock();connection.getresponse.return_value=response
        with patch('kalshi_bot.streams.UnixHTTP',return_value=connection),patch('kalshi_bot.streams.ws_headers',return_value={}):feed._run(feed.ticker,1)
        self.assertIn(('Stream verification expired; resnapshot required',None),errors)

    def test_idle_real_http_stream_closes_old_reader_and_recovers_automatically(self):
        import os,tempfile,threading
        from http.server import BaseHTTPRequestHandler
        from kalshi_bot.gateway import Server
        requests=[];recovered=threading.Event();done=threading.Event();errors=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                requests.append(time.monotonic());number=len(requests)
                self.send_response(200);self.end_headers()
                try:
                    self.wfile.write(json.dumps(snapshot()).encode()+b'\n');self.wfile.flush()
                    if number==1:done.wait(.6)
                    else:
                        while not done.wait(.03):
                            self.wfile.write(b'{"type":"heartbeat","verified":true}\n');self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError):pass
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'gateway.sock');server=Server(path,Handler);server.config={'client_uid':os.getuid()}
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();feed=BookFeed(Mock(),path,None)
            wait=feed.stopping.wait
            def notified():
                if feed.error:errors.append((feed.error,feed.quote(feed.ticker)))
                elif len(requests)>=2 and feed.quote(feed.ticker) is not None:recovered.set()
            feed.on_update=notified
            try:
                with patch('kalshi_bot.streams.ws_headers',return_value={}),patch('kalshi_bot.streams.STREAM_IDLE_TIMEOUT',.15),patch.object(feed.stopping,'wait',side_effect=lambda seconds:wait(min(seconds,.02))):
                    feed.watch('KXBTC15M-T');self.assertTrue(recovered.wait(2),'A silent old reader must not stall automatic resnapshot')
                    self.assertGreaterEqual(len(requests),2);self.assertTrue(any(price is None for _,price in errors))
                    self.assertIsNotNone(feed.quote('KXBTC15M-T'))
            finally:done.set();feed.close();server.shutdown();server.server_close();thread.join(timeout=2)
