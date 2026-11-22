"""Marketable protective limits, independent of the stop trigger."""
from decimal import Decimal as D
from .api import SchemaError, decimal


def protective_limit(market, side):
    """Lowest positive held-side price on the exchange's advertised grid.

    This is a crossing limit: the exchange matches available better bids first.
    NO orders use the complementary YES grid, so use its highest price.
    """
    ranges=market.get('price_ranges')
    if not isinstance(ranges,list) or not ranges:
        raise SchemaError('Protective exit needs verified market price_ranges')
    candidates=[]
    for band in ranges:
        try:
            start=decimal(band['start'],'grid start');end=decimal(band['end'],'grid end');step=decimal(band['step'],'grid step')
        except (KeyError,TypeError):
            raise SchemaError('Invalid protective exit grid') from None
        if not 0<=start<end<=1 or step<=0 or step>D('.01'):
            raise SchemaError('Invalid protective exit grid')
        first=start if start>0 else step
        last=start+((end-start)//step)*step
        if last>=1:last-=step
        value=first if side=='yes' else 1-last
        if D('.0001')<=value<1 and value*10000==int(value*10000):candidates.append(value)
    if side not in ('yes','no') or not candidates:
        raise SchemaError('No valid protective exit price')
    return min(candidates)
