"""Shared entry authorization and held-side Bitcoin target protection."""
import math
import time
from decimal import Decimal as D
from .api import SchemaError,decimal
from .strategy import policy

BUY_CUSHION=D('50')
EXIT_CUSHION=D('35')
REFERENCE_TTL=2.5
VOLATILITY_TTL=2.5


def reference_evidence(market,reference,side,*,wall=None,mono=None):
    wall=time.time() if wall is None else wall
    mono=time.monotonic() if mono is None else mono
    if not market or not reference or side not in ('yes','no'):
        raise SchemaError('Bitcoin reference or target unavailable')
    target=decimal(market.get('floor_strike'),'Bitcoin target')
    value=decimal(reference.get('value'),'Bitcoin reference')
    stamp=reference.get('source_ms');sample=reference.get('sample_mono')
    if (reference.get('status')!='Live' or type(stamp) not in (int,float)
            or type(sample) not in (int,float) or not 0<=wall-stamp/1000<=REFERENCE_TTL
            or not 0<=mono-sample<=REFERENCE_TTL or target<=0 or value<=0):
        raise SchemaError('Bitcoin reference is stale or unavailable')
    difference=value-target;cushion=difference if side=='yes' else -difference
    return {'ticker':market['ticker'],'side':side,'btc_target':str(target),
            'btc_reference':str(value),'reference_source_ms':stamp,
            'reference_age_seconds':wall-stamp/1000,'target_difference_usd':str(difference),
            'held_side_cushion_usd':str(cushion),'buy_cushion_usd':str(policy().target_buy_usd),
            'exit_cushion_usd':str(policy().target_exit_usd)}


def entry_verdict(market,reference,side,tracker,*,wall=None,mono=None):
    wall=time.time() if wall is None else wall
    mono=time.monotonic() if mono is None else mono
    try:evidence=reference_evidence(market,reference,side,wall=wall,mono=mono)
    except (SchemaError,ValueError,TypeError,KeyError):
        return 'Fresh Bitcoin reference and target required',{}
    if D(evidence['held_side_cushion_usd'])<D(str(policy().target_buy_usd)):
        return f'Entry blocked: Bitcoin must be at least ${policy().target_buy_usd:g} beyond target on the bought side',evidence
    recent=[(t,v) for t,v in tracker.samples if wall-60<=t<=wall]
    # Missing/one-sided books never count as zero volatility or sufficient coverage.
    distinct={int(t) for t,_ in recent}
    if (not recent or wall-recent[-1][0]>VOLATILITY_TTL or len(distinct)<45
            or wall-recent[0][0]<55):
        return 'Entry blocked: volatility data stale or insufficient; need recent valid coverage',evidence
    values=[D(str(v)) for _,v in recent]
    shock=float((max(values)-min(values))*100)
    avg5=tracker.window_pct(wall,300);avg15=tracker.window_pct(wall,900)
    limits=policy()
    evidence.update({'shock_1m_range':shock,'average_5m':avg5,'average_15m':avg15,
                     'shock_1m_limit':limits.max_shock_1m_pct,
                     'average_5m_limit':limits.max_volatility_5m_pct,
                     'average_15m_limit':limits.max_volatility_pct})
    if any(v is None or not math.isfinite(v) for v in (shock,avg5,avg15)):
        return 'Entry blocked: volatility unavailable',evidence
    if shock>=limits.max_shock_1m_pct or avg5>=limits.max_volatility_5m_pct or avg15>=limits.max_volatility_pct:
        return 'Entry blocked: volatility limit reached',evidence
    return None,evidence
