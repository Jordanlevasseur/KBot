import json
import tempfile
import unittest
from strategy_fixture import configure_policy

def setUpModule():
    configure_policy(92,71)

from decimal import Decimal as D
from pathlib import Path
from unittest.mock import Mock,patch
from kalshi_bot.api import APIError
from kalshi_bot.execution import Journal,affordable,order_payload
from kalshi_bot.trade_api import validate_write,CREATE
from kalshi_bot.trade_api import OrderNotSent


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'orders.db'
        self.j=Journal(self.path,'demo-test')
        self.j.reconcile([],[],[])
        self.j.running=True
        self.b=Mock()
        self.b.create.side_effect=lambda p:{'client_order_id':p['client_order_id'],'order_id':'exchange-'+p['client_order_id']}

    def tearDown(self):
        self.j.close()
        self.tmp.cleanup()

    def buy(self):
        return self.j.submit(self.b,'KXBTC15M-TEST','buy','yes',D('.96'),10,900,780)

    def reconcile(self,status='executed',filled='10',position='10',action='buy',price='.94'):
        row=self.j.db.execute('SELECT * FROM orders ORDER BY rowid DESC LIMIT 1').fetchone()
        remote={'client_order_id':row['client_id'],'order_id':row['exchange_id'],'ticker':row['ticker'],'type':'limit','status':status,'initial_count_fp':row['quantity'],'yes_price_dollars':json.loads(row['payload'])['price'],'fill_count_fp':filled,'remaining_count_fp':str(D(row['quantity'])-D(filled))}
        fills=[{'fill_id':row['client_id'],'order_id':row['exchange_id'],'count_fp':filled,'yes_price_dollars':price,'no_price_dollars':str(1-D(price))}] if D(filled) else []
        # Include prior orders/fills, as paginated exchange snapshots do.
        oldorders=getattr(self,'oldorders',[])
        oldfills=getattr(self,'oldfills',[])
        orders=[o for o in oldorders if o['order_id']!=remote['order_id']]+[remote]
        allfills=[f for f in oldfills if f['order_id']!=remote['order_id']]+fills
        positions=[{'ticker':row['ticker'],'position_fp':position}] if D(position) else []
        self.j.reconcile(orders,allfills,positions)
        self.oldorders,self.oldfills=orders,allfills

    def test_price_improvement_is_accepted(self):
        self.buy()
        self.reconcile(price='.94')
        self.assertEqual(self.j.position(),{('KXBTC15M-TEST','yes'):D(10)})

    def test_all_allowed_limits_and_no_side_complement(self):
        for side in ('yes','no'):
            for p in ('.95','.96','.97','.98','.99'):
                body=order_payload('KXBTC15M-T','buy',side,D(p),10,900,'test-id')
                self.assertEqual(validate_write('POST',CREATE,body,now=780),CREATE)
                self.assertEqual(D(body['price']),D(p) if side=='yes' else 1-D(p))
        for p in ('.91','1','.86'):
            with self.assertRaises(ValueError):
                order_payload('KXBTC15M-T','buy','yes',D(p),1,900,'test-id')

    def test_sizing_budget(self):
        n=affordable(10000,D('.96'),D('.02'))
        self.assertEqual(n,50)
        self.assertLessEqual(n*D('.98'),100)

    def test_unknown_submit_waits_for_exchange_evidence_without_replay(self):
        self.b.create.side_effect=APIError()
        with self.assertRaises(APIError): self.buy()
        cid=self.j.active()['client_id']
        self.j.close()
        self.j=Journal(self.path,'demo-test')
        self.b.create.side_effect=lambda p:{'client_order_id':p['client_order_id'],'order_id':'recovered'}
        self.b.create.reset_mock()
        with self.assertRaises(RuntimeError): self.j.recover_pending(self.b,[],781)
        self.b.create.assert_not_called()
        self.j.running=True
        with self.assertRaises(RuntimeError): self.j.recover_pending(self.b,[],781)
        self.b.create.assert_not_called()
        self.assertEqual(self.j.active()['client_id'],cid)
        self.assertEqual(self.j.db.execute('SELECT count(*) FROM orders').fetchone()[0],1)

    def test_pause_arriving_before_submit_prevents_the_order(self):
        self.j.before_submit=lambda:setattr(self.j,'running',False)
        with self.assertRaises(RuntimeError): self.buy()
        self.b.create.assert_not_called()
        self.assertIsNone(self.j.active())

    def test_no_duplicate_after_ambiguous_create(self):
        self.b.create.side_effect=APIError()
        with self.assertRaises(APIError): self.buy()
        with self.assertRaises(RuntimeError): self.buy()
        self.assertEqual(self.b.create.call_count,1)

    def test_retry_rejection_cannot_erase_an_uncertain_original_buy(self):
        self.b.create.side_effect=APIError()
        with self.assertRaises(APIError):self.buy()
        cid=self.j.active()['client_id']
        self.b.create.side_effect=APIError(400)
        self.b.create.reset_mock()
        with self.assertRaises(RuntimeError):self.j.recover_pending(self.b,[],781)
        self.b.create.assert_not_called()
        self.assertEqual(self.j.active()['client_id'],cid)
        with self.assertRaises(RuntimeError):self.buy()
        self.assertEqual(self.j.db.execute('SELECT count(*) FROM orders').fetchone()[0],1)

    def test_local_never_sent_order_does_not_leave_an_ambiguous_intent(self):
        self.b.create.side_effect=OrderNotSent('cutoff passed before send')
        with self.assertRaises(OrderNotSent):self.buy()
        self.assertIsNone(self.j.active())
        self.assertEqual(self.j.db.execute('SELECT status FROM orders').fetchone()[0],'rejected')

    def test_opposite_side_cannot_open_during_first_position(self):
        self.buy();self.reconcile()
        calls=self.b.create.call_count
        with self.assertRaises(RuntimeError):self.j.submit(self.b,'KXBTC15M-TEST','buy','no',D('.99'),1,900,800)
        self.assertEqual(self.b.create.call_count,calls)

    def test_terminal_rejection_does_not_consume_buy(self):
        self.b.create.side_effect=APIError(400)
        with self.assertRaises(APIError): self.buy()
        self.j.reconcile([],[],[])
        self.assertIsNone(self.j.active())
        self.assertEqual(self.j.db.execute('SELECT buys FROM markets').fetchone()[0],0)

    def test_partial_fill_counts_once_and_blocks_second_buy(self):
        self.buy()
        self.reconcile(status='resting',filled='2',position='2')
        self.reconcile(status='resting',filled='3',position='3')
        self.assertEqual(self.j.db.execute('SELECT buys FROM markets').fetchone()[0],1)
        with self.assertRaises(RuntimeError): self.buy()

    def test_stop_survives_restart_and_rebound(self):
        self.buy(); self.reconcile()
        self.assertTrue(self.j.latch_stop('KXBTC15M-TEST','yes',D('.70')))
        self.j.close(); self.j=Journal(self.path,'demo-test')
        self.assertTrue(self.j.latch_stop('KXBTC15M-TEST','yes',D('.90')))
        self.assertFalse(self.j.running)

    def test_stale_position_blocks_orders(self):
        self.buy()
        with self.assertRaises(RuntimeError): self.reconcile(position='0')
        self.assertFalse(self.j.ready)

    def test_sell_below_71_rejected(self):
        self.buy(); self.reconcile()
        self.j.latch_stop('KXBTC15M-TEST','yes',D('.70'))
        with self.assertRaises(ValueError):
            self.j.submit(self.b,'KXBTC15M-TEST','sell','yes',D('.70'),10,900,800)

    def test_complete_sale_allows_many_sequential_buys(self):
        for number in range(1,9):
            self.buy(); self.reconcile()
            self.j.latch_stop('KXBTC15M-TEST','yes',D('.70'))
            self.j.submit(self.b,'KXBTC15M-TEST','sell','yes',D('.71'),10,900,800)
            self.reconcile(price='.88',position='0')
        self.assertEqual(self.j.db.execute('SELECT buys FROM markets').fetchone()[0],8)
        self.buy()
        self.assertIsNotNone(self.j.active())

    def test_partial_sale_no_reentry(self):
        self.buy(); self.reconcile()
        self.j.latch_stop('KXBTC15M-TEST','yes',D('.70'))
        self.j.submit(self.b,'KXBTC15M-TEST','sell','yes',D('.71'),10,900,800)
        self.reconcile(status='resting',filled='1',price='.88',position='9')
        with self.assertRaises(RuntimeError): self.buy()

    def test_cancel_ack_not_treated_as_terminal(self):
        self.buy(); self.reconcile(status='resting',filled='0',position='0')
        self.j.cancel(self.b)
        self.assertIsNotNone(self.j.active())
        self.assertFalse(self.j.ready)

    def test_window_excludes_close_and_too_early(self):
        for now in (659,900):
            with self.assertRaises(RuntimeError):
                self.j.submit(self.b,'KXBTC15M-TEST','buy','yes',D('.96'),1,900,now)

    def test_unmanaged_positions_fail_closed(self):
        with self.assertRaises(RuntimeError):
            self.j.reconcile([],[],[{'ticker':'OTHER','position_fp':'1'}])

    def test_settlement_reconciles_position(self):
        self.buy(); self.reconcile()
        self.j.reconcile(self.oldorders,self.oldfills,[],[{'ticker':'KXBTC15M-TEST'}])
        self.assertEqual(self.j.position(),{})

    def test_gateway_rejects_arbitrary_order(self):
        body=order_payload('KXBTC15M-T','buy','yes',D('.96'),1,900,'test-id')
        body['price']='0.5000'
        with self.assertRaises(ValueError): validate_write('POST',CREATE,body,now=780)
        with self.assertRaises(ValueError): validate_write('POST',CREATE.replace('/events',''),body,now=780)



class TradingTransportTests(unittest.TestCase):
    def test_signed_write_round_trip_over_unix_socket(self):
        import base64,os,threading,time
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from kalshi_bot.gateway import Server,TradingHandler
        from kalshi_bot.trade_api import TradingSigner,OrderBroker
        key=ed25519.Ed25519PrivateKey.generate()
        pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
        signer=TradingSigner('generated-test-key',pem)
        captured=[]
        def upstream(config,target,headers,method='GET',body=None):
            stamp=headers['kalshi-access-timestamp']
            key.public_key().verify(base64.b64decode(headers['kalshi-access-signature']),(stamp+method+target.split('?')[0]).encode())
            captured.append((method,target,body))
            value={'order_id':'test-order','client_order_id':body['client_order_id']} if body else {'order_id':'test-order','reduced_by':'1.00'}
            return 201 if method=='POST' else 200,{},json.dumps(value).encode()
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'gateway.sock')
            server=Server(path,TradingHandler)
            server.config={'client_uid':os.getuid(),'allow_trading':True}
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            with patch('kalshi_bot.gateway.forward',side_effect=upstream):
                thread.start()
                try:
                    broker=OrderBroker(signer,path,True)
                    body=order_payload('KXBTC15M-T','buy','no',D('.96'),1,int(time.time())+110,'test-id')
                    self.assertEqual(broker.create(body)['order_id'],'test-order')
                    broker.cancel('test-order','KXBTC15M-T')
                    self.assertEqual([c[0] for c in captured],['POST','DELETE'])
                    self.assertEqual(captured[0][2]['price'],'0.0400')
                finally:
                    server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_broker_default_cannot_submit(self):
        from kalshi_bot.trade_api import OrderBroker
        broker=OrderBroker(Mock(),'/nonexistent')
        with self.assertRaises(RuntimeError): broker.create({})

    def test_no_write_failover_after_unknown_network_result(self):
        from kalshi_bot.gateway import forward
        import time
        config={'allow_trading':True,'host':'external-api.demo.kalshi.co','addresses':['8.8.8.8','1.1.1.1']}
        body=order_payload('KXBTC15M-T','buy','yes',D('.96'),1,int(time.time())+110,'test-id')
        connection=Mock()
        connection.request.side_effect=OSError('unknown result')
        with patch('kalshi_bot.gateway.PinnedHTTPS',return_value=connection) as factory:
            with self.assertRaises(APIError): forward(config,CREATE,{},'POST',body)
            self.assertEqual(factory.call_count,1)


if __name__=='__main__': unittest.main()
