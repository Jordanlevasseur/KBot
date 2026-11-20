# Delivery status

Trade-history profit column — 2026-11-13: net realized profit on BUY fills,
including actual fees and confirmed settlement payouts, with Open/partial markers.
A read-only desktop background reader updates the display; trading-worker modules
are unchanged. **346 tests passed**, plus native asynchronous profit rendering.
No bot/gateway restart, account connection, trades or journal writes were performed.
See [TRADE_PROFIT.md](TRADE_PROFIT.md).


Persistent connection/status and idle-lock update — 2026-11-13: account-state
renewal no longer appears as an account disconnect; recovery is automatic, idle
stream readers close promptly and empty end-market snapshots clear safely. The
app inhibits automatic screen locking while open. **337 tests passed**, plus
native dashboard, both-side mocked trading/protective recovery and real Cinnamon
inhibitor checks. Financial/security modules and execution-call structures remain
unchanged. No gateway restart, live order, cancellation or trading activation.
See [PERSISTENT_CONNECTION.md](PERSISTENT_CONNECTION.md).


Missed eligible NO buy repaired — 2026-11-13: saved quotes confirmed 98.8–98.9c
with entries enabled, but stale account preparation prevented submission. Fixed
gateway CPU/TLS overhead, trusted GET timestamps, independent collection refresh,
repeated rollover invalidation and oversized account-wide fill reads. Confirmed
closed-order fills are retained; current/held/unresolved fills and global exposure
checks stay fresh. **319 offline tests passed**, both-side mock trading/protective
pipelines passed, and a GET-only account check stayed READY after warm-up. No
orders/cancellations or trading activation were performed. See
[MISSED_BUY_REPAIR.md](MISSED_BUY_REPAIR.md) for evidence and limits.


Automatic sleep protection — 2026-11-13: desktop startup acquires a logind
sleep/lid block inhibitor before starting the worker; it is released on close.
Screen locking is not inhibited. Lost protection pauses entries without stopping
protective monitoring. **293 offline tests passed, 0 failed** (25.503 s), plus
the native desktop test and real local logind acquire/list/release verification.
No actual suspend/lock, real account access, trading activation or global power
setting change was performed. See [desktop power behavior](DESKTOP.md#screen-locking-and-laptop-sleep).


Standard prepared execution — 2026-11-13: all demo/live trading launches now
use the precomputed-order path, with no checkbox or command-line opt-in.
Simulation, monitoring, secure gateway isolation, explicit live connection and
Start controls remain. **285 offline tests passed**, plus the native desktop,
default-path mock demo and both-side protective restart/recovery pipelines.
No real-money order or live account request was performed. See
[PREPARED_EXECUTION.md](PREPARED_EXECUTION.md) for timing boundaries and risks.

Independent decision cadence — 2026-11-13: a separate 250 ms strategy loop
continues during slow execution calls, with a readonly committed journal view,
executor wake-up signals and visible actual cadence/DELAYED status. Optional
three-second desktop post-cycle waiting is removed. Required rate limits and
fresh order-validation checks remain; live submission latency is not guaranteed.
**261 offline tests passed**, plus both-side 14-second-stall cadence tests
(56 checks each, max 250.882 ms), isolated protective recovery and native GUI.
No live account or financial action was performed. See [DECISION_CADENCE.md](DECISION_CADENCE.md).


Faster armed-entry update — 2026-11-13: shared ordinary read pacing is 200 ms,
with an independent 1.05-second balance bucket and shared retry cooldown.
All fresh reconciliation/price/cash/fee/cutoff checks remain. Four-sample mocked
receipt-to-broker latency improved **4338.48 -> 1431.47 ms (67.01% less delay)**.
**248 offline tests passed, 0 failed**; isolated demo, both protective recovery
pipelines and hidden GUI passed. No live account/orders or gateway restart.
See [ENTRY_LATENCY.md](ENTRY_LATENCY.md) for measurements and limits.


Current whole-contract sizing update — 2026-11-13: purchases target
**floor(available Bitcoin cash in dollars / 2) whole contracts**, independent of
eligible price. $26.87 targets 13; $53.40 targets 26; $100 targets 50. Fees are
reserved separately and total market cash remains the cap. This supersedes the
previous half-cash dollar budget. **242 offline tests passed, 0 failed**, including
100,001 balances at all five limits; isolated mocked demo and hidden GUI passed.
No live account/order actions were performed. See [CONTRACT_SIZING.md](CONTRACT_SIZING.md).


Current cash fix: live rejections identified insufficient market-exchange cash.
Buys now read primary-account cash on the chosen market exchange rather than
aggregate cash. Live read-only checks verified corrected sizing; no real orders
were submitted. **235 offline tests passed**, plus isolated demo and native GUI.
The app and protected connection service were updated after the user confirmed
paused/flat/closed. See [CASH_SCOPE_FIX.md](CASH_SCOPE_FIX.md).

Latest rejected-entry diagnosis: NO 97c was submitted at 9:28:20 PM Pacific
for the 9:30 market and returned HTTP 400. The old detailed rejection reason
was not retained. Safe public error codes are now displayed/audited; uncertain
creates are read-reconciled without replay because closed client IDs can be reused.
**228 offline tests passed** and both isolated protective demos passed with zero
recovery POSTs. See [EXCHANGE_REJECTION_REPORT.md](EXCHANGE_REJECTION_REPORT.md).

Latest protective verification: **221 offline tests passed, 0 failed**; both-side
isolated protective demos, 300 seeded sequences/30,000 randomized steps and
read-only local history audit completed. Fixed lost crossings when arming and
stopped zero-fill buys remaining open, plus detailed exit lifecycle diagnostics.
Local records contain four settled buys and $0.1654 net profit; no live protective
sell is recorded or verified. See [PROTECTIVE_SELL_REPORT.md](PROTECTIVE_SELL_REPORT.md).

Latest performance update: **176 offline tests**, mocked demo, hidden GUI,
credential/isolation preflight and disposable firewall verification passed.
Measured receipt-to-order handoff improved from 4.91 to 4.33 seconds; hidden
dashboard latency from 424 to 32 ms. Larger generated-history reconciliation
improved from 2.07 seconds to 129 ms. Quiet worker CPU is about 0.66% of one
core, using about 32 MiB RAM. See [PERFORMANCE.md](PERFORMANCE.md) for the
measurement conditions, small CPU/timing variations and remaining limits.

Current entry window: **final four minutes (240 seconds)** for YES and NO,
including the independent gateway's write guard. 168 offline tests passed;
mocked demo integration bought with 227.61 seconds remaining. Other trading rules
are preserved. Live trading remains opt-in and was not used for these tests.

Latest: the 4:30 PM missed-NO audit showed no submission, unavailable executable
data at decision points and one old type-only RuntimeError. The exact displayed
98c instant was not recorded. Display/execution now share one cache; flat streams
avoid irrelevant stop-database work, and timestamped eligibility/stage/failure
evidence is persisted. Decimal 98.4c asks use 99c limits on either side. See
[NO_MISS_REPORT.md](NO_MISS_REPORT.md) and VALIDATION.md for findings and tests.

Current refresh repair: independent event-driven YES/NO display updates, autonomous
market discovery/rollover, separate account and price health, verified upstream
heartbeats, safe reconnects, stronger Pause and ambiguous-order recovery. All 151
offline tests, hidden desktop integration, and the real isolated demo-mode pipeline
with a mocked exchange passed. Official demo acceptance remains unverified because
demo credentials/gateway are absent. No real-money testing or automatic live start
was performed. See [REPAIR_REPORT.md](REPAIR_REPORT.md) for current findings and limits.
The earlier dated development stages below are retained as history.

Current installed entry update: qualifying price moves within 95c–99c now reprice
and resize instead of being skipped. Portfolio pagination and stop persistence
overhead were reduced, and the dashboard exposes actual YES/NO buy asks plus
recorded entry/skip decisions. 130 offline tests and hidden desktop integration
passed; read-only live stream/account diagnostics also passed. Existing trading
history was preserved. No additional live order was
submitted to test this update. See VALIDATION.md and ONLINE.md for current evidence
and limits. Earlier development-stage statements below are historical.

Implemented and offline-tested:

- Existing transactional strategy simulator, persistent stops, two-buy sequencing,
  partial fills, settlement, restart protection, and terminal controls.
- Official read-only API connector: Ed25519/RSA-PSS, signing without query strings,
  bounded retries, rate-limit backoff, collection pagination, fixed-point books,
  Bitcoin-series discovery, and official market cutoff parsing.
- Local credential configuration with private files, no repository credentials,
  no private-key logging, environment checks, and public-key-bound account caches.
- Account monitor for balances, positions, resting orders, fills, settlements, market
  prices, stale-cache handling, local logs, and explicit connection authorization.
- Network-isolated monitor, fixed-official-host TLS Unix gateway, generated nft UID
  destination policy, kernel-policy verification, boot restoration, OS bind-denial
  preflight, dedicated service-account provisioning, and administrator setup guide.

No host installation or account connection has occurred. Credentials are not
configured. Provisioning scripts and systemd deployment require administrator review
and installation. Actual Kalshi schema/account acceptance must be tested read-only
against demo first. No real money is required or authorized for testing.

The user subsequently approved price improvement below 95c. Only submitted buy
limits must be one of 95c–99c. That supersedes the earlier strict fill-floor policy.

The execution candidate now implements journaled V2 buy/sell/cancel requests,
partial-fill/order/position reconciliation, same-ID submission recovery, two-buy
sequencing, persistent stop latching, settlement records, and explicit gateway and
client activation gates. See [trading setup and acceptance limits](TRADING.md).

Actual demo acceptance is still outstanding because no demo account credentials
have been configured. The OS service deployment is also unexecuted. The external
fee reserve must be independently verified for current series/event fees. This is
a testable trading candidate, not a production-validated real-money service. No
account connection or real-money trade has occurred.

Latest validation: 88 offline tests passed. Known additional production gap: the
execution candidate currently polls REST; it does not continuously stream the
order book and may miss brief stop crossings. Do not describe it as a complete
continuous-monitoring real-money bot or turn it on before resolving that limitation
and completing demo/fee/deployment acceptance checks.

## Desktop application

A native Tk desktop app now provides a simulation dashboard, Start/Pause controls,
trade history, activity logs, local credential setup, offline credential checks,
explicit demo/live read-only connections, and an optional demo-trading candidate.
It defaults to offline simulation with paused entries and exposes no production
order-submission mode. The background worker remains namespace-isolated and never
receives a display socket or home directory. User-owned application snapshots and
menu/desktop shortcuts are installed by `deploy/install-desktop.py`; user state and
credentials are separate from installed code and from the terminal simulator.
See `docs/DESKTOP.md`. Packaging does not resolve the candidate's production gaps.

## Version 0.4 connection update (supersedes earlier locked/polling notes)

Live mode now has separate Connect and real-money Start confirmations. The gateway
is installed and verified through the real worker sandbox without a key. Streamed
sequenced books replace polling as the entry/stop price source; REST still reconciles
account state. Entry fees use automatic official series/event metadata. The timer
uses monotonic elapsed time and defaults to 1×. The active production gateway uses
systemd socket activation and kernel bind/listen filtering plus its dedicated UID
firewall. No account authentication or real-money order has occurred during this
update. See ONLINE.md for current steps and remaining account-specific validation.

## November 14: uncapped sequential purchases

The user removed the per-market two-purchase cap. Demo, simulation, and live execution now allow further eligible entries after each complete sale and reconciliation. A transactional startup migration removes the old SQLite CHECK constraint while preserving counts, stops, orders, fills, and history. Existing journals are migrated only by their next normal owner startup; no running production journal was edited for this change. Existing entry window, fee reserves, price validation, single-position rule, and protective exits are preserved. Earlier reports describing two-buy limits document the prior rule.

## November 14: final 30-second purchase block

Both sides now require 30 < seconds remaining <= 240. New buy orders expire 30 seconds early; protective sells retain the official close. The purchase cap remains removed. 370 offline tests and native desktop/mocked demo integration passed. See CLOSING_PURCHASE_CUTOFF.md.
