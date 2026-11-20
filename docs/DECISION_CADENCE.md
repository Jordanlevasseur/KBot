# Independent strategy decisions — November 13, 2026

The trading worker now evaluates strategy every **250 ms** (target 4 checks/s).
This is independent of network-bound execution, market discovery and dashboard
rendering. The optional **three-second desktop post-cycle pause is removed**;
the executor resumes fresh checks immediately and processes controls without a
post-cycle sleep. API pacing, server backoff, reconnection and reconciliation
remain. The artificial 14-second delay described below exists only in the test.

## Evaluation and execution

The new `decisions.py` loop uses the canonical validated book and a separate
read-only SQLite connection (`mode=ro`, `query_only=ON`). Each short consistent
read transaction checks committed positions, active orders, remaining quantities,
persistent stops, market buy counts and the account snapshot timestamp, and is
closed before sleeping. Both YES and NO use identical price rules. It evaluates
entry windows, cutoff/rollover, stale prices/account context, pending or opposing
positions, two-buy sequencing and all-position protective exits, including gaps
below 86c and latched stops through rebounds.

Eligible candidates and protective recommendations notify the existing execution
worker through its coalesced wake-up pipe. They never authorize an order. Order
submission still belongs to one worker and passes the existing full fresh
reconciliation, market cash, fee, exchange-status, cutoff, final-price, explicit
Start and durable-order-intent checks. No cached decision bypasses those checks.
Sizing and cash scope are unchanged. Existing sell orders remain in place, and
unknown submissions are reconciled without replay.

The requested faster assessment frequency is implemented. **Order submission
can still wait for exchange responses and required validation.** This update
does not establish a 500 ms order-submission guarantee or remove the previously
observed slow live REST checks. The older 1.43 s submission result was a mocked
benchmark. Kalshi's [balance endpoint limit](https://docs.kalshi.com/api-reference/portfolio/get-balance)
allows one request per second on Basic/Advanced; a full fresh-balance-validated
order cycle cannot be promised twice per second. Actual Internet and exchange
latency remain outside the local scheduler's control.

## Dashboard and diagnostics

The existing dashboard adds a strategy-check line with a count, decision/reason
and actual measured interval. The separate execution stage continues to show
account/fee/price checks and submission status. A check older than 500 ms is
marked **DELAYED**; the display does not present an old interval as current.
Minimizing the window does not suspend the worker.

Timestamped `strategy_decision` diagnostics contain the assessment, counters,
last/max interval and explicit `execution_authorized: false`. Changed assessments
are recorded immediately; unchanged states receive a one-second counter summary
rather than four duplicate database writes each second. The decision engine
still evaluates every quarter second. Existing diagnostic bounds are preserved.

## Measured verification

The isolated demo integration used generated credentials, temporary journals and
the real Unix gateway/worker with synthetic exchange responses. Each scenario
held one mock positions request for 14 seconds (below the existing 15-second
HTTP timeout), changed prices out of range and back during the stall, and
required fresh validation before submitting the eventual mock buy.

| During blocked account request | YES | NO |
|---|---:|---:|
| Artificial request duration | 14.000 s | 14.000 s |
| Independent strategy checks | 56 | 56 |
| Measured checks/second | 3.997 | 3.996 |
| Median interval | 250.111 ms | 250.238 ms |
| Largest interval | 250.595 ms | 250.882 ms |
| Orders before required checks completed | 0 | 0 |
| Validated buys after checks | 1 | 1 |
| Worker CPU, one core, during stall | 0.839% | 1.587% |
| Worker resident RAM | 33.410 MiB | 33.441 MiB |

Both scenarios reevaluated changing prices while execution was blocked, rejected
nonqualifying prices, and submitted no duplicate order. Short resource samples
are test measurements, not normal-live CPU or hard real-time guarantees. CPU
starvation, suspension or OS scheduling can delay any Python process; the cadence
warning exposes missed targets. No busy-spin loop or extra network polling was
added to the independent strategy evaluator.

- **261 offline tests passed, 0 failed** in 24.532 s. Nine new assessment tests
  cover cadence during slow I/O, readonly views, both sides/decimal asks, stale
  data/account views, market windows, pending/opposing positions, buy limits,
  gap triggers and all-share protection. Existing Start/Pause/Quit coverage now
  also asserts no positive post-cycle control wait; its focused rerun passed.
- Final isolated mock demo passed decimal NO, two-buy sequencing, protective
  sell, reconnection, stale rejection, settlement and rollover. First buy was
  230.38 s before cutoff; 152 market events arrived.
- Both isolated protective pipelines passed after removing the optional pause:
  all 50 held shares sold in each synthetic scenario, partial fills and restarts
  preserved, zero uncertain-order replay POSTs and one protective order per side.
- Hidden native GUI checks passed, including cadence and DELAYED warning; GUI
  RAM 39.69 MiB. All application Python sources parsed successfully.

No production account requests, financial transactions, cancellations, live
activation, credential changes, gateway restart or production journal writes
were performed for development/testing. Reopen the installed app to load the
code; live connection and Start remain explicit user actions.

Raw results: [cadence](decision-cadence-results.json),
[protective recovery](decision-cadence-protective.json).
