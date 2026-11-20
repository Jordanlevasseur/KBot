# Connect the read/write API key

The production gateway was installed and its local health response was verified
both from the laptop user and from the application's isolated worker namespace.
This verification used no private key and made no authenticated account request.

## In the desktop app

1. Close and reopen **Kalshi Bitcoin Bot** after the update so the new controls load.
2. Open **Account setup → Configure production API key…**. Enter your key ID and
   select the downloaded private-key file on this laptop. A read/write trading key
   is appropriate. The old read-only label referred to the old app mode, not key scope.
3. Select **Live trading • real money** in the mode selector.
4. Click **Check credentials offline** to validate format, permissions and isolation.
5. Click **Connect…**. This authenticates reads and reconciles the account. New buys
   remain paused; existing bot-owned positions can resume protective management.
6. Review the balance, market, positions and order state. Only click **Start live
   trading** and accept its separate real-money confirmation when you intend orders
   to be submitted. No real-money testing was performed during development.

Do not paste a private key into chat. Configuration stays in the app's private
production directory on the laptop. Existing credentials are never overwritten.
Use a dedicated revocable key with only required read/trade permissions if those
scopes are exposed by your account. Transfer/withdrawal/deposit APIs remain absent.

If the gateway needs a restart or update, choose **Install / repair connection
service…** while Live trading is selected. Your laptop requests administrator
permission locally. The installer installs only this app's gateway, dedicated user,
service units and scoped firewall policy; it never reads an API key or submits an
order. It checks an actual client health response before reporting success.

## Timer

Simulation now defaults to **1×**. Its clock uses elapsed monotonic time, including
SQLite work, rather than advancing a fixed number of seconds per loop. 5× and 15×
remain explicitly accelerated simulation choices. Account/trading countdowns use
the official market cutoff and update between worker messages. Keep the laptop's
system clock synchronized; exchange authentication also requires an accurate clock.

## Refresh and market rollover

The dashboard keeps the current contract and its official countdown visible even
outside the entry window and when neither side qualifies for a buy. Discovery
runs independently of account reconciliation, about every ten seconds and again
at the current contract's cutoff. If the filtered market list is empty, expired,
or future-only, it checks the official series with a bounded near-term close-time
filter. The old contract is excluded at its cutoff. Display and execution share
one canonical sequenced cache per market. Another market's cache is retained for
an existing protected position during rollover, without switching it away.

**Refresh now** requests another cycle without restarting the worker, changing
Start/Pause, or clearing the journal. Account-only modes refresh automatically five
seconds after a completed snapshot. Trading mode reconciles continuously with up
to a three-second idle interval. Independent stream notifications wake trading and display work immediately.
Display bursts coalesce to at most 20 updates/second, with a 0.5-second health
fallback; Tk renders queued updates every 50 ms, including during slow account
requests or when minimized. Full account reconciliation remains mandatory. See
[performance measurements](PERFORMANCE.md).
No mobile application or iPhone supplies prices. The dashboard separately shows
account status, market-data status, last successful data update, selected side,
automatic-purchase state and the last decision/skip reason. Account reads and
discovery share rate pacing. Minimized means still running; closing stops workers.

Idle data stays usable only while the official WebSocket peer answers Ping/Pong.
A local timer alone cannot refresh data validity. Sequence gaps, replayed snapshots,
delayed timestamps, unverified silence and disconnects invalidate entry quotes and
trigger a fresh subscription. Prices become unavailable while reconnecting, with
that status shown explicitly. A stale indicator does not prove trading has stopped;
known protective exits continue while the worker is open.

This version requires gateway stream protocol 2. The reviewed connection installer
checks that capability after an update; the desktop refuses a legacy trading
gateway rather than weakening freshness verification. Update the gateway only
after pausing entries and closing flat, so protection is not interrupted.

The header displays the confirmed session: **LIVE TRADING • ON**, **PAUSED**,
**DISCONNECTED**, **READ ONLY**, or an error/stale state. It no longer displays the
fixed “LIVE OFF BY DEFAULT” caption during an enabled session. Startup still uses
offline simulation; real-money entries need the existing explicit Start confirmation.

## Execution

Decimal executable asks are supported. For example, a NO ask of **98.4c** uses an
allowed **99c limit**, and may fill at 98.4c or better. Rounding the limit down to
98c would leave that order waiting while the ask stays above 98c. An ask above 99c
is skipped; it is never rounded down to bypass the maximum. Both sides use the
same checks and fee-inclusive sizing at the submitted limit.

The dashboard shows **price eligibility**, the official entry window, the current
execution stage, and the proposed limit separately. Price eligibility does not
mean account, authorization and freshness checks have finished. Timestamped
observations and stages are kept in the private journal's `execution_events` table
(about 10,000 recent events), with important decisions/failures also in its audit.
Failures include sanitized reasons, stage and HTTP status rather than class alone.
See [NO_MISS_REPORT.md](NO_MISS_REPORT.md) for the confirmed 4:30 PM investigation.

The dashboard shows **YES • BUY / SELL** and **NO • BUY / SELL** separately.
Buy prices are asks; sell prices are bids. **POSITION SELL BID** displays the
held side explicitly (for example, `NO 98.00c`). It says `No position` when flat,
and never defaults that field to YES. A quote for another market is not used for
the held position. Stop monitoring uses the held contract's own bid.

A 1.8c YES sell bid implies a 98.2c NO buy ask, not a 98.2c NO sell bid. The
NO sell bid is read independently from its side of the book; spreads matter.
See the official [order-book explanation](https://docs.kalshi.com/getting_started/orderbook_responses).
Each evaluated entry or
wait condition is recorded in the Activity log, including paused entries, the
entry window, two-buy limit, unavailable stream, insufficient cash, prices leaving
the allowed range, and the official cutoff. Existing logs from before this update
contain balances but cannot establish the exact reason for an earlier missed buy.

If an ask changes during balance/fee checks but remains within 95c–99c, the bot
updates its limit to the current allowed cent price and recalculates quantity with
the same whole-contract target, capped by total cash including fees. A price outside the range or an expired
cutoff still blocks entry. Qualifying stream updates shorten the idle wait; account
reconciliation and rate limits still apply, so brief opportunities can be missed.

Portfolio reads use the supported 1,000-record pages with complete pagination.
Reconciliation still checks the entire returned account history. Saved snapshots
contain owned orders and unresolved account orders, avoiding repeated large writes
of unrelated completed history. Stop monitoring reuses its private SQLite connection
and preserves an already durable stop without repeatedly rewriting it. Delayed
timestamped stream messages require a new snapshot before prices can authorize buys.

Buy limits are restricted to 95c–99c; cheaper fills are permitted. New orders require
reconciled state, whole-contract sizing within total available cash including fees, a fresh sequenced
book, and the final **240-second (four-minute)** entry window. Two buys are allowed only in the defined
buy/sell/buy sequence. Stops at observed bids <=86c latch durably, including during
REST reconciliation, and remain latched after rebounds. Exits never intentionally
limit below 86c. A resting limit does not guarantee a fill.

Books arrive through the official WebSocket host, with sequence-gap detection,
resnapshot/reconnection and freshness checks. Loss of stream consistency blocks new
entries; known latched exits can continue through the REST path. A separate SQLite
connection persists threshold observations while account projections are being read.
Any software/network outage still has latency and can lose events while disconnected;
no application can guarantee an exit at 86c.

Fees are checked from current official KXBTC15M series metadata and event fee changes.
The reserve covers a worst-case quadratic fee across cheaper execution prices and
centicent rounding over fractional fills. Unknown fee models fail closed. The GUI
fee field is optional; an entered positive value can increase the automatic reserve,
not reduce it. No manual fee guess is required to connect.

Use a dedicated account with netting enabled. Unmanaged positions/open orders or
incomplete exchange projections block new execution rather than adopting them.
Historical orders/fills disappearing from the live API may require manual archive
reconciliation. The current repair was tested with generated keys and mocked
exchange responses. Official demo acceptance and prolonged live-network soak
testing remain separate checks; see REPAIR_REPORT.md.

## Security deployment

The frontend has no Internet transport. Workers run in private namespaces with no
home directories or display sockets, and access only their own state, selected key
directory and local gateway socket. The gateway accepts only the designated local
UID, verifies fixed-host TLS, and can send TCP 443 only to pinned official REST and
WebSocket IPs through the dedicated UID's nft policy. It cannot use arbitrary URLs.

This laptop did not enforce the original systemd SocketBindDeny setting. The current
deployment instead uses **systemd socket activation plus kernel syscall filtering**:
systemd creates the sole local Unix listener, and the unprivileged gateway cannot
call bind/listen. The gateway verifies that the OS denies Internet binds before
serving. The socket belongs to the local client with mode 0600; parent traversal
permits reaching only that known socket. No TCP listener exists. The root policy
service verifies the scoped nft rules before the gateway/socket can start.

The gateway does not contain or store the private key. The installed gateway does
not connect to an account until you explicitly use Connect in the app. It was
started for this session but not automatically enabled at boot; use Install / repair
connection service after a reboot if necessary. Root/admin control is trusted.


## Strategy checks and order execution

The strategy line evaluates every 250 ms independently of slow account/API
calls, shows its check counter and measured interval, and marks a check older
than 500 ms as DELAYED. The execution stage separately shows required account,
fee, cutoff and submission checks. The optional three-second pause between
desktop trading cycles is removed; API rate limits/backoff and reconciliation
remain. Evaluation cadence does not guarantee a 500 ms exchange submission.
See [cadence verification](DECISION_CADENCE.md). Cash handling is unchanged.

## Protective sell status

The existing Stop-loss field now distinguishes a triggered stop, pending buy
cancellation, acknowledged sell, accepted/open sell, uncertain submission,
rejection, partial fill and completed exit. Settlement without enough sell fills
is explicitly distinguished from a completed protective sale. The stop stays
latched through rebounds and restarts. A submitted or accepted 86c limit can
remain unfilled. See [protective verification](PROTECTIVE_SELL_REPORT.md) for
both-side tests, reproduced fixes and the read-only local history findings.


## Rejected or uncertain orders

Known public exchange codes now identify price, quantity, shard-balance and other
rejections without logging private response text. A rejected order does not use a
purchase allowance. A network timeout/5xx can have an unknown outcome: the app
retains the original intent and waits for exchange records without replaying it.
If no confirmation appears, explicit reconciliation may be needed. Reusing a
client ID cannot guarantee deduplication after an order closes. Do not delete the
journal to clear this warning. See [current rejection report](EXCHANGE_REJECTION_REPORT.md).


## Bitcoin cash versus total account cash

The cash card labels the amount being displayed. **TOTAL ACCOUNT CASH** can
include money on other exchange balances. New buys always check **AVAILABLE FOR
BITCOIN** using the selected market and primary account; other exchange money
cannot fund that order. The target is floor(available Bitcoin cash in dollars / 2)
whole contracts: $26.87 targets 13. Fees are reserved from remaining cash, with total
cost capped at the available market cash. This replaces the former fee-inclusive
half-cash dollar budget; cost can now exceed half the cash. See [contract sizing](CONTRACT_SIZING.md).
Missing or changed scope blocks
new buys. The connection service must advertise market-specific cash support;
use Install / repair in Account setup if an older service is detected.
See [the confirmed cash-sizing fix](CASH_SCOPE_FIX.md) for the live read-only checks.
