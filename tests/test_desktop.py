import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from kalshi_bot.desktop_runtime import Worker,paths,sandbox_command

ROOT=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('desktop_installer',ROOT/'deploy/install-desktop.py')
installer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class DesktopTests(unittest.TestCase):
    def test_simulation_worker_receives_no_display_home_or_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            command=sandbox_command(ROOT,d,'simulation')
        self.assertIn('--unshare-all',command)
        self.assertIn('--clearenv',command)
        self.assertIn('--cap-drop',command)
        self.assertNotIn('/credentials',command)
        self.assertNotIn('DISPLAY',command)
        self.assertNotIn('/home',command)
        self.assertNotIn('--connect',command)
        self.assertNotIn('--trade',command)

    def test_only_supported_gui_modes_and_explicit_production_gate(self):
        with tempfile.TemporaryDirectory() as d:
            for mode in ('live','unknown'):
                with self.assertRaises(ValueError): sandbox_command(ROOT,d,mode)

    def test_account_credentials_only_mounted_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            credential=Path(d)/'credentials'; credential.mkdir(mode=0o700)
            command=sandbox_command(ROOT,d,'demo-monitor',credential,preflight=True)
            offset=command.index(str(credential))
            self.assertEqual(command[offset-1],'--ro-bind')
            self.assertIn('--preflight',command)
            self.assertNotIn('--connect',command)

    def test_demo_trading_requires_explicit_fee_reserve(self):
        with tempfile.TemporaryDirectory() as d:
            credentials=Path(d)/'credentials'; credentials.mkdir()
            for value in ('0','-1','NaN'):
                with self.assertRaises((ValueError,ArithmeticError)):
                    sandbox_command(ROOT,d,'demo-trading',credentials,fee_reserve=value)
            automatic=sandbox_command(ROOT,d,'demo-trading',credentials)
            self.assertNotIn('--fee-reserve',automatic)
            production=sandbox_command(ROOT,d,'production-trading',credentials)
            self.assertIn('--authorize-live-orders',production)
            command=sandbox_command(ROOT,d,'demo-trading',credentials,fee_reserve='.02')
            self.assertIn('--fee-reserve',command)
            self.assertIn('demo-trading',command)

    def test_install_copies_application_not_secrets_or_state(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d); (home/'Desktop').mkdir()
            result=installer.install(home)
            base=Path(result['installed_source'])
            self.assertTrue((base/'kalshi_bot/desktop.py').is_file())
            self.assertTrue((base/'docs/DESKTOP.md').is_file())
            self.assertTrue((base/'assets/kalshi-bitcoin-bot.svg').is_file())
            self.assertFalse((base/'state').exists())
            self.assertFalse((base/'tests').exists())
            entry=Path(result['menu']).read_text()
            self.assertIn('Terminal=false',entry)
            self.assertIn('Name=Kalshi Bitcoin Bot',entry)
            self.assertNotIn('--trade',entry)
            self.assertTrue(os.access(result['application'],os.X_OK))
            self.assertEqual(Path(result['desktop']).stat().st_mode&0o777,0o755)
            installer.install(home)  # Updating the managed snapshot is supported.

    def test_installer_preserves_unmanaged_destination(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d)
            path=home/'.local/bin/kalshi-bitcoin-bot'
            path.parent.mkdir(parents=True); path.write_text('unrelated file')
            with self.assertRaises(RuntimeError): installer.install(home)
            self.assertEqual(path.read_text(),'unrelated file')

    def test_paths_honor_xdg_and_do_not_use_checkout_state(self):
        with patch.dict(os.environ,{'XDG_STATE_HOME':'/state-test','XDG_CONFIG_HOME':'/config-test'}):
            state,config=paths()
            self.assertEqual(state,Path('/state-test/kalshi-bitcoin-bot'))
            self.assertEqual(config,Path('/config-test/kalshi-bitcoin-bot'))

    def test_worker_process_json_protocol_and_cleanup(self):
        import sys
        events=queue.Queue(); worker=Worker(events)
        script='import json,sys; print(json.dumps({"type":"ready"}),flush=True); m=json.loads(input()); print(json.dumps({"type":"ack","command":m["command"]}),flush=True)'
        worker.launch([sys.executable,'-u','-c',script])
        generation,message=events.get(timeout=5)
        self.assertEqual(message['type'],'ready')
        self.assertTrue(worker.send('pause'))
        generation,message=events.get(timeout=5)
        self.assertEqual(message,{'type':'ack','command':'pause'})
        done=[]; worker.stop(callback=lambda:done.append(True))
        deadline=time.monotonic()+5
        while not done and time.monotonic()<deadline: time.sleep(.01)
        self.assertTrue(done)
        self.assertFalse(worker.send('start'))

    def test_full_worker_pipe_never_blocks_gui_commands_or_stop(self):
        import sys
        events=queue.Queue();worker=Worker(events)
        worker.launch([sys.executable,'-u','-c','import time; time.sleep(30)'])
        try:
            before=time.monotonic()
            for _ in range(2000):
                if not worker.send('refresh',padding='x'*3000):break
            else:self.fail('Expected pipe backpressure')
            self.assertLess(time.monotonic()-before,1)
            process=worker.process
            process.kill()
            worker.stop()
        finally:
            if worker.process:worker.process.kill();worker.stop()

    def test_simulation_state_uses_actual_confirmed_entry_fills(self):
        from kalshi_bot.core import Engine
        from kalshi_bot.__main__ import feed
        from kalshi_bot.desktop_worker import simulation_state
        with tempfile.TemporaryDirectory() as d:
            engine=Engine(Path(d)/'db')
            engine.start(); q=feed(engine,780)
            state=simulation_state(engine,q)
            self.assertEqual(state['entry_cents'],96)
            self.assertEqual(state['buys'],1)
            self.assertTrue(state['history'])
            engine.close()


if __name__=='__main__': unittest.main()
