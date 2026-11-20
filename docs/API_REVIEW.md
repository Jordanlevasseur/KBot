> Superseded policy note: the user now permits cheaper fills. Submitted buy limits
> remain 95c–99c. The former strict fill-floor discussion below is historical.
> Current execution and acceptance instructions are in [TRADING.md](TRADING.md).

# Official Kalshi API review

Reviewed 2026-11-11 using official documentation. No account credentials supplied,
no authenticated requests sent, no orders submitted.

| Topic | Verified interface / implication |
|---|---|
| Demo | Separate mock funds and credentials; REST root `https://external-api.demo.kalshi.co/trade-api/v2` |
| Production | REST root `https://external-api.kalshi.com/trade-api/v2`; intentionally unused |
| Authentication | Ed25519 recommended or RSA-PSS SHA256; headers carry key ID, millisecond timestamp, and base64 signature over timestamp + HTTP method + path without query |
| Orders | Current V2 `POST /portfolio/events/orders`; fixed-point dollar price and string contract quantity, client order ID, expiry, time-in-force, post-only, pause cancellation, reduce-only |
| Direction | V2 order prices are YES-leg prices. `bid` buys YES; `ask` sells YES, economically buying NO at the complement price. A future adapter must translate NO carefully, rather than reuse legacy action/side payloads |
| Book | `/markets/{ticker}/orderbook`, `orderbook_fp.yes_dollars` and `no_dollars`; bid-only books, ask inferred as one minus the opposing bid |
| Discovery | Paginate `/markets` with series filter and status; series identity and contract rules require independent validation; do not identify by title alone |
| Portfolio | Balance, positions, orders, fills, and settlements must all be paginated/reconciled as applicable; closed unsettled positions are still exposure |
| Rate limiting | Separate token-based read/write budgets, additional balance limits; endpoint costs and current account tier must drive scheduling |

## Blocking price constraint

The official help page describes a buy limit as a **maximum** acceptable price and
explicitly allows cheaper execution. Current V2 fields contain no minimum buy-fill
price. Immediate-or-cancel and fill-or-kill constrain duration/quantity rather than
impose an execution-price floor. Book snapshots cannot eliminate the race between
observation and execution. After-the-fact fill verification cannot undo a prohibited
purchase. No external order path is enabled in this application.

`post_only` exists, but the reviewed order schema does not establish the full
required guarantee for every eventual maker fill and every supported market price
grid. This implementation does not silently switch to a maker-only strategy or
claim that this flag solves the constraint. If Kalshi confirms an enforceable
mechanism that guarantees only the five requested fill prices, it can be evaluated
with dedicated integration tests before implementation.

## Credentials for a future integration

Do not put a key in this repository, state directory, terminal command line, logs,
or chat. The current simulator neither needs nor accepts one. A future integration
should use a separate revocable demo key first; production needs separate explicit
authorization. Select the narrowest scopes the account exposes and verify their
actual permissions. Do not assume all account/key types expose identical scopes.
Use a dedicated restricted service account and private credential directory outside
the checkout (directory 0700, private key 0600), ideally systemd encrypted credentials.
Mount only that key into the isolated process, validate ownership/modes and reject
symlinks. Never mount an entire home directory. Withdrawal/deposit/transfer endpoints
must remain absent. A future network-capable service needs an independently tested
OS-enforced destination policy; the current offline network namespace must not be
removed to obtain connectivity.

## Sources

- [API keys](https://docs.kalshi.com/getting_started/api_keys)
- [Demo environment](https://docs.kalshi.com/getting_started/demo_env)
- [Create Order V2](https://docs.kalshi.com/api-reference/orders/create-order-v2)
- [Order direction](https://docs.kalshi.com/getting_started/order_direction)
- [Market order book](https://docs.kalshi.com/api-reference/market/get-market-orderbook)
- [Markets](https://docs.kalshi.com/api-reference/market/get-markets)
- [Rate limits](https://docs.kalshi.com/getting_started/rate_limits)
- [Positions](https://docs.kalshi.com/api-reference/portfolio/get-positions)
- [Limit order execution](https://help.kalshi.com/en/articles/13823811-limit-orders)
- [Fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf)
