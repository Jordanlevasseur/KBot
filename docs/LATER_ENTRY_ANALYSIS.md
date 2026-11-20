# Later-entry comparison: 19 steady winning holds

All 19 markets were read through to official close using complete cursor pagination: 173,188 public non-block trades across 185 unauthenticated GET requests. Scope is the 19 profitable first purchases since fill prefix 0726b5c, through November 14, 2026 at 18:00 Pacific. Re-buys and markets with protective exits are excluded. Original quantities are held fixed. Actual net profit on these 19 purchases was $6.1082. No bot settings, application code, account credentials, exchange orders, or production journal records were changed.

## What the numbers establish

All 19 had at least one later public trade at or below the original held-side average fill price. Eighteen had a later buyer-initiated print at a 95–99 cent price no higher than their original fill, before the 30-second purchase blackout. The remaining purchase originally filled below 95 cents; its later lower prints cannot establish that the configured entry-ask requirement was met. Several of the other latest prints were only fractions of a second after entry. A latest print is not proof that the full original quantity could be bought then.

The following windows count only prints after the actual purchase, inside the indicated final-time window, and strictly before 30 seconds remaining. They require cumulative observed buyer-initiated volume at qualifying prices to cover the original share count. The fee column additionally applies an illustrative standard taker-fee estimate to the first qualifying prints needed for that quantity. These are candidate opportunities, not confirmed alternative executions. The windows are intervals, not orders assumed to execute exactly at their starting time.

| Later-entry window | Price/observed-volume candidates | Same-or-better profit under fee model |
|---|---:|---:|
| 180 to >30 seconds remaining | 11 / 19 | 10 / 19 |
| 120 to >30 seconds remaining | 6 / 19 | 6 / 19 |
| 90 to >30 seconds remaining | 5 / 19 | 5 / 19 |
| 60 to >30 seconds remaining | 1 / 19 | 1 / 19 |
| 45 to >30 seconds remaining | 0 / 19 | 0 / 19 |

## Per-market price evidence

Local times are Pacific on November 14. Latest print below means the last observed buyer-initiated trade between 95 and 99 cents, at or below the original average entry, after purchase but before the blackout. It may cover less than the original quantity and is not a confirmed same-profit entry.

| Market close | Side | Shares | Original entry price | Original time remaining | Latest qualifying print time remaining | Print price | Print quantity |
|---|---|---:|---:|---|---|---:|---:|
| 11:45 | YES | 9.00 | 95.3c | 2.58 min (154.7s) | 2.36 min (141.7s) | 95.2000 | 2.00 |
| 12:00 | NO | 9.00 | 95c | 3.25 min (195.2s) | 1.27 min (76.2s) | 95.0000 | 49.31 |
| 12:15 | NO | 9.00 | 94.1c | 2.60 min (156.1s) | No in-band buyer print | — | — |
| 12:30 | NO | 9.00 | 95c | 1.51 min (90.5s) | 0.98 min (58.9s) | 95.0000 | 1.00 |
| 12:45 | NO | 10.00 | 96.2c | 2.01 min (120.8s) | 1.04 min (62.6s) | 96.1000 | 0.01 |
| 13:00 | NO | 10.00 | 97.4c | 3.97 min (238.5s) | 3.92 min (235.4s) | 97.4000 | 25.00 |
| 13:15 | NO | 10.00 | 96c | 3.97 min (238.4s) | 2.85 min (171.2s) | 95.9000 | 2.00 |
| 13:45 | YES | 10.00 | 98c | 3.99 min (239.2s) | 3.39 min (203.5s) | 98.0000 | 66.93 |
| 14:00 | NO | 10.00 | 97c | 3.99 min (239.6s) | 3.99 min (239.2s) | 97.0000 | 18.71 |
| 14:30 | YES | 10.00 | 97c | 3.86 min (231.6s) | 2.79 min (167.2s) | 97.0000 | 257.36 |
| 15:00 | NO | 9.00 | 95.2c | 3.99 min (239.2s) | 3.56 min (213.4s) | 95.2000 | 0.01 |
| 15:15 | YES | 10.00 | 95c | 2.73 min (163.8s) | 2.45 min (146.9s) | 95.0000 | 145.70 |
| 15:45 | NO | 10.00 | 98.9c | 3.98 min (238.5s) | 1.20 min (72.2s) | 98.9000 | 0.01 |
| 16:00 | YES | 10.00 | 99c | 3.16 min (189.6s) | 3.16 min (189.3s) | 99.0000 | 239.25 |
| 16:15 | YES | 10.00 | 96c | 1.19 min (71.1s) | 1.18 min (71.1s) | 95.9000 | 0.01 |
| 16:30 | NO | 10.00 | 97.9c | 3.99 min (239.1s) | 3.98 min (238.7s) | 97.9000 | 273.71 |
| 17:00 | NO | 10.00 | 97.8c | 3.99 min (239.6s) | 3.97 min (238.3s) | 97.8000 | 25.00 |
| 17:30 | NO | 10.00 | 95c | 3.98 min (238.6s) | 2.82 min (169.3s) | 95.0000 | 15.83 |
| 17:45 | NO | 11.00 | 97.7c | 3.91 min (234.7s) | 1.87 min (111.9s) | 97.6000 | 17.10 |

## Assumptions and limits

Illustrative only: default taker multiplier M=1, fee 0.07*C*P*(1-P), applied to the volume-weighted first observed qualifying buyer prints, cost rounded up to $0.0001. Same original quantity and $1 winning settlement. Original fees are taken from actual fills. Historical event-specific overrides, queue priority, simultaneous depth, competition, slippage, latency, funding and subsequent portfolio resizing are not reconstructed. A favorable observed volume is not a reserved fill.

Actual executable asks, full historical order-book depth, and your hypothetical queue allocation are unavailable. Public trades at a price show transactions occurred, not that another order would receive them. The fee model uses the standard multiplier and does not verify historical event overrides. Maker and taker fees may differ. Price improvement below the entry band is permitted, but those prints alone do not prove an eligible ask existed when an order could be sent. Only winners were selected, so the analysis cannot show whether waiting would improve the strategy or reduce overall losses.

Sources: [official public trades API](https://docs.kalshi.com/api-reference/market/get-trades), [official fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf), [official fee rounding](https://docs.kalshi.com/getting_started/fee_rounding). Raw comparisons, 30-second price ranges, request counts and per-window quantities/notionals are in LATER_ENTRY_ANALYSIS.json; a compact price-only table is in LATER_ENTRY_ANALYSIS.csv.
