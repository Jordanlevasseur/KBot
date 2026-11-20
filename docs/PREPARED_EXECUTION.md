# Precalculated entry execution — November 13, 2026

The [missed-buy repair](MISSED_BUY_REPAIR.md) supersedes the original all-history
refresh implementation below: current risk is freshly read and confirmed closed
fills are retained. Timestamp and CPU/gateway fixes are documented there.

**Standard execution for all desktop demo/live trading sessions.** There is no
opt-in switch. Simulation and read-only monitoring retain their existing paths;
production connection and Start authorization remain explicit. Updating this
code does not start trading, connect an account or change credentials.

## Waiting state and background preparation

The new `prepared.py` engine and `prepared_runtime.py` runtime maintain a versioned
prepared state while quotes are outside the eligible range and while entries are
paused. Three bounded GET workers asynchronously read account orders, fills,
positions, settlements, market-scoped primary-account cash, market/exchange status
and official series/event fees. They share the existing rate gate with discovery.
The worker remains namespace-isolated, uses the existing credential store and
Unix gateway, and makes no new kinds of external request. The gateway, host
allowlist and firewall were not modified.

Completed account collections are fully validated by the existing journal
reconciler outside the valid quote-to-order path. Missing/inconsistent fills,
positions, unknown orders and ambiguous submissions prevent arming. A single
journal/order owner is retained. Account GETs may overlap, but an inconsistent
projection fails reconciliation and is never used to authorize a cached order.

Every usable balance prepares all ten YES/NO and 95/96/97/98/99c limit variants.
Each has a preassigned single-use client ID, exact whole quantity, fee reserve,
maximum total cash commitment, official expiration and complete order payload.
Quantity is `floor(available_market_cash_cents / 200)`, capped when necessary by
fee-inclusive affordability. Thus $6 prepares three contracts; the existing
market-specific cash scope remains unchanged. There is no combined-cash fallback
or movement of money between exchange balances.

SQLite `prepared_entry` stores the templates, quantities, costs, fee evidence,
generation and preparation/expiration timestamps. `entry_reservations` tracks
whole-order and remaining commitments after partial fills. It is a local cash
commitment record, not an exchange escrow. Generation changes invalidate earlier
plans on observed balance, order, fill, position or market changes. Fee changes
require updated entry templates. Cached authorization is always disarmed after
restart; pending order IDs and reservations survive for reconciliation.

## Critical execution path

A ready qualifying quote is processed before newly arrived background application
or preparation work. The path reads the stored state, performs local version,
freshness, available-reserve, official-window, market/scope, position, buy-count,
price and permission checks, selects the already-built side/limit variant, and
claims it atomically. The existing journal transaction durably saves the pending
order and cash reservation together before transmission. It then sends through
the existing validated gateway. No balance GET, portfolio GET, full reconciliation
or position sizing is started on that path.

Current authentication signing and durable SQLite persistence remain on the send
path for security and crash safety. A fixed authorization deadline is carried
through persistence/signing and checked again immediately before the HTTP write.
The final price, fee, generation and queued-control guards can abort a locally
recorded intent before any send. Such a local rejection is not a filled purchase.
Acknowledgments are recorded; subsequent background reads reconcile actual fills,
remaining quantities, cash and settlement. Unknown creates are never replayed.

The event-driven execution loop coalesces busy book traffic with a 10 ms local
CPU ceiling. Required read/write rate limits, server backoff and order validation
remain. The existing independent 250 ms strategy assessment is preserved.

## Freshness limits

All deadlines use monotonic time. Age starts when a request begins, including
pacing, retries, pagination and processing. The account timestamp is the earliest
read start among all four complete collections. A late response therefore cannot
make old data appear newly verified.

| Prepared dependency | Maximum age |
|---|---:|
| Market-specific available cash | 2.5 s |
| Complete reconciled account cohort | 5 s |
| Verified market and exchange trading status | 5 s |
| Official series/event fee reserve | 30 s |

The canonical stream retains its existing sequencing, timestamp, persistence and
five-second verified-liveness checks. Expired or uncertain preparation blocks a
new buy and continues background refresh; it does not cause critical-path GETs.
Very slow responses may prevent preparation from becoming ready. This is an
intentional fail-closed result, not a promise to trade every displayed price.

## Preserved trading and protection

- Existing allowed prices and decimal asks are unchanged: e.g. 98.4c selects the
  prepared 99c limit. Submitted limits never exceed 99c; cheaper fills remain valid.
- The final four-minute entry window, combined two-purchase cap, complete first
  sale requirement and single global exposure/order guards remain in force.
- Protective exits precede new entries and cover the entire remaining held side.
  Bids at or below 86c, including gaps below it, latch protection. The sell limit
  stays 86c and may remain unfilled without buyers at that price or higher.
- Partial buys are reconciled/canceled before protective selling. Unknown buy
  acknowledgments wait for reconciliation rather than repeated cancellation or
  generation churn. Durable cancel markers permit retry only after newer account
  evidence. This prevents fast-loop retries from starving background recovery.
- Cash/fee refresh uncertainty blocks entries without discarding an otherwise
  valid protective account/market lease. Pause blocks entries while protection
  continues; Quit prevents new transmission.

## Performance: standard default path

24 fresh isolated demo sessions alternated YES and NO, with generated RSA-2048
PSS/SHA256 keys, fake $6 cash and a synthetic exchange returning responses after
20 ms. Each session warmed the exact cached quantity before introducing its
qualifying quote. The real namespace-isolated worker, durable journal, signer,
Unix transport and gateway request validation were exercised.

**Boundary: canonical qualifying book receipt to the gateway receiving and
validating the complete POST.** This includes local scheduling, final guards,
persistence, signing and Unix transport. It excludes Internet/TLS transit to
Kalshi, exchange acceptance, queueing and fills. No live timing claim is made.

| Metric | Final result |
|---|---:|
| Median receipt to gateway | 15.72 ms |
| 95th percentile (linear interpolation) | 24.02 ms |
| Worst of 24 samples | 25.17 ms |
| Critical-path balance/other GET calls | 0 |
| Critical-path sizing calls | 0 |
| Critical-path reconciliations | 0 |
| Median initial preparation time | 3.296 s |
| Initial preparation range | 2.706–4.178 s |
| Peak worker resident RAM in samples | 33.840 MiB |

Every sample submitted the precalculated three contracts. Instrumentation fails
the test if a main-thread GET, sizing call or reconciliation starts between the
qualifying receipt and the send. Background readers continue independently.
The dataset uses a small generated history and warm state; it does not establish
latency for a large production history, cold startup, an unready lease, live
traffic or an overloaded/suspended laptop. Gateway outbound connection/TLS setup
is unchanged. RSA key sizes other than 2048 bits may have different signing cost.

## Tests and fixes

**285 offline tests passed, 0 failed**, standard-path full run 23.918 s. The 24 new
prepared-state tests cover precomputed quantities, every cent-limit/side and
fractional ask, cash changes, stale dependencies, full request/pagination age,
atomic claims/reservations, partial fills and reserved remainder, max two buys,
complete-sale sequencing, pending/opposing exposure, canceled/rejected/uncertain
orders, final price/fee/expiry/Pause changes and disarmed restart caches.

Final prepared-mode mock demo passed YES buy, protected full sale before NO buy,
98.4c -> 99c, two-buy cap, reconnect/stale rejection, settlement and rollover.
First buy occurred 230.77 s before cutoff. Both prepared protective pipelines
passed lost acknowledgments, read-only uncertain recovery (zero replay POSTs),
partial fills, gateway restart, paused worker restart, below-limit nonfills and
confirmed full exits (50/50 synthetic contracts per side). Hidden GUI checks
passed, including the readiness/reserved-funds display; GUI RAM 39.78 MiB.

Integration found and fixed a startup resource issue: additional readers exceeded
virtual address reservations with default allocator arenas/stacks. Prepared mode
uses two allocator arenas and 1 MiB stacks, keeping the existing 500 MiB address
limit. Partial thread startup cleanup is safe. The synthetic exchange oracle was
corrected to clear its held-side marker after complete liquidation. A restart
test now requires a genuinely new connection, rather than accepting an older
queued connected event. No security check was relaxed to make a test pass.

## Files and use

New: `kalshi_bot/prepared.py`, `kalshi_bot/prepared_runtime.py`,
`tests/test_prepared.py`, `tests/prepared_pipeline.py`.
Existing changes: journal optional prepared payload/reservation hooks, broker
pre-wire guard, standard worker/launcher dispatch, GUI readiness fields,
thread-start cleanup, and existing demo/protective/desktop test harnesses.
Simulation and read-only paths are unchanged. Every normal demo/live trading
launch uses prepared execution automatically. The synchronous implementation is
retained only as an internal preflight/regression reference, with no user mode
switch. The dashboard displays prepared-order readiness and reserved funds.
An updated installed app must be reopened to load this code. Live account
connection and Start confirmations remain separate; neither is automatic.

Reproduce with generated data only, from the project directory:

```sh
TMPDIR=/dev/shm python3 -B -m unittest discover -s tests -p 'test_*.py'
TMPDIR=/dev/shm python3 -B tests/prepared_pipeline.py --samples 24 --key-type rsa --output /tmp/prepared-latency.json
TMPDIR=/dev/shm python3 -B tests/demo_pipeline.py --no-ask .984
TMPDIR=/dev/shm python3 -B tests/protective_pipeline.py --output /tmp/prepared-protective.json
TMPDIR=/dev/shm python3 -B tests/desktop_integration.py
```

Raw measurements: [standard-path latency](standard-prepared-latency.json),
[standard-path protective recovery](standard-protective-results.json).

Cached state cannot lock money against another device/account client or guarantee
instant notification of an external change. Material observed changes invalidate
the plan; bounded age, local reservation and exchange rejection/reconciliation
remain the safeguards between observations. Official demo/live exchange acceptance
for this new path is unverified. Actual network and fill performance require a
separately authorized controlled check; no real protective sale is claimed from
these synthetic tests. Current official references:
[balance scope and limits](https://docs.kalshi.com/api-reference/portfolio/get-balance),
[rate limits](https://docs.kalshi.com/getting_started/rate_limits),
[V2 order API](https://docs.kalshi.com/api-reference/orders/create-order-v2).
