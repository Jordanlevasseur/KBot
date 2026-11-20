"""Confirmed-fill timestamps only; no exchange or production-state access."""
import hashlib
import json
import os
from pathlib import Path
import queue
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from kalshi_bot.profits import ProfitReader,trade_times_from_database,trade_time_text


class TradeTimeTests(unittest.TestCase):
    def pacific(self,callback):
        try:
            with patch.dict(os.environ,{'TZ':'America/Los_Angeles'}):
                time.tzset();callback()
        finally:time.tzset()

    def fixture(self,path):
        db=sqlite3.connect(path)
        db.executescript('CREATE TABLE orders(exchange_id TEXT,ticker TEXT,side TEXT,action TEXT);CREATE TABLE fills(id TEXT,order_id TEXT,payload TEXT);CREATE TABLE settlements(ticker TEXT,payload TEXT);')
        db.executemany('INSERT INTO orders VALUES(?,?,?,?)',[('o-buy','KXBTC15M-T','yes','buy'),('o-sell','KXBTC15M-T','yes','sell'),('o-other','KXBTC15M-OTHER','no','buy')])
        values=[('b','o-buy',{'created_time':'2026-11-14T10:29:45.329642Z','ts':0}),('s','o-sell',{'ts':1794652191.326398}),('other','o-other',{'ts':0}),('missing','o-buy',{})]
        db.executemany('INSERT INTO fills VALUES(?,?,?)',[(i,o,json.dumps(v)) for i,o,v in values]);db.commit();db.close()

    def test_exchange_utc_and_offset_convert_to_local_with_seconds_and_zone(self):
        def check():
            expected='2026-11-14 02:29:45 PST'
            self.assertEqual(trade_time_text({'created_time':'2026-11-14T10:29:45.329642Z','ts':0}),expected)
            self.assertEqual(trade_time_text({'created_time':'2026-11-14T05:29:45.329642-05:00'}),expected)
        self.pacific(check)

    def test_unix_seconds_fallback_and_invalid_values_are_not_fabricated(self):
        def check():
            self.assertEqual(trade_time_text({'ts':'1794652191.326398'}),'2026-11-14 02:29:51 PST')
            self.assertEqual(trade_time_text({'created_time':'invalid','ts':1794652191.326398}),'2026-11-14 02:29:51 PST')
            for value in ({},None,{'created_time':'2026-11-14T10:29:45'},{'ts':True},{'ts':'NaN'},{'ts':float('inf')},{'ts':1e100},{'created_time':[]},{'ts':object()}):
                with self.subTest(value=str(value)):self.assertEqual(trade_time_text(value),'—')
        self.pacific(check)

    def test_daylight_saving_repeated_hour_has_distinct_zone(self):
        def check():
            self.assertEqual(trade_time_text({'created_time':'2026-11-01T08:30:00Z'}),'2026-11-01 01:30:00 PDT')
            self.assertEqual(trade_time_text({'created_time':'2026-11-01T09:30:00Z'}),'2026-11-01 01:30:00 PST')
        self.pacific(check)

    def test_historical_buy_sell_and_missing_times_are_read_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db';self.fixture(path);before=hashlib.sha256(path.read_bytes()).hexdigest()
            values=trade_times_from_database(path,['KXBTC15M-T'])
            self.assertEqual(set(values),{'b','s','missing'});self.assertEqual(values['b']['created_time'],'2026-11-14T10:29:45.329642Z')
            self.assertEqual(values['s'],{'ts':1794652191.326398});self.assertEqual(values['missing'],{})
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
            self.assertEqual(trade_times_from_database(path,[]),{})
            self.assertEqual(trade_times_from_database(Path(directory)/'absent',['KXBTC15M-T']),{})
            self.assertFalse((Path(directory)/'absent').exists())
            link=Path(directory)/'link';link.symlink_to(path)
            self.assertEqual(trade_times_from_database(link,['KXBTC15M-T']),{})

    def test_background_reader_keeps_times_when_profit_fields_are_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db';self.fixture(path);events=queue.Queue();reader=ProfitReader(events)
            try:
                reader.request(('times',),9,'production-trading',path,['KXBTC15M-T'])
                generation,result=events.get(timeout=2)
                self.assertEqual(generation,9);self.assertEqual(result['values'],{})
                self.assertEqual(set(result['trade_times']),{'b','s','missing'})
                self.assertEqual(result['key'],('times',));self.assertEqual(result['mode'],'production-trading')
            finally:reader.close()

    def test_reader_restart_reloads_persisted_times(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db';self.fixture(path);previous=None
            for generation in (1,2):
                events=queue.Queue();reader=ProfitReader(events)
                try:
                    reader.request(('times',),generation,'demo-trading',path,['KXBTC15M-T'])
                    observed,result=events.get(timeout=2);self.assertEqual(observed,generation)
                    if previous is not None:self.assertEqual(result['trade_times'],previous)
                    previous=result['trade_times']
                finally:reader.close()


if __name__=='__main__':unittest.main()
