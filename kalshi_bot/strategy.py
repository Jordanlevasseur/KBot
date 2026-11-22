"""Shared strategy policy and timing/sizing rules."""
from dataclasses import dataclass
from collections import deque
import math

ENTRY_WINDOW_SECONDS=240
ENTRY_CUTOFF_SECONDS=30


@dataclass(frozen=True)
class StrategyPolicy:
    buy_min_cents:int=96
    sell_cents:int=92
    entry_window_seconds:int=240
    entry_cutoff_seconds:int=30
    max_volatility_pct:float=100.0
    max_volatility_5m_pct:float=100.0
    max_shock_1m_pct:float=100.0
    target_buy_usd:float=50.0
    target_exit_usd:float=35.0

    def __post_init__(self):
        if (any(type(v) not in (int,float) or not math.isfinite(v) for v in (self.target_buy_usd,self.target_exit_usd))
                or not 0<self.target_exit_usd<self.target_buy_usd):
            raise ValueError('Bitcoin target buffers must be positive dollars, with exit below buy')
        if type(self.buy_min_cents) is not int or not 50<=self.buy_min_cents<=99:
            raise ValueError('Buy threshold must be a whole cent from 50 to 99')
        if type(self.sell_cents) is not int or not 1<=self.sell_cents<self.buy_min_cents:
            raise ValueError('Sell trigger/limit must be a whole cent below the buy threshold')
        if type(self.entry_window_seconds) is not int or not 60<=self.entry_window_seconds<=900:
            raise ValueError('Entry window must be between 60 and 900 seconds')
        if type(self.entry_cutoff_seconds) is not int or not 0<=self.entry_cutoff_seconds<self.entry_window_seconds:
            raise ValueError('Closing block must be nonnegative and shorter than the entry window')
        if not isinstance(self.max_volatility_pct,(int,float)) or not 0<self.max_volatility_pct<=100:
            raise ValueError('Volatility limit must be between 0 and 100 percent')
        if not isinstance(self.max_volatility_5m_pct,(int,float)) or not 0<self.max_volatility_5m_pct<=100:
            raise ValueError('5-minute volatility limit must be between 0 and 100 percent')
        if not isinstance(self.max_shock_1m_pct,(int,float)) or not 0<self.max_shock_1m_pct<=100:
            raise ValueError('1-minute shock limit must be between 0 and 100 percent')


_POLICY=StrategyPolicy()


def policy():
    return _POLICY


def set_policy(buy_min_cents,sell_cents,entry_window_seconds=240,entry_cutoff_seconds=30,max_volatility_pct=100.0,max_volatility_5m_pct=100.0,max_shock_1m_pct=100.0,target_buy_usd=50.0,target_exit_usd=35.0):
    global _POLICY,ENTRY_WINDOW_SECONDS,ENTRY_CUTOFF_SECONDS
    _POLICY=StrategyPolicy(buy_min_cents,sell_cents,entry_window_seconds,entry_cutoff_seconds,float(max_volatility_pct),float(max_volatility_5m_pct),float(max_shock_1m_pct),float(target_buy_usd),float(target_exit_usd))
    ENTRY_WINDOW_SECONDS=_POLICY.entry_window_seconds
    ENTRY_CUTOFF_SECONDS=_POLICY.entry_cutoff_seconds
    return _POLICY


class VolatilityTracker:
    """YES-side volatility sampled once per second.

    YES and NO are complementary contracts, so tracking both would count the
    same movement twice.  A rolling 30-second high-low spread is the atomic
    observation; longer values are averages of those spreads.
    """
    def __init__(self): self.samples=deque(); self.missing=deque()
    def add(self,now,prices):
        value=prices.get('yes_ask') if prices else None
        if value is not None:self.samples.append((float(now),float(value)))
        else:self.missing.append(float(now))
        cutoff=float(now)-930
        while self.samples and self.samples[0][0]<cutoff:self.samples.popleft()
        while self.missing and self.missing[0]<cutoff:self.missing.popleft()
    def reset(self): self.samples.clear(); self.missing.clear()
    @staticmethod
    def _ranges(points):
        """Same inclusive 30-second ranges, without quadratic history scans."""
        ordered=sorted(enumerate(points),key=lambda item:item[1][0])
        lows=deque();highs=deque();right=0;result=[None]*len(points)
        for index,(stamp,_) in ordered:
            while right<len(ordered) and ordered[right][1][0]<=stamp:
                point=ordered[right][1]
                while lows and lows[-1][1]>=point[1]:lows.pop()
                while highs and highs[-1][1]<=point[1]:highs.pop()
                lows.append(point);highs.append(point);right+=1
            while lows and lows[0][0]<stamp-30:lows.popleft()
            while highs and highs[0][0]<stamp-30:highs.popleft()
            result[index]=(stamp,(highs[0][1]-lows[0][1])*100)
        return result
    def snapshot(self,now):
        now=float(now); points=[(t,v) for t,v in self.samples if t<=now]
        if not points:
            return {'market_high_cents':None,'market_low_cents':None,'market_range_cents':None,
                    'range_1m_cents':None,'range_5m_cents':None,'range_15m_cents':None,
                    'avg_1m_cents':None,'avg_5m_cents':None,'avg_15m_cents':None,
                    'sample_count':0,'missing_count':len(self.missing),'coverage_seconds':0.0,'status':'WARMING UP'}
        def direct(seconds):
            vals=[v for t,v in points if t>=now-seconds]
            return (max(vals)-min(vals))*100 if vals else None
        baselines=self._ranges(points)
        def average(seconds):
            vals=[v for t,v in baselines if t>=now-seconds]
            return sum(vals)/len(vals) if vals else None
        return {'market_high_cents':max(v for _,v in points)*100,
                'market_low_cents':min(v for _,v in points)*100,
                'market_range_cents':(max(v for _,v in points)-min(v for _,v in points))*100,
                'range_1m_cents':direct(60),'range_5m_cents':direct(300),'range_15m_cents':direct(900),
                'avg_1m_cents':average(60),'avg_5m_cents':average(300),'avg_15m_cents':average(900),
                'sample_count':len(points),'missing_count':sum(1 for t in self.missing if t>=now-900),
                'coverage_seconds':max(0.0,now-points[0][0]),
                'status':'PARTIAL' if self.missing else ('LIVE' if now-points[-1][0]<=3 else 'PARTIAL')}
    def average_pct(self,now):
        return self.window_pct(now,900)
    def window_pct(self,now,seconds):
        now=float(now); cutoff=now-float(seconds)
        points=[(timestamp,value) for timestamp,value in self.samples if cutoff<=timestamp<=now]
        if not points:return None
        # The 30-second gauge is the direct high-low spread.  Longer gauges
        # average this same rolling spread at each observed sample time.
        if seconds<=30:
            values=[value for _,value in points]
            return (max(values)-min(values))*100
        rolling=[value for timestamp,value in self._ranges([(t,v) for t,v in self.samples if t<=now]) if cutoff<=timestamp<=now]
        return sum(rolling)/len(rolling) if rolling else None


def entry_open(cutoff,now):
    return ENTRY_CUTOFF_SECONDS<cutoff-now<=ENTRY_WINDOW_SECONDS


def contract_target(cash_cents):
    """One whole contract per $2 of spendable cash; discard the remainder.

    This is a contract-count allocation, not a dollar budget divided by price.
    Callers must separately reserve fees and enforce total-cash affordability.
    """
    if type(cash_cents) is not int or cash_cents<0:
        raise ValueError('Invalid cash')
    return cash_cents//200
