"""Saved strategy defaults stay consistent across fresh launches."""
from dataclasses import asdict
import inspect
import unittest
from kalshi_bot.strategy import DEFAULT_POLICY, StrategyPolicy, policy, set_policy
from kalshi_bot.desktop_runtime import sandbox_command


EXPECTED = dict(buy_min_cents=96, sell_cents=89, entry_window_seconds=240,
                entry_cutoff_seconds=15, max_volatility_pct=12.0,
                max_volatility_5m_pct=11.0, max_shock_1m_pct=11.0,
                target_buy_usd=30.0, target_exit_usd=19.0)


class SavedDefaultsTests(unittest.TestCase):
    def test_fresh_policy_matches_saved_settings(self):
        self.assertEqual(asdict(StrategyPolicy()), EXPECTED)
        self.assertEqual(asdict(DEFAULT_POLICY), EXPECTED)

    def test_launcher_defaults_match_every_strategy_field(self):
        parameters = inspect.signature(sandbox_command).parameters
        for name, value in EXPECTED.items():
            with self.subTest(name=name):
                self.assertEqual(parameters[name].default, value)

    def test_omitted_settings_use_defaults_and_explicit_changes_survive(self):
        previous = asdict(policy())
        self.addCleanup(lambda: set_policy(**previous))
        self.assertEqual(asdict(set_policy(96, 89)), EXPECTED)
        configured = set_policy(95, 71, 180, 20, 30, 25, 6, 50, 35)
        self.assertEqual(configured.max_shock_1m_pct, 6)
        self.assertEqual(configured.target_buy_usd, 50)
        self.assertEqual(configured.entry_window_seconds, 180)

    def test_order_expiry_uses_explicit_policy_instead_of_imported_defaults(self):
        from decimal import Decimal
        from kalshi_bot.execution import order_payload
        from kalshi_bot.trade_api import CREATE, validate_write
        previous = asdict(policy())
        self.addCleanup(lambda: set_policy(**previous))
        for blackout in (15, 30):
            set_policy(96, 89, entry_cutoff_seconds=blackout)
            order = order_payload('KXBTC15M-T', 'buy', 'no',
                                  Decimal('.98'), 3, 900, 'defaults-test')
            self.assertEqual(order['expiration_time'], 900-blackout)
            self.assertEqual(validate_write('POST', CREATE, order, now=660), CREATE)
            with self.assertRaises(ValueError):
                validate_write('POST', CREATE, order, now=659.99)
