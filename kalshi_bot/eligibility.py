"""Identical price tests for both outcomes; no direction-specific priority."""
from decimal import Decimal,ROUND_CEILING
import hashlib
from .strategy import policy


def eligible_sides(prices):
    minimum=Decimal(policy().buy_min_cents)/100
    return [side for side in ('yes','no') if prices and prices.get(side+'_ask') is not None
            and minimum<=Decimal(str(prices[side+'_ask']))<=Decimal('.99')]


def ordered_candidates(prices,ticker,previous_side=None):
    sides=eligible_sides(prices)
    # Lower eligible price first. Exact ties alternate after a completed purchase;
    # the first tie uses a stable, direction-neutral market hash across restarts.
    tie=('no' if previous_side=='yes' else 'yes') if previous_side else ('yes','no')[hashlib.sha256(ticker.encode()).digest()[0]%2]
    return sorted(sides,key=lambda side:(Decimal(str(prices[side+'_ask'])),side!=tie))


def entry_limit(ask):
    return Decimal(str(ask)).quantize(Decimal('.01'),rounding=ROUND_CEILING)
