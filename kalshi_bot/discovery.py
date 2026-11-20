"""Discover active BTC markets, tolerating an empty filtered exchange response."""
import datetime as dt
import time
from .api import TICKER,SchemaError


def timestamp(value):
    try:
        stamp=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
        if stamp.utcoffset() is None: raise ValueError()
        return stamp.timestamp()
    except (ValueError,TypeError,AttributeError):
        raise SchemaError('Invalid official market timestamp') from None


def active_markets(markets,now):
    if not isinstance(markets,list): raise SchemaError('Invalid market collection')
    result=[]
    for market in markets:
        if not isinstance(market,dict): raise SchemaError('Invalid market record')
        ticker=market.get('ticker')
        if not isinstance(ticker,str) or not TICKER.fullmatch(ticker): raise SchemaError('Unexpected discovery ticker')
        if market.get('market_type')!='binary': raise SchemaError('Discovered market is not binary')
        if market.get('status') not in ('open','active'): continue
        if timestamp(market['open_time'])<=now<timestamp(market['close_time']): result.append(market)
    return sorted(result,key=lambda m:timestamp(m['close_time']))


def open_markets(client,now=None):
    wall_clock=now is None
    filtered=client.pages('/markets',{'series_ticker':'KXBTC15M','status':'open'})
    current=time.time() if wall_clock else now
    markets=active_markets(filtered,current)
    if not markets:
        # Status=open has sometimes been empty despite an active contract.
        # Constrain the official fallback to near-term closures, so far-future
        # or historical contracts cannot crowd the active one off the first page.
        low=int(time.time() if wall_clock else now)
        recent=client.get('/markets',{'series_ticker':'KXBTC15M','limit':200,
                                     'min_close_ts':low,'max_close_ts':low+1800})['markets']
        markets=active_markets(recent,time.time() if wall_clock else now)
    return markets
