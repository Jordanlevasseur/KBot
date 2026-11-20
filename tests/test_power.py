"""No power transitions, credentials or exchange calls: mocked inhibitor tests."""
import ast
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch
from kalshi_bot.power import KeepAwake,PowerUnavailable


class PowerTests(unittest.TestCase):
    def process(self):
        p=Mock();p.poll.return_value=None
        return p

    def started(self,p):
        return patch('kalshi_bot.power.subprocess.Popen',return_value=p),patch('kalshi_bot.power.select.select',return_value=([p.stdout],[],[])),patch('kalshi_bot.power.os.read',return_value=b'READY\n')

    def test_blocks_sleep_and_lid_without_blocking_screen_lock(self):
        p=self.process();spawn,ready,read=self.started(p)
        with spawn as create,ready,read:
            awake=KeepAwake();awake.start();awake.start()
            create.assert_called_once()
            command=create.call_args.args[0]
            self.assertIn('--what=sleep:handle-lid-switch',command)
            self.assertIn('--mode=block',command)
            self.assertNotIn('--what=idle',command)
            ast.parse(command[-1])
            self.assertTrue(awake.active())
            awake.close();awake.close()
            p.stdin.close.assert_called_once();p.stdout.close.assert_called_once()
            self.assertFalse(awake.active())

    def test_unavailable_binary_fails_closed(self):
        awake=KeepAwake()
        with patch('kalshi_bot.power.subprocess.Popen',side_effect=FileNotFoundError):
            with self.assertRaises(PowerUnavailable):awake.start()
        self.assertFalse(awake.active())

    def test_permission_denial_and_handshake_timeout_release_resources(self):
        for response,waiting in ((b'',True),(b'WRONG!',True),(b'READY\n',False)):
            with self.subTest(response=response,waiting=waiting):
                p=self.process()
                with patch('kalshi_bot.power.subprocess.Popen',return_value=p),patch('kalshi_bot.power.select.select',return_value=([p.stdout] if waiting else [],[],[])),patch('kalshi_bot.power.os.read',return_value=response):
                    awake=KeepAwake()
                    with self.assertRaises(PowerUnavailable):awake.start()
                    self.assertFalse(awake.active());p.stdin.close.assert_called_once()

    def test_holder_exit_is_detected_and_reacquired(self):
        p=self.process();spawn,ready,read=self.started(p)
        with spawn as create,ready,read:
            awake=KeepAwake();awake.start();p.poll.return_value=1
            self.assertFalse(awake.active())
            replacement=self.process();create.return_value=replacement
            awake.start();self.assertTrue(awake.active())
            p.stdin.close.assert_called_once();awake.close()

    def test_unresponsive_holder_is_reaped(self):
        p=self.process();p.wait.side_effect=[subprocess.TimeoutExpired('holder',3),subprocess.TimeoutExpired('holder',2),0]
        awake=KeepAwake();awake.process=p;awake.close()
        p.terminate.assert_called_once();p.kill.assert_called_once()
        p.stdout.close.assert_called_once()

    def test_monitor_serializes_blocked_calls_and_close_does_not_wait(self):
        import queue
        import threading
        import time
        from kalshi_bot.power import PowerMonitor
        entered=threading.Event();release=threading.Event();threads=[]
        awake=Mock();screen=Mock();awake.active.return_value=False
        def start():
            threads.append(threading.get_ident());entered.set();release.wait(2)
        awake.start.side_effect=start
        messages=queue.Queue();monitor=PowerMonitor(messages,awake,screen,interval=.01)
        monitor.start();self.assertTrue(entered.wait(1))
        time.sleep(.04);awake.start.assert_called_once()
        before=time.monotonic();monitor.close()
        self.assertLess(time.monotonic()-before,.1)
        release.set();monitor.thread.join(1)
        self.assertFalse(monitor.thread.is_alive())
        self.assertNotEqual(threads[0],threading.get_ident())
        awake.close.assert_called_once();screen.close.assert_called_once()

    def test_loss_is_reported_before_recovery_and_never_resumes_entries(self):
        import queue
        import threading
        from kalshi_bot.power import PowerMonitor
        messages=queue.Queue();awake=Mock();screen=Mock()
        awake.active.return_value=False
        monitor=PowerMonitor(messages,awake,screen,interval=.001)
        observed=[]
        def recover():
            if awake.start.call_count==2:
                while not messages.empty():observed.append(messages.get_nowait()['type'])
                monitor.close()
        awake.start.side_effect=recover
        monitor.start()
        monitor.thread.join(1)
        self.assertEqual(observed,['power-ready','power-status','power-lost'])
        self.assertFalse(monitor.thread.is_alive())
        self.assertEqual(awake.start.call_count,2)

    def test_failed_start_reports_unavailable_without_claiming_ready(self):
        import queue
        from kalshi_bot.power import PowerMonitor
        messages=queue.Queue();awake=Mock();screen=Mock()
        awake.start.side_effect=PowerUnavailable()
        monitor=PowerMonitor(messages,awake,screen)
        monitor.start()
        self.assertEqual(messages.get(timeout=1),{'type':'power-failed','initial':True})
        monitor.close();monitor.thread.join(1)
