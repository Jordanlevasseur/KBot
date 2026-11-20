"""Immutable fill history accelerates reads without hiding live exposure."""
from decimal import Decimal as D
import tempfile
import unittest
from strategy_fixture import configure_policy

def setUpModule():
    configure_policy(92,86)

from pathlib import Path
from unittest.mock import Mock
from kalshi_bot.api import SchemaError
from kalshi_bot.prepared import FillHistory,Refreshers,combined_fills
from test_prepared import Fixture


class FillHistoryTests(unittest.TestCase):
    def test_closed_confirmed_history_retained_but_current_and_held_fills_fetched(self):
        with tempfile.TemporaryDirectory() as directory:
            f=Fixture(directory)
            try:
                f.qualify();f.execute();f.venue.fill(f.venue.orders[0],3);f.publish()
                history=FillHistory();history.update(f.journal)
                mutable,records=history.snapshot()
                self.assertIn(f.market['ticker'],mutable);self.assertEqual(len(records),1)
                client=Mock();client.observed_at=None;client.pages.return_value=[records[0][1]]
                readers=Refreshers(f.inputs,lambda:client,lambda:[f.market],lambda:None,history.snapshot)
                try:
                    values,_=readers._read_pages('/portfolio/fills')
                    self.assertEqual(len(values),1)
                    client.pages.assert_called_once_with('/portfolio/fills',{'ticker':f.market['ticker']})
                    # A missing held-side fill cannot be supplied from the old cache.
                    client.pages.return_value=[];values,_=readers._read_pages('/portfolio/fills');self.assertEqual(values,[])
                    # Close and reconcile the first position, then change market.
                    f.feed.prices['yes_bid']=D('.80');f.execute();f.venue.fill(f.venue.orders[-1],3);f.publish()
                    history.update(f.journal);mutable,records=history.snapshot()
                    self.assertEqual(mutable,());self.assertEqual(len(records),2)
                    next_market={**f.market,'ticker':'KXBTC15M-NEXT'}
                    readers.market_source=lambda:[next_market]
                    values,_=readers._read_pages('/portfolio/fills')
                    self.assertEqual(len(values),2)
                    self.assertEqual(client.pages.call_args.args[1],{'ticker':'KXBTC15M-NEXT'})
                finally:readers.close()
            finally:f.close()

    def test_unresolved_order_market_is_fetched_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            f=Fixture(directory)
            try:
                f.qualify();f.execute();history=FillHistory();history.update(f.journal)
                client=Mock();client.observed_at=None;client.pages.return_value=[]
                next_market={**f.market,'ticker':'KXBTC15M-NEXT'}
                readers=Refreshers(f.inputs,lambda:client,lambda:[next_market],lambda:None,history.snapshot)
                try:
                    readers._read_pages('/portfolio/fills')
                    requested=set(client.pages.call_args.args[1]['ticker'].split(','))
                    self.assertEqual(requested,{f.market['ticker'],next_market['ticker']})
                finally:readers.close()
            finally:f.close()

    def test_duplicate_immutable_fills_deduplicate_and_conflicts_fail_closed(self):
        row={'fill_id':'synthetic','order_id':'order','count_fp':'1','yes_price_dollars':'.98','no_price_dollars':'.02','fee_cost':'.01'}
        self.assertEqual(len(combined_fills([row],[{**row,'count_fp':'1.00'}])),1)
        for changed in ({**row,'count_fp':'2'},{**row,'order_id':'different'},{**row,'fee_cost':'.02'}):
            with self.assertRaises(SchemaError):combined_fills([row],[changed])

    def test_foreign_or_oversized_ticker_scope_is_rejected(self):
        from kalshi_bot.prepared import Inputs
        for scope in (('UNRELATED',),tuple('KXBTC15M-'+str(n) for n in range(101))):
            readers=Refreshers(Inputs(),Mock,lambda:[],lambda:None,lambda:(scope,()))
            try:
                with self.assertRaises(SchemaError):readers._read_pages('/portfolio/fills')
            finally:readers.close()
