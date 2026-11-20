# Final-four-minute threshold study: latest 10,000 markets

## First qualifying side per market

This view avoids counting a later winning opposite-side touch as a new success for the original opportunity. When both sides first qualify in the same minute, their order is unknown. Flip-rate ranges allocate those ambiguous cases to either outcome; they are data-order bounds, not statistical confidence intervals. Confirmed dip-and-win counts remain lower bounds.

| Threshold | Markets | Known original side dipped and won | Known original side lost | Both-side first-touch order unknown | Flip-rate bounds |
|---|---:|---:|---:|---:|---:|
| 96¢ | 10000 | 3043 (30.43%) | 316 | 59 | 3.16–3.75% |
| 97¢ | 10000 | 3119 (31.19%) | 253 | 50 | 2.53–3.03% |
| 98¢ | 10000 | 3188 (31.88%) | 178 | 46 | 1.78–2.24% |

Completed markets selected: 10000; valid analyzed markets: 10000; excluded: 0.
First close: 2026-08-02T02:00:00-07:00; last close: 2026-11-15T11:30:00-08:00. Discovery anchored to Kalshi server Date: Sun, 15 Nov 2026 19:58:39 GMT.

## What the counts mean

Threshold means trade price at or above 96, 97, or 98 cents, not the Bitcoin dollar price. We examine the first qualifying minute inside the final four minutes, once per side per threshold per market. A side may already have reached the threshold before this window; this is not an assertion that its first lifetime crossing occurred here. YES and NO receive symmetric treatment. A market can contribute two side events if both sides qualify. These are overlapping cohorts, not independent experiments.

Confirmed dip means a price below the threshold at the close of the touch minute, or a low below it in a later minute. The touch-minute low could have preceded the qualifying high and is not counted as an after-touch dip unless the close proves it. Thus confirmed-dip counts are conservative lower bounds. The separate uncertainty column counts cases where a same-minute dip is possible but not confirmed. High/low/close do not establish every intraminute path. Settlement direction, unlike path ordering, is known.

## Main results

| Price | Side events | Same side won | Opposite won | Confirmed dip then same side won | Confirmed dip then opposite won | Additional dip cases uncertain |
|---|---:|---:|---:|---:|---:|---:|
| 96¢ | 10,383 | 9,999 (96.30%) | 384 (3.70%) | 3,077 (29.63%) | 384 (3.70%) | 4,757 (45.82%) |
| 97¢ | 10,311 | 9,999 (96.97%) | 312 (3.03%) | 3,138 (30.43%) | 312 (3.03%) | 5,120 (49.66%) |
| 98¢ | 10,228 | 9,999 (97.76%) | 229 (2.24%) | 3,207 (31.36%) | 229 (2.24%) | 5,604 (54.79%) |

Percentages above use all qualifying side events at that threshold. “Dip and win” means the original side ultimately settled as winner, not necessarily that a trade returned to the threshold before close. Actual confirmed return trades are tabulated separately below.

| Price | Confirmed dips | Of those dips, original side won | Of those dips, opposite won | Confirmed trade recovery after dip |
|---|---:|---:|---:|---:|
| 96¢ | 3,461 | 3,077 (88.90%) | 384 (11.10%) | 3,177 |
| 97¢ | 3,450 | 3,138 (90.96%) | 312 (9.04%) | 3,218 |
| 98¢ | 3,436 | 3,207 (93.34%) | 229 (6.66%) | 3,260 |

## Depth after touching

For each cell: total confirmed at-or-below events / eventual original-side winners / opposite-side winners. All are trade prices, not executable full-size bids.

| Low at or below | After 96¢ | After 97¢ | After 98¢ |
|---|---:|---:|---:|
| 95¢ | 2995 / 2611 / 384 | 2306 / 1994 / 312 | 1567 / 1338 / 229 |
| 92¢ | 2081 / 1697 / 384 | 1624 / 1312 / 312 | 1119 / 890 / 229 |
| 90¢ | 1777 / 1393 / 384 | 1387 / 1075 / 312 | 959 / 730 / 229 |
| 86¢ | 1455 / 1071 / 384 | 1141 / 829 / 312 | 788 / 559 / 229 |
| 80¢ | 1100 / 716 / 384 | 875 / 563 / 312 | 611 / 382 / 229 |
| 71¢ | 850 / 466 / 384 | 686 / 374 / 312 | 492 / 263 / 229 |
| 63¢ | 741 / 357 / 384 | 597 / 285 / 312 | 434 / 205 / 229 |
| 50¢ | 599 / 215 / 384 | 482 / 170 / 312 | 354 / 125 / 229 |
| 25¢ | 474 / 90 / 384 | 382 / 70 / 312 | 278 / 49 / 229 |
| 10¢ | 412 / 28 / 384 | 335 / 23 / 312 | 247 / 18 / 229 |

## YES versus NO

| Price | Side | Qualifying events | Opposite side won |
|---|---|---:|---:|
| 96¢ | YES | 5142 | 199 (3.87%) |
| 96¢ | NO | 5241 | 185 (3.53%) |
| 97¢ | YES | 5096 | 153 (3.00%) |
| 97¢ | NO | 5215 | 159 (3.05%) |
| 98¢ | YES | 5056 | 113 (2.23%) |
| 98¢ | NO | 5172 | 116 (2.24%) |

## Limits and use

This is a descriptive candle study, not a trading backtest or profit forecast. It includes the last 30 seconds when your bot is not allowed to buy. Late inevitable winners can make settlement win rates look more favorable than an executable entry strategy. Price improvement, fees, latency, partial fills, queue position, depth, cash, orders and rebuys are not simulated. A threshold touch does not prove an entry at exactly that price could fill. Existing bid/ask, volume and open-interest candles are retained; historical full-depth order books and underlying Bitcoin tick paths are not provided by these candles. Do not interpret a traded-price crossing as proof a protective limit sell would execute. No settings, services, credentials or production journals were changed.

Artifacts: THRESHOLD_10000.json (summaries, side splits, coverage and exclusions); THRESHOLD_10000_PAIRS.csv (each side/threshold event and confirmed/possible low); THRESHOLD_10000_MINUTES.csv (all final-four-minute candles, trade/quote ranges, volumes and open interest); research/threshold_10000_data (discovery and compressed raw public candles). Six synthetic analysis tests passed.

Sources: [Kalshi batch candles](https://docs.kalshi.com/api-reference/market/batch-get-market-candlesticks), [historical markets](https://docs.kalshi.com/api-reference/historical/get-historical-markets).
