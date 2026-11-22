import os
import subprocess
import sys
import tempfile
import unittest
from strategy_fixture import configure_policy

def setUpModule():
    configure_policy(92,86)

from pathlib import Path
from kalshi_bot.core import Engine, Quote, fee, size


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'state.db'
        self.e = Engine(self.path, cash=10000)
        self.e.start()
        self.t = 780

    def tearDown(self):
        self.e.close()
        self.tmp.cleanup()

    def tick(self, bid=94, ask=96, liquidity=100000, **kw):
        q = Quote('KXBTC15M-TEST', 900, self.t, bid, 100-ask, ask, 100-bid, liquidity, **kw)
        self.e.tick(q)
        self.t += 1
        return q

    def position(self):
        return self.e.row('SELECT * FROM positions WHERE qty>0')

    def buys(self):
        return self.e.row('SELECT * FROM markets')['buys']

    def active(self):
        return self.e.row("SELECT * FROM orders WHERE status='active'")

    def test_stop_records_only_real_transitions_and_preserves_history(self):
        self.e.stop()
        count=self.e.row('SELECT count(*) AS n FROM events')['n']
        for _ in range(81):self.e.stop()
        self.assertEqual(self.e.row('SELECT count(*) AS n FROM events')['n'],count)
        self.e.start();self.e.stop()
        self.assertEqual(self.e.row('SELECT count(*) AS n FROM events')['n'],count+1)

    def test_repeated_stop_keeps_protective_order_and_fills_intact(self):
        self.tick()
        self.tick(bid=85,liquidity=0)
        order=self.active()
        self.assertEqual(order['action'],'sell')
        fills=[dict(row) for row in self.e.db.execute('SELECT * FROM fills')]
        self.e.stop()
        for _ in range(3):self.e.stop()
        self.assertEqual(self.active(),order)
        self.assertEqual([dict(row) for row in self.e.db.execute('SELECT * FROM fills')],fills)

    def test_allowed_prices_and_fee_budget(self):
        for p in range(92,100):
            n = size(10000,p)
            self.assertEqual(n,50)
            self.assertLessEqual(n*(p+2),10000)
            with tempfile.TemporaryDirectory() as d:
                e = Engine(Path(d)/'db',cash=10000)
                e.start()
                e.tick(Quote('KXBTC15M-P',900,780,94,100-p,p,6))
                self.assertEqual(e.row('SELECT * FROM fills')['price'],p)
                self.assertEqual(e.row('SELECT * FROM account')['cash'],10000-n*p-fee(n,p))
                e.close()

    def test_invalid_prices(self):
        for p in (0,1,86,91,100,101,95.5,True):
            with self.assertRaises(ValueError):
                size(10000,p)
        self.tick(ask=91)
        self.tick(ask=100)
        self.assertIsNone(self.position())

    def test_window(self):
        self.t=659
        self.tick()
        self.assertIsNone(self.position())
        self.tick()
        self.assertIsNotNone(self.position())

    def test_no_entry_at_close(self):
        self.t=900
        self.tick()
        self.assertIsNone(self.position())

    def test_repeated_buys_only_after_complete_sale(self):
        self.tick()
        self.assertEqual(self.buys(),1)
        self.tick(bid=85,ask=87,liquidity=0)
        sell=self.active()
        self.assertEqual(sell['price'],1)
        self.tick(bid=88,ask=90,liquidity=1)
        self.assertEqual(self.buys(),1)
        self.assertIsNotNone(self.position())
        self.tick(bid=88,ask=90)
        self.assertIsNone(self.position())
        cash=self.e.row('SELECT * FROM account')['cash']
        self.tick(ask=99)
        self.assertEqual(self.buys(),2)
        self.assertEqual(self.position()['qty'],size(cash,99))
        self.tick(bid=85,ask=87)
        self.tick(bid=89,ask=91)
        self.tick()
        self.assertIsNotNone(self.position())
        self.assertEqual(self.buys(),3)

    def test_partial_buy_counts_once(self):
        self.tick(liquidity=1)
        self.tick(liquidity=1)
        self.assertEqual(self.position()['qty'],2)
        self.assertEqual(self.buys(),1)
        self.assertEqual(self.active()['filled'],2)

    def test_partial_entry_canceled_before_stop_sell(self):
        self.tick(liquidity=2)
        buy=self.active()['id']
        self.tick(bid=85,ask=87,liquidity=0)
        self.assertEqual(self.e.row('SELECT * FROM orders WHERE id=?',(buy,))['status'],'canceled')
        self.assertEqual(self.active()['action'],'sell')
        self.assertEqual(self.active()['qty'],2)

    def test_buy_price_improvement_accepted_in_simulation(self):
        self.tick(liquidity=0)
        self.tick(ask=94)
        self.assertIsNotNone(self.position())
        self.assertEqual(self.e.row("SELECT * FROM fills")["price"],94)

    def test_unfilled_retry_not_buy_count(self):
        self.tick(liquidity=0)
        self.e.cancel(self.active()['id'],rejected=True)
        self.tick(liquidity=0)
        self.e.cancel(self.active()['id'])
        self.tick()
        self.assertEqual(self.buys(),1)

    def test_stop_persists_rebound_and_restart(self):
        self.tick()
        self.tick(bid=85,ask=87,liquidity=0)
        oid=self.active()['id']
        self.e.close()
        self.e=Engine(self.path)
        self.assertEqual(self.position()['stop'],1)
        self.assertEqual(self.active()['id'],oid)
        self.assertEqual(self.e.row('SELECT * FROM account')['running'],0)
        self.tick(bid=88,ask=90)
        self.assertIsNone(self.position())

    def test_unlimited_safe_sell_retries(self):
        self.tick()
        self.tick(bid=86,ask=88,liquidity=0)
        for _ in range(40):
            self.e.cancel(self.active()['id'],rejected=bool(self.t%2))
            self.tick(bid=84,ask=86,liquidity=0)
            self.assertEqual(self.active()['price'],1)
        self.tick(bid=87,ask=89)
        self.assertIsNone(self.position())
        self.assertEqual(self.buys(),1)

    def test_keep_valid_sell_and_failed_execution(self):
        self.tick()
        self.tick(bid=85,ask=87,liquidity=0)
        oid=self.active()['id']
        for _ in range(10):
            self.tick(bid=83,ask=85,liquidity=0)
            self.assertEqual(self.active()['id'],oid)
        self.assertIsNotNone(self.position())

    def test_network_disconnect(self):
        self.tick()
        self.tick(bid=85,ask=87,liquidity=0)
        before=self.position()
        self.tick(bid=90,ask=92,connected=False)
        self.assertEqual(before,self.position())
        self.tick(bid=90,ask=92)
        self.assertIsNone(self.position())

    def test_replayed_quote_does_not_duplicate(self):
        q=self.tick(liquidity=1)
        self.e.tick(q)
        self.assertEqual(self.position()['qty'],1)

    def test_settlement_idempotent_and_cash(self):
        self.tick()
        cost=self.position()['cost']
        qty=self.position()['qty']
        self.t=900
        self.tick(result='yes')
        self.tick(result='yes')
        a=self.e.row('SELECT * FROM account')
        self.assertEqual(a['cash'],10000-cost+qty*100)
        self.assertEqual(a['pnl'],qty*100-cost)
        self.assertIsNone(self.position())

    def test_loss_settlement(self):
        self.tick()
        cost=self.position()['cost']
        self.t=900
        self.tick(result='no')
        self.assertEqual(self.e.row('SELECT * FROM account')['pnl'],-cost)

    def test_no_side_handling(self):
        self.e.tick(Quote('KXBTC15M-NO',900,780,4,95,5,96))
        self.assertEqual(self.position()['side'],'no')
        self.e.tick(Quote('KXBTC15M-NO',900,781,14,85,15,86,liquidity=0))
        self.assertEqual(self.position()['stop'],1)
        self.e.tick(Quote('KXBTC15M-NO',900,782,12,87,13,88))
        self.assertIsNone(self.position())

    def test_global_single_position(self):
        self.tick()
        self.e.tick(Quote('KXBTC15M-OTHER',901,781,94,4,96,6))
        self.assertEqual(self.e.db.execute('SELECT count(*) FROM positions WHERE qty>0').fetchone()[0],1)

    def test_stop_entries_continues_exits(self):
        self.tick()
        self.e.stop()
        self.tick(bid=85,ask=87)
        self.tick(bid=88,ask=90)
        self.tick()
        self.assertIsNone(self.position())
        self.assertEqual(self.buys(),1)

    def test_cutoff_expires_sell_without_fake_fill(self):
        self.tick()
        self.tick(bid=85,ask=87,liquidity=0)
        self.t=900
        self.tick(bid=90,ask=92)
        self.assertIsNotNone(self.position())
        self.assertIsNone(self.active())

    def test_bad_series(self):
        with self.assertRaises(ValueError):
            self.e.tick(Quote('OTHER',900,780,94,4,96,6))

    def test_atomic_rollback(self):
        with self.assertRaises(RuntimeError):
            with self.e.transaction():
                self.e.db.execute('UPDATE account SET cash=123')
                raise RuntimeError('simulated crash')
        self.assertEqual(self.e.row('SELECT * FROM account')['cash'],10000)

    def test_lock_prevents_duplicate_process(self):
        with self.assertRaises(RuntimeError):
            Engine(self.path)

    def test_private_files(self):
        self.assertEqual(os.stat(self.path).st_mode & 0o777,0o600)

    def test_recovery_rejects_inconsistent_count(self):
        self.tick()
        self.e.db.execute('UPDATE markets SET buys=2')
        with self.assertRaises(RuntimeError):
            self.e.reconcile()

    def test_no_cash_no_buy(self):
        self.e.db.execute('UPDATE account SET cash=100')
        self.tick()
        self.assertIsNone(self.position())

    def test_real_modes_fail_closed_without_db(self):
        for mode in ('demo','live'):
            p=subprocess.run([sys.executable,'-m','kalshi_bot','--mode',mode,'--db',str(Path(self.tmp.name)/'never.db')],capture_output=True,text=True)
            self.assertNotEqual(p.returncode,0)
            self.assertIn('No connection attempted',p.stderr)
            self.assertFalse((Path(self.tmp.name)/'never.db').exists())

class RecoveryTests(unittest.TestCase):
    def test_actual_process_death_rolls_back(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db'
            e=Engine(path)
            e.close()
            script='''import os,sys
from kalshi_bot.core import Engine
e=Engine(sys.argv[1])
e.db.execute('BEGIN IMMEDIATE')
e.db.execute('UPDATE account SET cash=123')
os._exit(17)
'''
            p=subprocess.run([sys.executable,'-c',script,str(path)])
            self.assertEqual(p.returncode,17)
            e=Engine(path)
            self.assertEqual(e.row('SELECT * FROM account')['cash'],100000)
            e.close()

    def test_disconnected_cutoff_does_not_retroactively_buy(self):
        from kalshi_bot.__main__ import feed
        with tempfile.TemporaryDirectory() as d:
            e=Engine(Path(d)/'db')
            e.start()
            feed(e,655)
            feed(e,901)
            self.assertEqual(e.db.execute('SELECT count(*) FROM fills').fetchone()[0],0)
            self.assertEqual(e.row("SELECT * FROM markets WHERE ticker='KXBTC15M-SIM-0'")['settled'],1)
            e.close()

    def test_unsafe_main_fails_without_creating_state(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db'
            p=subprocess.run([sys.executable,'-m','kalshi_bot','--db',str(path),'--steps','1'],capture_output=True,text=True)
            self.assertNotEqual(p.returncode,0)
            self.assertIn('Isolation verification failed',p.stderr)
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
