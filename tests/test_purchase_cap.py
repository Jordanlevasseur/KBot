"""Uncapped sequential entries and lossless legacy migration; temporary DBs only."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from strategy_fixture import configure_policy

def setUpModule():
    configure_policy(92,86)

from decimal import Decimal as D
from kalshi_bot.core import Engine,Quote
from kalshi_bot.execution import Journal
from kalshi_bot.prepared import PreparedEngine
from kalshi_bot.schema import remove_purchase_cap
from test_prepared import Fixture


def legacy_markets(path):
    # Reconstruct only a disposable fixture's former markets schema.
    db=sqlite3.connect(path)
    try:
        sql=db.execute("SELECT sql FROM sqlite_master WHERE name='markets'").fetchone()[0]
        sql=sql.replace('CREATE TABLE markets','CREATE TABLE markets_legacy',1).replace('CHECK(buys>=0)','CHECK(buys<=2)')
        with db:
            db.execute(sql);db.execute('INSERT INTO markets_legacy SELECT * FROM markets')
            db.execute('DROP TABLE markets');db.execute('ALTER TABLE markets_legacy RENAME TO markets')
    finally:db.close()


class PurchaseCapTests(unittest.TestCase):
    def test_migration_preserves_rows_index_trigger_and_unrelated_tables(self):
        db=sqlite3.connect(':memory:')
        try:
            db.executescript("""CREATE TABLE markets(ticker TEXT PRIMARY KEY,buys INTEGER DEFAULT 0 CHECK(buys<=2),stop INTEGER DEFAULT 0,settled INTEGER DEFAULT 0);
            INSERT INTO markets VALUES('KXBTC15M-OLD',2,1,0);
            CREATE TABLE history(value TEXT);INSERT INTO history VALUES('keep');
            CREATE INDEX market_stop_index ON markets(stop);
            CREATE TRIGGER market_change AFTER UPDATE OF buys ON markets BEGIN INSERT INTO history VALUES('changed');END;""")
            before=tuple(db.execute('SELECT * FROM markets').fetchone())
            self.assertTrue(remove_purchase_cap(db));self.assertEqual(tuple(db.execute('SELECT * FROM markets').fetchone()),before)
            self.assertFalse(remove_purchase_cap(db))
            self.assertEqual(db.execute('SELECT * FROM history').fetchall(),[('keep',)])
            with db:db.execute('UPDATE markets SET buys=3')
            self.assertEqual(db.execute('SELECT * FROM history').fetchall(),[('keep',),('changed',)])
            self.assertIsNotNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='market_stop_index'").fetchone())
            with self.assertRaises(sqlite3.IntegrityError),db:db.execute('UPDATE markets SET buys=-1')
        finally:db.close()

    def test_failed_migration_rolls_back_original_schema_and_records(self):
        db=sqlite3.connect(':memory:')
        try:
            db.executescript("CREATE TABLE markets(ticker TEXT PRIMARY KEY,buys INTEGER CHECK(buys<=2));INSERT INTO markets VALUES('bad',-1);")
            before=db.execute("SELECT sql FROM sqlite_master WHERE name='markets'").fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):remove_purchase_cap(db)
            self.assertEqual(db.execute("SELECT sql FROM sqlite_master WHERE name='markets'").fetchone()[0],before)
            self.assertEqual(db.execute('SELECT * FROM markets').fetchall(),[('bad',-1)])
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='markets_uncapped'").fetchone())
        finally:db.close()

    def test_migration_does_not_commit_an_existing_transaction(self):
        db=sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE markets(ticker TEXT,buys INTEGER CHECK(buys<=2))')
            db.execute("INSERT INTO markets VALUES('pending',2)")
            with self.assertRaises(RuntimeError):remove_purchase_cap(db)
            self.assertTrue(db.in_transaction);db.rollback()
            self.assertEqual(db.execute('SELECT count(*) FROM markets').fetchone()[0],0)
        finally:db.close()

    def test_dependent_view_fails_closed(self):
        db=sqlite3.connect(':memory:')
        try:
            db.executescript('CREATE TABLE markets(ticker TEXT,buys INTEGER CHECK(buys<=2));CREATE VIEW saved AS SELECT * FROM markets;')
            with self.assertRaises(RuntimeError):remove_purchase_cap(db)
            self.assertEqual(db.execute('SELECT * FROM saved').fetchall(),[])
        finally:db.close()

    def test_simulator_repeated_round_trips_require_full_sale(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=Engine(Path(directory)/'db',cash=10000);engine.start();now=660
            def tick(bid=94,ask=96,liquidity=100000):
                nonlocal now
                engine.tick(Quote('KXBTC15M-T',900,now,bid,100-ask,ask,100-bid,liquidity));now+=1
            try:
                for count in range(1,9):
                    tick();self.assertEqual(engine.row('SELECT buys FROM markets')['buys'],count)
                    tick(86,87,1);self.assertEqual(engine.row('SELECT buys FROM markets')['buys'],count)
                    self.assertIsNotNone(engine.row('SELECT * FROM positions WHERE qty>0'))
                    tick(88,90);self.assertIsNone(engine.row('SELECT * FROM positions WHERE qty>0'))
                engine.reconcile()
            finally:engine.close()

    def test_simulator_legacy_restart_preserves_stop_and_allows_third(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'db';engine=Engine(path,cash=10000);engine.start()
            engine.tick(Quote('KXBTC15M-T',900,660,94,4,96,6))
            engine.tick(Quote('KXBTC15M-T',900,661,86,13,87,14))
            engine.tick(Quote('KXBTC15M-T',900,662,94,4,96,6))
            engine.tick(Quote('KXBTC15M-T',900,663,80,18,82,20))
            before=[tuple(r) for r in engine.db.execute('SELECT * FROM orders')];engine.close();legacy_markets(path)
            engine=Engine(path)
            try:
                self.assertEqual([tuple(r) for r in engine.db.execute('SELECT * FROM orders')],before)
                self.assertFalse(engine.row('SELECT running FROM account')['running'])
                self.assertEqual(engine.row('SELECT * FROM positions')['stop'],1)
                engine.start();engine.tick(Quote('KXBTC15M-T',900,664,88,10,90,12))
                engine.tick(Quote('KXBTC15M-T',900,665,94,4,96,6))
                self.assertEqual(engine.row('SELECT buys FROM markets')['buys'],3)
            finally:engine.close()

    def test_prepared_eight_alternating_round_trips_and_guard_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            f=Fixture(directory)
            try:
                for count,side in enumerate(('yes','no')*4,1):
                    f.qualify(side);f.execute();buy=f.venue.orders[-1];f.venue.fill(buy,3);f.publish()
                    self.assertEqual(f.journal.db.execute('SELECT buys FROM markets').fetchone()[0],count)
                    f.feed.prices[side+'_bid']=D('.71');f.execute();sell=f.venue.sells()[-1]
                    f.venue.fill(sell,1);f.publish();f.qualify('no' if side=='yes' else 'yes')
                    attempts=len(f.venue.attempts)
                    for _ in range(3):f.execute()
                    self.assertEqual(len(f.venue.attempts),attempts)
                    self.assertEqual(len(f.journal.position()),1)
                    f.venue.fill(sell,2);f.publish();self.assertEqual(f.journal.position(),{})
                self.assertEqual(len(f.venue.attempts),16)
                self.assertEqual(len({o['client_order_id'] for o in f.venue.orders}),16)
                f.qualify();f.journal.running=False;f.execute();f.journal.running=True
                f.feed.fresh=False;f.execute();f.feed.fresh=True;f.qualify();f.clock.now+=10;f.execute()
                f.publish();f.engine.execute(f.market,now=f.venue.close)
                self.assertEqual(len(f.venue.attempts),16)
            finally:f.close()

    def test_live_journal_legacy_restart_retains_history_and_third_buy(self):
        with tempfile.TemporaryDirectory() as directory:
            f=Fixture(directory)
            try:
                for side in ('yes','no'):
                    f.qualify(side);f.execute();f.venue.fill(f.venue.orders[-1],3);f.publish()
                    f.feed.prices[side+'_bid']=D('.71');f.execute();f.venue.fill(f.venue.sells()[-1],3);f.publish()
                tables=('orders','fills','markets','audit','snapshots','entry_reservations')
                before={t:[tuple(r) for r in f.journal.db.execute('SELECT * FROM '+t)] for t in tables}
                path=Path(directory)/'db';f.journal.close();legacy_markets(path)
                f.journal=Journal(path,'prepared-offline')
                for t in tables:self.assertEqual([tuple(r) for r in f.journal.db.execute('SELECT * FROM '+t)],before[t])
                self.assertFalse(f.journal.running);self.assertFalse(f.journal.ready)
                f.journal.running=True;f.engine=PreparedEngine(f.journal,f.inputs,f.feed,f.venue,clock=f.clock)
                f.publish();f.qualify('yes');f.execute();f.venue.fill(f.venue.orders[-1],3);f.publish()
                self.assertEqual(f.journal.db.execute('SELECT buys FROM markets').fetchone()[0],3)
                self.assertEqual(len(f.journal.position()),1)
            finally:f.close()


if __name__=='__main__':unittest.main()
