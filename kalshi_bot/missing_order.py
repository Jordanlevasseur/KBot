"""Bounded background-only verification of missing, finalized exchange orders.

This component does not authorize any trade. Journal.reconcile independently
validates the evidence against a fresh account cohort before setting ready.
"""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from decimal import Decimal as D
from .api import APIError, SchemaError, TICKER

PROOF_LIFETIME = 600.0
REFRESH_AFTER = 300.0
RETRY_DELAY = 90.0


class MissingOrderVerifier:
    def __init__(self, client_factory, clock=time.time):
        self.client_factory = client_factory
        self.clock = clock
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='missing-order-read')
        self.jobs = {}
        self.cache = {}
        self.retry_after = {}
        self.closed = False

    def _fetch(self, client_id, exchange_id, ticker):
        if not TICKER.fullmatch(ticker):
            raise SchemaError('Invalid historical order ticker')
        client = self.client_factory()
        direct = None
        direct_missing = False
        try:
            result = client.get('/portfolio/orders/' + exchange_id)
            direct = result.get('order')
            if not isinstance(direct, dict):
                raise SchemaError('Invalid direct order lookup')
        except APIError as exc:
            if exc.status != 404:
                raise
            # A 404 is an observation, not confirmation of cancellation.
            direct_missing = True
        market_response = client.get('/markets/' + ticker)
        market = market_response.get('market')
        if not isinstance(market, dict):
            raise SchemaError('Invalid finalized market response')
        market_orders = client.pages('/portfolio/orders', {'ticker': ticker})
        market_fills = client.pages('/portfolio/fills', {'ticker': ticker})
        settlements = client.pages('/portfolio/settlements')
        positions = client.pages('/portfolio/positions')
        for collection in (market_orders, market_fills, settlements, positions):
            if not isinstance(collection, list) or any(not isinstance(r, dict) for r in collection):
                raise SchemaError('Incomplete historical verification collection')
        return {
            'client_id': client_id,
            'exchange_id': exchange_id,
            'ticker': ticker,
            'direct_order': direct,
            'direct_missing': direct_missing,
            'market': market,
            'market_orders': market_orders,
            'market_fills': market_fills,
            'settlements': settlements,
            'positions': positions,
            'verified_at': self.clock(),
        }

    def proofs(self, journal, account_orders):
        """Never performs I/O or waits for a future on the execution thread."""
        now = self.clock()
        remote = {r.get('client_order_id') for r in account_orders}
        requested = []
        for r in journal.db.execute("SELECT client_id,exchange_id,ticker,cutoff,status,filled FROM orders WHERE status='canceled'"):
            if (r['client_id'] not in remote and r['exchange_id']
                    and D(r['filled']) == 0
                    and now > float(r['cutoff']) + 86400):
                requested.append((r['client_id'], r['exchange_id'], r['ticker']))
        wanted = {x[0]: x for x in requested}
        with self.lock:
            for cid, job in list(self.jobs.items()):
                if not job.done():
                    continue
                del self.jobs[cid]
                self.retry_after[cid] = now + RETRY_DELAY
                try:
                    proof = job.result()
                    if (proof['client_id'],proof['exchange_id'],proof['ticker']) == wanted.get(cid):
                        self.cache[cid] = proof
                except APIError as exc:
                    # A transient refresh failure does not revoke still-valid
                    # evidence. The existing expiry and fresh account checks
                    # remain mandatory. Auth/schema failures still fail closed.
                    if exc.status not in (0,429) and exc.status < 500:
                        self.cache.pop(cid, None)
                except Exception:
                    # Error or incomplete evidence never becomes a proof.
                    self.cache.pop(cid, None)
            for cid in list(self.cache):
                if cid not in wanted or now - self.cache[cid]['verified_at'] >= PROOF_LIFETIME:
                    del self.cache[cid]
            for cid, eid, ticker in requested:
                if len(self.jobs) >= 1:
                    break  # Never queue a backlog of exchange reads.
                proof=self.cache.get(cid)
                if ((proof is not None and now-proof['verified_at']<REFRESH_AFTER)
                        or cid in self.jobs or now < self.retry_after.get(cid, 0)):
                    continue
                if self.closed:
                    continue
                self.jobs[cid] = self.pool.submit(self._fetch, cid, eid, ticker)
                break
            return {cid: self.cache[cid] for cid in wanted if cid in self.cache}

    def close(self):
        with self.lock:
            self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)


def _require_canonical_direction(local, exchange, label):
    """Compare exposure, not Kalshi's deprecated action/side representation.

    Kalshi's documented direction table:
        buy-yes  / sell-no  -> outcome yes, book bid
        buy-no   / sell-yes -> outcome no,  book ask
    Require BOTH canonical direction fields and their mutual agreement.
    Invalid or missing fields fail closed rather than trusting legacy direction.
    """
    action, side = local['action'], local['side']
    if action not in ('buy', 'sell') or side not in ('yes', 'no'):
        raise RuntimeError(label + ': invalid local direction')
    expected = 'yes' if ((action == 'buy') == (side == 'yes')) else 'no'
    expected_book = 'bid' if expected == 'yes' else 'ask'
    if (exchange.get('outcome_side') != expected
            or exchange.get('book_side') != expected_book):
        raise RuntimeError(label + ': canonical exchange direction differs or missing')


def verified_missing_order(row, proof, local_orders, account_orders, account_fills,
                           positions, settlements, *, now=None):
    """Fail-closed assessment of settled zero-fill orders using independent reads."""
    from decimal import Decimal as D
    from .api import decimal
    now = time.time() if now is None else now
    if not isinstance(proof, dict) or any(proof.get(k) != row[k] for k in ('client_id', 'exchange_id', 'ticker')):
        raise RuntimeError('Missing order verification is not available')
    at = proof.get('verified_at')
    if type(at) not in (int, float) or not 0 <= now-at < PROOF_LIFETIME:
        raise RuntimeError('Missing order evidence is stale')
    if (row['status'] != 'canceled' or D(row['filled']) != 0 or not row['exchange_id']
            or now <= float(row['cutoff']) + 86400):
        raise RuntimeError('Missing order is not eligible for historical verification')
    for key in ('market_orders', 'market_fills', 'settlements', 'positions'):
        if not isinstance(proof.get(key), list) or any(not isinstance(x, dict) for x in proof[key]):
            raise RuntimeError('Incomplete independent historical evidence')
    ticker, eid = row['ticker'], row['exchange_id']
    found = [o for o in proof['market_orders'] if o.get('order_id') == eid or o.get('client_order_id') == row['client_id']]
    direct = proof.get('direct_order')
    if direct is not None:
        if not isinstance(direct, dict) or len(found) > 1 or (found and found[0] != direct):
            raise RuntimeError('Contradictory direct order evidence')
        found = [direct]
    elif proof.get('direct_missing') is not True:
        raise RuntimeError('Direct order verification incomplete')
    if len(found) > 1:
        raise RuntimeError('Ambiguous missing order identity')
    if found:
        actual = found[0]
        if (actual.get('order_id') != eid or actual.get('client_order_id') != row['client_id']
                or actual.get('ticker') != ticker or actual.get('status') != 'canceled'
                or decimal(actual.get('fill_count_fp'), 'historical filled quantity') != 0):
            raise RuntimeError('Missing order identity or canceled state differs')
        _require_canonical_direction(row, actual, 'Direct missing-order identity')
    for other in proof['market_orders']:
        if other.get('ticker') != ticker or other.get('status') not in ('executed','canceled','rejected','expired'):
            raise RuntimeError('Open or inconsistent historical market order')
    # A zero-fill canceled order may have no account settlement. The public
    # exchange market itself must be independently finalized; any portfolio
    # settlement that does exist must agree with that market's outcome.
    market = proof.get('market')
    if (not isinstance(market, dict) or market.get('ticker') != ticker
            or market.get('status') != 'finalized'
            or market.get('result') not in ('yes', 'no')
            or not isinstance(market.get('settlement_ts'), str)
            or not market['settlement_ts']):
        raise RuntimeError('Market finalization not independently confirmed')
    independently_settled = [x for x in proof['settlements'] if x.get('ticker') == ticker]
    freshly_settled = [x for x in settlements if x.get('ticker') == ticker]
    if (len(independently_settled) > 1 or len(freshly_settled) > 1
            or any(s.get('market_result') != market['result']
                   for s in independently_settled + freshly_settled)):
        raise RuntimeError('Portfolio settlement contradicts finalized market')
    for rows in (proof['positions'], positions):
        if any(p.get('ticker') == ticker and decimal(p.get('position_fp'), 'position') != 0 for p in rows):
            raise RuntimeError('Historical market has an open position')
    owned = {r['exchange_id']: r for r in local_orders
             if r['ticker'] == ticker and r['exchange_id']}
    totals = {}
    seen = set()
    for fill in proof['market_fills']:
        fid, oid = fill.get('fill_id'), fill.get('order_id')
        if not isinstance(fid,str) or not fid or fid in seen or not isinstance(oid,str):
            raise RuntimeError('Missing or duplicate historical fill identity')
        seen.add(fid)
        amount=decimal(fill.get('count_fp'),'historical fill count')
        if amount <= 0:
            raise RuntimeError('Invalid historical fill quantity')
        local = owned.get(oid)
        if local is not None:
            _require_canonical_direction(local, fill, 'Historical exchange fill')
        totals[oid] = totals.get(oid,D(0)) + amount
    if totals.get(eid,D(0)) or any(f.get('order_id') == eid for f in account_fills):
        raise RuntimeError('Missing order has a fill')
    if any(oid not in owned for oid in totals):
        raise RuntimeError('Unmanaged historical market fill')
    if any(totals.get(oid,D(0)) != D(r['filled']) for oid,r in owned.items()):
        raise RuntimeError('Exchange market fills disagree with local orders')
    for o in proof['market_orders']:
        local = owned.get(o.get('order_id'))
        if (local is None or o.get('client_order_id') != local['client_id']
                or decimal(o.get('fill_count_fp'),'market order fills') != totals.get(o['order_id'],D(0))):
            raise RuntimeError('Exchange market orders disagree with local orders')
        _require_canonical_direction(local, o, 'Historical exchange order')
    for o in account_orders:
        if o.get('ticker') == ticker and o.get('order_id') not in owned:
            raise RuntimeError('Unmanaged order in freshly confirmed settled market')
    for f in account_fills:
        if f.get('ticker') == ticker and f.get('order_id') not in owned:
            raise RuntimeError('Unmanaged fill in freshly confirmed settled market')
    if any(o.get('order_id') == eid for o in account_orders):
        raise RuntimeError('Exchange account listing contradicts missing order')
    # 404 alone does not satisfy verification; finalized market, complete
    # ticker-specific fills, all market orders and zero positions are required.
    return found[0] if found else None
