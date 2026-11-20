"""Profit is a read-only view of confirmed cash flows, never a trading input."""
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import queue
import sqlite3
import tempfile
import unittest
from kalshi_bot.profits import realized,from_database,profit_text,ProfitReader


def fill(identity,action,qty,price,fee=0,side='yes',ticker='KXBTC15M-P'):
    return {'id':identity,'action':action,'qty':str(qty),'price':str(price),'fee':str(fee),'side':side,'ticker':ticker}


class ProfitTests(unittest.TestCase):
    def test_both_sides_settlement_includes_actual_entry_fees(self):
        for side in ('yes','no'):
            rows=[fill('buy','buy',3,98,3,side=side)]
            self.assertEqual(realized(rows,{'KXBTC15M-P':300})['buy'],{'status':'closed','profit_cents':'3'})
            self.assertEqual(realized(rows,{'KXBTC15M-P':0})['buy']['profit_cents'],'-297')

    def test_protective_sale_includes_both_fees_and_never_double_counts_sell_row(self):
        rows=[fill('buy','buy',3,98,3),fill('sell','sell',3,86,3)]
        values=realized(rows,{})
        self.assertEqual(values,{'buy':{'profit_cents':'-42','status':'closed'}})
        self.assertEqual(profit_text(values['buy']),'-$0.42');self.assertEqual(profit_text(values.get('sell')),'—')

    def test_partial_exit_is_marked_then_settlement_closes_remainder(self):
        rows=[fill('buy','buy',3,98,3),fill('sell','sell',1,86,1)]
        value=realized(rows,{})['buy']
        self.assertEqual(value,{'profit_cents':'-14','status':'partial'})
        self.assertEqual(profit_text(value),'-$0.14 (partial)')
        self.assertEqual(realized(rows,{'KXBTC15M-P':200})['buy'],{'profit_cents':'-12','status':'closed'})

    def test_fractional_fills_allocate_basis_and_fees_without_losing_remainder(self):
        rows=[fill('a','buy','.5',96,'.2'),fill('b','buy','1.5',98,'.6'),fill('s','sell',1,99,'.4')]
        partial=realized(rows,{})
        self.assertEqual(D(partial['a']['profit_cents']),D('1.1'))
        self.assertEqual(partial['b']['status'],'partial')
        closed=realized(rows,{'KXBTC15M-P':100})
        self.assertEqual(sum(D(v['profit_cents']) for v in closed.values()),D('2.8'))

    def test_two_purchases_and_opposite_side_have_separate_results(self):
        rows=[fill('a','buy',1,98,1),fill('s','sell',1,86,1),fill('b','buy',2,95,2,side='no')]
        values=realized(rows,{'KXBTC15M-P':200})
        self.assertEqual(values['a']['profit_cents'],'-14');self.assertEqual(values['b']['profit_cents'],'8')

    def test_open_unknown_and_exact_zero_are_not_guessed_profits(self):
        buy=fill('b','buy',1,98,1)
        self.assertEqual(profit_text(realized([buy],{})['b']),'Open')
        self.assertEqual(profit_text(realized([buy,fill('s','sell',1,100,1)],{})['b']),'$0.00')
        self.assertEqual(profit_text(realized([buy,fill('s','sell',2,86)],{})['b']),'—')
        self.assertEqual(profit_text(realized([buy],{'KXBTC15M-P':101})['b']),'—')

    def test_live_journal_uses_held_side_and_timestamps_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db';db=sqlite3.connect(path)
            db.executescript('CREATE TABLE orders(exchange_id TEXT,ticker TEXT,side TEXT,action TEXT,intent_at REAL); CREATE TABLE fills(id TEXT,order_id TEXT,payload TEXT); CREATE TABLE settlements(ticker TEXT,payload TEXT);')
            db.executemany('INSERT INTO orders VALUES(?,?,?,?,?)',[('b','KXBTC15M-P','no','buy',1),('s','KXBTC15M-P','no','sell',2)])
            buy={'fill_id':'buy','count_fp':'10.00','yes_price_dollars':'.02','no_price_dollars':'.98','fee_cost':'.01','created_time':'2026-11-13T18:00:00Z'}
            sell={'fill_id':'sell','count_fp':'10.00','yes_price_dollars':'.01','no_price_dollars':'.99','fee_cost':'.01','created_time':'2026-11-13T18:01:00Z'}
            # API arrival order can be newest first.
            db.executemany('INSERT INTO fills VALUES(?,?,?)',[('sell','s',json.dumps(sell)),('buy','b',json.dumps(buy))]);db.commit();db.close()
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            values=from_database(path,['KXBTC15M-P'])
            self.assertEqual(D(values['buy']['profit_cents']),8)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)

    def test_simulation_settlement_matches_existing_cash_profit(self):
        from kalshi_bot.core import Engine,Quote
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db';engine=Engine(path,cash=600)
            try:
                engine.start();engine.tick(Quote('KXBTC15M-P',900,780,97,2,98,3))
                engine.tick(Quote('KXBTC15M-P',900,900,99,0,100,1,result='yes'))
                values=from_database(path,['KXBTC15M-P'],simulation=True)
                self.assertEqual(sum(D(v['profit_cents']) for v in values.values()),engine.row('SELECT pnl FROM account')['pnl'])
                self.assertTrue(all(v['status']=='closed' for v in values.values()))
            finally:engine.close()

    def test_background_reader_reports_only_its_requested_mode_key_and_generation(self):
        events=queue.Queue();reader=ProfitReader(events)
        try:
            reader.request(('synthetic',),7,'simulation',Path('/nonexistent-profit-test'),['KXBTC15M-P'])
            generation,message=events.get(timeout=2)
            self.assertEqual(generation,7);self.assertEqual(message['key'],('synthetic',));self.assertEqual(message['values'],{})
        finally:reader.close()
        self.assertFalse(reader.thread.is_alive())
