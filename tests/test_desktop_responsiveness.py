"""Headless callback regression tests; no accounts, production files or services."""
import queue
import threading
import time
import unittest
from unittest.mock import Mock, patch
from kalshi_bot.desktop import Application


class ResponsivenessTests(unittest.TestCase):
    def app(self):
        app=Application.__new__(Application)
        app.root=Mock();app.closing=False;app.ui_jobs=queue.Queue()
        app.power_messages=queue.Queue();app.events=queue.Queue()
        app.worker=Mock();app.worker.generation=1
        app.power_ready=True;app.power_pause_pending=False
        app.notice=Mock();app.power_status=Mock();app.render_clock=Mock()
        return app

    def test_background_completion_updates_ui_only_from_poll_thread(self):
        app=self.app();entered=threading.Event();release=threading.Event()
        ui_thread=threading.get_ident();work_threads=[];done_threads=[]
        def work():
            work_threads.append(threading.get_ident());entered.set()
            release.wait(2);return 'done'
        app.background(work,lambda result:done_threads.append((threading.get_ident(),result)))
        self.assertTrue(entered.wait(1))
        for _ in range(5):app.poll()
        self.assertEqual(done_threads,[])
        release.set()
        deadline=time.monotonic()+1
        while app.ui_jobs.empty() and time.monotonic()<deadline:time.sleep(.001)
        app.poll()
        self.assertEqual(done_threads,[(ui_thread,(True,'done'))])
        self.assertNotEqual(work_threads,[ui_thread])

    def test_loss_retries_pause_under_backpressure_and_never_stops_exits(self):
        app=self.app();app.worker.send.side_effect=[False,True]
        app.power_messages.put({'type':'power-lost'})
        app.poll()
        self.assertFalse(app.power_ready);self.assertTrue(app.power_pause_pending)
        app.power_messages.put({'type':'power-ready','initial':False})
        app.poll()
        self.assertTrue(app.power_ready);self.assertFalse(app.power_pause_pending)
        self.assertEqual([c.args for c in app.worker.send.call_args_list],[('pause',),('pause',)])
        app.worker.stop.assert_not_called()

    def test_unavailable_sleep_protection_blocks_start(self):
        app=self.app();app.switching=False;app.power_ready=False
        app.start()
        app.worker.send.assert_not_called()

    def test_pending_activity_flushes_without_another_state_event(self):
        app=self.app();app.pending_activity=[('control','Entries stopped')]
        app.activity=Mock();app.update_table=Mock();app._activity_redraw_at=time.monotonic()-2
        app.poll()
        app.update_table.assert_called_once_with(app.activity,app.pending_activity)

    def test_mode_cannot_change_during_connection_preparation(self):
        app=self.app();app.switching=True;app.switching_mode='simulation';app.mode=Mock()
        app.change_mode()
        app.mode.set.assert_called_once_with('Simulation • offline')
        app.worker.stop.assert_not_called()

    def test_worker_shutdown_bounds_final_wait_and_avoids_reader_locks(self):
        import subprocess
        from kalshi_bot.desktop_runtime import Worker
        worker=Worker(queue.Queue());process=Mock();worker.process=process
        process.wait.side_effect=subprocess.TimeoutExpired('worker',1)
        done=threading.Event()
        with patch.object(worker,'send',return_value=False),patch('kalshi_bot.desktop_runtime.os.killpg'):
            worker.stop(callback=done.set)
            self.assertTrue(done.wait(1))
        self.assertEqual([c.kwargs['timeout'] for c in process.wait.call_args_list],[8,3,2])
        process.stdout.close.assert_not_called();process.stderr.close.assert_not_called()

    def test_shutdown_has_deadline_even_if_worker_never_calls_back(self):
        app=self.app();app.data={};app.power=Mock();app.install_process=None
        app.connection=Mock()
        with patch('kalshi_bot.desktop.time.monotonic',side_effect=[10,10,26]):
            app.close()
            app.root.destroy.assert_not_called()
            callback=app.root.after.call_args.args[1]
            callback()
        app.root.destroy.assert_called_once();app.power.close.assert_called_once()
