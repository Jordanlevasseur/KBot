# Existing bot repair report

The later [missed-NO investigation](NO_MISS_REPORT.md) supersedes this stage's
separate display/trading streams with one canonical cache per market, adds precise
decision evidence, and explicitly verifies decimal asks and fills.

Completed in the existing project on 2026-11-12. No real-money order or automatic
live-session activation was used for development or verification.

## What was broken

- The worker published stream quotes only during a three-second idle wait after
  synchronous portfolio reconciliation. Slow reads froze the apparent prices and
  delayed market discovery even when the WebSocket itself was receiving updates.
- Account-message age was used as market-data health. This could show stale or
  disconnected status despite current prices; the last market update was absent.
- Unfiltered discovery fallback could miss the active contract behind unrelated
  close times. Discovery could also use the time sampled before a slow request.
- A local gateway receive timeout generated a heartbeat without proving the
  official peer was still connected. Replayed/malformed snapshots were not always
  guaranteed to invalidate the previous valid book.
- Old stream reads could remain blocked during switching/closing. Uncertain buy
  recovery could retry while paused, and a retry rejection could erase the
  uncertainty of an earlier request. Buffered input could delay a queued Pause.

## What changed

- `market_data.py` adds independent read-only discovery and a 0.5-second display
  publisher using the existing isolated Unix gateway and sequenced `BookFeed`.
  The protective stream stays attached to its held market while a separate display
  stream follows the next active contract. No phone or mobile app supplies data.
- Discovery uses official timestamps after requests and a restricted near-term
  close-time fallback. Display rollover does not wait for account settlement reads.
- Stream protocol 2 verifies upstream Ping/Pong before refreshing idle validity.
  Unverified silence, stale timestamps, bad sequences and replayed snapshots block
  entries until a fresh snapshot. Reconnects and stop persistence remain automatic.
- The dashboard separates account status from market status and shows all four
  YES/NO prices, selected side, purchase state/count, position/orders, last decision
  and last successful data-update time. It updates while hidden/minimized. The
  existing dashboard can scroll on smaller screens instead of clipping controls.
- Separate API readers share rate pacing. Controls are checked between account
  reads and just before submission; raw bounded JSON input preserves batched Pause.
- A never-sent local order is distinguished from an uncertain network result.
  Uncertain recovery retains its original client ID, and paused buys cannot retry.
  Limit orders, half-cash fee sizing, order reconciliation, SQLite history and
  single-position/two-buy protections remain in place.

## Verification

- **151 offline tests passed.** This includes both sides at 95c–99c, price
  improvement, fee-inclusive resizing, final-two-minute entry, 86c stops, partial
  fills, first-sale sequencing, opposing-position/duplicate prevention, restarts,
  expired markets, narrow discovery filters, reconnects and heartbeat verification.
- **Desktop integration passed:** real Linux isolation and simulation start/pause,
  persistent restart, YES/NO labels, held-side bids, hidden-window updates, late
  account replies during rollover, stale state and readable skip decisions.
- **Demo-mode integration passed twice** with the real isolated worker and real
  local Unix gateway, generated demo keys and a mocked exchange. The final run
  published 106 market events and executed exactly two simulated buys: YES, its
  protective sale, then NO held through settlement. It also exercised slower
  account reads, cheaper fills, stream disconnection, stale timestamps and rollover.
- Desktop integration current RSS: 39.34 MiB. This is a finite test observation,
  not a prolonged production performance certification.
- Security controls were preserved: official pinned TLS destinations, dedicated
  UID firewall, OS bind/listen denial, private credentials and namespace isolation.
  Deployment checks are recorded in VALIDATION.md after installation.

## Demo and remaining live-readiness limits

Official Kalshi demo acceptance is **not verified**: no demo key directory or demo
gateway is configured on this laptop. The mocked demo-mode run proves the local
pipeline and strategy, not official demo liquidity, permissions or account schema.
Production account authentication/trading was not used to test this repair.

Long-duration live-network soak testing remains outstanding. API outages, laptop
suspension and loss of connectivity can delay observations and execution; an 86c
limit sell may remain unfilled. Unmanaged positions/open orders or incomplete account
projections deliberately block new risk. Historical orders/fills disappearing from
live endpoints may still require manual archive reconciliation.

The app continues to start in paused simulation. Production needs an explicit
Connect and Start confirmation. Gateway protocol 2 is checked before a live worker
can connect; no insecure fallback was added.
