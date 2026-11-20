"""Generated credentials only. Verify sandbox account preflight makes no connection."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from kalshi_bot.credentials import configure

root=Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory() as d:
    d=Path(d)
    source=d/'generated.pem'
    key=ed25519.Ed25519PrivateKey.generate()
    source.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    source.chmod(0o600)
    directory=d/'demo'
    configure(directory,'demo','generated-offline-key',source)
    environment={**os.environ,'KALSHI_ACCOUNT_STATE':str(d/'state')}
    result=subprocess.run([str(root/'deploy/run-account.sh'),str(directory),'demo'],env=environment,capture_output=True,text=True,timeout=30)
    if result.returncode:
        print(result.stderr)
        raise SystemExit(result.returncode)
    assert 'No connection attempted' in result.stdout
    assert 'BEGIN PRIVATE KEY' not in result.stdout+result.stderr
    print(result.stdout.strip())
