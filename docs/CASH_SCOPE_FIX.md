# Bitcoin cash sizing: confirmed cause and correction

Sizing-policy update: the cash-scope correction below remains in force. The former
half-cash dollar budget is now replaced by whole-contract sizing; see
[CONTRACT_SIZING.md](CONTRACT_SIZING.md). Historical quantities/checks below use
the earlier policy.

November 12, 2026, Pacific time. This follow-up investigates the user's concern
that a local restriction prevented purchases and request for live verification.
The agent performed authenticated **read-only live checks** and offline/mock
verification. No real-money buy, sell, cancellation, funding action or transfer
was submitted. The app remains closed; trading was not activated.

## Actual exchange rejection

New saved decisions identify the reason that the earlier logger could not retain:
Kalshi returned **HTTP 400 `insufficient_balance`** on NO attempts around 9:58 PM
and YES attempts around 10:12–10:13 PM Pacific. Automatic entries were enabled.
The attempts reached order submission, at legal 97c–99c limits, with 12 or 13 whole
contracts. These were definitive rejections, with no fills or consumed buys.
The original 9:28 PM response's details remain unavailable; the newer responses
now provide direct exchange evidence of insufficient spendable cash.

The application incorrectly used an **aggregate account balance** for sizing.
The [official Balance API](https://docs.kalshi.com/api-reference/portfolio/get-balance)
includes all exchange indexes when no index is supplied. An order can spend only
cash available on its market's exchange, using the same subaccount as the order.
The [market metadata](https://docs.kalshi.com/api-reference/market/get-market)
provides that exchange index.

An authenticated read-only check confirmed that the rejected Bitcoin market is
on exchange index 2, aggregate cash exceeds that market bucket, and that bucket
could not cover the prior 13-contract order. The breakdown is not used to size
orders: it sums across subaccounts and cannot establish primary-subaccount cash.

## Implemented correction

- New `buying_power.py` reads `/portfolio/balance` with **`subaccount=0` and the
  chosen market's `exchange_index`**, matching the order's primary account.
- New buys allocate 50% of this actually spendable market cash, with the existing
  fee reserve and whole-contract rounding. They never fall back to combined cash
  if the market has no money or its cash scope cannot be verified.
- Fixed-point cash precision is rounded down conservatively. The exact cash
  reader also drives the displayed scoped balance.
- The final market refresh must agree with the scope used for sizing. A changed
  scope blocks submission until fresh checks are repeated.
- The dashboard distinguishes **AVAILABLE FOR BITCOIN** from **TOTAL ACCOUNT CASH**.
  Ordinary account/holding refreshes may show the total label; a new-buy sizing
  read shows the Bitcoin-specific amount. Aggregate cash never authorizes a buy.
- The isolated gateway accepts only the scoped **read** on the existing balance
  endpoint, primary account 0 and a bounded integer exchange selector. Those
  selectors are denied on other paths. No transfer/deposit/withdrawal endpoint,
  additional network destination or write field was added.
- Health advertises `market_balance_scope: 1`; trading connection setup rejects
  an older gateway until repaired. The setup helper verifies that capability.

The YES/NO price rules, 240-second window, two-buy sequencing, fee reserves,
protective limit, duplicate guards and reconciliation policy are unchanged.
This is a correction to the definition of available cash, not a relaxed spending
or account-security control.

## Live read-only verification

The user confirmed entries paused, no positions/orders, and the window closed
before the protected connection service was updated. The app was not restarted.
Kernel network/bind restrictions and pinned official destinations were preserved.

A second isolated check used the actual configured key via the read-only signer
and GET-only transport. It fetched market metadata, correctly scoped primary cash
and official series/event fee data. At that instant, the existing 50% sizing rule
could cover **3 contracts at a hypothetical 97c limit**, compared with the prior
attempted 13. This calculation was not an order, and does not imply the live ask
was 97c or that the current market was within the entry window.

No raw key, signed header, account identifier or account balance is exported.
The sanitized result is [LIVE_READONLY_CASH_CHECK.json](LIVE_READONLY_CASH_CHECK.json).
It records zero order, transfer and other write requests. This proves live read
connectivity and the corrected inputs, **not real-money fill execution**. A real
money test must be initiated by the user through their application controls.

## Tests and deployment

**235 offline tests passed, 0 failed.** Added coverage includes:

- Both YES and NO with $27 aggregate cash but only $6.20 on the market exchange:
  a 97c limit with a 3c fee reserve sizes to 3 contracts, rather than 13.
- Zero market cash never falls back to other exchange cash.
- Missing/invalid market identity or exchange scope fails closed.
- Fractional-cent cash is rounded down, and invalid cash cannot authorize a buy.
- Only the narrowly allowed primary-account balance query passes gateway policy.
- A market scope change during pre-submit checks prevents submission.

The existing isolated mocked demo passed YES/NO buys, decimal NO asks, protective
selling, complete first sale before the second buy, two buys combined, network
reconnection, stale rejection, settlement and market rollover. Hidden desktop
checks verified both cash labels. Application/deployment syntax checks passed.
Real gateway health matched from host and isolated app, including the 240-second
window and new read-scope capability.

The installed application and connection service were updated. Credentials and
production journal records were not altered. No live trading was enabled. The
app loads the correction on its next normal launch. Any actual order still
requires fresh price, time, cash, permission and reconciliation checks, and may
be rejected if exchange/account conditions change between those reads and submit.
