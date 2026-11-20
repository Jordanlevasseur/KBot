"""Explicit historical strategy fixtures; never load or alter user settings."""
from dataclasses import asdict
import unittest
from kalshi_bot.strategy import policy, set_policy


def configure_policy(buy=92, sell=86):
    previous=asdict(policy())
    unittest.addModuleCleanup(lambda: set_policy(**previous))
    set_policy(buy,sell)
