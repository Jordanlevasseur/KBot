"""Verify a real logind inhibitor, without sleeping, locking or starting the bot."""
from pathlib import Path
import json
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from kalshi_bot.power import KeepAwake


def records(pid):
    result=subprocess.run(['/usr/bin/systemd-inhibit','--list','--no-pager','--no-legend'],capture_output=True,text=True,timeout=5,check=True)
    return [line for line in result.stdout.splitlines() if 'Kalshi Bitcoin Bot' in line and str(pid) in line.split()]

awake=KeepAwake()
try:
    awake.start();pid=awake.process.pid
    rows=records(pid)
    assert rows and any('sleep:handle-lid-switch' in row and 'block' in row for row in rows)
    assert all('idle' not in row for row in rows)
finally:awake.close()
assert not records(pid)
print(json.dumps({'result':'passed','os_sleep_inhibitor_verified':True,'lid_inhibitor_verified':True,'screen_lock_not_inhibited':True,'released_on_close':True,'actual_sleep_or_lock_attempted':False,'account_connected':False}))
