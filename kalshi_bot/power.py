"""Desktop-owned logind sleep inhibition, outside the isolated trading worker."""
import os
import select
import subprocess


class PowerUnavailable(RuntimeError):
    pass


class KeepAwake:
    """Keep an inhibitor alive until close, including while the desktop is locked.

    The isolated helper only waits for stdin EOF. A desktop crash closes that
    pipe too, so no persistent power setting or orphaned inhibitor is required.
    No idle inhibitor is requested: screen blanking/locking remain available.
    """
    def __init__(self):
        self.process=None

    def start(self):
        if self.process is not None:
            if self.active():return
            self.close()
        command=['/usr/bin/systemd-inhibit','--what=sleep:handle-lid-switch',
                 '--mode=block','--who=Kalshi Bitcoin Bot',
                 '--why=Keep market monitoring and protective orders running while locked',
                 '/usr/bin/python3','-I','-S','-B','-u','-c',
                 "import sys;print('READY',flush=True);sys.stdin.buffer.read()"]
        try:
            self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL,close_fds=True)
            ready,_,_=select.select([self.process.stdout],[],[],5)
            if not ready or os.read(self.process.stdout.fileno(),6)!=b'READY\n' or not self.active():
                raise PowerUnavailable('The operating system did not grant sleep protection')
        except (OSError,ValueError,PowerUnavailable) as exc:
            self.close()
            raise PowerUnavailable('Cannot keep the laptop awake through logind') from exc

    def active(self):
        return self.process is not None and self.process.poll() is None

    def close(self):
        process=self.process
        self.process=None
        if process is None:return
        try:
            if process.stdin:process.stdin.close()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
        finally:
            if process.stdout:process.stdout.close()


class ScreenLockGuard:
    """Cinnamon/GNOME session idle inhibitor, owned by a private D-Bus connection.

    No settings are edited. Disconnecting releases only this application's cookie;
    manual screen locking remains available. Never unlock an already locked screen.
    """
    SERVICE='org.gnome.SessionManager'
    PATH='/org/gnome/SessionManager'
    def __init__(self):self.bus=None;self.manager=None;self.cookie=None;self.owner=None
    def active(self):
        if self.bus is None or self.cookie is None:return False
        try:return self.bus.get_is_connected() and self.bus.get_name_owner(self.SERVICE)==self.owner
        except Exception:return False
    def start(self):
        if self.active():return
        self.close()
        try:
            import dbus
            self.bus=dbus.SessionBus(private=True);self.owner=self.bus.get_name_owner(self.SERVICE)
            self.manager=dbus.Interface(self.bus.get_object(self.SERVICE,self.PATH),'org.gnome.SessionManager')
            self.cookie=int(self.manager.Inhibit('Kalshi Bitcoin Bot',dbus.UInt32(0),
                'Keep automatic trading visible while the bot is open',dbus.UInt32(8),timeout=3))
        except Exception as exc:
            self.close();raise PowerUnavailable('Desktop idle inhibition unavailable') from exc
    def close(self):
        if self.bus is None:return
        try:
            if self.cookie is not None and self.active():
                try:self.manager.Uninhibit(self.cookie,timeout=3)
                except Exception:pass
        finally:
            try:self.bus.close()
            except Exception:pass
            self.bus=None;self.cookie=None;self.manager=None;self.owner=None


class PowerMonitor:
    """One daemon owns all power objects, including D-Bus connection lifetime.

    The UI consumes plain messages; recovery never overlaps, and close never
    joins an OS/D-Bus operation. Losing protection pauses entries before repair.
    """
    def __init__(self, messages, awake=None, screen=None, interval=2):
        import threading
        self.messages=messages
        self.awake=awake or KeepAwake()
        self.screen=screen or ScreenLockGuard()
        self.interval=interval
        self.stopping=threading.Event()
        self.thread=threading.Thread(target=self._run,daemon=True,name='desktop-power')

    def start(self):self.thread.start()
    def close(self):self.stopping.set()

    def _run(self):
        first=True
        unavailable=False
        try:
            while not self.stopping.is_set():
                if first or not self.awake.active():
                    if not first and not unavailable:self.messages.put({'type':'power-lost'})
                    unavailable=True
                    try:
                        self.awake.start()
                        unavailable=False
                        self.messages.put({'type':'power-ready','initial':first})
                    except Exception:
                        self.messages.put({'type':'power-failed','initial':first})
                first=False
                if self.stopping.is_set():break
                try:
                    if not self.screen.active():self.screen.start()
                    self.messages.put({'type':'power-status','okay':True})
                except Exception:self.messages.put({'type':'power-status','okay':False})
                self.stopping.wait(self.interval)
        finally:
            try:self.screen.close()
            finally:self.awake.close()
