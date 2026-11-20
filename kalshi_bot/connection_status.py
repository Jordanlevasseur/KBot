"""Passive private-API health; never authorizes or changes trading decisions."""
import threading
import time
from urllib.parse import urlsplit


class ConnectionHealth:
    def __init__(self,clock=time.monotonic):
        self.clock=clock;self.lock=threading.Lock();self.last_success=None;self.errors={};self.request_errors={}
        self.observation=0;self.last_success_observation=0;self.last_failure_observation=0
    def record(self,target,status):
        path=urlsplit(target).path
        if not path.startswith('/trade-api/v2/portfolio/'):return
        with self.lock:
            self.observation+=1
            if status==200:
                self.last_success=self.clock();self.errors.pop(path,None);self.request_errors.pop(path,None)
                self.last_success_observation=self.observation
            elif status in (401,403) or not status or status==429 or status>=500:
                self.errors[path]=int(status or 0)
                self.last_failure_observation=self.observation
            else:
                # A received 404 for a historical order proves the transport
                # worked. Its reconciliation result still blocks orders in the
                # engine, but must not latch the whole account as disconnected.
                self.errors.pop(path,None);self.request_errors[path]=int(status)
    def snapshot(self):
        with self.lock:
            age=None if self.last_success is None else max(0,self.clock()-self.last_success)
            auth=any(code in (401,403) for code in self.errors.values())
            reachable=age is not None and age<=15 and self.last_success_observation>self.last_failure_observation
            phase='authentication error' if auth else 'degraded' if self.errors and reachable else 'reconnecting' if self.errors or (age is not None and age>15) else 'connected' if age is not None else 'connecting'
            return {'account_connected':phase in ('connected','degraded'),'connection_phase':phase,
                    'auto_reconnecting':phase in ('connecting','reconnecting'),
                    'last_api_success_mono':self.last_success,
                    'account_request_error_codes':sorted(set(self.request_errors.values())),
                    'account_api_error':'Credentials rejected by the exchange' if auth else ('API request retry in progress' if self.errors else None)}
