# Missed NO purchase: findings and repair

Timing update: the user subsequently changed entry eligibility to the final four
minutes. The forensic timeline below describes the two-minute rule at that incident.

User-confirmed market: Bitcoin 15-minute contract ending **4:30 PM Pacific,
November 12, 2026**. Existing logs and the journal were inspected read-only before
changing application code. No real orders or credential changes were used.

## What the evidence proves

The bot **never reached order submission** for this market. There is no durable
buy intent for it, including no rejected or uncertain submission. The journal is
written before the HTTP order request, so this was not a submitted NO buy rejected
by Kalshi. Entries were recorded as enabled; successful reconciliations and cash
reads showed sufficient buying power. The saved state had no managed pending order
or position for this market. Earlier purchases belonged to different markets.

All times below are Pacific:

| Time | Trading record |
|---|---|
| 4:27:56 PM | NO ask 97.6c; waiting for the final-two-minute window. Price qualified for a 98c limit, but the window was not open. |
| 4:28:00 PM | Official entry window opened. |
| 4:28:23 PM | Waiting for a fresh sequenced book; no submission. |
| 4:29:15 PM | No executable NO ask recorded; no qualifying entry. |
| 4:29:35 PM | A RuntimeError blocked the cycle. The old logger saved only its class. |
| 4:29:57 PM | No executable NO ask recorded; no qualifying entry. |
| 4:30:00 PM | Official trading cutoff. |

Cycles took roughly 20–28 seconds. The recorded blockers were unavailable executable
data and one generic runtime failure, before submission. The old display frames
were not saved, so the records cannot prove whether the displayed 98c was an ask
at an evaluation instant, a separate display-stream quote, or a price between
account checks. The old RuntimeError's exact invariant cannot be reconstructed.

## Verified defects repaired

1. The dashboard and trader used different streams. Both now consume the same
   canonical, sequenced, freshness-checked cache per market. Another cache remains
   protected when a different held market spans rollover.
2. Flat trading streams queried SQLite for low bids on unheld sides, temporarily
   marking their quotes unavailable. That unnecessary work is removed. Tracking is
   armed after the durable buy intent and before sending, and for restored
   positions/pending orders. Intent timestamps guard against old stop observations.
3. Continuous eligibility observations now run independently of slow account calls.
   Timestamped observations and stages are written asynchronously with quote,
   sequence, freshness, window and entry-state evidence. Reconciliation is preserved.
4. Failures now record stage, side, sanitized reason, HTTP status and whether the
   failure was before submission, rejected, or uncertain. No raw API body or
   credential material is retained.
5. Both sides share eligibility logic. When both qualify, lower ask wins; exact
   ties initially use a stable market hash and alternate after a completed purchase.
   There is no fixed YES-first selection priority.

The dashboard separates price eligibility from execution readiness, shows both
sides' buy/sell prices, the official window, entry enablement, execution stage,
latest decision and timestamped errors. Safety checks remain authoritative.

## Files changed

- `kalshi_bot/streams.py`: BookHub, coherent frames and scoped stop tracking.
- `kalshi_bot/eligibility.py` (new): symmetric price checks and selection.
- `kalshi_bot/market_data.py`: canonical cache and independent observations.
- `kalshi_bot/diagnostics.py` (new): asynchronous evidence and sanitized errors.
- `kalshi_bot/trader.py`: explicit stages/failures and cached official discovery.
- `kalshi_bot/execution.py`: additive intent timestamp and pre-send tracking hook.
- `kalshi_bot/desktop_worker.py`: integration, precise errors and execution revisions.
- `kalshi_bot/desktop.py`: eligibility/stage display and timestamped errors.
- Tests: `test_missed_no.py`, `test_shared_book.py`, `test_entry_checks.py`,
  `desktop_integration.py`, `demo_pipeline.py`.
- Documentation: this report, ONLINE.md, VALIDATION.md, STATUS.md, REPAIR_REPORT.md.

## Verification

The exact **90 seconds, NO ask 98c, YES ask 3c, funded/enabled/flat/fresh** case
submitted a NO limit buy at 98c before application changes, proving that the NO
order mapping itself was correct. It also passes after the repair.

Decimal asks were also already eligible in the original comparison. Explicit
regressions now verify asks such as **98.4c** on both sides, a submitted **99c**
limit, fee-inclusive sizing at that ceiling, and acceptance of a **98.4c fill**.
The dashboard and evidence now show the executable ask and proposed limit together.
An ask above 99c still blocks entry; rounding it down would not create a valid
executable purchase within the strategy's ceiling.

The full suite exercises both directions, allowed/out-of-range prices, paused
entries, stale data, insufficient cash, pending orders, two buys, first-sale
sequencing, stops, opposing-position/duplicate prevention and restart/recovery.
New tests cover shared frames, flat streams avoiding stop-database work, pre-send
arming, old observations, persisted evidence, reconciliation errors and HTTP failures.

The real isolated worker and temporary Unix gateway passed the mocked demo pipeline:
YES buy, protective sale, NO limit buy at **98c**, two buys total, reconnects,
stale-data rejection, settlement and rollover. Hidden desktop checks passed for
eligibility, execution stages, timestamped failures and pause-state ordering.
Final counts and results are in VALIDATION.md.

## Remaining limits

A brief opportunity can disappear while required account/risk checks run. The
current executable ask is checked again before submitting; stale data, a price
outside 95c–99c, or an expired cutoff still blocks the trade.

Official demo acceptance is unverified because demo credentials/gateway are absent.
Prolonged live-network testing remains pending. Missing archived records or
unmanaged positions/orders still block execution. Recent diagnostic evidence is
bounded to about 10,000 events; persistent order/fill history is separate.
