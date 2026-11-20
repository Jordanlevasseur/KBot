"""Versioned prepared orders and bounded background-only account reads.

No account GET or sizing operation is performed by the critical submit method.
Prepared state never survives restart as authorization. All deadlines are monotonic.
"""
import copy
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from dataclasses import dataclass
from decimal import Decimal as D,ROUND_CEILING
import json
from pathlib import Path
import threading
import time
from types import MappingProxyType
import uuid
from .account import cutoff
from .api import SchemaError,TICKER,decimal
from .buying_power import market_scope,cash_cents
from .execution import affordable,order_payload
from .fees import entry_reserve
from .strategy import policy

CASH_TTL=2.5
ACCOUNT_TTL=5.0
MARKET_TTL=5.0
FEES_TTL=30.0


@dataclass(frozen=True)
class Plan:
    generation:int
    ticker:str
    action:str
    side:str
    price:D
    quantity:D
    cutoff:int
    reserve_cents:int
    payload:object
    verified_fee:object=None


def make_plan(generation,market,action,side,price,quantity,reserve,verified_fee=None):
    close=int(cutoff(market));cid=str(uuid.uuid4())
    body=order_payload(market['ticker'],action,side,price,quantity,close,cid)
    cost=int((D(quantity)*(price+reserve)*100).to_integral_value(rounding=ROUND_CEILING)) if action=='buy' else 0
    return Plan(generation,market['ticker'],action,side,price,D(quantity),close,cost,MappingProxyType(body),verified_fee)


def account_signature(data):
    # Ignore changing valuation/P&L fields, but include every order/fill/quantity.
    keys=(('client_order_id','order_id','ticker','type','status','initial_count_fp','fill_count_fp','remaining_count_fp','yes_price_dollars'),
          ('fill_id','order_id','count_fp','yes_price_dollars','no_price_dollars','fee_cost'),
          ('ticker','position_fp','netting_enabled'),None)
    projected=[]
    for values,fields in zip(data,keys):
        rows=[{k:v[k] for k in fields if k in v} if fields else v for v in values]
        projected.append(sorted((json.dumps(r,sort_keys=True,default=str) for r in rows)))
    return json.dumps(projected,separators=(',',':'))


class Inputs:
    """Atomic generations prevent in-flight pre-write reads rearming an old plan."""
    def __init__(self,clock=time.monotonic):
        self.clock=clock;self.lock=threading.RLock();self.generation=0;self.revision=0
        self.cash=None;self.market=None;self.exchange=None;self.fees=None;self.account=None
        self.account_seen=None;self.account_confirmed=None;self.last_cash=None;self.reason='Preparing account and buying power'
        self.read_times={};self.changed=threading.Event()
    def _invalidate(self,reason):
        self.generation+=1;self.revision+=1;self.cash=None;self.account=None;self.account_confirmed=None
        self.reason=reason;self.changed.set()
    def invalidate(self,reason):
        with self.lock:self._invalidate(reason)
    def epoch(self):
        with self.lock:return self.generation
    def set_account(self,epoch,data,at):
        signature=account_signature(data)
        with self.lock:
            self.read_times['account_seconds']=max(0,self.clock()-at)
            if epoch!=self.generation:return False
            if self.account_seen is not None and signature!=self.account_seen:self._invalidate('Account orders, fills or positions changed')
            self.account_seen=signature
            self.account=(self.generation,copy.deepcopy(data),at)
            self.revision+=1;self.changed.set();return True
    def confirm_account(self,generation,at):
        with self.lock:
            if generation!=self.generation:return False
            self.account_confirmed=(generation,at);self.revision+=1;self.changed.set();return True
    def set_cash(self,epoch,market,cents,at):
        scope=(market['ticker'],market_scope(market)['exchange_index'])
        if type(cents) is not int or cents<0:raise ValueError('Invalid cash')
        with self.lock:
            self.read_times['cash_seconds']=max(0,self.clock()-at)
            if epoch!=self.generation:return False
            if self.last_cash is not None and self.last_cash!=(scope,cents):self._invalidate('Available market cash changed')
            self.last_cash=(scope,cents)
            self.cash=(self.generation,scope,cents,at);self.revision+=1;self.changed.set();return True
    def cash_uncertain(self):
        with self.lock:
            self.cash=None;self.revision+=1;self.reason='Cash refresh unavailable; buys blocked, protective account lease retained';self.changed.set()
    def fees_uncertain(self):
        with self.lock:
            self.fees=None;self.revision+=1;self.reason='Fee refresh unavailable; buys blocked, protective account lease retained';self.changed.set()
    def set_meta(self,kind,value,at):
        with self.lock:
            if kind=='market':
                market_scope(value)
                if self.market and any(self.market[0].get(k)!=value.get(k) for k in ('ticker','exchange_index','event_ticker','close_time','status','market_type')):self._invalidate('Verified market metadata changed')
            elif kind=='exchange' and value.get('trading_active') is not True:self._invalidate('Exchange is not trading')
            setattr(self,kind,(copy.deepcopy(value),at));self.revision+=1;self.changed.set()
    def view(self):
        with self.lock:
            return {'generation':self.generation,'revision':self.revision,'cash':self.cash,'market':self.market,
                    'exchange':self.exchange,'fees':self.fees,'account':self.account,'confirmed':self.account_confirmed,'reason':self.reason,'read_times':dict(self.read_times)}
    def fresh(self,view,ticker,*,selling=False):
        now=self.clock();generation=view['generation']
        confirmed=view['confirmed']
        if not confirmed or confirmed[0]!=generation or not 0<=now-confirmed[1]<=ACCOUNT_TTL:return 'Account reconciliation is stale or uncertain'
        market=view['market']
        if not market or market[0]['ticker']!=ticker or not 0<=now-market[1]<=MARKET_TTL:return 'Verified market state is stale or changed'
        if selling:return None
        exchange,fees,cash=view['exchange'],view['fees'],view['cash']
        if not exchange or not 0<=now-exchange[1]<=MARKET_TTL or exchange[0].get('trading_active') is not True:return 'Exchange trading status is stale or inactive'
        if not cash or cash[0]!=generation or cash[1]!=(ticker,market_scope(market[0])['exchange_index']) or not 0<=now-cash[3]<=CASH_TTL:return 'Available market cash is stale or uncertain'
        if not fees or fees[0][0]!=(ticker,market[0].get('event_ticker')) or not 0<=now-fees[1]<=FEES_TTL:return 'Verified fee reserve is stale or changed'
        return None
    def claim(self,plan):
        with self.lock:
            view=self.view()
            if plan.generation!=self.generation:raise RuntimeError('Prepared generation changed')
            reason=self.fresh(view,plan.ticker,selling=plan.action=='sell')
            if reason:raise RuntimeError(reason)
            if plan.action=='buy' and plan.verified_fee!=view['fees'][0][1]:raise RuntimeError('Prepared fee schedule changed; asynchronous recalculation required')
            if plan.action=='buy' and plan.reserve_cents>view['cash'][2]:raise RuntimeError('Prepared buying power no longer sufficient')
            deadlines=[view['confirmed'][1]+ACCOUNT_TTL,view['market'][1]+MARKET_TTL]
            if plan.action=='buy':deadlines.extend((view['cash'][3]+CASH_TTL,view['fees'][1]+FEES_TTL,view['exchange'][1]+MARKET_TTL))
            deadline=min(deadlines)
            self._invalidate('Prepared order consumed; awaiting acknowledgment and reconciliation')
            return self.generation,deadline


class FillHistory:
    """Main-owner projection of durable fills; background readers never use SQLite.

    Closed-order fills are confirmed facts, not a cached account authorization.
    Current, held and unresolved markets are always fetched authoritatively.
    Global orders/positions and the original strict reconciler remain required.
    """
    def __init__(self):self.lock=threading.Lock();self.value=((),())
    def update(self,journal):
        mutable={r[0] for r in journal.db.execute("SELECT DISTINCT ticker FROM orders WHERE status NOT IN ('executed','canceled','rejected','expired')")}
        mutable.update(t for t,_ in journal.position())
        records=tuple((r[0],json.loads(r[1])) for r in journal.db.execute('SELECT orders.ticker,fills.payload FROM fills JOIN orders ON orders.exchange_id=fills.order_id'))
        with self.lock:self.value=(tuple(sorted(mutable)),records)
    def snapshot(self):
        with self.lock:return copy.deepcopy(self.value)


def combined_fills(history,fresh):
    values={}
    for row in history+fresh:
        key=row.get('fill_id')
        if not isinstance(key,str) or not key:raise SchemaError('Invalid fill identity')
        previous=values.get(key)
        if previous is not None:
            if previous.get('order_id')!=row.get('order_id') or any(
                decimal(previous.get(k,'0'),'fill')!=decimal(row.get(k,'0'),'fill')
                for k in ('count_fp','yes_price_dollars','no_price_dollars','fee_cost')):
                raise SchemaError('Conflicting confirmed fill records')
        values[key]=row
    return list(values.values())


class Refreshers:
    """GET-only worker pool leaves gateway slots available for protective writes."""
    def __init__(self,inputs,client_factory,market_source,notify,fill_source=None):
        self.inputs=inputs;self.client_factory=client_factory;self.market_source=market_source;self.notify=notify;self.fill_source=fill_source
        self.stop=threading.Event();self.pool=ThreadPoolExecutor(max_workers=3,thread_name_prefix='prepare-read');self.threads=[]
    def client(self):return self.client_factory()
    def target(self):
        values=self.market_source();return values[0] if values else None
    def failed(self,exc=None):
        from .diagnostics import failure_reason
        reason='Background exchange refresh failed; new entries blocked'
        if exc is not None:reason+=': '+failure_reason(exc)
        self.inputs.invalidate(reason);self.notify()
    def _observed(self,client,started):
        observed=getattr(client,'observed_at',None)
        # Older gateways/test transports fall back to the conservative call age.
        return observed if type(observed) in (float,int) and started<=observed<=self.inputs.clock() else started
    def _get(self,path,params=None):
        started=self.inputs.clock();client=self.client();value=client.get(path,params)
        return value,self._observed(client,started)
    def _read_fees(self,market):
        started=self.inputs.clock();client=self.client();value=entry_reserve(client,market)
        return value,self._observed(client,started)
    def _read_pages(self,path):
        started=self.inputs.clock();client=self.client()
        if path=='/portfolio/fills' and self.fill_source is not None:
            mutable,history=self.fill_source();tickers=set(mutable);target=self.target()
            if target:tickers.add(target['ticker'])
            if len(tickers)>100 or any(not isinstance(t,str) or not TICKER.fullmatch(t) for t in tickers):
                raise SchemaError('Cannot verify the fresh fill market scope')
            baseline=[row for ticker,row in history if ticker not in tickers]
            fresh=client.pages(path,{'ticker':','.join(sorted(tickers))}) if tickers else []
            value=combined_fills(baseline,fresh)
        else:value=client.pages(path)
        return value,self._observed(client,started)
    def _accounts(self):
        paths=('/portfolio/orders','/portfolio/fills','/portfolio/positions','/portfolio/settlements')
        # Refresh fast collections while another connection is slow. A cohort
        # is published only when EVERY collection belongs to the current epoch
        # and its actual successful GET remains within the account age bound.
        epoch=self.inputs.epoch();latest={};last_published=None
        jobs={self.pool.submit(self._read_pages,path):(path,epoch) for path in paths}
        while not self.stop.is_set():
            done,_=wait(jobs,timeout=.1,return_when=FIRST_COMPLETED)
            current=self.inputs.epoch()
            if current!=epoch:epoch=current;latest={};last_published=None
            for job in done:
                path,submitted_epoch=jobs.pop(job)
                try:
                    data,at=job.result()
                    if submitted_epoch==epoch and epoch==self.inputs.epoch():latest[path]=(data,at)
                except Exception as exc:
                    if not self.stop.is_set():self.failed(exc)
                    epoch=self.inputs.epoch();latest={}
                if not self.stop.is_set():
                    current=self.inputs.epoch()
                    jobs[self.pool.submit(self._read_pages,path)]=(path,current)
            if len(latest)==len(paths):
                at=min(latest[path][1] for path in paths)
                cohort=(epoch,tuple(latest[path][1] for path in paths))
                if cohort!=last_published and 0<=self.inputs.clock()-at<=ACCOUNT_TTL:
                    if self.inputs.set_account(epoch,[latest[path][0] for path in paths],at):
                        self.notify();last_published=cohort
    def _cash(self):
        while not self.stop.is_set():
            try:
                hint=self.target();verified=self.inputs.view()['market']
                market=verified[0] if verified and hint and verified[0]['ticker']==hint['ticker'] else None
                if market:
                    epoch=self.inputs.epoch()
                    result,started=self.pool.submit(self._get,'/portfolio/balance',market_scope(market)).result()
                    self.inputs.set_cash(epoch,market,cash_cents(result),started);self.notify()
            except Exception:
                if not self.stop.is_set():self.inputs.cash_uncertain();self.notify()
            self.stop.wait(.05)
    def _metadata(self):
        while not self.stop.is_set():
            try:
                hint=self.target()
                if hint:
                    market_job=self.pool.submit(self._get,'/markets/'+hint['ticker'])
                    exchange_job=self.pool.submit(self._get,'/exchange/status')
                    response,market_started=market_job.result();market=response['market']
                    self.inputs.set_meta('market',market,market_started);self.notify()
                    exchange,exchange_started=exchange_job.result()
                    self.inputs.set_meta('exchange',exchange,exchange_started);self.notify()
                    fees=self.inputs.view()['fees'];key=(market['ticker'],market.get('event_ticker'))
                    if not fees or fees[0][0]!=key or self.inputs.clock()-fees[1]>FEES_TTL/2:
                        try:reserve,fee_started=self.pool.submit(self._read_fees,market).result()
                        except Exception:
                            if not self.stop.is_set():self.inputs.fees_uncertain();self.notify()
                            continue
                        self.inputs.set_meta('fees',(key,reserve),fee_started);self.notify()
            except Exception as exc:
                if not self.stop.is_set():self.failed(exc)
            self.stop.wait(.1)
    def start(self):
        for work in (self._accounts,self._cash,self._metadata):
            thread=threading.Thread(target=work,daemon=True);thread.start();self.threads.append(thread)
    def close(self):
        self.stop.set();self.pool.shutdown(wait=False,cancel_futures=True)
        for thread in self.threads:thread.join(timeout=.2)


class PreparedEngine:
    """Single execution owner. build/apply run ahead of, never inside, execute."""
    def __init__(self,journal,inputs,feed,broker,fee_floor=D(0),clock=time.monotonic,missing_verifier=None):
        self.journal=journal;self.inputs=inputs;self.feed=feed;self.broker=broker;self.fee_floor=fee_floor;self.clock=clock
        self.missing_verifier=missing_verifier
        self.plans={};self.sell=None;self.revision=-1;self.applied=None;self.claimed=None
        self.reason='Preparing';self.durable_revision=None;self.previous_side=None;self.scope_mismatch=None
        journal.db.executescript("""CREATE TABLE IF NOT EXISTS prepared_entry(id INTEGER PRIMARY KEY CHECK(id=1),generation INTEGER,status TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS entry_reservations(client_id TEXT PRIMARY KEY,generation INTEGER,cents INTEGER,status TEXT,original_cents INTEGER);
        CREATE TABLE IF NOT EXISTS prepared_cancellations(client_id TEXT PRIMARY KEY,after REAL);
        UPDATE prepared_cancellations SET after=0;
        UPDATE prepared_entry SET status='unarmed_after_restart';""")
        if 'original_cents' not in {r[1] for r in journal.db.execute('PRAGMA table_info(entry_reservations)')}:
            with journal.db:
                journal.db.execute('ALTER TABLE entry_reservations ADD COLUMN original_cents INTEGER')
                journal.db.execute('UPDATE entry_reservations SET original_cents=cents')
        journal.on_recorded=self._record
        self.previous_intent=getattr(journal,'on_intent',None)
        journal.on_intent=self._intent
        journal.before_transmit=self._transmit
        broker.before_wire=self._transmit
        self.claim_generation=None;self.claim_deadline=None
    def _intent(self,ticker,side):
        if self.previous_intent:self.previous_intent(ticker,side)
    def _transmit(self,payload):
        from .trade_api import OrderNotSent
        plan=self.claimed
        if plan is None:return
        if getattr(self.journal,'before_submit',None):self.journal.before_submit()
        if self.clock()>self.claim_deadline:raise OrderNotSent('Prepared authorization expired before transmission')
        if self.inputs.epoch()!=self.claim_generation:raise OrderNotSent('Prepared inputs changed before transmission')
        if time.time()>=plan.cutoff:raise OrderNotSent('Official cutoff passed before transmission')
        if plan.action=='buy':
            from .strategy import ENTRY_CUTOFF_SECONDS
            if time.time()>=plan.cutoff-ENTRY_CUTOFF_SECONDS:raise OrderNotSent('Final 30-second purchase cutoff reached before transmission')
            if not self.journal.running:raise OrderNotSent('Entries paused before transmission')
            fees=self.inputs.view()['fees']
            if not fees or plan.verified_fee!=fees[0][1]:raise OrderNotSent('Prepared fee schedule changed before transmission')
            prices=self.feed.quote(plan.ticker);ask=prices.get(plan.side+'_ask') if prices else None
            if ask is None or D(policy().buy_min_cents)/100>ask or ask>D('.99') or ask>plan.price:
                raise OrderNotSent('Prepared price became stale or changed before transmission')

    def _record(self,payload):
        if not self.claimed:return
        p=self.claimed
        self.journal.db.execute('INSERT INTO entry_reservations(client_id,generation,cents,status,original_cents) VALUES(?,?,?,?,?)',(payload['client_order_id'],p.generation,p.reserve_cents,'pending_reconciliation',p.reserve_cents))
        self.journal.db.execute("UPDATE prepared_entry SET status='consumed' WHERE id=1")
    def apply(self):
        view=self.inputs.view();account=view['account']
        if not account or account[0]!=view['generation'] or (account[0],account[2])==self.applied:return False
        if self.clock()-account[2]>ACCOUNT_TTL:return False
        generation,data,at=account
        was_ready=self.journal.ready
        try:
            self.journal.recover_pending(self.broker,data[0],time.time())
            proofs=self.missing_verifier.proofs(self.journal,data[0]) if self.missing_verifier else None
            self.journal.reconcile(*data,missing_proofs=proofs)
        except Exception:
            self.inputs.invalidate('Account reconciliation incomplete; prepared orders blocked');self.plans={};self.sell=None
            raise
        self.applied=(generation,at)
        self.inputs.confirm_account(generation,at)
        if not was_ready:
            self.journal.log('Account reconciliation completed successfully; historical records verified')
        with self.journal.db:
            for row in self.journal.db.execute("SELECT r.client_id,r.original_cents,o.quantity,o.filled,o.status FROM entry_reservations r JOIN orders o USING(client_id) WHERE r.status='pending_reconciliation'").fetchall():
                terminal=row['status'] in ('executed','canceled','rejected','expired')
                remaining=0 if terminal else int((D(row['original_cents'])*(D(row['quantity'])-D(row['filled']))/D(row['quantity'])).to_integral_value(rounding=ROUND_CEILING))
                self.journal.db.execute('UPDATE entry_reservations SET cents=?,status=? WHERE client_id=?',
                    (remaining,'reconciled' if terminal else 'pending_reconciliation',row['client_id']))
        # A cancellation is retried only after a newer authoritative order read.
        # While acknowledgment is uncertain, do not invalidate every quote tick.
        active=self.journal.active()
        with self.journal.db:
            self.journal.db.execute("DELETE FROM prepared_cancellations WHERE client_id IN (SELECT client_id FROM orders WHERE status IN ('executed','canceled','rejected','expired'))")
            if active and active['status']=='resting':
                self.journal.db.execute('DELETE FROM prepared_cancellations WHERE client_id=? AND after<?',(active['client_id'],at))
        return True
    def build(self,market):
        view=self.inputs.view()
        if self.revision==view['revision']:return False
        self.revision=view['revision'];self.plans={};self.sell=None
        if not market:self.reason='No active market';return False
        ticker=market['ticker'];self.reason=self.inputs.fresh(view,ticker)
        held=self.journal.position();active=self.journal.active()
        if active:self.reason='Outstanding order reserves buying power; awaiting reconciliation';return False
        verified=view['market'][0] if view['market'] else None
        if held:
            (t,side),quantity=next(iter(held.items()))
            self.reason=self.inputs.fresh(view,t,selling=True)
            if not self.reason and verified and verified['status'] in ('open','active'):
                self.sell=make_plan(view['generation'],verified,'sell',side,D(policy().sell_cents)/100,quantity,D(0))
        elif not self.reason and self.journal.ready:
            if verified.get('status') not in ('open','active'):self.reason='Market not active';return False
            prior=self.journal.db.execute("SELECT side FROM orders WHERE ticker=? AND action='buy' AND counted=1 ORDER BY rowid DESC LIMIT 1",(ticker,)).fetchone()
            self.previous_side=prior[0] if prior else None
            reserve=max(view['fees'][0][1],self.fee_floor);cash=view['cash'][2]
            for side in ('yes','no'):
                for cents in range(policy().buy_min_cents,100):
                    price=D(cents)/100
                    quantity=affordable(cash,price,reserve)
                    if quantity:self.plans[(side,price)]=make_plan(view['generation'],verified,'buy',side,price,quantity,reserve,view['fees'][0][1])
            if not self.plans:self.reason='Available cash cannot fund a whole-contract target';return False
        if self.plans or self.sell:
            ages=[ACCOUNT_TTL-(self.clock()-view['confirmed'][1]),MARKET_TTL-(self.clock()-view['market'][1])]
            if self.plans:ages.extend((CASH_TTL-(self.clock()-view['cash'][3]),FEES_TTL-(self.clock()-view['fees'][1]),MARKET_TTL-(self.clock()-view['exchange'][1])))
            payload={'prepared_at':time.time(),'expires_at':time.time()+max(0,min(ages)),
                     'cash_cents':view['cash'][2] if view['cash'] else None,'plans':[
                {'action':p.action,'side':p.side,'price':str(p.price),'quantity':str(p.quantity),
                 'reserve_cents':p.reserve_cents,'verified_fee':str(p.verified_fee) if p.verified_fee is not None else None,'payload':dict(p.payload)} for p in list(self.plans.values())+([self.sell] if self.sell else [])]}
            with self.journal.db:
                self.journal.db.execute("INSERT INTO prepared_entry VALUES(1,?,'ready',?) ON CONFLICT(id) DO UPDATE SET generation=excluded.generation,status=excluded.status,payload=excluded.payload",
                                        (view['generation'],json.dumps(payload)))
            self.reason='Ready: exact quantities and fee reserves prepared'
            return True
        return False
    def execute(self,market,now=None):
        # Critical path: only local guards, stored plan lookup, durable intent and POST.
        from .eligibility import eligible_sides,ordered_candidates
        from .strategy import entry_open,ENTRY_CUTOFF_SECONDS
        now=time.time() if now is None else now
        held=self.journal.position();active=self.journal.active()
        if held or (active and active['action']=='buy'):
            (ticker,side),quantity=next(iter(held.items())) if held else ((active['ticker'],active['side']),D(0))
            prices=self.feed.quote(ticker);bid=prices.get(side+'_bid') if prices else None
            stop=self.journal.latch_stop(ticker,side,bid)
            if active and active['action']=='buy' and (stop or not self.journal.running or not entry_open(active['cutoff'],now)):
                if not active['exchange_id']:
                    return {'status':'Awaiting uncertain buy reconciliation before cancellation','ticker':ticker}
                if self.journal.db.execute('SELECT 1 FROM prepared_cancellations WHERE client_id=?',(active['client_id'],)).fetchone():
                    return {'status':'Awaiting confirmed residual buy cancellation','ticker':ticker}
                if getattr(self.journal,'before_submit',None):self.journal.before_submit()
                self.inputs.invalidate('Residual buy cancellation; refresh required')
                with self.journal.db:self.journal.db.execute('INSERT INTO prepared_cancellations VALUES(?,?)',(active['client_id'],self.clock()))
                try:self.journal.cancel(self.broker)
                finally:
                    with self.journal.db:self.journal.db.execute('UPDATE prepared_cancellations SET after=? WHERE client_id=?',(self.clock(),active['client_id']))
                return {'status':'Canceling residual buy before further execution','ticker':ticker}
            if stop and not active and self.sell and self.sell.quantity==quantity:
                return self._send(self.sell,now)
            return {'status':'Protective exit awaiting prepared account state' if stop else 'Holding to settlement','ticker':ticker}
        if active:return {'status':'Maintaining existing order','ticker':active['ticker']}
        if not self.journal.running:return {'status':'Entries paused; protective exits remain enabled'}
        if not self.journal.ready:
            return {'status':'Account reconciliation incomplete; new buys blocked','ticker':market.get('ticker') if market else None}
        if not market:return {'status':'No active market'}
        ticker=market['ticker'];close=cutoff(market)
        verified=self.inputs.view()['market']
        if verified and (market_scope(market)!=market_scope(verified[0]) or market.get('event_ticker')!=verified[0].get('event_ticker')):
            mismatch=(ticker,market_scope(market)['exchange_index'],market.get('event_ticker'),
                      verified[0]['ticker'],market_scope(verified[0])['exchange_index'],verified[0].get('event_ticker'))
            if mismatch!=self.scope_mismatch:
                self.inputs.invalidate('Market discovery scope changed; asynchronous refresh required')
                self.scope_mismatch=mismatch
            return {'status':'Market scope changed; refreshing prepared state','ticker':ticker}
        self.scope_mismatch=None
        if not entry_open(close,now):
            reason='New purchases blocked during final 30 seconds' if 0<close-now<=ENTRY_CUTOFF_SECONDS else 'Waiting for final four-minute entry window'
            return {'status':reason,'ticker':ticker,'cutoff':close}
        prices=self.feed.quote(ticker)
        if prices is None:return {'status':'Waiting for fresh sequenced prices','ticker':ticker,'cutoff':close}
        for side in ordered_candidates(prices,ticker,self.previous_side):
            ask=prices[side+'_ask']
            # Select a stored cent-limit variant; never resize on the quote path.
            plan=next((self.plans.get((side,D(cents)/100)) for cents in range(policy().buy_min_cents,100) if D(cents)/100>=ask),None)
            if plan and plan.ticker==ticker and close>=plan.cutoff:
                return self._send(plan,now)
        return {'status':self.reason if eligible_sides(prices) else f'No qualifying {policy().buy_min_cents}–99c executable ask','ticker':ticker,'cutoff':close}
    def _send(self,plan,now):
        if self.clock()<getattr(self.broker,'next_write',0):return {'status':'Exchange write cooldown; prepared orders retained until fresh','ticker':plan.ticker}
        if now>=plan.cutoff:return {'status':'Official cutoff reached','ticker':plan.ticker}
        # A queued Pause is checked before a lease can be consumed and again by Journal.
        if getattr(self.journal,'before_submit',None):self.journal.before_submit()
        if plan.action=='buy' and not self.journal.running:return {'status':'Entries paused','ticker':plan.ticker}
        try:self.claim_generation,self.claim_deadline=self.inputs.claim(plan)
        except RuntimeError as exc:return {'status':str(exc),'ticker':plan.ticker}
        self.claimed=plan;self.plans={};self.sell=None
        try:
            self.journal.submit(self.broker,plan.ticker,plan.action,plan.side,plan.price,plan.quantity,plan.cutoff,now,prepared_payload=plan.payload)
        finally:self.claimed=None
        return {'status':('Buy' if plan.action=='buy' else 'Protective sell')+' submitted; awaiting confirmed fills',
                'ticker':plan.ticker,'side':plan.side,'limit':str(plan.price),'quantity':str(plan.quantity),'cutoff':plan.cutoff}
    def status(self,market):
        view=self.inputs.view();ticker=market['ticker'] if market else None
        reason=self.inputs.fresh(view,ticker) if ticker else 'No active market'
        duration=view['read_times'].get('account_seconds')
        if reason=='Account reconciliation is stale or uncertain' and duration is not None and duration>ACCOUNT_TTL:
            reason=f'Account refresh too slow: {duration:.1f}s (maximum {ACCOUNT_TTL:g}s); orders blocked'
        reserved=self.journal.db.execute("SELECT coalesce(sum(cents),0) FROM entry_reservations WHERE status='pending_reconciliation'").fetchone()[0]
        return {'ready':not reason and bool(self.plans or self.sell),'reason':reason or self.reason,'generation':view['generation'],
                'cash_cents':view['cash'][2] if view['cash'] else None,'reserved_cents':reserved,
                'refresh_reason':view['reason'],'refresh_timings':view['read_times'],'account_present':view['account'] is not None,'account_confirmed':view['confirmed'] is not None,
                'quantities':{side:{str(p):str(plan.quantity) for (s,p),plan in self.plans.items() if s==side} for side in ('yes','no')}}
