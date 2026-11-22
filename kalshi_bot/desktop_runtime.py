"""Desktop process boundary: no browser, shell evaluation, or Internet transport."""
import json
from .strategy import DEFAULT_POLICY
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading

MODES=('simulation','demo-monitor','production-monitor','demo-trading','production-trading')


def session_badge(mode,*,connected=False,running=False,stale=False,blocked=False,recovering=False,protecting=False):
    """Describe confirmed session state, never infer authorization from the mode."""
    if mode=='simulation': return 'SIMULATION'
    name='LIVE' if mode.startswith('production') else 'DEMO'
    if blocked: return name+' • CONNECTION ERROR'
    if recovering:return name+' • RECONNECTING'
    if protecting:return name+' • SAVING PROTECTION'
    if stale: return name+' • STATUS STALE'
    if not connected: return name+' • DISCONNECTED'
    if mode.endswith('-monitor'): return name+' • READ ONLY'
    return name+(' TRADING • ON' if running else ' TRADING • PAUSED')


def paths():
    home=Path.home()
    state=Path(os.environ.get('XDG_STATE_HOME',home/'.local/state'))/'kalshi-bitcoin-bot'
    config=Path(os.environ.get('XDG_CONFIG_HOME',home/'.config'))/'kalshi-bitcoin-bot'
    return state,config


def sandbox_command(root,state,mode,credentials=None,preflight=False,fee_reserve=None,buy_min_cents=DEFAULT_POLICY.buy_min_cents,sell_cents=DEFAULT_POLICY.sell_cents,entry_window_seconds=DEFAULT_POLICY.entry_window_seconds,entry_cutoff_seconds=DEFAULT_POLICY.entry_cutoff_seconds,max_volatility_pct=DEFAULT_POLICY.max_volatility_pct,max_volatility_5m_pct=DEFAULT_POLICY.max_volatility_5m_pct,max_shock_1m_pct=DEFAULT_POLICY.max_shock_1m_pct,target_buy_usd=DEFAULT_POLICY.target_buy_usd,target_exit_usd=DEFAULT_POLICY.target_exit_usd):
    if mode not in MODES: raise ValueError('Unsupported desktop mode')
    root=Path(root).resolve()
    state=Path(state).resolve()
    command=['/usr/bin/bwrap','--die-with-parent','--new-session','--unshare-all','--cap-drop','ALL',
             '--clearenv','--setenv','PATH','/usr/bin:/bin','--setenv','PYTHONPATH','/app',
             '--setenv','PYTHONDONTWRITEBYTECODE','1','--setenv','HOME','/tmp',
             '--ro-bind','/usr','/usr','--symlink','usr/bin','/bin','--symlink','usr/lib','/lib',
             '--symlink','usr/lib64','/lib64','--proc','/proc','--dev','/dev','--tmpfs','/tmp',
             '--dir','/etc','--ro-bind','/etc/ld.so.cache','/etc/ld.so.cache',
             '--dir','/app','--ro-bind',str(root/'kalshi_bot'),'/app/kalshi_bot',
             '--bind',str(state),'/state','--chdir','/app']
    if mode!='simulation':
        if credentials is None: raise ValueError('Configure credentials first')
        credential_path=Path(credentials)
        if credential_path.is_symlink() or not credential_path.is_dir():
            raise ValueError('A private credential directory is required')
        command+=['--ro-bind',str(credential_path.resolve()),'/credentials']
        gateway=Path('/run/kalshi-'+mode.split('-')[0])
        if gateway.is_dir() and not preflight:
            command+=['--ro-bind',str(gateway),'/gateway']
        else: command+=['--dir','/gateway']
    if mode.endswith('-trading'):command+=['--setenv','MALLOC_ARENA_MAX','2']
    command+=['/usr/bin/python3','-B','-m','kalshi_bot.desktop_worker','--mode',mode]
    command+=['--buy-min-cents',str(buy_min_cents),'--sell-cents',str(sell_cents),'--entry-window-seconds',str(entry_window_seconds),'--entry-cutoff-seconds',str(entry_cutoff_seconds),'--max-volatility-pct',str(max_volatility_pct),'--max-volatility-5m-pct',str(max_volatility_5m_pct),'--max-shock-1m-pct',str(max_shock_1m_pct)]
    command+=['--target-buy-usd',str(target_buy_usd),'--target-exit-usd',str(target_exit_usd)]
    if mode.endswith('-trading') and not preflight and fee_reserve is not None:
        from decimal import Decimal
        fee=Decimal(str(fee_reserve))
        if not fee.is_finite() or fee<=0: raise ValueError('Enter a verified positive fee reserve')
        command+=['--fee-reserve',str(fee)]
    if mode=='production-trading' and not preflight: command+=['--authorize-live-orders']
    if preflight: command+=['--preflight']
    return command


class Worker:
    def __init__(self,events):
        self.events=events
        self.process=None
        self.generation=0

    def launch(self,command):
        if self.process is not None: raise RuntimeError('A worker is already attached')
        self.generation+=1
        generation=self.generation
        self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                      text=True,bufsize=1,start_new_session=True)
        process=self.process
        os.set_blocking(process.stdin.fileno(),False)
        def read_output():
            try:
                for line in process.stdout:
                    if len(line)>2_000_000: continue
                    try: message=json.loads(line)
                    except (ValueError,TypeError): continue
                    self.events.put((generation,message))
            finally:
                process.wait()
                process.stdout.close()
                self.events.put((generation,{'type':'exit','code':process.returncode}))
        def read_errors():
            for line in process.stderr:
                # Worker exceptions are sanitized; do not retain arbitrary stderr.
                if 'bwrap:' in line:
                    self.events.put((generation,{'type':'error','message':'Linux isolation could not start. Check bubblewrap and user-namespace support.'}))
            process.stderr.close()
        threading.Thread(target=read_output,daemon=True).start()
        threading.Thread(target=read_errors,daemon=True).start()

    def send(self,command,**values):
        process=self.process
        if not process or process.poll() is not None: return False
        try:
            payload=(json.dumps({'command':command,**values})+'\n').encode()
            # Atomic nonblocking pipe writes: a wedged reader cannot freeze Tk.
            if len(payload)>4096:return False
            return os.write(process.stdin.fileno(),payload)==len(payload)
        except (BrokenPipeError,OSError,ValueError): return False

    def stop(self,callback=None):
        process=self.process
        if process is None:
            if callback: callback()
            return
        self.send('quit')
        self.process=None
        self.generation+=1
        def wait():
            try: process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                # Only this application's newly created process group is targeted.
                try: os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError: pass
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try: os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
                    try: process.wait(timeout=2)
                    except subprocess.TimeoutExpired: pass
            for stream in (process.stdin,):
                try: stream.close()
                except (OSError,ValueError): pass
            self.events.put((self.generation,{'type':'stopped'}))
            if callback: callback()
        threading.Thread(target=wait,daemon=True).start()


def gateway_health(environment):
    if environment not in ('demo','production'): raise ValueError('Unknown environment')
    from .api import UnixHTTP
    connection=UnixHTTP('/run/kalshi-'+environment+'/gateway.sock')
    # Keep the UI's connection preflight bounded. The worker performs its own
    # reconnect/reconciliation loop after launch; this check must never make
    # the desktop appear hung while the gateway is unavailable.
    connection.timeout=.35
    try:
        connection.request('GET','/internal/health')
        response=connection.getresponse()
        value=json.loads(response.read(4097))
        if response.status!=200 or not isinstance(value,dict) or value.get('environment')!=environment:
            raise ValueError('Gateway environment mismatch')
        return value
    finally: connection.close()
