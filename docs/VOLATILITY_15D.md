# Fifteen-day Bitcoin contract volatility diagnostic

Period: 2026-10-31T12:00:00-07:00 through 2026-11-15T11:00:00-08:00. Start exclusive; end inclusive. Anchored to Kalshi HTTP server Date, with one completed-quarter buffer. Pacific conversion includes daylight-saving change.

Coverage: 1424 settled markets out of 1,440 quarter-hour slots; 21360 valid minute candles; no missing candles in returned markets. The 16 absent slots close 00:15–02:00 Pacific on November 5 and 12. The API did not return settled markets for those slots; their cause was not verified.

Metric: mean of each one-minute trade high minus low, in cents. This is a range measure, not return standard deviation, net movement, account P/L, or a fill simulation. YES and complementary NO have equal ranges and are not counted twice. Minutes grouped by their starting Pacific hour. Markets traded by the bot and untraded markets receive equal treatment. No credentials or account APIs used. No runtime settings or services changed.

## Closing four minutes by hour

| Pacific hour start | Mean minute range | Markets |
|---|---:|---:|
| 16:00 | 18.06¢ | 60 |
| 11:00 | 17.36¢ | 56 |
| 03:00 | 16.20¢ | 60 |
| 02:00 | 15.36¢ | 60 |
| 04:00 | 15.33¢ | 60 |
| 12:00 | 14.87¢ | 60 |
| 10:00 | 14.83¢ | 60 |
| 00:00 | 13.94¢ | 52 |
| 07:00 | 13.78¢ | 60 |
| 05:00 | 13.74¢ | 60 |
| 08:00 | 13.71¢ | 60 |
| 13:00 | 13.65¢ | 60 |
| 06:00 | 13.35¢ | 60 |
| 21:00 | 12.84¢ | 60 |
| 23:00 | 12.69¢ | 60 |
| 15:00 | 12.58¢ | 60 |
| 18:00 | 12.57¢ | 60 |
| 17:00 | 12.47¢ | 60 |
| 14:00 | 12.36¢ | 60 |
| 09:00 | 12.27¢ | 60 |
| 20:00 | 11.77¢ | 60 |
| 22:00 | 11.61¢ | 60 |
| 01:00 | 11.48¢ | 56 |
| 19:00 | 9.95¢ | 60 |

## Within the full cycle

| Minutes remaining at start | Mean one-minute range | 95th percentile |
|---|---:|---:|
| 15 | 18.29¢ | 31.00¢ |
| 14 | 16.11¢ | 29.00¢ |
| 13 | 15.08¢ | 28.00¢ |
| 12 | 14.27¢ | 27.00¢ |
| 11 | 14.04¢ | 26.00¢ |
| 10 | 14.58¢ | 27.20¢ |
| 9 | 14.84¢ | 29.00¢ |
| 8 | 14.74¢ | 31.00¢ |
| 7 | 14.07¢ | 32.00¢ |
| 6 | 14.05¢ | 33.00¢ |
| 5 | 14.66¢ | 38.00¢ |
| 4 | 14.46¢ | 41.00¢ |
| 3 | 14.50¢ | 46.00¢ |
| 2 | 15.81¢ | 63.00¢ |
| 1 | 9.66¢ | 70.90¢ |

## Interpretation and limits

4–5 PM leads closing-four-minute average ranges (18.06¢); 11 AM–noon is second (17.36¢). Across full cycles 4–5 PM also leads (16.06¢), followed by 11 AM–noon (15.70¢). Differences are descriptive, not statistically established recurring schedules. Only 15 days are covered; minutes within markets and adjacent markets are dependent. No multiple-comparison correction or independent forward test is supplied. Lower average range does not imply safer trades or higher profit. These measures cover all price levels, not only 92–99¢ opportunities.

The first minute has the highest whole-cycle average range (18.29¢). In the closing window 2–1 minutes leads (15.81¢). Final-minute average is 9.66¢ with median 0.1¢ but 95th-percentile range 70.9¢; most settle quietly but a minority swing sharply. Minute candles cannot distinguish 60–30 seconds from the last 30 seconds. No exact turning points or guaranteed executable depths are inferred.

Source: [Official Kalshi batch candles](https://docs.kalshi.com/api-reference/market/batch-get-market-candlesticks). Raw batches and discovery manifest are in research/volatility_15days; full summaries in docs/VOLATILITY_15D.json.
