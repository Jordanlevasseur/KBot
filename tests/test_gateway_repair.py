"""Targeted root repair is exercised only against disposable/mock system paths."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

spec=importlib.util.spec_from_file_location('repair_gateway',Path(__file__).resolve().parent.parent/'deploy/repair-gateway-performance.py')
repair=importlib.util.module_from_spec(spec);spec.loader.exec_module(repair)


class GatewayRepairTests(unittest.TestCase):
    def test_refuses_symlink_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'target';target.write_text('preserve');link=root/'link';link.symlink_to(target)
            with self.assertRaises(RuntimeError):repair.trusted(link)
            self.assertEqual(target.read_text(),'preserve')

    def test_refuses_running_trading_worker_without_exposing_command(self):
        entry=MagicMock();entry.name='42';entry.stat.return_value.st_uid=1000
        entry.__truediv__.return_value.read_bytes.return_value=b'python3\0-m\0kalshi_bot.desktop_worker\0'
        with patch.object(Path,'iterdir',return_value=iter([entry])):
            with self.assertRaisesRegex(RuntimeError,'Close the desktop'):repair.ensure_closed(1000,Path('/home/test'))

    def exercise(self,fail=False):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            def path(value):
                candidate=Path(value)
                return root/str(candidate).lstrip('/') if candidate.is_absolute() else candidate
            source=root/'source';target=path('/opt/kalshi-bot');(source/'kalshi_bot').mkdir(parents=True);(target/'kalshi_bot').mkdir(parents=True)
            (source/'kalshi_bot/gateway.py').write_bytes(b'new reviewed gateway')
            old=target/'kalshi_bot/gateway.py';old.write_bytes(b'previous gateway')
            (source/'kalshi_bot/api.py').write_bytes(b'new API validator');(target/'kalshi_bot/api.py').write_bytes(b'previous API validator')
            config=path('/etc/kalshi-bot/production-gateway.json');config.parent.mkdir(parents=True)
            config.write_text(json.dumps({'environment':'production','host':'external-api.kalshi.com','uid':999,'client_uid':1000,'addresses':['8.8.8.8']}))
            unit=path('/etc/systemd/system/kalshi-production-gateway.service');unit.parent.mkdir(parents=True);unit.write_text('existing service')
            cpu=path('/sys/fs/cgroup/system.slice/kalshi-production-gateway.service/cpu.max');cpu.parent.mkdir(parents=True);cpu.write_text('50000 100000')
            digest=hashlib.sha256((source/'kalshi_bot/gateway.py').read_bytes()).hexdigest();api_digest=hashlib.sha256((source/'kalshi_bot/api.py').read_bytes()).hexdigest();restarts=[]
            def run(cmd,**kwargs):
                if 'restart' in cmd:
                    restarts.append(cmd)
                    if fail and len(restarts)==1:raise subprocess.CalledProcessError(1,cmd)
            with patch.object(repair,'SOURCE',source),patch.object(repair,'TARGET',target),patch.object(repair,'Path',side_effect=path),patch.object(repair,'trusted'),patch.object(repair,'ensure_closed') as closed,patch.object(repair.os,'geteuid',return_value=0),patch.object(repair.os,'chown'),patch.object(repair.pwd,'getpwuid',return_value=SimpleNamespace(pw_uid=1000,pw_dir='/home/test')),patch.object(repair.subprocess,'run',side_effect=run),patch('kalshi_bot.gateway.verify_firewall') as firewall,patch.object(repair.sys,'argv',['repair','--environment','production','--expected-sha256',digest,'--expected-api-sha256',api_digest]),contextlib.redirect_stdout(io.StringIO()):
                if fail:
                    with self.assertRaisesRegex(RuntimeError,'previous gateway restored'):repair.main()
                else:repair.main()
                self.assertEqual(closed.call_count,2)
                self.assertGreaterEqual(firewall.call_count,1)
            override=unit.with_name(unit.name+'.d')/'50-kalshi-prepared-performance.conf'
            if fail:
                self.assertEqual(old.read_bytes(),b'previous gateway');self.assertEqual((target/'kalshi_bot/api.py').read_bytes(),b'previous API validator');self.assertFalse(override.exists());self.assertEqual(len(restarts),2)
            else:
                self.assertEqual(old.read_bytes(),b'new reviewed gateway');self.assertIn('CPUQuota=50%',override.read_text());self.assertEqual(len(restarts),1)
            self.assertEqual(unit.read_text(),'existing service')
            self.assertEqual(json.loads(config.read_text())['addresses'],['8.8.8.8'])

    def test_targeted_repair_preserves_existing_service_and_network_config(self):self.exercise()
    def test_failed_service_update_restores_previous_gateway(self):self.exercise(True)
