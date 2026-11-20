"""Bounded JSON-lines controls without TextIO read-ahead losing queued Pause."""
import json
import os
import select


class CommandPipe:
    def __init__(self,stream):
        self.stream=stream
        self.buffer=b''
        self.closed=False

    def poll(self,timeout=0,wakeup=None):
        if self.closed: return []
        ready,_,_=select.select([self.stream]+([wakeup] if wakeup is not None else []),[],[],timeout)
        if wakeup is not None and wakeup in ready:wakeup.drain()
        if self.stream not in ready: return []
        try: fd=self.stream.fileno()
        except (AttributeError,OSError): fd=None
        raw=os.read(fd,4096) if fd is not None else self.stream.readline(4097).encode()
        if not raw:
            self.closed=True
            return [{'command':'quit'}]
        self.buffer+=raw
        if len(self.buffer)>8192:
            self.buffer=b''
            return [{'command':'invalid'}]
        messages=[]
        while b'\n' in self.buffer:
            line,self.buffer=self.buffer.split(b'\n',1)
            try:
                value=json.loads(line)
                if not isinstance(value,dict): raise ValueError()
                messages.append(value)
            except (ValueError,UnicodeError): messages.append({'command':'invalid'})
        return messages
