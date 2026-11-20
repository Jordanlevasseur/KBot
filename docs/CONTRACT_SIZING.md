# Whole-contract purchase sizing

User-requested change, 2026-11-13: target **floor(spendable Bitcoin-market cash in
cents / 200)** whole contracts on either YES or NO. The fraction is discarded:
$26.00, $26.87 and $27.99 each target 13 contracts; $28.00 targets 14.

The target is a contract count, independent of eligible price. It replaces the
former half-cash dollar budget divided by contract price plus fees. Actual cost
is quantity times the limit price plus fees and can exceed half the cash. For
example 13 contracts at 99c plus a 3c-per-contract reserve needs $13.26, so a
$26 balance still targets 13. Fees come from remaining cash. The existing verified
conservative fee reserve remains mandatory; quantity is reduced if the full
order plus fee reserve cannot fit total spendable Bitcoin-market cash.

The app still uses the market-specific primary-account balance, not combined
cash across exchanges. A large combined balance cannot fund an order when the
selected market has less spendable cash. No transfers or funding are performed.
Every purchase, including the second, uses a fresh market cash read. No unfilled
order is automatically enlarged after restart. Active order/position reconciliation,
two-buy sequencing, four-minute entry window, cent-limit validation and 86c
persistent protective exits are unchanged. Sub-cent asks still round up to an
eligible cent limit (98.4c ask -> 99c limit); quantities always round down.

Simulation and exchange execution share the contract-count target. Both GUI
rules and the live Start confirmation explain this sizing. Installation does
not connect to an account or enable entries. This update uses synthetic data
and disposable journals only; no real-money test is performed.

## Verification

- Complete offline suite: **242 passed, 0 failed** (22.903 seconds).
- General formula checked for every cent from $0 through $1,000: 100,001 balances
  at all five allowed limits, plus larger integer balances. No $26 special case.
- Both YES and NO submit 13 whole contracts for $26.87 at all five limits and a
  98.4c executable ask using a 99c limit. $26.00 through $27.99 also tested at
  every cent. Insufficient cash, fee caps and invalid values fail safely.
- Second buy recalculates contract count from fresh cash after a complete sale;
  pending orders still block duplicate/opposing purchases.
- Real isolated demo worker with generated credentials and a mocked exchange:
  both buys, protective exit, decimal NO entry, no third buy, reconnection, stale
  rejection, settlement and rollover passed. First buy occurred 228.07 seconds
  before cutoff; 119 market-data events arrived.
- Hidden desktop/sandbox checks passed, including history and paused restart;
  GUI RAM 39.71 MiB. All Python sources parsed successfully.
- No account authentication, real order, cancellation, funding, credential change
  or production journal modification was performed for this update.
