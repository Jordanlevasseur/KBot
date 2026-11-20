# Missed NO purchase: 9:30 PM market

> Follow-up: newer live attempts now report `insufficient_balance`. A read-only
> investigation confirmed aggregate cash was used instead of the market exchange
> cash. See [the implemented cash fix](CASH_SCOPE_FIX.md). The original 9:28 PM
> detailed response remains unavailable; historical evidence follows.

November 12, 2026, Pacific time. Investigation used the local journal/logs read-only.
No production account request, key access, live trade, cancellation or gateway
restart was performed. The production journal was not edited.

## Confirmed cause of the missed purchase

The bot recognized an opportunity and **attempted a NO limit buy at 97c**.
The durable intent was recorded at **9:28:20.281 PM Pacific**, with **99.719 seconds
remaining** before the official 9:30 PM cutoff. The order request returned
**HTTP 400**, recorded at **9:28:22.601 PM**. The journal marks it rejected,
with zero fills. It did not consume either allowed purchase for that market.

At the attempted buy:

- Automatic entries were enabled.
- Final executable NO ask and selected limit were both 97c.
- Final book status was connected, with verified age 0.0 seconds.
- The four-minute timing, whole-contract quantity, order direction and local
  request validation passed. NO 97c was encoded as a YES-leg ask at 3c.
- Saved cash evidence supports the local half-cash sizing calculation. This does
  not independently prove the exchange's shard-specific available buying power.
- The source, installed app and protected gateway had matching write validation
  and 240-second entry settings. A local gateway policy rejection returns 403;
  the forwarded 400 came from the upstream API response.

The cycle lasted 23.215 seconds, and other live cycles took roughly 9–17 seconds.
These measured live delays are longer than the earlier local mocked performance
measurements. Price monitoring continued independently; required account reads
and network transit still delayed final decisions.

After the rejected attempt, the 9:28:36 decision awaited fresh sequenced data.
At 9:28:52 the NO ask was **99.4c**, outside the user's 99c cap. Later decisions
had no executable NO ask. No additional qualifying order was submitted. The
saved log then acknowledges entries paused at 9:30:07.

**The detailed HTTP 400 reason cannot be recovered.** The previous order broker
received but discarded the response body and logged only its status. The available
evidence does not establish invalid price, insufficient buying power, a malformed
field or any other specific exchange reason. A successful local validation is
not proof that the exchange must accept an order. No real order was replayed to
try to reproduce the error.

## Diagnostics corrected

Known public exchange rejection codes now reach the dashboard and decision audit
with a fixed explanation. Examples include invalid price, unsupported quantity,
insufficient shard balance, inactive market and permissions errors. Flat and
nested documented error envelopes are supported.

The parser keeps only explicitly recognized public codes. It does not retain or
log arbitrary server `message`, `details`, headers, account identifiers or
response bodies. Unknown/malformed/oversized bodies remain a generic HTTP error.
Consequently some future rejections may still require exchange support; the
application does not promise to expose every free-text rejection detail.

Decision failure records include the safe exchange code and attempted side/limit.
The activity list displays that side/price. The desktop no longer says an order
is retained when a definitive rejection left no unresolved order.

## Important recovery correction

The current official [Order Errors documentation](https://docs.kalshi.com/getting_started/order_errors)
limits client-order-ID protection to open orders. An ID can become reusable after
its order fills, cancels or expires. The previous synthetic exchange deduplicated
IDs permanently, so those older mocked replay tests did not prove duplicate
prevention after a closed order disappears from a delayed read.

The app now **does not replay an uncertain create**, even with the same client ID.
It keeps the original durable intent and reads until exchange records establish
its outcome. If the original order appears, normal reconciliation resumes. If it
never appears, the app remains blocked for explicit/manual reconciliation; it
must not fabricate a rejection or reset the journal. This conservative limitation
can delay or prevent another purchase or protective retry during an outage.

A documented HTTP 429 rejection leaves no accepted order. It now terminates that
never-accepted local attempt while the existing broker cooldown and fresh account
checks still govern any later attempt. Unknown timeouts/5xx remain unresolved.
The change does not affect the recorded 97c order, whose HTTP 400 was definitive.

This supersedes the earlier report's assumption that reusing the same client ID
always makes an uncertain replay safe. The prior protective test results remain
historical; the current tests cover closed-ID reuse and require zero recovery POSTs.

## Verification

- **228 offline tests passed, zero failed**, including all existing strategy and
  protective tests under the updated recovery policy.
- New tests cover nested/flat error envelopes, credential-like strings, unknown
  codes, malformed/large bodies, safe dashboard/audit messages and a rejected
  NO 97c candidate consuming no additional purchase.
- A regression models an accepted, fully filled sell missing from a delayed order
  snapshot. Recovery performs no POST, then reconciles when its record appears.
- The real isolated demo worker/local gateway/hidden UI passed for YES and NO:
  accepted-but-lost response, lagging order read, read-only recovery, partial fill,
  gateway restart, worker restart, unfilled gap, and final exchange fill/position
  confirmation. Each used one buy and one protective sell, with **zero recovery
  reposts**. See [synthetic results](protective-demo-results.json).
- Existing hidden desktop integration passed. Credentials, strategy, prices,
  240-second entry window, fee reserves, isolation and rate limits are preserved.

The installed update takes effect on the next app launch. No running session was
restarted or enabled. This work fixes the missing rejection diagnostics and an
unsafe recovery assumption; it does **not** establish a specific reason for the
old HTTP 400 or prove future/live exchange acceptance.

## Official references

- [Create Order V2](https://docs.kalshi.com/api-reference/orders/create-order-v2):
  YES-leg pricing, required fields, expiry and response envelope.
- [Order Errors](https://docs.kalshi.com/getting_started/order_errors): public
  codes, exchange-shard balance errors, uncertain outcomes and client-ID limits.
