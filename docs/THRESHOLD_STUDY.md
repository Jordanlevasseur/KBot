# Bitcoin 15-minute buy/sell threshold research

## Conclusion

This screen found a research candidate, not a proven live optimum: buy at executable asks from 92 through 96 cents and latch a 50-cent protective sell limit. Keep the live configuration unchanged pending finer-resolution and forward simulation. Current baseline is 95–99-cent asks with a 71-cent trigger and limit. The study does not justify calling either pair the safest or best possible.

## Coverage and model

The study downloaded all 215 settled KXBTC15M markets from 2026-11-12T13:00:00-08:00 through 2026-11-14T18:30:00-08:00, including markets the bot never traded. All had usable data; none were dropped. There were 218 unauthenticated public GET requests. Both YES and NO were evaluated symmetrically. No credentials, account API calls, trading-service restarts, production journal writes, or application changes were used.

This is a MINUTE-RESOLUTION QUOTE SCREEN, not an exact replay of the live bot. Entry decisions use the historical opening executable ask at 240, 180, 120 and 60 seconds remaining. Every simulated buy is outside the closing 30 seconds. The history does not supply an exact 30-second quote, continuous sequenced books, queue positions, or depth; these were not invented. The final minute high/low spans 60–0 seconds, and is used only to assess protective exits on positions already held. Protective exits can continue until close.

The model uses fixed 10-share purchases with assumed funding and full size available at quoted prices. It charges standard multiplier-1 taker fees on all buys and sells, rounded upward to $0.0001, with no settlement fee. Current series metadata confirms quadratic fees and multiplier 1; historical event overrides are not separately reconstructed. Current 71-cent baseline is applied consistently to the entire period; it does not recreate older live sessions that used 86 cents. Dollar results are simulated and must not be confused with actual account P/L.

Two execution assumptions were tested: (1) touch-limit assumes the sell fills at its limit when the minute range permits; gaps, queue competition and missing depth can invalidate that assumption; (2) next-open recovery latches the stop and waits for a later observed minute-opening bid at or above the limit; otherwise the position settles. Neither scenario is a rigorous upper/lower bound because re-entry paths differ. One open-time decision per minute means intraminute repeat purchases and partial fills are not recreated.

## Selection and later-market check

The 1675 configurations cover buy minima 90–99 cents, buy maxima 95–99 cents, protective thresholds 50–90 cents, and separate hold-to-settlement benchmarks. There are 129 early training markets, 43 middle validation markets and 43 later check markets beginning 2026-11-14T08:00:00-08:00.

Protected candidates had to improve training profit without increasing closed-market drawdown in both execution assumptions, with a minimum activity requirement. All 876 training-qualified protected configurations received validation; validation also required no lower profit or greater drawdown than the baseline in either assumption. No-stop configurations were excluded from protected selection. The chosen protected parameters were determined from training/validation summaries rather than ranked on the later check.

Protocol disclosure: an initial exploratory run allowed no-stop configurations to compete and selected a 91–96-cent hold-to-settlement case. A protected-only run initially validated only the top 20 training settings; similar variants crowded out other buy bands. The final screen corrected that restriction to validate all training-qualified protected settings. Earlier outputs are retained in THRESHOLD_HOLD_BENCHMARK_EXPLORATION.json and THRESHOLD_TOP20_PROTOCOL.json. Since protocol refinements occurred during this research, the later-period result is exploratory chronological validation, not a pristine preregistered test or a substitute for fresh forward data.

| Configuration | Early training net | Middle validation net | Last 43 markets net | Last 43 buy cycles | Last 43 stop triggers |
|---|---:|---:|---:|---:|---:|
| Current: buy 95–99, sell 71 | $-25.45 to $-10.34 | $+6.44 | $-5.35 to $+0.15 | 25 | 2 |
| Candidate: buy 92–96, sell 50 | $-1.50 to $-1.19 | $+9.91 | $+8.25 | 15 | 0 |

The candidate had 15 winning simulated positions in the later 43 markets, but its 50-cent stop was NEVER triggered there. That check supports its selected entries in this small sample; it does not validate execution of a 50-cent protective sale. The lower stop also permits substantially larger loss before an attempted exit, and an unfilled limit can still lose the entire entry cost. The chosen threshold is at the lower edge of the tested protective range, another reason not to call it an established optimum.

## Whole-period descriptive results

Across all 215 markets, baseline simulated net was $-24.36 to $-3.75; the candidate was $+16.65 to $+16.96. Baseline closed-market drawdown was $+17.43 to $+34.48; candidate drawdown was $+12.20 to $+12.51. These include the fitting period and are not independent validation. Drawdowns are measured at market closures, not every intraminute mark-to-market movement.

The candidate had 73 buy cycles / 69 wins / 4 losses in the touch-limit scenario and 72 buy cycles / 70 wins / 2 losses in next-open recovery. Worst candidate market loss was $4.77 under touch-limit and $9.60 under recovery. The differing paths demonstrate why a high win rate or apparently better stop number is not enough to establish safety.

## Exploratory attribution

These extra comparisons were examined after selection, so they are descriptive and are not additional independent validation.

| Buy ask range | Stop/limit | Last 43 simulated net | Whole-period simulated net |
|---|---:|---:|---:|
| 95–96 | 71 | $-0.98 to $+0.47 | $-7.72 to $-1.36 |
| 92–96 | 71 | $-0.88 to $+6.66 | $+4.92 to $+13.04 |
| 92–96 | 50 | $+8.25 | $+16.65 to $+16.96 |
| 95–99 | 50 | $-3.76 to $+1.06 | $-18.16 to $-6.03 |
| 95–96 | 50 | $+2.06 | $-6.13 to $+3.52 |
| 92–96 | Hold to settlement | $+8.25 | $+17.20 |

The improvement depended on both entry selection and exit behavior; simply lowering the current stop to 50 cents did not produce a consistent advantage in both fill scenarios. Holding the candidate entries to settlement produced the same later-period return because no 50-cent stops triggered there. That benchmark is not a recommendation to remove protective selling.

## Statistical and practical limits

Paired moving-block resampling of the last 43 markets used four-market blocks and 2,000 resamples. Candidate-minus-baseline net intervals were approximately +$0.61 to +$17.81 for touch-limit and +$0.43 to +$35.79 for next-open recovery. These are conditional on this small, fitted historical sample and the model. They do not account for protocol changes, all multiple-testing effects, execution-model error, structural market changes, or future liquidity, and are not a live profit forecast.

Only about 2.2 days were observed, only 15 candidate trades occurred in the later block, and minute sampling omits many eligible prices and rapid protective events. The fee, full-depth, partial-fill, latency, reconnection and funding behaviors of the real bot were not replayed. A finer-resolution shadow/paper study using actual sequenced bid/ask updates, quantities, fees and unresolved-order behavior is required before recommending a live threshold change.

## Artifacts and checks

- THRESHOLD_QUOTES.csv: all 860 minute quote rows for both sides, with market results and time remaining.
- THRESHOLD_STUDY.json: detailed model, splits, selected configuration and conditional uncertainty.
- THRESHOLD_TRAIN_VALIDATION.csv: all 1,675 configurations and training/validation net results.
- THRESHOLD_MARKET_BASELINE.csv: baseline and frozen-candidate per-market results.
- THRESHOLD_ABLATIONS.json: post-selection entry/exit attribution.
- research/threshold_data: private compressed public source candles and market discovery manifest.
- Ten synthetic research tests passed: fees, settled wins, side symmetry, unfilled gap-downs, latched recovery, scenario distinction, final-30-second refusal, selection isolation, candidate coverage, NO quote conversion/missing values and closed-market drawdown.

Sources: [Kalshi historical candles](https://docs.kalshi.com/api-reference/market/get-market-candlesticks), [market discovery](https://docs.kalshi.com/api-reference/market/get-markets), [fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf).
