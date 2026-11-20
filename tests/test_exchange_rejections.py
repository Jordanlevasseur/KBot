"""Documented rejection diagnostics and unsafe closed-ID replay regression."""
import copy
import json
import tempfile
import time
import unittest
from strategy_fixture import configure_policy

def setUpModule():
    configure_policy(92,86)

from decimal import Decimal as D
from pathlib import Path
from unittest.mock import Mock,patch
from kalshi_bot.api import APIError,Client
from kalshi_bot.exchange_errors import public_code
from kalshi_bot.trade_api import OrderBroker
from kalshi_bot.execution import order_payload
from kalshi_bot.diagnostics import failure_reason
from test_protective_sells import Fixture,TICKER


class RejectionTests(unittest.TestCase):
    def test_flat_and_nested_documented_codes_are_retained_without_free_text(self):
        for body in ({'code':'invalid_price','message':'PRIVATE','details':'PRIVATE'},
                     {'error':{'code':'insufficient_balance','message':'PRIVATE','details':'PRIVATE'}}):
            code=public_code(json.dumps(body).encode());error=APIError(400,code)
            self.assertIsNotNone(code);self.assertIn(code,str(error));self.assertNotIn('PRIVATE',str(error))
    def test_unknown_codes_and_unstructured_bodies_cannot_leak_secrets(self):
        for raw in (b'PRIVATE',b'{"code":"private_key_secret"}',b'{"error":"PRIVATE"}',b'[]',b'{"code":[]}',b'{"code":true}',b'['*2000):
            self.assertIsNone(public_code(raw))
        error=APIError(400,'private_key_secret');self.assertIsNone(error.code)
        self.assertNotIn('private_key',str(error))
    def test_large_body_is_not_parsed(self):
        self.assertIsNone(public_code(b'{"code":"invalid_price","message":"'+b'x'*17000+b'"}'))
    def test_real_order_broker_keeps_public_rejection_reason(self):
        signer=Mock();signer.write_headers.return_value={}
        payload=order_payload(TICKER,'buy','no',D('.97'),13,int(time.time())+100,'synthetic')
        response=Mock(status=400);response.read.return_value=b'{"code":"invalid_price","details":"PRIVATE-ACCOUNT-OR-KEY"}'
        connection=Mock();connection.getresponse.return_value=response
        with patch('kalshi_bot.trade_api.UnixHTTP',return_value=connection):
            with self.assertRaises(APIError) as caught:OrderBroker(signer,'/unused',True).create(payload)
        self.assertEqual(caught.exception.code,'invalid_price')
        self.assertIn('invalid_price',failure_reason(caught.exception));self.assertNotIn('PRIVATE',str(caught.exception))
    def test_get_errors_keep_safe_code_without_retrying_definitive_rejection(self):
        transport=Mock(return_value=(400,{},b'{"error":{"code":"insufficient_balance","message":"PRIVATE"}}'))
        signer=Mock();signer.headers.return_value={}
        with self.assertRaises(APIError) as caught:Client(signer,transport).get('/portfolio/balance')
        self.assertEqual(caught.exception.code,'insufficient_balance');self.assertEqual(transport.call_count,1)
        self.assertNotIn('PRIVATE',str(caught.exception))
    def test_rejected_no_97_is_reported_with_exact_safe_code_and_consumes_no_buy(self):
        for code in ('invalid_order','invalid_price','insufficient_balance'):
            with self.subTest(code=code),tempfile.TemporaryDirectory() as directory:
                f=Fixture(directory,'no');f.cycle('.86');f.venue.fill(f.venue.sells()[0],10);f.cycle('.92')
                f.journal.running=True;f.feed.prices={'no_bid':D('.96'),'no_ask':D('.97'),'yes_bid':D('.03'),'yes_ask':D('.04')}
                def reject(payload):raise APIError(400,code)
                f.venue.create=reject
                try:
                    with self.assertRaises(APIError):f.cycle()
                    row=f.journal.db.execute("SELECT event FROM audit WHERE event LIKE 'Decision failure:%' ORDER BY id DESC LIMIT 1").fetchone()
                    detail=json.loads(row[0][len('Decision failure: '):])
                    self.assertEqual(detail['side'],'no');self.assertEqual(detail['limit'],'0.97')
                    self.assertEqual(detail['exchange_code'],code);self.assertEqual(detail['outcome'],'rejected')
                    self.assertEqual(f.journal.db.execute('SELECT buys FROM markets').fetchone()[0],1)
                    self.assertIsNone(f.journal.active())
                finally:f.close()
    def test_filled_order_missing_from_read_snapshot_cannot_be_replayed(self):
        for side in ('yes','no'):
            with self.subTest(side=side),tempfile.TemporaryDirectory() as directory:
                f=Fixture(directory,side);create=f.venue.create
                def accept_fill_then_timeout(payload):
                    reply=create(payload);order=f.venue.sells()[-1]
                    f.venue.fill(order,10);raise APIError()
                f.venue.create=accept_fill_then_timeout
                try:
                    with self.assertRaises(APIError):f.cycle('.86')
                    self.assertEqual(f.venue.position,0)
                    f.venue.hide.add(f.venue.sells()[-1]['order_id']);f.venue.create=Mock(side_effect=AssertionError('Closed ID can be reused: no replay allowed'))
                    with self.assertRaises(RuntimeError):f.cycle('.92')
                    f.venue.create.assert_not_called();self.assertFalse(f.journal.ready)
                    f.venue.hide.clear();f.cycle('.92')
                    self.assertEqual(f.journal.position(),{});self.assertIsNone(f.journal.active())
                    self.assertEqual(f.venue.sold,10)
                finally:f.close()


if __name__=='__main__':unittest.main()
