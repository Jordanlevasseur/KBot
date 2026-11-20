# Faster armed-entry response — November 13, 2026

The bot already subscribes to sequenced YES/NO books and wakes the execution
worker on qualifying updates. Profiling found most remaining local delay in
mandatory REST reads paced 500 ms apart. Saving the final intent and passing it
to the order broker took about 2 ms. Preparing frozen orders would not remove
the measured bottleneck or provide current cash, price and cutoff validation.

## Measured before and after

Four fresh isolated sessions before and four after, alternating YES/NO, used
identical synthetic 20 ms REST responses, generated demo credentials, a local Unix
gateway, and a withdrawn Tk window on this ThinkPad. These timings run from local
book receipt to the broker handoff after durable intent; they exclude Internet
transit, exchange acknowledgment and fills. Medians:

| Metric | Before | After |
|---|---:|---:|
| Receipt to eligible-side check | 2337.25 ms | 829.36 ms |
| Receipt to final decision | 4336.59 ms | 1429.51 ms |
| Decision to broker handoff | 2.19 ms | 2.25 ms |
| Receipt to broker handoff | 4338.48 ms | 1431.47 ms |
| Dashboard rendering after receipt | 28.85 ms | 22.24 ms |
| Observed requests before handoff, including discovery | 9 | 9 |
| Worker peak RAM at entry | 32.14 MiB | 32.04 MiB |
| Worker CPU time through startup and entry | 0.312 s | 0.281 s |
| Startup/entry CPU, percentage of one core | 4.03% | 9.79% |
| Quiet background CPU, percentage of one core | 1.22% | 1.33% |
| Quiet worker RAM | 32.38 MiB | 32.30 MiB |

**67.01% less mocked receipt-to-handoff delay**, about 3.03 times faster and below
half the former time. All four optimized samples were between 1430.66 and
1432.20 ms. The decision-to-handoff difference is ordinary test variation. The
startup CPU percentage is higher because comparable CPU work happens over a
shorter elapsed interval. Quiet samples cover about ten seconds each, with three
versus four completed account cycles; small differences do not establish a CPU
or RAM improvement. No dashboard code was changed by this update.

## Changes and rate limits

- Ordinary GET requests use a shared 200 ms dispatch interval (5 requests/s)
  across discovery and execution, replacing 500 ms. A second per-client pacing
  queue is removed when that shared gate is present. Standalone clients use the
  same conservative interval.
- Balance keeps a separate 1.05-second minimum interval. Its wait releases the
  rate-gate lock so a balance refresh cannot hold up market discovery. Balance
  requests do not spend the ordinary read bucket.
- Bounded exponential retry behavior remains; server-requested backoff is also
  shared with other clients using the affected read/balance bucket. Invalid or
  excessive retry delays still fail closed. Order-write pacing is unchanged.

Verified official documentation: [rate limits and tiers](https://docs.kalshi.com/getting_started/rate_limits)
sets Basic read budget to 200 tokens/s and the separate Basic balance bucket to
one request/s. The public [endpoint-cost list](https://docs.kalshi.com/api-reference/account/list-non-default-endpoint-costs)
was checked with one credential-free GET to Kalshi's official service: this bot's
ordinary allowlisted reads use the default 10-token cost. Five reads/s consumes
50 tokens/s, leaving margin within the documented Basic budget. Server 429s still
back off; other clients on the same account can consume its budget too. Future
exchange policies, Internet latency and market liquidity can change actual timing.
No new endpoint or host was added to the bot/gateway allowlist. The public
cost-list verification was a development check, not a new application connection.

## Safety and verification

All four portfolio collections remain fully read and reconciled before every
order. Every new buy still reads current market-scoped primary cash, official
series/event fees, exchange status, official cutoff/status and final fresh
sequenced prices. The per-balance whole-contract count, fee cap, four-minute
window, two-buy sequencing, stop protection, authorization and durable UUID
journal are unchanged. Prepared observations never authorize a trade by themselves.

- Complete offline suite: **248 passed, 0 failed** (22.597 s).
- Six added tests cover shared pacing across clients, the independent balance
  limit, balance wait concurrency, shared bucket-specific cooldown, bounded 429
  retry and both-side preservation of every fresh account/fee/cash/cutoff read.
  The previous 500 ms pacing assertion was updated to the new policy.
- The isolated mocked demo passed YES buy, protective exit, complete sale before
  NO buy, 98.4c ask -> 99c limit, two-buy cap, reconnect, stale rejection,
  settlement and automatic rollover; first buy was 231.26 seconds before cutoff.
- Isolated protective demos passed both YES and NO: 50 contracts owned/sold per
  side, partial fills, unfilled below-limit orders, rebound latch, gateway and
  paused-worker restart, hidden dashboard, confirmed-fill requirement, and zero
  uncertain-order recovery POSTs. Real-money sell execution is not verified.
- Hidden desktop and real Linux namespace checks passed; GUI RAM 39.72 MiB.
  All application Python sources parsed successfully.

Production credentials and journal were untouched. No authenticated live account
request, live order, cancellation, transfer or automatic trading activation was
performed. Updating the user application requires no gateway restart. Existing
streams, network isolation, read-only modes and live Start confirmation remain.

Raw measurements: [before](entry-latency-before.json), [after](entry-latency-after.json),
[quiet before](entry-latency-idle-before.json), [quiet after](entry-latency-idle-after.json),
[protective verification](entry-latency-protective.json).
