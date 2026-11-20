"""Session idle inhibition is scoped, reversible and independent of trading."""
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from kalshi_bot.power import ScreenLockGuard,PowerUnavailable

class ScreenLockTests(unittest.TestCase):
    def setup_bus(self):
        bus=Mock();bus.get_name_owner.return_value=':1.original';bus.get_is_connected.return_value=True
        manager=Mock();manager.Inhibit.return_value=37
        module=SimpleNamespace(SessionBus=Mock(return_value=bus),Interface=Mock(return_value=manager),UInt32=int)
        return bus,manager,module
    def test_idle_cookie_lasts_until_close_and_does_not_disable_manual_lock(self):
        bus,manager,module=self.setup_bus()
        with patch.dict(sys.modules,{'dbus':module}):
            guard=ScreenLockGuard();guard.start();guard.start();self.assertTrue(guard.active())
            module.SessionBus.assert_called_once_with(private=True)
            self.assertEqual(manager.Inhibit.call_args.args[3],8);manager.Inhibit.assert_called_once()
            guard.close();guard.close();manager.Uninhibit.assert_called_once_with(37,timeout=3);bus.close.assert_called_once()
            manager.Lock.assert_not_called();self.assertFalse(guard.active())
    def test_failed_request_releases_private_bus(self):
        bus,manager,module=self.setup_bus();manager.Inhibit.side_effect=RuntimeError('unavailable')
        with patch.dict(sys.modules,{'dbus':module}):
            guard=ScreenLockGuard()
            with self.assertRaises(PowerUnavailable):guard.start()
            bus.close.assert_called_once();manager.Uninhibit.assert_not_called();self.assertFalse(guard.active())
    def test_session_restart_cannot_release_another_apps_reused_cookie(self):
        bus,manager,module=self.setup_bus()
        with patch.dict(sys.modules,{'dbus':module}):
            guard=ScreenLockGuard();guard.start();bus.get_name_owner.return_value=':1.restarted'
            self.assertFalse(guard.active());guard.close();manager.Uninhibit.assert_not_called();bus.close.assert_called_once()
    def test_lost_desktop_lock_guard_does_not_pause_or_restart_worker(self):
        import tempfile
        from pathlib import Path
        from kalshi_bot import desktop
        with tempfile.TemporaryDirectory() as directory,patch.object(desktop.sys,'argv',['bot']),patch.object(desktop.tk,'Tk'),patch.object(desktop,'paths',return_value=(Path(directory),Path(directory))),patch.object(desktop,'PowerMonitor') as power,patch.object(desktop,'Application') as app:
            self.assertEqual(desktop.main(),0)
            app.return_value.worker.send.assert_not_called();app.return_value.worker.stop.assert_not_called()
            power.assert_called_once_with(app.return_value.power_messages)
            power.return_value.start.assert_called_once();power.return_value.close.assert_called_once()

    def test_failed_screen_inhibition_retries_without_losing_sleep_protection(self):
        import queue
        from kalshi_bot.power import PowerMonitor
        messages=queue.Queue();awake=Mock();screen=Mock()
        awake.active.return_value=True;screen.active.return_value=False
        screen.start.side_effect=PowerUnavailable()
        monitor=PowerMonitor(messages,awake,screen)
        with patch.object(monitor.stopping,'is_set',side_effect=[False,False,False,False,True]),patch.object(monitor.stopping,'wait'):
            monitor._run()
        results=list(messages.queue)
        self.assertEqual(screen.start.call_count,2)
        self.assertFalse(any(m['type'] in ('power-lost','power-failed') for m in results))
        self.assertEqual([m['okay'] for m in results if m['type']=='power-status'],[False,False])

    def test_disconnected_session_bus_cleanup_does_not_interrupt_app(self):
        bus,manager,module=self.setup_bus()
        with patch.dict(sys.modules,{'dbus':module}):
            guard=ScreenLockGuard();guard.start();bus.get_is_connected.return_value=False;bus.close.side_effect=RuntimeError('disconnected')
            guard.close();self.assertFalse(guard.active());manager.Uninhibit.assert_not_called()
