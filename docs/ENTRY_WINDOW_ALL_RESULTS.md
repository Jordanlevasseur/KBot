# All wins and losses: four-minute versus three-minute recorded entry windows

All saved completed purchase cycles in markets closing through November 14, 2026 18:15 Pacific; both wins and losses. Re-buys included in primary results and separately excluded for sensitivity.

Filter ACTUAL first and last confirmed fill times, not order-submission times. Require 30 < first-fill seconds remaining <= window and last fill >30 seconds remaining. Allocate actual sell proceeds, settlement payouts and actual fees by FIFO using full history before filtering. Preserve actual quantities and historical protective rules. No new balance sizing, delayed orders or replacement opportunities simulated.

The final 30 seconds are excluded from both comparisons. In-progress/later markets are excluded. No application setting or account order was changed.

| Recorded entry window | Closed purchases | Wins | Losses | Win rate | Net after fees |
|---|---:|---:|---:|---:|---:|
| 240 to >30 seconds remaining | 93 | 79 | 14 | 84.95% | $12.40 |
| 180 to >30 seconds remaining | 43 | 33 | 10 | 76.74% | $5.90 |

The additional 180–240-second band contained 46 wins and 4 losses and added $6.50 net. It contributed $12.7541 from winners and -$6.2556 from losses. Thus filtering out that band removes more recorded profit than recorded losses.

## First purchases only

| Recorded entry window | Closed first purchases | Wins | Losses | Win rate | Net after fees |
|---|---:|---:|---:|---:|---:|
| 240 to >30 seconds remaining | 85 | 74 | 11 | 87.06% | $12.45 |
| 180 to >30 seconds remaining | 35 | 28 | 7 | 80.00% | $5.95 |

## Excluded purchases

| Market | Seconds remaining at fill | Actual net after fees |
|---|---:|---:|
| KXBTC15M-26NOV121600-00 | 27.054 | $0.0300 |
| KXBTC15M-26NOV140530-30 | 14.670 | $-14.7000 |
| KXBTC15M-26NOV140600-00 | 12.227 | $0.1723 |
| KXBTC15M-26NOV140700-00 | 26.558 | $0.1191 |
| KXBTC15M-26NOV141130-30 | 29.542 | $0.5889 |

## Interpretation and limits

- This is an exact historical execution-time filter, not a counterfactual simulation of waiting and then entering.
- Some retained re-buys followed excluded first purchases. The first-purchase-only sensitivity removes this sequencing ambiguity; it does not generate new orders.
- Actual historical sell limits changed from 86 to 71 cents; results do not apply the current 71-cent stop retrospectively.
- Untraded markets and hypothetical delayed fills cannot be evaluated from owned fills alone. Full original sequenced books/queue history are unavailable.
- Changing earlier profits changes future cash and sizing; quantities are held at actual recorded values.
- Public trade prints cannot establish hypothetical executable asks, available size, fees, stops and queue allocation.

These records support keeping the four-minute window more strongly than a simple three-minute time filter. They do not prove a delayed-entry strategy would earn less or more. The missing counterfactual fill/quote evidence prevents that stronger conclusion.
