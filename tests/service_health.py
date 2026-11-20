"""Verify the real local gateway from the app sandbox, without any API key."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from kalshi_bot.desktop_runtime import sandbox_command,gateway_health
ROOT=Path(__file__).resolve().parent.parent
value=gateway_health('production')
assert value=={'environment':'production','trading_enabled':True,'streaming_enabled':True,'stream_protocol':2,'entry_window_seconds':240,'market_balance_scope':1},value
with tempfile.TemporaryDirectory() as d:
    directory=Path(d)
    credentials=directory/'empty-credentials'; credentials.mkdir(mode=0o700)
    state=directory/'state'; state.mkdir()
    command=sandbox_command(ROOT,state,'production-monitor',credentials)
    marker=command.index('/usr/bin/python3')
    code="from kalshi_bot.security import check_isolation; from kalshi_bot.api import UnixHTTP; import json; check_isolation(); c=UnixHTTP('/gateway/gateway.sock'); c.request('GET','/internal/health'); r=c.getresponse(); assert r.status==200; print(r.read().decode()); c.close()"
    command=command[:marker]+['/usr/bin/python3','-B','-c',code]
    output=subprocess.run(command,capture_output=True,text=True,timeout=20)
    if output.returncode: raise SystemExit(output.stderr or output.stdout)
    inside=json.loads(output.stdout)
    assert inside==value
print(json.dumps({'host_health':value,'sandbox_health':inside,'api_credentials_used':False,'account_requests_sent':0},indent=2))
