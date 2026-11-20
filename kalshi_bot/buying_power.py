"""Read primary-account cash on the chosen market's exchange, never combined cash."""
from decimal import ROUND_FLOOR
from .api import SchemaError,TICKER,decimal


def market_scope(market):
    ticker=market.get('ticker')
    index=market.get('exchange_index')
    if not isinstance(ticker,str) or not TICKER.fullmatch(ticker):
        raise SchemaError('Cannot verify Bitcoin cash market identity')
    if type(index) is not int or not 0<=index<=63:
        raise SchemaError('Cannot verify the market exchange cash scope')
    return {'subaccount':0,'exchange_index':index}


def market_cash(client,market):
    value=client.get('/portfolio/balance',market_scope(market))
    return cash_cents(value)


def cash_cents(value):
    cents=value.get('balance')
    if type(cents) is not int or cents<0:raise SchemaError('Invalid available market cash')
    if value.get('balance_dollars') is not None:
        exact=decimal(value['balance_dollars'],'market cash')
        if exact<0:raise SchemaError('Invalid available market cash')
        # Round down: neither reporting precision nor fractional-cent cash may
        # authorize spending more than the exchange's available balance.
        cents=min(cents,int((exact*100).to_integral_value(rounding=ROUND_FLOOR)))
    return cents
