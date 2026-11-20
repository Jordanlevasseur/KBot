# Unfilled protective sell: November 14, 2026, 2:30 AM Pacific

Sources: consistent read-only production SQLite transactions, the local desktop log, and Kalshi's unauthenticated public market-trades endpoint. No credentials, account requests, exchange writes, service restarts, or production journal modifications were used for this investigation.

Market: KXBTC15M-26NOV140530-30. Fifteen YES contracts filled at $0.98 at 02:29:45.329642 Pacific, costing $14.70 with zero recorded entry fees. The protective rule latched. A correct YES ask at $0.86 for all 15 contracts was durably recorded locally at 02:29:50.912273; the exchange's saved order has creation time 02:29:51.326398. It used good_till_canceled with expiration at 02:30:00, no post-only or fill-or-kill setting, and the exchange auto-routed it to shard 2. All recorded sell fills total zero. Its canceled timestamp is 02:30:00.000010, consistent with scheduled expiry. The stored order does not include a cancellation-reason field, so expiry is an inference from the exact time match. Settlement at 02:30:04.590142 was NO, with zero revenue.

## Public tape evidence

Endpoint: https://external-api.kalshi.com/trade-api/v2/markets/trades

Parameters: ticker=KXBTC15M-26NOV140530-30, min_ts=1794652170, max_ts=1794652200, limit=1000. Eight cursor pages returned the complete window: 7,633 unique public trades from 02:29:30.008228 to 02:29:59.984549 Pacific. Public requests carried no account authentication. The first preview request overlapped the first page and was not double-counted.

The last non-block trade at or above 86 cents was exactly 86 cents at 02:29:48.386852, about 2.94 seconds before the protective order's exchange creation. During 02:29:49, public YES trades ranged from 10 to 58 cents; during 02:29:50, they ranged from 5.9 to 24 cents. After the sell's exchange creation, 2,011 non-block trades ranged from 0.1 to 7.7 cents, with none at or above 86 cents. The first following trade at 02:29:51.332219 was 5.7 cents. Thus the order required a sale price far above subsequent observed trading and received no match before cutoff. Public executions are evidence of market prices, not a reconstruction of every bid, queue position, or hypothetical earlier fill.

## Timing limits

The saved audit reports a position-reconciliation mismatch at 02:29:46.413854 and a 404 order-read error at 02:29:47.038338. These preceded the sell's submission. Current code waits for a coherent reconciled account before transmitting protective exits. The retained 10,001-event market diagnostic ring no longer covers this market, and the 100 saved account snapshots also roll over. No service-journal entries were found for the short window. We cannot recover the first observed held-side bid crossing, its exact time, the account lease state on each cycle, or queue depth. Therefore we cannot assign the approximately six seconds between entry fill and sell acceptance wholly to application delay or prove an earlier order would have filled. A market-data gap or reconciliation delay remains possible; it is not established as the sole cause.

## Confirmed conclusion

The exchange recorded the correct sell side, 86-cent minimum, and full 15-share quantity; the order was not rejected for authentication or a disabled gateway. It remained entirely unfilled and ended at its scheduled expiration. The public tape confirms a collapse far below the permitted sale price before acceptance, followed by no trades meeting that minimum. Lowering the trigger would wait for a later crossing; it is different from permitting a lower exit limit after an earlier trigger. Neither setting guarantees a fill. No trading rule was changed as part of the investigation.
