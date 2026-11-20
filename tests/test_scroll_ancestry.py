"""Touchpad events outside the dashboard must return to Tk immediately."""
import unittest
from unittest.mock import Mock
from kalshi_bot.desktop import within_widget


class ScrollAncestryTests(unittest.TestCase):
    def test_root_empty_name_never_resolves_back_to_itself(self):
        root=Mock();root.winfo_parent.return_value=''
        root.nametowidget.return_value=root
        self.assertFalse(within_widget(root,Mock()))
        root.nametowidget.assert_not_called()

    def test_activity_and_history_children_stop_at_root(self):
        for name in ('activity','history','settings'):
            with self.subTest(tab=name):
                root=Mock();root.winfo_parent.return_value=''
                child=Mock();child.winfo_parent.return_value='.';child.nametowidget.return_value=root
                self.assertFalse(within_widget(child,Mock()))
                self.assertEqual(child.nametowidget.call_count,1)
                root.nametowidget.assert_not_called()

    def test_dashboard_descendant_and_canvas_itself_match(self):
        canvas=Mock();child=Mock();child.winfo_parent.return_value='.canvas';child.nametowidget.return_value=canvas
        self.assertTrue(within_widget(child,canvas));self.assertTrue(within_widget(canvas,canvas))

    def test_destroyed_widget_and_unexpected_cycle_are_bounded(self):
        child=Mock();child.winfo_parent.side_effect=KeyError()
        self.assertFalse(within_widget(child,Mock()))
        child=Mock();child.winfo_parent.return_value='.';child.nametowidget.return_value=child
        self.assertFalse(within_widget(child,Mock()));child.nametowidget.assert_called_once()

    def test_successful_reconciliation_records_resolution_only_on_transition(self):
        import tempfile
        from test_prepared import Fixture
        with tempfile.TemporaryDirectory() as directory:
            f=Fixture(directory)
            try:
                def count():return f.journal.db.execute("SELECT count(*) FROM audit WHERE event LIKE 'Account reconciliation completed%'").fetchone()[0]
                initial=count();self.assertEqual(initial,1)
                f.clock.now+=.1;f.publish();self.assertEqual(count(),initial)
                f.journal.ready=False;f.clock.now+=.1;f.publish()
                self.assertEqual(count(),initial+1)
            finally:f.close()
