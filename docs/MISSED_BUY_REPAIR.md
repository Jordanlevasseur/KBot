# Missed buy repair — November 13, 2026

## Verified missed opportunity

Read-only production journal inspection showed fresh NO asks of 98.8–98.9c at
14:12:44–14:12:46 Pacific, entries enabled, inside the four-minute window. The
executor blocked on stale/uncertain account reconciliation. No order was created
for that opportunity; no exchange rejection caused this miss. The prepared table
had no ready row. The last durable reconciliation was at 13:27:30 Pacific.
Later Pause records do not explain the earlier enabled, eligible opportunity.

## Defects and corrections

The prepared update increased continuous background reads, while the gateway
retained CPUQuota=10% of one CPU core and rebuilt the CA trust store per request.
Read-only order/status probes took 7.363/8.167s, exceeding the five-second account
lease. Gateway cgroup counters showed 309,962 throttled periods out of 344,840.
The managed service now permits 50% of one core, still with its original memory,
seccomp, dedicated UID, socket and firewall restrictions. It shares one immutable,
verified TLS context and maintains a bounded GET-only connection pool (four idle
sessions, 15-second idle expiry). Writes use fresh connections and never replay
an uncertain submission. The targeted administrator repair verifies the firewall,
refuses running workers, verifies reviewed source hashes, and rolls back on failure.

Freshness previously included rate queues, TLS setup and unsuccessful connection
attempts before a successful authenticated GET. The gateway now stamps that GET
start using the shared monotonic clock, after TLS and before sending its request.
The client rejects malformed/duplicate/out-of-request timestamps and keeps the
oldest successful page timestamp across pagination. Actual response transfer,
parsing and subsequent cache age still consume the same deadlines. An old gateway
without the header falls back conservatively; no freshness limit was increased.

The account reader also waited for every collection before refreshing any of
them. Collections now update independently. Only complete cohorts from the current
epoch, with every required collection inside the age bound, reach reconciliation.
Repeated identical discovery/verified-market mismatches invalidate once instead
of invalidating again on every 100 Hz execution pass.

The remaining sustained bottleneck was re-downloading 1,113 account-wide fills
on every refresh. A main-owner FillHistory projection retains durable confirmed
closed-order fills. Current, held and unresolved market fills are freshly queried
using the documented ticker filter. Cached fills for those markets are excluded,
so a missing current fill still blocks reconciliation. Duplicate IDs deduplicate;
conflicting order/count/price/fee records fail closed. Global orders, positions and
settlements are still fetched and the original strict reconciler still rejects
unmanaged orders, mismatched positions and incomplete fills. Background readers
receive an immutable projection and never access the owner's SQLite connection.

Bounded gzip negotiation was added with input/output size limits and malformed,
truncated, concatenated or unsupported-encoding rejection. The observed production
responses remained uncompressed (wire and decoded totals were equal); no measured
speed improvement is attributed to compression.

## Verification

- 319 offline tests passed, zero failed; final suite 47.430s under concurrent checks.
- The isolated mocked default trading pipeline passed both sides, fractional NO
  asks (98.4c -> 99c limit), two-buy sequencing, complete sale before reentry,
  stop latch, reconnect, stale rejection, settlement and rollover.
- Both protective recovery pipelines confirmed 50/50 synthetic contracts sold,
  partial fills, rebound latch, gateway/paused-worker restart and zero uncertain
  recovery replay POSTs. A submission was never treated as a completed sale.
- Real account checks used GET-only credentials inside the existing Linux sandbox.
  Positions/orders were verified flat before each connection-service restart.
- Prepared readiness used a disposable copy of the journal, a read-only signer,
  no eligible quote and a broker whose create/cancel methods raise. It never called
  execute. Production journal records and credentials were not modified.
- The final source check first reached READY after 20.855s of cold preparation and
  stayed READY for every observed sample in the following eight-second window.
  It prepared 11 whole contracts under the existing cash rule. The scoped fills
  refresh had an observed median 0.316s; the earlier account-wide probe was 3.970s.
  These are diagnostic runs under different network/load conditions, not a
  controlled latency comparison or a real-money execution guarantee.

The installed desktop modules are source-matched. Reopen the app normally and
choose the desired account mode, Connect and Start explicitly. Entries remain
paused by default; an initial cold preparation interval is expected. An external
outage, genuinely stale data, insufficient market cash, an unresolved order,
position mismatch or exchange rejection still blocks buys safely. No live buy or
protective sale was submitted by this repair; exchange execution remains unverified.

[Read-only evidence](missed-buy-readonly-verification.json) ·
[Protective evidence](missed-buy-protective-verification.json).

Official references: [fill filtering](https://docs.kalshi.com/api-reference/portfolio/get-fills),
[CPU quotas](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html),
[shared SSL contexts](https://docs.python.org/3/library/ssl.html#ssl-contexts).
