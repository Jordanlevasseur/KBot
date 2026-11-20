"""Developer-only offline resource measurement. No network, no credentials."""
import json
import tempfile
import time
from pathlib import Path
from kalshi_bot.core import Engine
from kalshi_bot.__main__ import feed

with tempfile.TemporaryDirectory() as d:
    start=time.monotonic()
    cpu=time.process_time()
    e=Engine(Path(d)/'db')
    e.start()
    for t in range(775,925):
        feed(e,t)
    e.reconcile()
    s=e.status()
    e.close()
    elapsed=time.monotonic()-start
    # Linux rusage can retain the launcher's pre-exec peak; VmHWM describes
    # this Python process image's actual memory rather than the tool launcher.
    peak_mib=int(next(line for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('VmHWM:')).split()[1])/1024
    print(json.dumps({'ticks':150,'wall_seconds':round(elapsed,3),
        'cpu_seconds':round(time.process_time()-cpu,3),
        'peak_rss_mb':round(peak_mib,2),
        'database_bytes':sum(p.stat().st_size for p in Path(d).iterdir()),
        'account':s['account']},indent=2))
    assert peak_mib < 500
