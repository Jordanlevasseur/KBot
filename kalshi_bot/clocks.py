"""Elapsed-time clocks shared by the GUI and deterministic replay worker."""
import math
import time


class ReplayClock:
    def __init__(self,value,rate=1,clock=time.monotonic):
        self.clock=clock
        self.value=float(value)
        self.rate=float(rate)
        self.anchor=clock()

    def now(self):
        return self.value+max(0,self.clock()-self.anchor)*self.rate

    def set_rate(self,rate):
        if rate not in (1,5,15): raise ValueError('Unsupported replay speed')
        self.value=self.now()
        self.anchor=self.clock()
        self.rate=float(rate)


class Countdown:
    def __init__(self,clock=time.monotonic):
        self.clock=clock
        self.remaining=None
        self.anchor=clock()
        self.rate=1

    def sync(self,state):
        value=state.get('remaining')
        self.remaining=None if value is None else max(0,float(value))
        self.rate=float(state.get('clock_rate',1))
        sample=state.get('clock_sample_mono',self.clock())
        now=self.clock()
        self.anchor=float(sample) if isinstance(sample,(int,float)) and 0<=now-sample<120 else now

    def seconds(self):
        if self.remaining is None: return None
        return max(0,math.ceil(self.remaining-max(0,self.clock()-self.anchor)*self.rate))

    def text(self):
        seconds=self.seconds()
        return '—' if seconds is None else f'{seconds//60:02d}:{seconds%60:02d}'
