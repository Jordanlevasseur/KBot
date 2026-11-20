"""Offline regression tests for conservative historical order reconciliation."""
from copy import deepcopy
from decimal import Decimal as D
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from kalshi_bot.api import APIError
from kalshi_bot.execution import Journal
from kalshi_bot.missing_order import MissingOrderVerifier


TICKER='KXBTC15M-SYNTHETIC'
BUY='synthetic-buy'
SELL='synthetic-sell'
BUY_EID='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
SELL_EID='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'


class MissingOrderTests(unittest.TestCase):
    def test_renewal_starts_before_expiry_without_revoking_valid_evidence(self):
        from concurrent.futures import Future
        now=[time.time()]
        verifier=MissingOrderVerifier(Mock(),clock=lambda:now[0])
        self.addCleanup(verifier.close)
        proof=deepcopy(self.proof);proof['verified_at']=now[0]-301
        verifier.cache[SELL]=proof
        pending=Future()
        from unittest.mock import patch
        with patch.object(verifier.pool,'submit',return_value=pending) as submit:
            returned=verifier.proofs(self.j,[self.buy])
            self.assertEqual(returned[SELL],proof);submit.assert_called_once()
            self.reconcile(proof=returned[SELL])
            self.assertTrue(self.j.ready)
            pending.set_exception(APIError(0))
            self.assertEqual(verifier.proofs(self.j,[self.buy])[SELL],proof)
            now[0]+=300
            self.assertNotIn(SELL,verifier.proofs(self.j,[self.buy]))

    def test_refresh_schema_failure_revokes_previous_proof(self):
        from concurrent.futures import Future
        from kalshi_bot.api import SchemaError
        verifier=MissingOrderVerifier(Mock());self.addCleanup(verifier.close)
        verifier.cache[SELL]=deepcopy(self.proof)
        failed=Future();failed.set_exception(SchemaError('Invalid response'))
        verifier.jobs[SELL]=failed
        self.assertNotIn(SELL,verifier.proofs(self.j,[self.buy]))

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.j=Journal(Path(self.tmp.name)/'journal.sqlite3','test-only')
        self.j.reconcile([],[],[])
        cutoff=int(time.time())-2*86400
        self.cutoff=cutoff
        with self.j.db:
            self.j.db.execute("INSERT INTO markets(ticker) VALUES(?)",(TICKER,))
            for cid,eid,action,price,filled,status,counted in (
                (BUY,BUY_EID,'buy','.96','15','executed',1),
                (SELL,SELL_EID,'sell','.86','0','canceled',0),
            ):
                self.j.db.execute("""INSERT INTO orders
                    (client_id,exchange_id,ticker,action,side,price,quantity,cutoff,status,filled,counted,payload)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (cid,eid,TICKER,action,'yes',price,'15',cutoff,status,filled,counted,
                     json.dumps({'price':price})))
        self.buy={'client_order_id':BUY,'order_id':BUY_EID,'ticker':TICKER,
            'type':'limit','status':'executed','initial_count_fp':'15',
            'yes_price_dollars':'.96','fill_count_fp':'15','remaining_count_fp':'0',
            'outcome_side':'yes','book_side':'bid'}
        self.fill={'fill_id':'filled-buy','order_id':BUY_EID,'ticker':TICKER,
            'count_fp':'15','yes_price_dollars':'.96','no_price_dollars':'.04','fee_cost':'0',
            'outcome_side':'yes','book_side':'bid'}
        self.outcome={'ticker':TICKER,'market_result':'no'}
        self.market={'ticker':TICKER,'status':'finalized','result':'no',
                     'settlement_ts':'2026-11-14T10:30:04Z'}
        self.proof={'client_id':SELL,'exchange_id':SELL_EID,'ticker':TICKER,
            'market':self.market,
            'direct_order':None,'direct_missing':True,
            'market_orders':[self.buy],'market_fills':[self.fill],
            'settlements':[self.outcome],'positions':[],
            'verified_at':time.time()}

    def tearDown(self):
        self.j.close()
        self.tmp.cleanup()

    def reconcile(self, proof=None, orders=None, fills=None, positions=None, settlements=None):
        return self.j.reconcile(
            [self.buy] if orders is None else orders,
            [self.fill] if fills is None else fills,
            [] if positions is None else positions,
            [self.outcome] if settlements is None else settlements,
            missing_proofs={SELL:self.proof if proof is None else proof}
        )

    def test_absent_settled_zero_fill_sell_is_safe_after_independent_checks(self):
        self.reconcile()
        self.assertTrue(self.j.ready)
        self.assertEqual(self.j.position(),{})
        row=self.j.db.execute('SELECT evidence FROM historical_order_verifications').fetchone()
        self.assertEqual(row[0],'settled_zero_fill')

    def test_missing_proof_is_not_accepted(self):
        with self.assertRaisesRegex(RuntimeError,'Known order absent'):
            self.j.reconcile([self.buy],[self.fill],[],[self.outcome])
        self.assertFalse(self.j.ready)

    def test_missing_sell_with_confirmed_fill_is_blocked(self):
        p=deepcopy(self.proof)
        p['market_fills'].append({'fill_id':'sell-fill','order_id':SELL_EID,'count_fp':'1'})
        with self.assertRaises(RuntimeError):self.reconcile(proof=p)
        self.assertFalse(self.j.ready)

    def test_open_position_and_unsettled_market_remain_blocked(self):
        p=deepcopy(self.proof)
        p['positions']=[{'ticker':TICKER,'position_fp':'15'}]
        with self.assertRaises(RuntimeError):self.reconcile(proof=p)
        p=deepcopy(self.proof)
        p['market']={**self.market,'status':'active'}
        with self.assertRaises(RuntimeError):self.reconcile(proof=p)
        with self.assertRaises(RuntimeError):self.reconcile(settlements=[])

    def test_unmanaged_market_order_is_blocked(self):
        p=deepcopy(self.proof)
        p['market_orders'].append({'ticker':TICKER,'order_id':'unknown',
            'client_order_id':'unknown','status':'resting','fill_count_fp':'0'})
        with self.assertRaises(RuntimeError):self.reconcile(proof=p)

    def test_older_or_incomplete_evidence_is_blocked(self):
        for mutation in ({'verified_at':time.time()-601},
                         {'direct_missing':False},
                         {'market_fills':[]},
                         {'positions':None},
                         {'market':{}},
                         {'market':{**self.market,'settlement_ts':None}}):
            p={**self.proof,**mutation}
            with self.subTest(mutation=mutation),self.assertRaises(RuntimeError):
                self.reconcile(proof=p)

    def test_duplicate_and_mismatched_fill_history_are_blocked(self):
        p=deepcopy(self.proof)
        p['market_fills'].append(deepcopy(self.fill))
        with self.assertRaises(RuntimeError):self.reconcile(proof=p)
        p=deepcopy(self.proof)
        p['market_fills'][0]['count_fp']='14'
        with self.assertRaises(RuntimeError):self.reconcile(proof=p)

    def test_direct_found_canceled_zero_fill_uses_strict_normal_order_validation(self):
        p=deepcopy(self.proof)
        p['direct_order']={'client_order_id':SELL,'order_id':SELL_EID,'ticker':TICKER,
            'type':'limit','status':'canceled','initial_count_fp':'15',
            'yes_price_dollars':'.86','fill_count_fp':'0','remaining_count_fp':'15',
            'outcome_side':'no','book_side':'ask'}
        self.reconcile(proof=p)
        self.assertTrue(self.j.ready)

    def test_direct_found_with_fill_stays_blocked(self):
        p=deepcopy(self.proof)
        p['direct_order']={'client_order_id':SELL,'order_id':SELL_EID,'ticker':TICKER,
            'type':'limit','status':'canceled','initial_count_fp':'15',
            'yes_price_dollars':'.86','fill_count_fp':'1','remaining_count_fp':'14',
            'outcome_side':'no','book_side':'ask'}
        with self.assertRaises(RuntimeError):self.reconcile(proof=p)

    def test_late_new_fill_requires_new_verification(self):
        self.reconcile()
        p=deepcopy(self.proof)
        p['market_fills'].append({'fill_id':'late','order_id':SELL_EID,'count_fp':'1'})
        with self.assertRaises(RuntimeError):self.reconcile(proof=p)

    def test_unmanaged_executed_order_in_new_account_snapshot_is_blocked(self):
        extra={'order_id':'unmanaged','client_order_id':'unmanaged','ticker':TICKER,
               'type':'limit','action':'buy','status':'executed','fill_count_fp':'0'}
        with self.assertRaises(RuntimeError):
            self.reconcile(orders=[self.buy,extra])

    def test_idempotent_fill_and_settlement_evidence(self):
        recorded=[]
        self.j.research_event=lambda kind,values:recorded.append(kind)
        self.reconcile()
        self.reconcile()
        self.assertEqual(recorded.count('fill'),1)
        self.assertEqual(recorded.count('settlement'),1)
        self.assertEqual(self.j.db.execute('SELECT COUNT(*) FROM historical_order_verifications').fetchone()[0],1)

    def test_background_verifier_http_404_is_not_a_proof_alone(self):
        class FakeClient:
            def get(self,path):
                if path.startswith('/markets/'):
                    return {'market':self_market}
                raise APIError(404,'not_found')
            def pages(self,path,params=None):
                return {'/portfolio/orders':[self_order],
                        '/portfolio/fills':[self_fill],
                        '/portfolio/settlements':[self_outcome],
                        '/portfolio/positions':[]}[path]
        self_order,self_fill,self_outcome,self_market=self.buy,self.fill,self.outcome,self.market
        client=FakeClient()
        ver=MissingOrderVerifier(lambda:client)
        try:
            assert ver.proofs(self.j,[self.buy]) == {}
            for _ in range(100):
                proofs=ver.proofs(self.j,[self.buy])
                if proofs:break
                time.sleep(.005)
            self.assertIn(SELL,proofs)
            self.reconcile(proof=proofs[SELL])
        finally:ver.close()

    def test_market_finalized_without_portfolio_settlement_for_zero_fill_buy(self):
        # A canceled unfilled buy has no portfolio settlement. An executed buy
        # without settlement remains blocked by normal position reconciliation.
        with self.j.db:
            self.j.db.execute('DELETE FROM orders')
            self.j.db.execute('DELETE FROM markets')
            self.j.db.execute('INSERT INTO markets(ticker) VALUES(?)',(TICKER,))
            self.j.db.execute("""INSERT INTO orders
                (client_id,exchange_id,ticker,action,side,price,quantity,cutoff,status,filled,counted,payload)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (BUY,BUY_EID,TICKER,'buy','yes','.96','3',self.cutoff,'canceled','0',0,
                 json.dumps({'price':'.96'})))
        proof={'client_id':BUY,'exchange_id':BUY_EID,'ticker':TICKER,
               'direct_order':None,'direct_missing':True,'market':self.market,
               'market_orders':[],'market_fills':[],'settlements':[],
               'positions':[],'verified_at':time.time()}
        self.j.reconcile([],[],[],[],missing_proofs={BUY:proof})
        self.assertTrue(self.j.ready)
        self.assertEqual(self.j.position(),{})

    def test_market_must_be_finalized_and_match_ticker(self):
        for market in (
            {**self.market,'status':'active'},
            {**self.market,'ticker':'KXBTC15M-OTHER'},
            {**self.market,'result':None},
            {**self.market,'settlement_ts':None},
        ):
            with self.subTest(market=market),self.assertRaises(RuntimeError):
                self.reconcile(proof={**self.proof,'market':market})

    def test_available_portfolio_settlement_must_match_market(self):
        disagree={'ticker':TICKER,'market_result':'yes'}
        with self.assertRaises(RuntimeError):
            self.reconcile(proof={**self.proof,'settlements':[disagree]})
        with self.assertRaises(RuntimeError):
            self.reconcile(settlements=[disagree])

    def test_legacy_sell_yes_representation_of_buy_no_is_accepted(self):
        # Real API pattern: local buy-NO, order sell-YES, fill sell-NO;
        # canonical order/fill both outcome=no book=ask; 25 filled.
        with self.j.db:
            self.j.db.execute("""UPDATE orders SET action='buy',side='no',price='.96',
                quantity='25',filled='25',payload=? WHERE client_id=?""",
                (json.dumps({'price':'.04'}), BUY))
            self.j.db.execute("""UPDATE orders SET side='no',quantity='25'
                WHERE client_id=?""", (SELL,))
        self.buy.update(action='sell',side='yes',outcome_side='no',book_side='ask',
                        fill_count_fp='25',initial_count_fp='25',
                        yes_price_dollars='.04',no_price_dollars='.96')
        self.fill.update(action='sell',side='no',outcome_side='no',book_side='ask',
                         count_fp='25',yes_price_dollars='.044',
                         no_price_dollars='.956')
        self.proof['market_orders']=[self.buy]
        self.proof['market_fills']=[self.fill]
        self.reconcile()
        self.assertTrue(self.j.ready)
        self.assertEqual(self.j.position(),{})
        self.assertEqual(self.j.db.execute(
            'SELECT COUNT(*) FROM historical_order_verifications'
        ).fetchone()[0],1)

    def test_canonical_market_order_wrong_outcome_blocked(self):
        p=deepcopy(self.proof)
        p['market_orders'][0]['outcome_side']='no'
        with self.assertRaisesRegex(RuntimeError, 'canonical exchange direction'):
            self.reconcile(proof=p)
        self.assertFalse(self.j.ready)

    def test_canonical_market_fill_wrong_outcome_blocked(self):
        p=deepcopy(self.proof)
        p['market_fills'][0]['outcome_side']='no'
        with self.assertRaisesRegex(RuntimeError, 'canonical exchange direction'):
            self.reconcile(proof=p)
        self.assertFalse(self.j.ready)

    def test_missing_canonical_direction_fails_closed(self):
        for field in ('outcome_side','book_side'):
            with self.subTest(field=field):
                p=deepcopy(self.proof)
                p['market_orders'][0].pop(field)
                with self.assertRaisesRegex(RuntimeError, 'canonical exchange direction'):
                    self.reconcile(proof=p)
                self.assertFalse(self.j.ready)

    def test_legacy_fields_not_authoritative_when_canonical_agrees(self):
        p=deepcopy(self.proof)
        p['market_orders'][0].update(action='sell',side='no')
        p['market_fills'][0].update(action='sell',side='no')
        self.reconcile(proof=p)
        self.assertTrue(self.j.ready)

if __name__=='__main__':
    unittest.main()
