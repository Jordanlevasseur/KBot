"""Deterministic relay failures; no network or credentials."""
import asyncio
import unittest
from unittest.mock import Mock, patch
from kalshi_bot.streams import relay, safe_stream_failure


class Finished(BaseException):pass


class Connection:
    def __init__(self,ws,error=None):self.ws=ws;self.error=error
    async def __aenter__(self):
        if self.error:raise self.error
        return self.ws
    async def __aexit__(self,*args):return False


class WS:
    def __init__(self,pong_error=None):self.pings=0;self.pong_error=pong_error
    async def send(self,body):pass
    async def recv(self):return '{"type":"subscribed"}'
    async def ping(self):
        self.pings+=1
        future=asyncio.get_running_loop().create_future()
        if self.pong_error:future.set_exception(self.pong_error)
        else:future.set_result(None)
        return future


class StreamFailureTests(unittest.TestCase):
    config={'environment':'demo','ws_host':'external-api-ws.demo.kalshi.co','ws_addresses':['192.0.2.1','192.0.2.2']}

    def test_failed_control_frame_pong_is_not_retried_as_recv_timeout(self):
        first=WS(TimeoutError());second=WS();frames=[]
        def write(body):
            frames.append(body)
            if len(frames)==2:raise Finished()
        with patch('kalshi_bot.streams.socket.create_connection',return_value=Mock()),patch('websockets.connect',side_effect=[Connection(first),Connection(second)]),patch('kalshi_bot.streams.time',Mock(monotonic=Mock(side_effect=[0,2,3]))),patch('kalshi_bot.streams.safe_stream_failure') as log:
            with self.assertRaises(Finished):asyncio.run(relay(self.config,'KXBTC15M-T',{},write))
        self.assertEqual(first.pings,1)
        self.assertFalse(any(f.get('verified') for f in frames))
        self.assertEqual(log.call_args.args[0],'peer-verification')

    def test_local_write_timeout_is_never_misclassified_as_peer_failure(self):
        ws=WS();sock=Mock()
        with patch('kalshi_bot.streams.socket.create_connection',return_value=sock),patch('websockets.connect',return_value=Connection(ws)),patch('kalshi_bot.streams.safe_stream_failure') as log:
            with self.assertRaises(OSError):asyncio.run(relay(self.config,'KXBTC15M-T',{},Mock(side_effect=TimeoutError())))
        self.assertEqual(ws.pings,0)
        self.assertEqual([c.args[0] for c in log.call_args_list],['local-write','local-write'])
        self.assertEqual(sock.close.call_count,2)

    def test_handshake_failure_recovers_with_next_approved_address(self):
        for failure in (TimeoutError(),):
            with patch('kalshi_bot.streams.socket.create_connection',return_value=Mock()) as tcp,patch('websockets.connect',side_effect=[Connection(None,failure),Connection(WS())]),patch('kalshi_bot.streams.safe_stream_failure') as log:
                with self.assertRaises(Finished):asyncio.run(relay(self.config,'KXBTC15M-T',{},Mock(side_effect=Finished())))
            self.assertEqual(tcp.call_count,2)
            self.assertEqual(log.call_args.args[0],'websocket-handshake')

    def test_failure_logs_are_bounded_and_never_include_exception_text(self):
        error=RuntimeError('SECRET signed header');error.status_code=401
        with patch('kalshi_bot.streams._FAILURE_NEXT',{}),patch('kalshi_bot.streams.time.monotonic',return_value=100),patch('kalshi_bot.streams.logging.getLogger') as logger:
            for _ in range(100):safe_stream_failure('receive',error)
            safe_stream_failure('websocket-handshake',error)
            self.assertEqual(logger.return_value.warning.call_count,2)
            self.assertNotIn('SECRET',str(logger.return_value.warning.call_args_list))
