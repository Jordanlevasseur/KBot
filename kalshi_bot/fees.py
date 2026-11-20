"""Conservative entry fee reserve from official series and event fee metadata."""
from decimal import Decimal as D,ROUND_CEILING
from .api import SchemaError,decimal,TICKER


def entry_reserve(client,market):
    series=client.get('/series/KXBTC15M')['series']
    if series.get('ticker')!='KXBTC15M' or series.get('fee_type') not in ('quadratic','quadratic_with_maker_fees'):
        raise SchemaError('Unsupported series fee model')
    multiplier=decimal(series['fee_multiplier'],'series fee multiplier')
    event=market.get('event_ticker')
    if not isinstance(event,str) or not TICKER.fullmatch(event):
        raise SchemaError('Cannot verify the market event fee schedule')
    changes=client.pages('/events/fee_changes',{'event_ticker':event})
    for change in changes:
        if change.get('event_ticker')!=event: raise SchemaError('Fee event mismatch')
        kind=change.get('fee_type_override')
        if kind is not None and kind not in ('quadratic','quadratic_with_maker_fees'):
            raise SchemaError('Unsupported event fee model')
        value=change.get('fee_multiplier_override')
        if value is not None:
            multiplier=max(multiplier,decimal(value,'event fee multiplier'))
    if multiplier<0: raise SchemaError('Negative fee multiplier')
    # Max quadratic fee at p=.5, including price improvement. A cent per whole
    # contract additionally covers <=100 fractional .01 fills, each rounded up
    # to a .0001-dollar centicent. Round the reserve UP to a whole cent.
    return (D('.07')*multiplier/4+D('.01')).quantize(D('.01'),rounding=ROUND_CEILING)
