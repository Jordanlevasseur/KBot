# Validation results

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


## Market-specific cash correction — 2026-11-12

- New live logs show HTTP 400 `insufficient_balance` on both sides. Authenticated
  read-only checks confirmed combined account cash was larger than cash available
  on the Bitcoin market's exchange. No live order/cancellation/funding was sent.
- Buys now query primary-account cash scoped to the selected market, keep the
  existing 50% allocation/fee reserve and fail closed on missing/changed scope.
  The cash card explicitly distinguishes total account cash from Bitcoin cash.
- **235 offline tests passed, 0 failed.** Added both-side aggregate-vs-scoped-cash,
  zero cash, precise cash rounding, scope validation/change and gateway policy tests.
- Isolated mocked demo passed entry, protective sell, complete-sale second buy,
  decimal NO, two-buy cap, reconnection, staleness, settlement and rollover.
  Hidden native GUI cash-label/recovery tests passed; current RSS 39.61 MiB.
- User confirmed paused/flat/closed before the existing gateway was updated.
  Host/isolated health both advertise `market_balance_scope: 1` and 240 seconds.
- A read-only live sizing calculation at hypothetical 97c supports 3 contracts
  including official fee reserve, versus the previously rejected 13. No order
  was submitted and no claim of a real fill is made. See [CASH_SCOPE_FIX.md](CASH_SCOPE_FIX.md).


## Rejected NO entry / recovery correction — 2026-11-12

- Read-only evidence: a NO 97c buy intent at 9:28:20 PM Pacific was rejected with
  HTTP 400, no fills and no purchase count. The old detailed reason is unavailable.
- Fixed safe public error-code propagation and misleading pending-order messages.
  Unknown/free-text server bodies, credentials and account IDs remain excluded.
- Removed uncertain-create replays after verifying official client-ID protection
  lasts only while an order is open. Missing/lagging records now block writes;
  visible original orders reconcile normally. Definitive 429 retains backoff.
- **228 offline tests passed, 0 failed.** Both real isolated protective demo flows
  passed with synthetic exchange data and zero recovery POSTs. Closed-ID reuse,
  lost acknowledgments, partial fills and restarts are covered.
- No production journal edit, key access, account request, real trade, cancellation
  or gateway restart. This does not prove a fix for the old exchange rejection.
- See [EXCHANGE_REJECTION_REPORT.md](EXCHANGE_REJECTION_REPORT.md).


## Protective selling — 2026-11-12

- Original 176-test baseline passed before runtime edits. Final full suite:
  **221 passed, 0 failed**. Additional seed-offset protective runs passed 40/40
  twice. Across those runs: 300 distinct side/seed sequences, 30,000 steps,
  no oversell and no simultaneous active protective orders.
- Both YES and NO passed exact-86c triggering, 90/87/86.01c holds, gaps,
  rebounds, partial buys/sells, pending orders, late cutoff, API failures,
  rate-limit backoff and recovery. Real abrupt process death after synthetic
  acceptance preserved the pending ID and stop without resubmitting a sell.
- Comprehensive real isolated demo worker/temporary gateway/hidden UI tests
  passed for each side: same-ID lost-ack recovery, partial fill, gateway restart,
  worker restart paused, unfilled gap and completion only after fills/positions.
- Fixed arming/current-crossing persistence, same-side second-buy rearming and
  stopped zero-fill pending-buy cancellation/wakeup. Added explicit lifecycle
  diagnostics and accurate protective/recovery audit stages. Rules are unchanged.
- Native desktop integration and generated-key/isolation preflight passed.
  No production gateway restart, account API request, real order, production
  cancellation, credential edit or production journal mutation was performed.
- Local read-only audit: four executed/settled buys, matching fills/saved exchange
  projections, recorded net $0.165400 (rounded $0.17), zero protective sells.
  Latest saved snapshot: 7:50:28 PM Pacific, Nov 12. No current account request.
- See [PROTECTIVE_SELL_REPORT.md](PROTECTIVE_SELL_REPORT.md), its sanitized audit
  and synthetic integration artifacts for the complete scenario matrix and limits.


## ThinkPad 16 GB performance update — 2026-11-12

- Profiled the actual i7-2640M laptop with generated demo credentials and mocked
  exchange data before runtime edits. Implemented stream notifications, faster
  bounded display updates, one fresh sizing balance read, linear full-history
  reconciliation, additive SQL indexes and an 8 MiB bounded journal page cache.
- **176 offline tests passed.** Full mocked demo, hidden desktop integration,
  generated-key/isolation preflight and disposable kernel-firewall checks passed.
- Median mocked receipt-to-final-decision: 4,905 to 4,326 ms; decision-to-order
  broker handoff: 2.18 to 2.72 ms (no improvement claimed at that small scale).
  Hidden dashboard: 424 to 32 ms. Generated 2,000-order history: 2,068 to 129 ms.
- Quiet worker CPU: 0.69% to 0.66% of one core; resident memory about 32.20 MiB
  in both versions. Entry/startup CPU: 5.21% to 5.84%; entry peak RAM about
  31.96 MiB in both. These are short local tests, not Internet latency guarantees.
- Final GUI current/peak RSS: 39.68 MiB. Simulator peak RSS: 16.68 MiB.
  Corrected developer RAM counters to use Linux VmHWM, excluding the launcher
  process image's inherited rusage high-water mark. Worker limits are unchanged.
- Final-four-minute timing, exact allowed buy limits, fee-inclusive 50% sizing,
  two-buy sequencing, durable stops, reconciliation, authorization, rate limits
  and OS isolation remain in force. No real account, key or order was used.
- Installed snapshot byte checks and real local gateway health passed from host
  and isolated app, without keys or account requests. The gateway was not restarted.
- See [PERFORMANCE.md](PERFORMANCE.md) and its raw JSON/cProfile artifacts.


## Four-minute entry window — 2026-11-12

- User requested final-four-minute entries. The shared timing guard now enforces
  `0 < cutoff - now <= 240` across simulation, execution, pre-submit validation,
  gateway policy, dashboard eligibility and stream-driven wakeups.
- **168 offline tests passed.** Both sides reject 241 seconds, accept 240, 210
  and 181 seconds, and reject the official cutoff. Dashboard boundary checks pass.
- Mocked demo integration passed with the real isolated worker/local gateway.
  The first buy occurred with **227.61 seconds remaining**, proving execution
  during the newly allowed minute. The NO ask was 98.4c with a 99c limit.
  Stop-loss, two-buy sequencing, reconnection, stale-data rejection, settlement
  and automatic market rollover all passed. No real orders or account access.
- Hidden desktop integration passed; current GUI RSS was 39.66 MiB.
- Installed app and protected production gateway were updated. Host and isolated
  app health checks both confirmed `entry_window_seconds: 240`, with no API key
  used and zero account requests. Live entries were not started.
- The prior timing changes and forensic timelines below are retained as history.

## Three-minute entry window — 2026-11-12

- User requested final-three-minute entries. A shared strategy constant now
  enforces `0 < cutoff - now <= 180` in simulation, execution, pre-submit checks,
  gateway policy, dashboard eligibility and stream-driven wakeups.
- **168 offline tests passed.** Both YES and NO are rejected at 181 seconds,
  accepted at 180, 150 and 121 seconds, and rejected at the official cutoff.
  Fees, decimal limits, stop-loss behavior and two-buy sequencing still pass.
- Mocked demo integration passed with the real isolated worker/local gateway.
  Its first buy occurred with **167.39 seconds remaining**, proving execution in
  the newly allowed minute. The NO ask was 98.4c with a 99c limit. No real orders.
- Gateway health now reports its entry-window setting, and the desktop refuses
  a mismatched trading gateway. The installer verifies the setting after upgrading.
- The existing gateway was upgraded and verified from both the laptop user and
  isolated app: `entry_window_seconds: 180`. No credentials/account requests were
  used. The app was not started and live entries were not enabled.
- Historical two-minute incident reports remain unchanged as historical evidence.

## Missed NO / decimal-price repair — 2026-11-12

- Inspected the real journal and logs read-only before modifying code. The
  confirmed 4:30 PM market had no submitted buy intent. Entries were enabled;
  decision records showed missing fresh/executable NO data and one RuntimeError
  whose older logger omitted the exact message. No real order was rejected for
  this market. See NO_MISS_REPORT.md for the timeline and limits of that evidence.
- **165 offline tests passed**, including the exact 90-second NO ask 98c / YES ask
  3c scenario, symmetric blocking cases, decimal asks on both sides, ceiling-limit
  sizing and decimal fill acceptance. A 98.4c ask submits a 99c limit; asks above
  99c remain blocked. The baseline exact-98c test also passed before code changes.
- Shared-cache, scoped stop tracking, pre-send arming, prior-observation guards,
  continuous timestamped evidence and detailed rejection/reconciliation tests passed.
- The mocked demo pipeline passed with the real isolated worker and temporary
  local gateway at NO ask 98c, then again at **NO ask 98.4c / NO limit 99c** with a
  simulated 98.4c fill. Each run completed exactly two buys, first-sale sequencing,
  protective selling, network recovery, stale-data rejection, settlement and rollover.
- Final hidden desktop integration passed: ask-to-limit display, eligible side,
  execution stage, timestamped errors, acknowledged pause ordering, all four
  prices, rollover and updates while hidden. Current GUI RSS was 39.70 MiB.
- Tests used temporary journals and generated keys. No real-money order, live
  session activation, credential edit, production journal migration or gateway
  restart was performed for this repair. The additive intent timestamp/event
  schema is applied when the user next opens their trading journal through the app.

Official demo acceptance and prolonged live-network testing remain unverified.
Required account/risk checks may outlast brief prices; stale/unavailable data and
unreconciled account state still block execution.

## Complete refresh/recovery repair — 2026-11-12

- **151 offline tests passed** in the final suite, including all trading rules,
  both sides, partial fills, no opposing holdings, uncertain submission safety,
  Pause, freshness, replay rejection, rate pacing and discovery boundaries.
- **Real isolated demo-mode pipeline passed twice against a mocked exchange.**
  The final run published 106 market events, completed a YES buy and protective
  sale, then a NO buy and settlement, with two buys total. It verified continued
  display updates during slow reads, reconnects, stale timestamp rejection and
  autonomous rollover. No production credentials/socket were mounted.
- **Hidden desktop integration passed:** all four prices, selected held-side bid,
  rollover despite a late account update, hidden-window updates, last-data time,
  history, namespace isolation and restart/pause behavior. Current GUI RSS was
  39.34 MiB; this is a finite sample, not prolonged resource certification.
- Generated-key credential/isolation preflight passed with no connection.
- The generated network policy was installed and verified in a disposable
  network namespace. A formerly permitted local connection was blocked and counted;
  the test did not modify the laptop's host firewall.
- After the user confirmed paused, no positions/orders and closed, the existing
  protected production gateway was upgraded using the reviewed installer. Its
  scoped policy was restored/verified and the existing bind/listen denial preserved.
- Gateway health passed from the laptop user and the real isolated worker:
  production environment, streaming available, **stream protocol 2**. These checks
  used no API credentials and sent zero account requests. Starting the gateway
  did not start the application or activate automatic purchases.

Official demo exchange acceptance remains unverified: no demo credentials or demo
gateway are configured. No real-money orders were placed during this repair.
See REPAIR_REPORT.md for findings, implementation and remaining live-readiness limits.

## Explicit YES/NO prices — 2026-11-12

- 135 offline tests passed; hidden desktop integration also passed.
- Both sides' buy asks and sell bids are displayed with explicit YES/NO labels.
  The position bid is selected by held side and exact market; no position means
  no generic default-to-YES position price. Missing books show unavailable prices.
- A generated book with YES bid 1.8c, NO bid 98c and NO ask 98.2c displays the
  held NO bid as `NO 98.00c`, even if a legacy generic bid field contains 1.8c.
- The NO execution test verifies that the low YES bid does not latch a NO stop;
  a NO bid of 86c does latch and create an 86c NO protective limit sell.
- Only existing local journal records were read for the account diagnosis.
  No new authenticated exchange request, order, cancellation, worker shutdown,
  or trading restart was performed to test this display update.

## Entry responsiveness update — 2026-11-12

- 130 offline tests passed in the final run (temporary memory-backed files).
- Existing trading history was preserved; no additional real-money orders were
  submitted during that investigation.
- The prior log recorded cash but no skip decisions, so the exact cause of the
  reported historical miss cannot be proven. Account checks were roughly 25–30
  seconds apart. Historical orders, fills and settlements were being repeatedly
  paginated in 200-record batches; the price stream was working.
- Fixed a concrete skip condition: a move within the allowed range during checks
  (for example, 95c to 97c) now reprices and resizes rather than skipping the entry.
- Added synthetic tests for YES/NO buys at all five allowed prices, range exits,
  unavailable quotes, fee-inclusive sizing after repricing, explicit cash/skip
  records, full large-page pagination, unchanged reconciliation protections,
  repeated latched-stop observations, and responsive Pause during qualifying prices.
- Recovery tests also cover delayed exchange messages and an unpersisted stop
  crossing followed by database contention, disconnection, and a price rebound.
- Hidden desktop integration passed with the actual YES/NO buy-price row and
  readable decision messages, plus prior simulation/restart/isolation checks.
- Two revised read-only cycles took 9.916s and 17.938s with a temporary memory-backed
  journal. Network/disk latency varies; these are not guaranteed response times.
  The live stream reached sequence 9,243, remained valid, and had a 0.002s heartbeat
  age at observation. A 5s stream sample used 0.414 CPU seconds; peak RSS was
  35.07 MiB. No broker write methods were available in that diagnostic.

## Current refresh and rollover update — 2026-11-12

- 121 offline tests passed. The final run used temporary memory-backed files;
  an earlier 119-test run also passed with disk-backed SQLite databases.
- Hidden desktop integration passed: real namespace isolation, Start/Pause IPC,
  persisted simulation restart, unchanged trade-history rows, market/countdown
  rollover, truthful live badge, stale-data display, and Refresh preserving pause.
- The updated installed package made two successful read-only official market
  discovery requests through the production gateway. Both found the active
  Bitcoin 15-minute contract with its official cutoff.
  No order-writing code was included in that verification process.
- Reviewed application files were copied atomically into the installed package
  and checked byte-for-byte. Credentials, account journal, and gateway were preserved.
- Desktop test current RSS: 38.64 MiB; combined test-process peak: 265.51 MiB.
  These are finite test observations, not continuous live-performance guarantees.

No real-money orders were submitted for this update. Real boundary rollover was
tested with synthetic exchange responses; live discovery was checked within one
market. The historical development results below describe their original stages.

## Original simulation baseline

Linux / Python 3.12.3, 2026-11-11. All activity used synthetic prices and simulated
cash. No account connection or access to real account credentials occurred.

| Check | Result |
|---|---|
| Automated unit/recovery suite | 30 tests passed, final run 49.514 seconds |
| Actual process death | Subprocess exited mid-transaction; restart recovered committed cash and released process lock |
| Offline sandbox replay | 1,000 ticks completed; partial fills, two buys in each eligible market, stop and rebound exits, settlement and restart exercised |
| Namespace security check | Only isolated loopback, personal paths absent, effective capabilities zero, external test route unavailable |
| Failure when isolation unavailable | Restricted parent environment denied network namespace operations; launcher exited; direct runtime also failed closed |
| Terminal UI smoke test | Opened in sandbox; entries paused after restart; restored position shown; synthetic settlement updated balance/P&L; quit cleanly |
| Resource benchmark | 150 ticks, 21.469 wall seconds, 0.203 CPU seconds, 16.46 MiB peak RSS, 53,248 bytes of persistent state after close |
| Demo/live flags | Rejected before account connection or database creation |
| systemd service | Not installed/executed; verifier reports absent expected `/opt/kalshi-bot` installation path |

Timing reflects durable SQLite writes on this workspace's storage and is not a
latency guarantee for another computer. CPU time was under 1% of wall time in the
benchmark; that is a finite offline sample, not continuous-operation certification.
The terminal refresh sleeps by default. SQLite state grows with retained history;
long-duration disk retention remains operator-managed.

Not validated: actual Kalshi schema responses, authenticated requests, demo fills,
real fee schedules, live transport/order recovery, multi-hour/multi-day operation,
network-capable allowlisting, or the optional systemd deployment. The simulator
cannot establish profitability or guarantee that a real 86c stop would fill.

## Read-only connection development

Added offline API/authentication/account/security tests, including real Ed25519 and
RSA-PSS verification against generated keys, pagination, redirects, bounded failures,
Unix-socket gateway round trips with mocked upstream, strict refusal of order writes,
private credential handling, stale cache/identity separation, and firewall verification.

The actual generated nft policy was installed **only in a disposable user/network
namespace**, its kernel JSON was verified, and a formerly working local connection
was blocked with a positive terminal-reject counter. No host nft table was changed.
Sandbox account preflight with a generated dummy key passed and made no connection.

The privileged systemd/ACL deployment and real Kalshi demo/production requests remain
unexecuted acceptance checks. Do not interpret offline tests as completed live account
integration or as support for external stop execution.

## Final offline regression result

- 66 automated tests passed in 38.205 seconds.
- Peak RSS of the full test process (including cryptography and generated keys):
  34.34 MiB. This is a finite test measurement, not a continuous-operation benchmark.
- Both generated systemd units passed `systemd-analyze verify` with exit code 0.
- Account-launcher shell syntax passed `sh -n`.
- Generated-key sandbox account preflight and the disposable-namespace kernel nft
  rejection test passed separately.
- No real API key was configured, no authenticated Kalshi request was sent, and no
  host service/user/firewall deployment was performed.

## Revised buy-price policy and execution candidate

The user explicitly accepted cheaper fills on submitted 95c–99c buy limits.
The simulator and new execution candidate follow that revised rule.

Final regression: **88 tests passed in 70.492 seconds**. This includes original
simulation/account tests, durable external order journal tests, signed V2 POST and
DELETE requests over a local Unix gateway with mocked upstream, price improvement,
YES/NO complement prices, partial fills, two-buy sequencing, persistent stops,
unknown-submission same-ID recovery, and a full synthetic two-buy/two-exit cycle.
No actual Kalshi demo or production endpoint was contacted. No real key was used.
Python syntax and trading-launcher shell syntax checks passed.

Production is not validated: actual demo execution, operator fee-reserve verification,
netting behavior, deployed ACL/systemd behavior, historical data recovery, and
continuous streamed stop monitoring remain acceptance/development work. The current
candidate uses REST polling and may miss brief executable-bid crossings. See TRADING.md.

## Laptop desktop application — 2026-11-12

- 97 automated tests passed in 65.136 seconds.
- Native Tk window smoke test passed; the application window was captured and
  visually reviewed. No account/worker was started during that visual smoke test.
- A separate real namespace + GUI integration test passed start/pause IPC,
  confirmed fill/history display, restart with a restored position and paused
  entries, and refusal to connect without local credentials. All prices/cash were
  synthetic. Generation checks prevent stale worker events passing as recovery.
- Current GUI-test RSS was 38.41 MiB; peak recorded for that test process was
  271.95 MiB. These are finite local measurements, not a continuous-use guarantee.
  Workers retain their 500 MiB address-space ceiling.
- Installer tests confirmed that code/guides/icon are installed, credentials/state
  are not copied, and unmanaged destination files are preserved.
- No Kalshi account was connected and no real-money order was submitted. The app
  has no production-order mode. Gateway deployment and official demo acceptance
  remain separate steps; packaging does not remove the known REST polling limit.

## Version 0.4 production connection infrastructure — 2026-11-12

- 111 offline tests passed in 64.527 seconds, including elapsed-time clocks,
  production authorization, sequenced books, durable streamed stop observations,
  automatic series/event fee reserves, and credential-free local health checks.
- Production gateway installed and started with explicit administrator authorization.
- The laptop did not enforce the original SocketBindDeny setting. Startup remained
  fail-closed. It was replaced with systemd Unix socket activation and kernel
  bind/listen syscall denial; the dedicated UID nft policy remains enforced.
- The installer now verifies a real client response, not merely Type=simple status.
- Actual local health checks passed from both the laptop user and the app's real
  isolated namespace: production environment, trading transport enabled, streaming
  transport enabled. The checks used no API credentials and sent zero account requests.
- Actual account authentication, exchange WebSocket subscription, and real fills
  remain unverified until the user configures the key and connects. No real-money
  trade or authenticated Kalshi account request was performed during development.
