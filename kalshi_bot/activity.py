"""Bounded, selectable notifications; price data always stays in the canonical book."""
import os
import select
import threading


class Wakeup:
    def __init__(self):
        self.read_fd,self.write_fd=os.pipe2(os.O_NONBLOCK|os.O_CLOEXEC)
        self.lock=threading.Lock()
        self.pending=False
        self.closed=False

    def fileno(self):return self.read_fd

    def notify(self):
        with self.lock:
            if self.closed or self.pending:return
            try:os.write(self.write_fd,b'.')
            except BlockingIOError:pass
            self.pending=True

    def drain(self):
        with self.lock:
            if self.closed:return
            try:os.read(self.read_fd,4096)
            except BlockingIOError:pass
            self.pending=False

    def wait(self,timeout):
        ready,_,_=select.select([self],[],[],timeout)
        if ready:self.drain()
        return bool(ready)

    def close(self):
        with self.lock:
            if self.closed:return
            self.closed=True
            os.close(self.read_fd);os.close(self.write_fd)
