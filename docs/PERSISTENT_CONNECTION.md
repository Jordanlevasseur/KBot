# Persistent connection and automatic screen locking — November 13, 2026

The working order routes, pricing rules, quantity sizing, cash/fee reserves,
maximum purchases, stop-loss logic, reconciliation, duplicate protection and
credential storage were preserved. No gateway restart or root service change
was performed for this update. No real orders, cancellations or trading activation
were performed. No new account probes were started; diagnosis used local records
read-only and testing used generated credentials, synthetic exchanges and local OS IPC.

## What the connection labels meant

The account-connected flag incorrectly depended on the current prepared-state
reconciliation generation. Buys, fills, cash changes and market rollover purposely
invalidate that generation, which made the UI offer Reconnect even while the API
key and transport were healthy. Manually reconnecting then restarted the worker
and required the normal Connect/Start confirmation again.

A passive private-API health observer now separates transport/authentication from
entry readiness. Temporary failures show automatic recovery; renewing account
checks does not claim the account disconnected. Actual 401/403 errors remain
visible and cannot be cleared by success on a different endpoint. No cached
health flag authorizes a trade. Existing fresh-data checks stay authoritative.
A genuinely stopped worker displays Disconnected and cannot appear active/retrying.
Explicit first-session Connect/Start and paused restart behavior are unchanged.

The dashboard separately labels Reconnecting, Saving protection, and genuinely
expired status. It distinguishes the last price change from feed verification.
True stale prices still block new entries; short reconnections do not require
manual refreshing or new credential entry.

## Automatic stream recovery

The old streaming HTTP reader could wait 15 seconds while data expires after
five. HTTP/1.0 response ownership also clears HTTPConnection.sock, so the previous
cleanup path could miss the still-open response socket. The client now retains
that socket, shuts it down on replacement/error, and closes response readers.
After the first valid snapshot, its idle read deadline is four seconds; initial
handshake allowance remains unchanged. A silent-stream regression verifies
that failure drops usable quotes and automatically recovers a fresh snapshot.
Sequence, subscription, persistence and five-second price freshness checks remain.

Kalshi documents an empty snapshot when a market is determined. A metadata-only
empty snapshot now clears all old liquidity instead of raising KeyError. It
cannot provide an executable price. Partial malformed snapshots still fail closed.
Delayed control acknowledgments cannot invalidate an otherwise valid book;
control traffic cannot refresh prices or keep expired verification alive.

Refresh now wakes the existing read-only discovery loop. It does not disconnect
the worker, pause/re-enable entries or restart the account session. Independent
monitoring and existing automatic reconnect loops continue while minimized.

## Screen locking

While the app is open, a private Cinnamon/GNOME session-bus connection holds an
idle-inhibitor cookie (flag 8). This prevents automatic idle screen locking without
editing global preferences. The existing logind sleep/lid inhibitor remains.
Closing the app releases only its cookie. Manual locking remains available; the
app never unlocks an already locked screen. If Cinnamon restarts, it reacquires
its inhibitor; cleanup cannot release a reused cookie belonging to another app.
If idle inhibition is unavailable, the power line reports this and retries without
pausing or restarting trading. Existing sleep-protection failure handling is unchanged.
Linux Mint's existing python3-dbus package supplies the desktop IPC dependency.

## Verification

- 337 offline tests passed, zero failed; final full suite 29.519 seconds.
- Native dashboard checks passed at 39.82 MiB peak RSS, including recovery versus
  account checks, stopped-worker status, protective-save status and real expiry.
- Real Cinnamon acquire/release verification passed. No screen lock/unlock or
  global settings change was attempted.
- Default isolated mock trading passed YES/NO, fractional NO asks, complete sale
  before reentry, two-buy cap, stale rejection, reconnect, settlement and rollover.
- Both protective recovery pipelines passed, with 50/50 synthetic contracts sold
  per side, partial fills, lost acknowledgments and zero uncertain-order replays.
- Two instrumented mock fast-path sessions performed zero critical-path balance
  GETs, sizing calls or full reconciliations. These are local smoke checks, not a
  live execution or network-latency guarantee.
- Whole-module SHA-256 checks preserve the ten financial/security core modules.
  AST checks preserve the stream signing/subscription functions and all runtime
  Journal/OrderBroker/PreparedEngine construction and order-execution calls.

Reopen the desktop application to load the update. Temporary network/stream
failures recover automatically. An outage, invalid credentials or genuinely stale
market/account data can still prevent entries; these safeguards were not relaxed.

[Core module hashes](connection-route-baseline.json) ·
[Execution-call invariants](connection-execution-invariants.json) ·
[Protective tests](persistent-protective-results.json) ·
[Fast-path smoke checks](persistent-fastpath.json).

Official behavior: [Kalshi order-book stream](https://docs.kalshi.com/websockets/orderbook-updates),
[Cinnamon idle inhibitor](https://raw.githubusercontent.com/linuxmint/cinnamon-session/master/cinnamon-session/org.gnome.SessionManager.xml).
