"""Passive connection reporting cannot enable trades or suppress validation."""
import unittest
from unittest.mock import Mock
from kalshi_bot.connection_status import ConnectionHealth

class Clock:
    def __init__(self):self.now=100.
    def __call__(self):return self.now

class ConnectionStatusTests(unittest.TestCase):
    def test_old_request_timeout_does_not_hide_new_authenticated_success(self):
        clock=Clock();health=ConnectionHealth(clock)
        health.record('/trade-api/v2/portfolio/orders/old',0)
        health.record('/trade-api/v2/portfolio/balance',200)
        value=health.snapshot()
        self.assertTrue(value['account_connected'])
        self.assertEqual(value['connection_phase'],'degraded')
        self.assertEqual(value['account_api_error'],'API request retry in progress')
        self.assertNotIn('account_checks_ready',value)
        clock.now+=16
        self.assertFalse(health.snapshot()['account_connected'])

    def test_historical_not_found_does_not_latch_transport_reconnecting(self):
        health=ConnectionHealth();old='/trade-api/v2/portfolio/orders/old'
        health.record(old,404)
        health.record('/trade-api/v2/portfolio/balance',200)
        self.assertTrue(health.snapshot()['account_connected'])
        self.assertFalse(health.snapshot()['auto_reconnecting'])
        self.assertEqual(health.snapshot()['account_request_error_codes'],[404])
        health.record(old,200)
        self.assertEqual(health.snapshot()['account_request_error_codes'],[])

    def test_historical_not_found_does_not_hide_real_auth_failure(self):
        health=ConnectionHealth();health.record('/trade-api/v2/portfolio/orders',401)
        health.record('/trade-api/v2/portfolio/orders/old',404)
        health.record('/trade-api/v2/portfolio/balance',200)
        self.assertEqual(health.snapshot()['connection_phase'],'authentication error')

    def test_public_data_does_not_claim_authenticated_account(self):
        health=ConnectionHealth();health.record('/trade-api/v2/exchange/status',200)
        value=health.snapshot();self.assertFalse(value['account_connected']);self.assertTrue(value['auto_reconnecting'])
    def test_private_success_survives_expected_account_cache_invalidation(self):
        from tempfile import TemporaryDirectory
        from test_prepared import Fixture
        from kalshi_bot.prepared_runtime import state
        with TemporaryDirectory() as directory:
            f=Fixture(directory)
            try:
                health=ConnectionHealth(clock=f.clock);health.record('/trade-api/v2/portfolio/balance',200)
                f.inputs.invalidate('Prepared order consumed')
                result=state(f.journal,f.inputs,f.engine,f.feed,Mock(execution_at=0),f.market,{},'demo',health)
                self.assertTrue(result['account_connected']);self.assertFalse(result['account_checks_ready'])
            finally:f.close()
    def test_transient_failure_retries_without_new_authorization(self):
        health=ConnectionHealth();path='/trade-api/v2/portfolio/orders'
        health.record(path,200);health.record(path,502)
        value=health.snapshot();self.assertTrue(value['auto_reconnecting']);self.assertEqual(value['connection_phase'],'reconnecting')
        health.record(path,200);self.assertTrue(health.snapshot()['account_connected'])
        self.assertNotIn('running',health.snapshot())
    def test_auth_error_is_not_cleared_by_another_endpoint(self):
        health=ConnectionHealth();orders='/trade-api/v2/portfolio/orders'
        health.record(orders,401);health.record('/trade-api/v2/portfolio/balance',200)
        self.assertEqual(health.snapshot()['connection_phase'],'authentication error')
        self.assertFalse(health.snapshot()['auto_reconnecting'])
        health.record(orders,200);self.assertTrue(health.snapshot()['account_connected'])
    def test_silent_private_api_becomes_automatic_recovery(self):
        clock=Clock();health=ConnectionHealth(clock);health.record('/trade-api/v2/portfolio/balance',200);clock.now+=16
        value=health.snapshot();self.assertTrue(value['auto_reconnecting']);self.assertFalse(value['account_connected'])
