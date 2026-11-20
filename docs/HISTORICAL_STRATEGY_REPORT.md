# Historical strategy report — 2026-11-19

## Result

The available price history does **not** identify a stable best combination of entry window, buy threshold, and protective sell limit. The historical winner changes when the period changes. It would be misleading to change the live bot to the highest-profit historical combination.

This report did not change settings, start trading, submit orders, or alter account history.

## Data used

The replay used **4,989** settled Kalshi Bitcoin 15-minute markets from the local archived public-candle collection. Each included at least 15 minutes of one-minute YES bid/ask history and a recorded settlement. The archive manifest listed 10,000 markets but only 3,723 compressed files were present; 4,989 individual markets were usable because some files contain batches. Markets without sufficient history were excluded.

The bot's local diagnostic database also contains 407,093 market observations, 103,559 volatility readings, and 74,953 strategy-decision records across about 10 days. Those records are useful for reviewing the bot's actual behavior. They do not provide a public, settled, 30-second historical order book for every archived market, so they cannot prove a universal volatility-filter optimum.

Kalshi's public candles endpoint provides historic candles, while the normal orderbook endpoint describes a current book. The historical archive therefore cannot prove queue position or a specific limit-order fill. Kalshi's documented fee schedule was included using the general taker formula `round up(0.07 × contracts × price × (1 − price))`; maker/taker classification and product-specific exceptions cannot be reconstructed from the candle data. [Kalshi candlestick API documentation](https://docs.kalshi.com/api-reference/market/get-market-candlesticks) and [fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf).

## Replay rules

- Fixed 10-contract position, one first eligible entry per market, no rebuys and no compounding.
- Eligible entry: first observed one-minute opening executable ask from the selected lower threshold through 99¢, inside a 3-, 4-, or 5-minute entry window; final 30 seconds excluded.
- YES and NO are evaluated equally; the higher eligible implied probability receives the deterministic tie-break.
- A protective stop latches when the observed executable bid is at or below the sell limit.
- A protective sale is counted only when a **later minute-opening executable bid** is at or above that limit. A candle touching the level is not counted as a fill.
- An unfilled stop holds to settlement.
- This is conservative about stop fills, but still cannot model sub-minute order sequence, quantity at the price, queue priority, partial fills, latency, or slippage.

The 4,989 markets were split chronologically: 60% training, 20% validation, and an untouched final 20% holdout. The holdout figures below are descriptive; they were not used to select a setting.

## Entry-window comparison: best hindsight result in the untouched holdout

| Entry window | Hindsight buy range | Stop limit | Entries | Net P/L | Maximum drawdown | Stops triggered / confirmed filled / unfilled |
|---:|---:|---:|---:|---:|---:|---:|
| 3 minutes | 96–99¢ | 80¢ | 431 | +$23.10 | $25.18 | 8 / 4 / 4 |
| 4 minutes | 96–99¢ | 80¢ | 540 | +$30.88 | $24.61 | 14 / 9 / 5 |
| 5 minutes | 96–99¢ | 80¢ | 632 | +$53.92 | $23.96 | 15 / 10 / 5 |

These are **hindsight-only** combinations. Their earlier results were negative. For example, the 5-minute / 96–99¢ / 80¢ row lost $28.17 in training and $13.91 in validation before making $53.92 in the holdout. It is not evidence that 80¢ or five minutes will work going forward.

## Buy-threshold comparison: best hindsight configuration per threshold

All rows below are the best 3/4/5-minute and tested stop combination selected using the final holdout itself. They show the trade-off, not a recommendation.

| Buy lower limit | Window | Stop | Holdout net P/L | Drawdown | Entries | Wins / losses |
|---:|---:|---:|---:|---:|---:|---:|
| 92¢ | 5 min | 80¢ | +$14.05 | $30.52 | 803 | 765 / 38 |
| 93¢ | 5 min | 80¢ | +$28.73 | $27.95 | 780 | 747 / 33 |
| 94¢ | 5 min | 80¢ | +$31.40 | $27.70 | 741 | 714 / 27 |
| 95¢ | 5 min | 80¢ | +$45.10 | $20.73 | 694 | 675 / 19 |
| 96¢ | 5 min | 80¢ | +$53.92 | $23.96 | 632 | 618 / 14 |
| 97¢ | 5 min | 80¢ | +$25.01 | $26.38 | 541 | 527 / 14 |
| 98¢ | 5 min | 80¢ | +$18.31 | $19.08 | 377 | 371 / 6 |
| 99¢ | 5 min | 86¢ | +$3.44 | $1.39 | 53 | 52 / 1 |

The pattern is sensible as a description of this sample: lower thresholds create more opportunities and larger drawdowns; 99¢ creates few opportunities and still has an occasional full loss. But the rows are optimized after seeing the outcome period and therefore cannot establish that 96¢ is objectively best.

## Current-policy context

The current saved policy is 96–99¢, a 4-minute window, final 30-second blackout, and an 86¢ protective limit. On the archived replay it produced:

| Period | Net P/L | Max drawdown | Entries | Wins / losses | Stops triggered / confirmed filled / unfilled |
|---|---:|---:|---:|---:|---:|
| Training | −$27.53 | $64.24 | 1,738 | 1,686 / 52 | 76 / 20 / 56 |
| Validation | −$35.81 | $63.99 | 543 | 525 / 18 | 19 / 7 / 12 |
| Holdout | +$13.63 | $24.07 | 540 | 524 / 16 | 19 / 11 / 8 |

The important result is the sign reversal: positive holdout performance does not cancel two earlier losing periods.

For comparison, 96–99¢ / 4 minutes / 71¢ was negative in training (−$35.27) and validation (−$30.82), then +$9.94 in holdout. Lowering the stop did not make the sample stable. Holding to settlement also lost money in the earlier small study. A stop limit is a risk-control order, not a reliable way to manufacture positive expectancy.

## Volatility

The local bot history can calculate its own 30-second high-minus-low range every second, plus 1-, 5-, and 15-minute averages. The public archive is minute-resolution and cannot recreate those 30-second readings. Any claim that a particular 30-second volatility cutoff would have maximized historic profit across all 4,989 markets would be invented.

Use the local history to assess how a selected cutoff affects actual bot entries only after it has collected sufficient settled markets under unchanged policy. A sound future comparison should freeze the entry/exit policy, divide markets chronologically, and evaluate only the later period. The log system now keeps the required reading, decision, and order evidence to do that.

## What this supports

- Four and five minutes generated more historical opportunities than three minutes in this replay.
- A final-30-second entry blackout was retained in every configuration.
- Stops frequently did **not** have a confirmed historical fill under the conservative rule. In the 4-minute hindsight winner, 5 of 14 triggered stops remained unfilled and went to settlement.
- The data supports treating stop thresholds as downside controls, not as profit settings.

## What it does not support

- Changing the live bot to 96¢ / 80¢ / 5 minutes based on this study.
- Claiming that any stop would have filled whenever a one-minute candle touched it.
- Claiming a best 30-second, 1-minute, 5-minute, or 15-minute volatility cutoff from minute candles.
- Claiming this replay proves real-money profitability.

## Reproducible files

- `research/full_historical_replay.py` — read-only replay source.
- `docs/FULL_HISTORICAL_REPLAY.json` — every tested configuration and chronological split.
- `docs/FULL_HISTORICAL_REPLAY.csv` — flat table for inspection.

No bot configuration was changed by this report.
