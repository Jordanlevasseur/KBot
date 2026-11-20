# Protective sell verification

> Follow-up safety correction: the earlier same-ID replay assumption below is
> superseded by [the rejection/recovery report](EXCHANGE_REJECTION_REPORT.md).
> Uncertain creates now wait for read-only reconciliation; a closed client ID
> is not a durable exchange deduplication guarantee. Historical results follow.

Verified on November 12, 2026 (Pacific time), in the existing application.
No real-money order, production cancellation, live-session activation or account
API request was performed. Production credentials were not read or changed.
Production records were inspected only using SQLite `mode=ro`, `query_only=ON`
and a consistent read transaction. No production journal migration was run.

## Result and scope

**Final full offline suite: 221 passed, 0 failed.** The original 176-test baseline
passed before runtime changes. Two additional protective-suite runs passed 40/40
each with seed offsets 50 and 100. The final full suite uses offset 0. Together
those runs exercised **300 distinct side/seed sequences and 30,000 randomized
steps**, without an oversell or simultaneous active protective orders. Development
reruns are not added to the headline test count.

The real isolated demo worker, temporary Unix gateway and hidden native dashboard
passed the comprehensive protective integration on both YES and NO. Each case
bought 49 generated contracts, partially sold 3.25, and eventually confirmed all
49 sold, with **one distinct protective sell order**, despite a lost create
response, a lagging order snapshot, gateway restart and worker restart. The same
client ID was used for uncertain-create recovery. These are synthetic exchange
results, not official demo or production exchange execution evidence.

## Requested price scenarios

The same tests run for YES and NO. Executable **held-side bid** is used throughout.

| Scenario | YES | NO | Verified behavior |
|---|---|---|---|
| 98c buy, bid falls to 90c | Pass | Pass | Hold; no protective order. |
| Bid falls to 87c | Pass | Pass | Hold; no protective order. A bid of 86.01c also holds. |
| Bid reaches exactly 86c | Pass | Pass | Latch stop and submit an 86c limit for the owned side/quantity. |
| Bid gaps from 95c to 80c | Pass | Pass | Latch; limit remains 86c. No invented fill at 80c. |
| Bid briefly hits 85c then rebounds to 92c | Pass | Pass | Saved stop remains active through the rebound and restart. |
| Repeated crossings | Pass | Pass | Preserve the existing active sell; no duplicate or unnecessary cancellation. |
| Trigger with 10 seconds remaining | Pass | Pass | Submit before cutoff in the synthetic clock case. A separate delayed-reconciliation test crosses cutoff and blocks a late submission. |
| Partial buy fill | Pass | Pass | Request cancellation of residual buy; wait for terminal cancellation and all fills; sell the actual confirmed quantity, including additional fills during cancellation. |
| Another order pending | Pass | Pass | Keep managed sell; cancel a stopped residual buy, including zero-fill pending buys with entries enabled. Unmanaged orders block writes. |
| Stream interruption | Pass, with observability limit | Pass, with observability limit | Previously observed/latched stop persists and can drive an exit without a fresh quote. An unseen crossing cannot be inferred; a new stop waits for valid data. |

NO selling is encoded as a YES-price **14c bid**, equivalent to an **86c NO sell**;
YES selling is an **86c ask**. Both wire encodings, quantities and gateway order
validation are checked. An unheld contract's low bid must not trigger the held
contract's stop.

## Order lifecycle and duplicate prevention

Tests cover successful submission, definitive exchange rejection, delayed
acknowledgment, timeout before receipt, timeout after acceptance, a lagging order
snapshot, partial fills, canceled residual sells, completely unfilled orders,
expiry, settlement and an existing open sell after restart.

- A create acknowledgment changes no owned quantity. Fills and the account
  position projection must reconcile before a completed exit is reported.
- A partially filled sell remains open for its remainder. If it is confirmed
  canceled, a replacement covers only the remaining owned quantity.
- A rejected sell retains the stop and can be retried. Twelve consecutive
  definitive rejections followed by success were tested; buys are not consumed
  by these sell attempts.
- Uncertain creates retain their durable client ID. Recovery first checks saved
  exchange orders and, when necessary, retries that same ID. A retry rejection
  cannot erase uncertainty about the original request.
- Actual abrupt process death (`os._exit`) after synthetic acceptance, before
  recording its acknowledgment, was tested for both sides. Reopening the journal
  and reconciling the synthetic exchange order did not submit another sell.
- Partial account projections block writes instead of allowing a duplicate or an
  oversized replacement. Delayed cancellation acknowledgment is not treated as
  cancellation proof.
- The actual order-broker backoff path was tested: after HTTP 429 with Retry-After,
  five calls resulted in one HTTP attempt during the backoff period.
- Expiry and settlement without enough sell fills are reported as settlement
  without a complete protective exit. They are never called a successful sale.

The randomized exchange oracle rejects wrong-side sales, sale quantities above
its owned position, overlapping active orders and total sold quantity exceeding
buys. Each sequence includes price movements, missing/stale data, account/network
errors, partial fills, delayed/uncertain creates, cancellations and restarts.
Every sequence is then recovered and closed against a coherent synthetic account.

## Connectivity and background operation

Full account reconciliation remains required before writes. Protective management
continues while entries are paused, and held/pending risk takes priority over any
qualifying opposite-side buy. Stop observations can be persisted independently
while account reads are delayed, so a later rebound does not clear the instruction.

The isolated integration restarted only its own temporary gateway and worker.
The existing production gateway and any unrelated application were left running.
A worker restart restores the protective order and latched stop with entries
paused. The hidden dashboard continues processing stream and execution messages.
A gap below the limit leaves the sell unfilled, and the UI shows partial/open
state until synthetic exchange fill and position confirmation arrive.

Additional validation passed: the existing isolated demo's two-buy/decimal-NO/
settlement/rollover regression, hidden desktop controls/recovery integration and
generated-key credential/isolation preflight. Final native GUI RSS was 39.61 MiB.
No exchange rate limit, isolation, authentication or reconciliation check was
removed to obtain an execution.

## Defects reproduced and fixed

1. **A current crossing could be lost when arming protection.** A fresh bid already
   below 86c was hidden but not immediately persisted. If the next stream message
   was a rebound, the crossing could disappear. `BookFeed.arm` now persists the
   currently valid held-side crossing synchronously after the durable buy intent
   and before its network write. It also handles a second same-side buy when the
   feed's side is already armed. Stale prices cannot seed a new stop. Persistence
   failure blocks that new buy before HTTP and keeps quotes unavailable.
2. **A stopped, zero-fill pending buy was maintained while entries were enabled.**
   Stop cancellation had been handled in the filled-position branch. The pending
   order branch now requests cancellation too. The worker wakes immediately for
   this latched pending risk instead of waiting out its normal idle interval.
   It still waits for confirmed fills/cancellation before any protective sell.
3. **Protective lifecycle display was too general.** The dashboard now distinguishes
   triggered, waiting for buy cancellation, acknowledged, accepted/open, uncertain,
   rejected, partially filled, retry needed, completed and settled without a full
   protective exit. Completed means confirmed sell fills, not submission or a
   position disappearing through settlement. Long status text wraps in the
   existing dashboard.
4. **Failure stages and recovery outcomes were incomplete.** Protective submits
   and residual-buy cancellations now identify the held side and relevant stage
   in the decision audit. A same-ID recovery attempt is no longer described as
   blocked before submission. Response bodies and credentials remain excluded.

No price, allocation, fee, entry timing, buy-count or position ownership rule was
changed. Entry stays final **240 seconds**; buy limits stay **95c–99c**; cheaper
fills remain allowed; protective limit stays **86c**; buys remain capped at two
per market after complete first sale.

Runtime changes: `streams.py`, `execution.py`, `trader.py`, new read-only diagnostic
helper `protective.py`, `desktop_worker.py`, `desktop.py` and `diagnostics.py`.
Verification: new `tests/test_protective_sells.py`, `tests/protective_pipeline.py`
and `tests/audit_local_history.py`.

## Read-only real-money history findings

The local journal contains **four completed buys**, rather than three: **three NO
and one YES**, each matched by stored fills, settlement data and the latest saved
exchange-order projection. All four markets are marked settled. There are **zero
protective sell records** and no latched stops for these trades.

Recorded settlement proceeds, less the matching fill costs and fees, total
**$0.165400**, which rounds to the reported **$0.17**. Fees are subtracted once;
settlement revenue is counted once per market. Unrelated settlement rows are not
included. No account identifier, key ID, order ID or balance is exported.

The latest stored reconciliation is **November 12, 2026, 7:50:28 PM Pacific**. It
contains all four owned orders, zero nonzero positions and zero open orders.
This is a saved snapshot, not a new query of current exchange state. The two older
orders have legacy zero intent timestamps, so this journal alone cannot reprove
their exact entry-window timing. No live protective sell fill exists in these
records, so **live protective execution has not been verified**.

Read-only audits before and after testing found the same trade totals and saved
snapshot. Database and WAL digests were unchanged during each audit. No production
`Journal` instance was opened, no lock ownership was taken and no schema/order/
fill/settlement record was changed. See [sanitized audit](LOCAL_TRADE_AUDIT.json)
and [synthetic integration results](protective-demo-results.json).

## Remaining risks and readiness

The implementation is ready for **further separately authorized, controlled live
verification**, supported by the tests above. Successful live protective execution
and official demo exchange acceptance remain unverified. This report does not
activate or authorize any real-money verification.

- An 86c limit is a minimum sale price. A bid below 86c, insufficient buyers,
  queue position or market closure can leave part or all of it unfilled.
- A crossing occurring entirely during an outage, suspension or stopped worker
  cannot be reconstructed without trustworthy exchange evidence. Persistent
  latching protects observed crossings; it cannot observe an unavailable feed.
- API outages, account projection lag, rate limits, disk failures and time spent
  reconciling can prevent submission before cutoff. The 10-second test is not a
  promise of live-network timing. Known/ambiguous orders remain blocked pending
  reliable reconciliation rather than being replaced speculatively.
- Manual/concurrent account trading can change holdings between API snapshots
  and submission. The bot blocks visible unmanaged activity; these tests prove
  the owned-position workflows, not atomic isolation from other account actors.
- Client-ID duplicate prevention depends on the exchange honoring its uniqueness
  semantics. Power loss, unavailable storage and exchange-side failures cannot
  be eliminated by an application test.

Installed fixes take effect on the next normal app launch. No running session was
restarted and no new trading was enabled. The application retains its simulation,
read-only, demo and explicit live modes and its existing security boundary.

## Reproduce offline

Use the existing reviewed dependencies and Linux isolation. All trading test
commands below use generated credentials/synthetic exchange state only:

```sh
TMPDIR=/dev/shm python3 -B -m unittest discover -s tests -p 'test_*.py'
TMPDIR=/dev/shm KALSHI_TEST_SEED_OFFSET=50 python3 -B -m unittest discover -s tests -p test_protective_sells.py
TMPDIR=/dev/shm KALSHI_TEST_SEED_OFFSET=100 python3 -B -m unittest discover -s tests -p test_protective_sells.py
TMPDIR=/dev/shm python3 -B tests/protective_pipeline.py --output /tmp/kalshi-protective-demo.json
```

The audit script is separate: it requires an explicitly selected local database,
opens it read-only and exports only aggregate evidence. It does not connect an
account or modify the production journal.
