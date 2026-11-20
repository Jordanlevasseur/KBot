# Trading candidate: revised price rule

The user approved cheaper fills: **submitted buy limits must be 95c, 96c, 97c,
98c, or 99c; actual fills may be cheaper.** This supersedes the former strict
execution-price floor. The price-policy blocker is resolved.

A separate execution candidate now implements authenticated V2 creates/cancels,
YES/NO complement prices, an on-disk order journal, partial-fill reconciliation,
two filled buy orders per market, persistent 86c stops, settlement records, and
same-client-ID recovery after uncertain submissions. The read-only account
monitor remains read-only. Nothing is installed, connected, or enabled automatically.

## Current validation boundary

Offline tests and mock HTTP/Unix-socket tests are not official demo acceptance.
No actual demo account is configured, no real Kalshi account has been accessed,
and no real-money trades have been tested. This candidate is **not production
validated**. Live trading must remain off until demo acceptance and administrator
installation are complete. Production authorization flags are manual gates, not
a certification that those checks have passed.

## Local setup and demo acceptance

First follow [account setup](ACCOUNT_SETUP.md) for local credential protection and
the administrator-reviewed gateway installation. Use a **demo trading key**, not
a production key. Grant only required trading/read permissions if configurable.
The earlier read-only key may not authorize create/cancel requests.

Install the updated reviewed source, the new launcher, and explicitly provision the
demo gateway with narrow order writes enabled (administrator commands; not run here):

```sh
sudo install -m 0644 kalshi_bot/*.py /opt/kalshi-bot/kalshi_bot/
sudo install -m 0755 deploy/run-trading.sh deploy/provision-gateway.py /opt/kalshi-bot/deploy/
sudo /usr/bin/python3 /opt/kalshi-bot/deploy/provision-gateway.py --environment demo --client-user "$USER" --allow-trading
```

Without `--allow-trading`, the gateway still refuses order writes. The OS destination
and file isolation rules remain unchanged. Only the V2 create and single-order cancel
paths are added, with ticker, direction, price, quantity, execution flags, and expiry
validation. There is no transfer/withdrawal/deposit API.

Offline preflight makes no connection:

```sh
./deploy/run-trading.sh "$HOME/.config/kalshi-bot-demo" demo
```

After explicit approval for demo access, start the demo gateway, then launch the demo
trader with `--connect --trade --fee-reserve VALUE`. `VALUE` is a verified **maximum
fee in dollars per contract** covering the applicable series/event fees, cheaper
fills, and fragmented fills. There is deliberately no guessed external fee default.
Verify it from the official current fee schedule and market-specific multipliers.
The fee reserve is added to the limit price; whole-contract quantity fits within
50% of reported cash. A larger reserve reduces purchased quantity. The code does
not independently certify that an operator-provided reserve matches future fees.

In the terminal, `s` starts new entries, `p` pauses entries and requests cancellation
of residual buy quantities, and `q` exits. Protective exits continue while entries
are paused; they stop processing when the application exits. A resting sell remains
on the exchange until its expiry/cancellation. An unconfirmed buy cancellation must
be reconciled after restart. `--headless` explicitly starts entries without a terminal.

The execution journal is `trading.sqlite3` in a state directory separate from the
simulation and account-monitor databases. Its identity is bound to environment and
public-key fingerprint. Do not remove this state to recover a real account.

## Execution and recovery behavior

- Entry requires an open binary KXBTC15M market, more than 30 and at most 240
  seconds remaining, an eligible ask,
  whole-contract quantity, fresh cash, and no existing position/order. There is
  no per-market purchase-count cap. A sub-cent ask within 95–99c rounds up to an allowed whole-cent limit.
- The official cutoff/status is refreshed before entry. Buy orders expire 30
  seconds before cutoff; protective sell orders expire at cutoff. Local time
  must be synchronized with NTP.
- A buy counts once on the first confirmed fill, including partial fills. Retries
  and zero-fill rejected orders do not consume a buy. Complete liquidation
  is required before every re-entry. The completed-purchase count remains recorded.
- YES and NO are translated to V2 YES-leg prices and bid/ask directions. A NO buy
  at 96c is an ask on the YES book at 4c; a NO exit at 86c is a YES bid at 14c.
- A bid <=86c latches the stop in SQLite. Residual buys must be confirmed canceled
  before an exit is created. The exit limit stays 86c after rebounds. Valid resting
  sells are maintained; terminal zero/partial exits can be retried on later cycles.
- Partial fills may be fractional even when buy quantities are whole. Exits use
  the exact remaining quantity to avoid leaving dust. Verify venue quantity precision
  during demo acceptance; unsupported precision fails closed.
- POST is journaled before submission. An uncertain create is reconciled by reads
  and is never automatically replayed: client IDs can become reusable after an
  order closes. The original intent remains unresolved until exchange evidence
  appears or explicit reconciliation resolves it. The gateway never tries a
  second IP after an uncertain write. Definitive 429 rejections retain the broker
  cooldown before another fresh, eligible attempt.
- A DELETE response is not treated as proof that cancellation is complete. Orders,
  cumulative fills, individual fill records, and actual positions must reconcile
  before another order. A lagging read/feed pauses execution instead of guessing.
- Unmanaged positions or open orders block execution. Use a dedicated account with
  netting enabled; do not manually trade the same account while this bot runs.
  Resting GTC exits cannot use the current API's IOC-only reduce-only option, so
  concurrent external account changes are not supported.
- Confirmed settlements clear journal exposure and preserve the raw settlement
  record. API balance is refreshed and logged. Fills preserve actual prices and fees.

An expired ambiguous submission, archived/missing historical orders/fills, account
mismatch, or unsupported schema requires manual reconciliation. It is not safe to
invent terminal order states. Automatic recovery covers known, visible orders;
it does not guarantee unattended recovery from every historical-data outage.

## Acceptance checks before production

Use only demo funds to verify: real API field formats; series/event fee reserve;
account netting; partial fills and fractional exits; rejected/canceled orders; timeout
and process-kill recovery; persistent stop/rebound behavior; market closure and
settlement; and the deployed gateway's ACL/systemd behavior. Retain journal evidence.
The synthetic tests cannot substitute for these venue-specific checks.

Only after these pass should production installation and a separate production key
be considered. Production requires all three explicit client flags `--connect
--trade --authorize-live-orders` and an administrator-enabled production trading
gateway. No production command has been executed or authorized during development.

The optional `deploy/kalshi-trading-demo.service.example` provides bounded resources
and restart-on-failure for a later administrator-reviewed demo deployment. Its
placeholders deliberately prevent unattended activation. The user, credential
folder ownership, state directory ownership, gateway dependency, and verified fee
reserve must be configured explicitly. This service example has not been deployed.

The current candidate uses REST polling, not an order-book WebSocket stream.
It latches stops on observed executable bids; a brief crossing entirely between
polls or during a disconnection can be missed. This remains a material limitation
relative to continuous stop monitoring and must be addressed/evaluated before a
production launch. A limit sell still cannot guarantee execution at 86c.
