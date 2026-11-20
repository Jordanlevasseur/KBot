"""Rollover and session indicators use synthetic exchange data only."""
import datetime as dt
import io
import json
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import Mock

from kalshi_bot.api import SchemaError
from kalshi_bot.desktop_runtime import session_badge
from kalshi_bot.discovery import open_markets
from kalshi_bot.execution import Journal
from kalshi_bot.trader import cycle
from kalshi_bot.streams import Book,BookFeed
from test_trader import Venue


def market(ticker,opened,closed,status='active'):
    def stamp(value): return dt.datetime.fromtimestamp(value,dt.timezone.utc).isoformat()
    return {'ticker':ticker,'market_type':'binary','exchange_index':0,'status':status,
            'open_time':stamp(opened),'close_time':stamp(closed)}


class DiscoveryTests(unittest.TestCase):
    def test_rollover_without_reset_even_if_filtered_list_still_has_expired_market(self):
        old=market('KXBTC15M-OLD',0,900)
        new=market('KXBTC15M-NEW',900,1800)
        client=Mock()
        client.pages.return_value=[old]
        client.get.return_value={'markets':[new,old]}
        self.assertEqual(open_markets(client,899),[old])
        client.get.assert_not_called()
        self.assertEqual(open_markets(client,900),[new])
        self.assertEqual(open_markets(client,901),[new])

    def test_future_only_or_empty_filtered_list_uses_official_fallback(self):
        current=market('KXBTC15M-NOW',0,900)
        future=market('KXBTC15M-NEXT',900,1800)
        for filtered in ([],[future]):
            client=Mock()
            client.pages.return_value=filtered
            client.get.return_value={'markets':[future,current]}
            self.assertEqual(open_markets(client,100),[current])
            client.get.assert_called_once_with('/markets',{'series_ticker':'KXBTC15M','limit':200,'min_close_ts':100,'max_close_ts':1900})

    def test_closed_future_and_empty_markets_never_become_entries(self):
        client=Mock(); client.pages.return_value=[]
        client.get.return_value={'markets':[market('KXBTC15M-A',0,900,'settled'),market('KXBTC15M-B',900,1800)]}
        self.assertEqual(open_markets(client,100),[])

    def test_unexpected_ticker_or_timestamp_fails_closed(self):
        for record in (market('OTHER-A',0,900),{**market('KXBTC15M-A',0,900),'open_time':'bad'}):
            client=Mock(); client.pages.return_value=[record]
            with self.assertRaises(SchemaError): open_markets(client,100)


class CycleRefreshTests(unittest.TestCase):
    def test_running_displays_current_market_outside_entry_window_and_after_rollover(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'db','offline')
            venue=Venue(); journal.running=True
            try:
                now=venue.close-800
                state=cycle(journal,venue,venue,D('.03'),now=now)
                self.assertEqual(state['ticker'],'KXBTC15M-T')
                self.assertEqual(state['cutoff'],venue.close)
                self.assertIn('Waiting',state['status'])
                venue.close+=900
                state=cycle(journal,venue,venue,D('.03'),now=now+900)
                self.assertEqual(state['cutoff'],venue.close)
                self.assertEqual(venue.orders,[])
            finally: journal.close()

    def test_no_qualifying_price_keeps_market_and_cutoff_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'db','offline')
            venue=Venue(); venue.bid=D('.49'); venue.ask=D('.50'); journal.running=True
            try:
                state=cycle(journal,venue,venue,D('.03'),now=venue.close-100)
                self.assertEqual(state['ticker'],'KXBTC15M-T')
                self.assertEqual(state['cutoff'],venue.close)
                self.assertEqual(venue.orders,[])
            finally: journal.close()

    def test_paused_refreshes_and_subscribes_without_enabling_orders(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'db','offline'); venue=Venue()
            feed=Mock(); feed.quote.return_value={'yes_bid':D('.96')}
            try:
                state=cycle(journal,venue,venue,None,now=venue.close-100,book_feed=feed)
                self.assertEqual(state['ticker'],'KXBTC15M-T')
                feed.watch.assert_called_with('KXBTC15M-T')
                self.assertEqual(state['bid'],'0.96')
                self.assertFalse(journal.running)
                self.assertEqual(venue.orders,[])
            finally: journal.close()


class BadgeTests(unittest.TestCase):
    def test_live_status_reflects_confirmed_session_and_errors(self):
        self.assertEqual(session_badge('production-trading'),'LIVE • DISCONNECTED')
        self.assertEqual(session_badge('production-trading',connected=True),'LIVE TRADING • PAUSED')
        self.assertEqual(session_badge('production-trading',connected=True,running=True),'LIVE TRADING • ON')
        self.assertIn('STALE',session_badge('production-trading',connected=True,running=True,stale=True))
        self.assertIn('ERROR',session_badge('production-trading',connected=True,running=True,blocked=True))
        self.assertEqual(session_badge('production-monitor',connected=True),'LIVE • READ ONLY')
        self.assertEqual(session_badge('simulation',running=True),'SIMULATION')


class MonitorRefreshTests(unittest.TestCase):
    def test_manual_refresh_is_one_shot_and_does_not_resume_paused_polling(self):
        from unittest.mock import patch
        from kalshi_bot.desktop_worker import run_account
        raw={'positions':[],'open_orders':[],'fills':[],'cash_cents':100}
        ticks=iter(range(100,2000,100))
        with patch('kalshi_bot.credentials.load'),patch('kalshi_bot.api.Client'),\
             patch('kalshi_bot.account.AccountStore') as store,\
             patch('kalshi_bot.account.snapshot',return_value=raw) as snapshot,\
             patch('kalshi_bot.desktop_worker.MarketMonitor'),\
             patch('kalshi_bot.desktop_worker.emit'),\
             patch('kalshi_bot.desktop_worker.sys.stdin',io.StringIO(''.join(json.dumps({'command':c})+'\n' for c in ('pause','refresh','unknown','quit')))),\
             patch('kalshi_bot.desktop_worker.select.select',return_value=([True],[],[])),\
             patch('kalshi_bot.desktop_worker.time.monotonic',side_effect=lambda:next(ticks)):
            run_account(Path('/unused'),'production',False)
        self.assertEqual(snapshot.call_count,2)
        store.return_value.close.assert_called_once()


class StreamRolloverTests(unittest.TestCase):
    def test_late_old_market_snapshot_cannot_invalidate_new_subscription(self):
        from unittest.mock import patch
        feed=BookFeed(Mock(),'/unused','/unused')
        feed.ticker='KXBTC15M-OLD'; feed.generation=1; feed.book=Book(feed.ticker)
        new_book=Book('KXBTC15M-NEW')
        new_book.consume({'type':'orderbook_snapshot','seq':1,'sid':2,
                          'msg':{'market_ticker':'KXBTC15M-NEW','yes_dollars_fp':[['.50','1']], 'no_dollars_fp':[['.49','1']]}})
        response=Mock(status=200)
        def delayed_message(_):
            import json
            feed.generation=2; feed.ticker='KXBTC15M-NEW'; feed.book=new_book
            return json.dumps({'type':'orderbook_snapshot','seq':1,'sid':1,
                               'msg':{'market_ticker':'KXBTC15M-OLD','yes_dollars_fp':[], 'no_dollars_fp':[]}}).encode()
        response.readline.side_effect=delayed_message
        connection=Mock(); connection.getresponse.return_value=response
        with patch('kalshi_bot.streams.UnixHTTP',return_value=connection),patch('kalshi_bot.streams.ws_headers',return_value={}):
            feed._run('KXBTC15M-OLD',1)
        self.assertTrue(new_book.valid)
        self.assertEqual(new_book.prices()['yes_bid'],D('.50'))


if __name__=='__main__': unittest.main()
