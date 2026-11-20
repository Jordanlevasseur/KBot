"""Offline API contract/security tests. No external sockets or real credentials."""
import base64
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from kalshi_bot.api import APIError, Client, GatewayTransport, ROOT, SchemaError, Signer, allowed_path, book_prices
from kalshi_bot.account import AccountStore, cutoff, safe_text, snapshot
from kalshi_bot.credentials import configure, load, private_read
from kalshi_bot.gateway import Handler, Server, forward, nft_policy, validate_config, verify_firewall


def signer(key=None):
    key=key or ed25519.Ed25519PrivateKey.generate()
    pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    return Signer('offline-test-key',pem),key,pem


def encoded(body):
    return 200,{},json.dumps(body).encode()


class AuthTests(unittest.TestCase):
    def test_ed25519_signs_path_without_query(self):
        s,key,_=signer()
        headers=s.headers('GET',ROOT+'/portfolio/orders?limit=20',1234)
        key.public_key().verify(base64.b64decode(headers['KALSHI-ACCESS-SIGNATURE']),b'1234GET/trade-api/v2/portfolio/orders')

    def test_rsa_pss_signatures(self):
        s,key,_=signer(rsa.generate_private_key(public_exponent=65537,key_size=2048))
        headers=s.headers('GET',ROOT+'/portfolio/balance',9)
        key.public_key().verify(base64.b64decode(headers['KALSHI-ACCESS-SIGNATURE']),b'9GET/trade-api/v2/portfolio/balance',padding.PSS(mgf=padding.MGF1(hashes.SHA256()),salt_length=32),hashes.SHA256())

    def test_writes_never_signed(self):
        s,_,_=signer()
        with self.assertRaises(ValueError):
            s.headers('POST',ROOT+'/portfolio/events/orders')

    def test_key_errors_do_not_echo_key(self):
        with self.assertRaises(ValueError) as caught:
            Signer('valid','PRIVATE-SECRET'.encode())
        self.assertNotIn('PRIVATE-SECRET',str(caught.exception))

    def test_local_private_configuration_environment_and_modes(self):
        _,_,pem=signer()
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'input.pem'
            source.write_bytes(pem)
            source.chmod(0o600)
            directory=Path(d)/'credentials'
            configure(directory,'demo','test-id',source)
            self.assertEqual(directory.stat().st_mode&0o777,0o700)
            self.assertEqual((directory/'private.pem').stat().st_mode&0o777,0o600)
            self.assertIsInstance(load(directory,'demo'),Signer)
            with self.assertRaises(ValueError):
                load(directory,'production')
            with self.assertRaises(ValueError):
                configure(directory,'demo','test-id',source)

    def test_world_readable_and_symlink_keys_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'key'
            path.write_text('not-secret')
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                private_read(path)
            alias=Path(d)/'alias'
            alias.symlink_to(path)
            with self.assertRaises(OSError):
                private_read(alias)

    def test_credentials_cannot_be_saved_in_repository(self):
        _,_,pem=signer()
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'key'
            source.write_bytes(pem)
            source.chmod(0o600)
            with self.assertRaises(ValueError):
                configure(Path(__file__).resolve().parent.parent/'forbidden-credentials','demo','id',source)

    def test_symlinked_parent_cannot_redirect_credentials_into_repository(self):
        _,_,pem=signer()
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'key'
            source.write_bytes(pem)
            source.chmod(0o600)
            alias=Path(d)/'repo-alias'
            alias.symlink_to(Path(__file__).resolve().parent.parent,target_is_directory=True)
            with self.assertRaises(ValueError):
                configure(alias/'forbidden-credentials','demo','id',source)


class ClientTests(unittest.TestCase):
    def client(self,transport):
        s,_,_=signer()
        self.sleeps=[]
        return Client(s,transport,sleep=self.sleeps.append,clock=lambda:100)

    def test_endpoint_allowlist_rejects_money_movement_and_arbitrary_urls(self):
        for target in ('https://evil.example/a',ROOT+'/portfolio/withdrawals',ROOT+'/portfolio/deposits',ROOT+'/api_keys',ROOT+'/portfolio/events/orders',ROOT+'/markets/../../etc',ROOT+'/markets/KXBTC15M-X%2f../a',ROOT+'/markets?limit=1&limit=2',ROOT+'/markets?series_ticker=OTHER',ROOT+'/markets#fragment'):
            with self.subTest(target=target),self.assertRaises(ValueError):
                allowed_path(target)

    def test_expected_read_endpoints_allowed(self):
        for path in ('/markets?series_ticker=KXBTC15M&status=open','/portfolio/balance','/portfolio/positions','/portfolio/orders','/portfolio/fills','/portfolio/settlements','/markets/KXBTC15M-X/orderbook?depth=10'):
            self.assertTrue(allowed_path(ROOT+path).startswith(ROOT))

    def test_pagination_collects_all_pages(self):
        transport=Mock(side_effect=[encoded({'fills':[{'fill_id':'1'}],'cursor':'next'}),encoded({'fills':[{'fill_id':'2'}]})])
        result=self.client(transport).pages('/portfolio/fills')
        self.assertEqual(len(result),2)
        self.assertIn('cursor=next',transport.call_args[0][0])

    def test_repeated_cursor_fails_instead_of_partial_snapshot(self):
        transport=Mock(return_value=encoded({'orders':[],'cursor':'same'}))
        with self.assertRaises(SchemaError):
            self.client(transport).pages('/portfolio/orders')
        self.assertEqual(transport.call_count,2)

    def test_rate_limits_and_server_errors_retry(self):
        transport=Mock(side_effect=[(429,{'Retry-After':'3'},b'ignored'),(503,{},b''),encoded({'balance':100})])
        self.assertEqual(self.client(transport).get('/portfolio/balance')['balance'],100)
        self.assertIn(3,self.sleeps)

    def test_network_failure_retries_bounded(self):
        transport=Mock(side_effect=APIError())
        with self.assertRaises(APIError):
            self.client(transport).get('/portfolio/balance')
        self.assertEqual(transport.call_count,4)

    def test_auth_failure_not_retried_or_logged(self):
        transport=Mock(return_value=(401,{},b'PRIVATE-RESPONSE'))
        with self.assertRaises(APIError) as e:
            self.client(transport).get('/portfolio/balance')
        self.assertNotIn('PRIVATE-RESPONSE',str(e.exception))
        self.assertEqual(transport.call_count,1)

    def test_long_retry_after_fails_closed(self):
        with self.assertRaises(APIError):
            self.client(Mock(return_value=(429,{'Retry-After':'500'},b''))).get('/portfolio/balance')

    def test_redirect_never_followed(self):
        transport=Mock(return_value=(302,{'Location':'https://evil.example/'},b''))
        with self.assertRaises(APIError):
            self.client(transport).get('/portfolio/balance')
        self.assertEqual(transport.call_count,1)

    def test_schema_errors(self):
        for response in ((200,{},b'not-json'),encoded([])):
            with self.assertRaises(SchemaError):
                self.client(Mock(return_value=response)).get('/portfolio/balance')

    def test_order_submissions_blocked_without_transport(self):
        transport=Mock()
        with self.assertRaises(RuntimeError):
            self.client(transport).submit_order(price=95)
        transport.assert_not_called()

    def test_precise_yes_no_book_complements(self):
        prices=book_prices({'orderbook_fp':{'yes_dollars':[['0.9500','2.0'],['0.96','0']], 'no_dollars':[['0.0300','5']]}})
        self.assertEqual(str(prices['yes_ask']),'0.9700')
        self.assertEqual(str(prices['no_ask']),'0.0500')

    def test_invalid_book_prices_rejected(self):
        for price in ('NaN','Infinity','-0.01','1.01'):
            with self.assertRaises(SchemaError):
                book_prices({'orderbook_fp':{'yes_dollars':[[price,'1']],'no_dollars':[]}})


class SnapshotTests(unittest.TestCase):
    def fake(self):
        client=Mock()
        client.get.side_effect=lambda path,params=None: {'balance':1200} if path=='/portfolio/balance' else ({'orderbook_fp':{'yes_dollars':[['0.95','2']],'no_dollars':[['0.03','1']]}} if path.endswith('orderbook') else {'trading_active':True})
        def pages(path,params=None):
            if path=='/portfolio/positions':
                return [{'ticker':'KXBTC15M-X','position_fp':'-2.00','realized_pnl_dollars':'0.32','market_exposure_dollars':'1.92'}]
            if path=='/markets':
                return [{'ticker':'KXBTC15M-X','market_type':'binary','status':'active','open_time':'1970-01-01T00:00:00Z','close_time':'2030-01-01T00:00:00Z'}]
            return []
        client.pages.side_effect=pages
        return client

    def test_account_snapshot_no_side_and_cutoff(self):
        client=self.fake()
        state=snapshot(client,now=100)
        self.assertEqual(state['cash_cents'],1200)
        self.assertEqual(state['positions'][0]['side'],'no')
        self.assertEqual(state['positions'][0]['quantity'],'2.00')
        self.assertTrue(state['read_only'])
        self.assertIn('read-only',state['entries_blocked'])

    def test_partial_snapshot_is_not_persisted(self):
        client=self.fake()
        client.pages.side_effect=APIError()
        with tempfile.TemporaryDirectory() as d:
            store=AccountStore(Path(d)/'db','demo')
            with self.assertRaises(APIError):
                store.save(snapshot(client))
            self.assertIsNone(store.latest())
            store.close()

    def test_restart_and_environment_separation(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db'
            store=AccountStore(path,'demo')
            store.save({'read_only':True,'cash_cents':123})
            store.close()
            store=AccountStore(path,'demo')
            self.assertEqual(store.latest()[1]['cash_cents'],123)
            store.close()
            with self.assertRaises(ValueError):
                AccountStore(path,'production')

    def test_bad_cutoffs_and_terminal_escapes(self):
        for value in ('bad','2030-01-01T00:00:00',None):
            with self.assertRaises(SchemaError):
                cutoff({'close_time':value})
        self.assertNotIn('\033',safe_text('\033[2Jmalicious'))

    def test_unknown_balance_does_not_become_zero(self):
        client=self.fake()
        client.get.return_value={}
        client.get.side_effect=None
        with self.assertRaises(SchemaError):
            snapshot(client)


class GatewayTests(unittest.TestCase):
    def config(self):
        return {'environment':'demo','host':'external-api.demo.kalshi.co','uid':999,'client_uid':1000,'addresses':['8.8.8.8','2606:4700:4700::1111']}

    def test_firewall_has_narrow_allows_and_uid_terminal_reject(self):
        text=nft_policy(self.config())
        self.assertIn('ip daddr { 8.8.8.8 } tcp dport 443',text)
        self.assertIn('ip6 daddr { 2606:4700:4700::1111 } tcp dport 443',text)
        self.assertIn('meta skuid 999 counter reject',text)
        self.assertNotIn('flush ruleset',text)
        self.assertNotIn('dport 53',text)

    def test_arbitrary_gateway_host_private_ip_root_uid_rejected(self):
        for change in ({'host':'evil.example'},{'addresses':['127.0.0.1']},{'uid':0},{'client_uid':0}):
            with self.assertRaises(ValueError):
                validate_config({**self.config(),**change})

    def test_missing_firewall_fails_before_network(self):
        import subprocess
        with patch('kalshi_bot.gateway.subprocess.run',side_effect=subprocess.CalledProcessError(1,'nft')):
            with self.assertRaises(subprocess.CalledProcessError):
                verify_firewall(self.config())

    def test_firewall_verifier_exact_kernel_rule_shape(self):
        config=self.config()
        def match(left,right):
            return {'match':{'op':'==','left':left,'right':right}}
        uid=match({'meta':{'key':'skuid'}},999)
        rules=[]
        for protocol,address in (('ip','8.8.8.8'),('ip6','2606:4700:4700::1111')):
            rules.append({'rule':{'expr':[uid,match({'payload':{'protocol':protocol,'field':'daddr'}},address),match({'payload':{'protocol':'tcp','field':'dport'}},443),{'counter':{'packets':0,'bytes':0}},{'accept':None}]}})
        rules.append({'rule':{'expr':[uid,{'counter':{}},{'reject':{'type':'icmpx','expr':'port-unreachable'}}]}})
        data={'nftables':[{'chain':{'hook':'output','prio':0,'policy':'accept'}},*rules]}
        with patch('kalshi_bot.gateway.subprocess.run',return_value=Mock(stdout=json.dumps(data))):
            verify_firewall(config)
        rules.pop()
        with patch('kalshi_bot.gateway.subprocess.run',return_value=Mock(stdout=json.dumps({'nftables':[data['nftables'][0],*rules]}))):
            with self.assertRaises(RuntimeError):
                verify_firewall(config)

    def test_unix_gateway_to_client_end_to_end_no_external_network(self):
        s,_,_=signer()
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'gateway.sock')
            server=Server(path,Handler)
            server.config={**self.config(),'client_uid':os.getuid()}
            def mock_forward(config,target,headers):
                self.assertEqual(target,ROOT+'/portfolio/balance')
                self.assertIn('kalshi-access-signature',headers)
                return encoded({'balance':1234})
            with patch('kalshi_bot.gateway.forward',side_effect=mock_forward) as upstream:
                thread=threading.Thread(target=server.serve_forever,daemon=True)
                thread.start()
                try:
                    client=Client(s,GatewayTransport(path))
                    self.assertEqual(client.get('/portfolio/balance')['balance'],1234)
                    self.assertEqual(upstream.call_count,1)
                    # HTTP writes cannot reach the upstream, even with valid auth.
                    from kalshi_bot.api import UnixHTTP
                    conn=UnixHTTP(path)
                    try:
                        conn.request('POST',ROOT+'/portfolio/events/orders',body='{}')
                        self.assertEqual(conn.getresponse().status,501)
                    except BrokenPipeError:
                        # The GET-only server may reject and close the socket
                        # before HTTPConnection finishes sending the body.
                        pass
                    finally: conn.close()
                    self.assertEqual(upstream.call_count,1)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

    def test_tls_uses_pinned_ip_and_fixed_official_hostname(self):
        config=self.config()
        response=Mock(status=200)
        response.read.return_value=b'{}'
        response.getheaders.return_value=[]
        connection=Mock()
        connection.getresponse.return_value=response
        with patch('kalshi_bot.gateway.PinnedHTTPS',return_value=connection) as tls:
            forward(config,ROOT+'/portfolio/balance',{})
            tls.assert_called_once_with('external-api.demo.kalshi.co','8.8.8.8')
        with patch('kalshi_bot.gateway.PinnedHTTPS') as tls:
            with self.assertRaises(ValueError):
                forward(config,'https://evil.example/',{})
            tls.assert_not_called()

class IdentityTests(unittest.TestCase):
    def test_cache_rejects_a_different_key_identity(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db'
            store=AccountStore(path,'demo','first-key-hash')
            store.close()
            with self.assertRaises(ValueError):
                AccountStore(path,'demo','different-key-hash')

    def test_key_fingerprint_is_stable_without_disclosing_private_material(self):
        s,_,pem=signer()
        self.assertEqual(s.fingerprint,Signer('offline-test-key',pem).fingerprint)
        self.assertNotEqual(s.fingerprint,Signer('other-id',pem).fingerprint)
        self.assertEqual(len(s.fingerprint),64)


class BindIsolationTests(unittest.TestCase):
    def test_missing_os_bind_denial_blocks_gateway(self):
        from kalshi_bot.gateway import verify_bind_denial
        fake=Mock()
        fake.__enter__=Mock(return_value=fake)
        fake.__exit__=Mock(return_value=False)
        with patch('kalshi_bot.gateway.socket.socket',return_value=fake):
            with self.assertRaises(RuntimeError):
                verify_bind_denial()

    def test_os_bind_denial_is_required_for_both_ip_families(self):
        import errno
        from kalshi_bot.gateway import verify_bind_denial
        fake=Mock()
        fake.__enter__=Mock(return_value=fake)
        fake.__exit__=Mock(return_value=False)
        fake.bind.side_effect=PermissionError(errno.EPERM,'denied')
        with patch('kalshi_bot.gateway.socket.socket',return_value=fake):
            verify_bind_denial()
        self.assertEqual(fake.bind.call_count,2)


if __name__=='__main__':
    unittest.main()
