# Public-price dips before same-side winning re-entry

All dates: November 14, 2026. Times: America/Los_Angeles (Pacific). The four cases are the same YES -> sell -> YES -> winning settlement sequences identified in the preceding local journal audit.

| Market close | First fill | Lowest public YES trade before re-entry | Low time | Winning re-entry fill | Lowest public YES trade after the sale and before re-entry |
|---|---:|---:|---|---:|---:|
| 00:15 | 96 cents | 73 cents | 00:13:34.501 | 97 cents | 86 cents |
| 01:45 | 96.6 cents | 74 cents | 01:43:40.103 | 97.9 cents | 78 cents |
| 02:45 | 91.8 cents | 87 cents | 02:42:50.809 | 97 cents | 89 cents |
| 04:00 | 95.9 cents | 86 cents | 03:59:28.425 | 98.4 cents | 86 cents |

Read-only local fills define exact intervals from first entry fill through second entry fill: 00:12:38.528272–00:13:49.044454; 01:41:36.947644–01:44:04.461749; 02:42:45.048476–02:42:57.391373; 03:59:19.941586–03:59:33.442408. All prices in the table exclude fees.

Source: unauthenticated HTTPS GET https://external-api.kalshi.com/trade-api/v2/markets/trades with ticker, min_ts=floor(first entry fill), max_ts=ceil(second entry fill), limit=1000 and cursor pagination. Tickers respectively: KXBTC15M-26NOV140315-15, KXBTC15M-26NOV140445-45, KXBTC15M-26NOV140545-45, KXBTC15M-26NOV140700-00. All pages were completed. Exact timestamps were filtered back to the fill interval; trade IDs were deduplicated and block trades excluded. Counts were 5,864, 6,617, 1,318, and 2,268 non-block trades (16,067 total) across 19 public API requests. No authenticated account requests, credentials, exchange writes, or production database writes were used.

These are the minimum completed trade prices during the specified intervals, not the minimum executable bids or minima of the whole 15-minute market. A bid can be lower than the lowest completed trade; an 87-cent trade minimum does not prove the 86-cent bid trigger never occurred. This data cannot establish a safer stop threshold or guarantee a hypothetical exit. Prices below 95 cents reflect the previously authorized price improvement on valid submitted buy limits.
