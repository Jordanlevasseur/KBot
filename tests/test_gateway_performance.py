"""TLS trust remains strict while avoiding repeated CA parsing under load."""
import ssl
import gzip
import threading
import unittest
from unittest.mock import patch,Mock
from kalshi_bot import gateway


class GatewayPerformanceTests(unittest.TestCase):
    def test_client_disconnect_after_success_does_not_send_false_502(self):
        handler=Mock();handler.path='/trade-api/v2/portfolio/balance'
        handler.headers.get.return_value=None
        handler.headers.get_all.return_value=['synthetic-auth']
        handler.end_headers.side_effect=BrokenPipeError()
        with patch.object(gateway,'forward',return_value=(200,{},b'{}')):
            gateway.Handler.do_GET(handler)
        handler.send_error.assert_not_called()
        self.assertTrue(handler.close_connection)

    def test_concurrent_connections_share_verified_context_without_weakening_tls(self):
        context=ssl.create_default_context();created=[];connections=[]
        with patch.object(gateway,'_TLS_CONTEXT',None),patch.object(gateway.ssl,'create_default_context',return_value=context) as factory:
            threads=[threading.Thread(target=lambda:created.append(gateway.tls_context())) for _ in range(20)]
            for thread in threads:thread.start()
            for thread in threads:thread.join()
            for _ in range(20):connections.append(gateway.PinnedHTTPS('external-api.demo.kalshi.co','8.8.8.8'))
            factory.assert_called_once()
            self.assertTrue(all(c is context for c in created))
            self.assertTrue(all(c._context is context and c.host=='external-api.demo.kalshi.co' and c.address=='8.8.8.8' for c in connections))
            self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
            for connection in connections:connection.close()

    def test_gzip_and_identity_produce_identical_bounded_json(self):
        data=b'{"synthetic_history":"'+b'x'*500000+b'"}'
        compressed=gzip.compress(data)
        self.assertLess(len(compressed),len(data)/100)
        self.assertEqual(gateway.decoded_response(compressed,[('Content-Encoding','gzip')]),data)
        self.assertEqual(gateway.decoded_response(data,[]),data)
        self.assertEqual(gateway.decoded_response(data,[('Content-Encoding','identity')]),data)

    def test_gzip_bombs_truncation_unknown_and_duplicate_encodings_fail_closed(self):
        cases=[(gzip.compress(b'x'*(gateway.MAX_BODY+1)),[('Content-Encoding','gzip')]),
               (gzip.compress(b'{}')[:-1],[('Content-Encoding','gzip')]),
               (b'broken',[('Content-Encoding','gzip')]),
               (gzip.compress(b'{}')+gzip.compress(b'{}'),[('Content-Encoding','gzip')]),
               (b'{}',[('Content-Encoding','br')]),
               (b'{}',[('Content-Encoding','gzip'),('Content-Encoding','gzip')]),
               (b'x'*(gateway.MAX_BODY+1),[])]
        for data,headers in cases:
            with self.subTest(headers=headers):
                with self.assertRaises(gateway.APIError):gateway.decoded_response(data,headers)
