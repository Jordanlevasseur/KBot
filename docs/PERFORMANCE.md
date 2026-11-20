# Measured performance on the upgraded ThinkPad

Newer entry-response measurements and shared read-pacing update:
[ENTRY_LATENCY.md](ENTRY_LATENCY.md), November 13, 2026. The measurements below
are the earlier November 12 implementation and remain as history.

Measured November 12, 2026 on the laptop: **Intel Core i7-2640M @ 2.80 GHz**,
Linux reports **16,253,424 KiB** physical memory (about 15.50 GiB).
All trading tests used generated demo keys, temporary journals and a synthetic
exchange through the real isolated worker and local Unix gateway. No production
credentials, authenticated account requests or real-money orders were used.

## Before and after

The pipeline benchmark ran four fresh sessions before and four after, alternating
YES and NO. The real REST rate gate was retained; mock responses had a fixed
20 ms delay. The native Tk window was withdrawn, and its real event loop rendered
worker market messages. Values below are medians, not exchange latency guarantees.

| Measurement | Before | After |
|---|---:|---:|
| Receipt to eligible-side evaluation after reconciliation | 2855 ms | 2326 ms |
| Receipt to final buy decision | 4905 ms | 4326 ms |
| Final decision to order-broker handoff | 2.18 ms | 2.72 ms |
| Receipt to order-broker handoff | 4909 ms | 4329 ms |
| Receipt to hidden dashboard render | 423.9 ms | 32.0 ms |
| Observed requests before handoff, including concurrent discovery | 10 | 9 |
| Worker CPU during startup/entry, one core | 5.21% | 5.84% |
| Worker peak resident memory during entry | 31.96 MiB | 31.97 MiB |
| Quiet background worker CPU, one core | 0.69% | 0.66% |
| Quiet background worker resident memory | 32.20 MiB | 32.20 MiB |

The handoff occurs at `OrderBroker.create`, after the durable SQLite intent and
before signing/Unix HTTP. It excludes Internet transit, exchange acceptance and
fill latency. The decision-to-handoff difference is sub-millisecond scale test
variation; no improvement is claimed there. The faster dashboard and shorter
startup window slightly increase measured entry CPU percentage. Steady-state
quiet measurements exclude three initial samples and cover about ten seconds,
with entries paused, verified stream heartbeats and full account reconciliation.
They are short local measurements; active exchange traffic and disk load vary.
The small quiet-CPU difference is within short-run variation, rather than evidence
of a significant idle-CPU reduction.

A separate repeated full-reconciliation profile used **2,000 generated owned
orders and 4,000 remote fills**, including unrelated history, without account
snapshot filtering. Five runs were measured after warming the database:

| Full-history reconciliation | Before | After |
|---|---:|---:|
| Median wall time | 2068.2 ms | 129.1 ms |
| Median CPU time | 2022.8 ms | 128.7 ms |
| SQL statements per complete check | 11,009 | 5,008 |

This is about 16.0 times faster for the generated larger history.
It does not imply the same speedup for Internet order submission.

Final desktop integration: **39.68 MiB** current/peak GUI resident memory.
The simulator exercised 150 ticks in **0.054 s**, using **0.047 s CPU** and
**16.68 MiB** peak resident memory. Linux `/proc/self/status` is used for these
process-image memory measurements: `getrusage().ru_maxrss` on the development
launcher inherited a peak from its earlier process image and overstated app RAM.
No production memory limit was relaxed to hide a failed measurement.

## Implemented changes

- Each canonical sequenced book now notifies independent trading and display
  subscribers immediately. Notifications coalesce into one byte per subscriber;
  they contain no prices or account data. Consumers re-read the validated book.
  Pause/quit and price events share a selectable wait, removing the 0.5 s entry
  polling delay. Periodic reconciliation and stale checks still run while idle.
- Display publishing wakes on market and execution changes, coalesces bursts to
  at most 20 frames/second and retains a 0.5 s health/countdown fallback. Tk drains
  its message queue every 50 ms instead of 150 ms, including while minimized.
- Full reconciliation builds per-snapshot order and fill indexes once, replacing
  repeated scans of every fill for each known order. Every known order, remote
  position, owned fill and unmanaged open order still participates in validation.
  Missing/duplicate fills and inconsistent account projections still block writes.
- Unchanged historical order rows are validated without rewriting them. Additive
  SQLite indexes support market/side and fill/order lookups; the journal's page
  cache may use up to 8 MiB, up from SQLite's usual approximately 2 MiB default.
  The 100-row dashboard fill history uses one join instead of up to 101 queries.
- Flat eligible buys read available cash once at sizing instead of twice in the
  same cycle. That read remains fresh and no earlier-cycle cash authorizes an
  order. Paused/nonqualifying cycles refresh displayed cash; held/pending risk
  retains its balance check before action. Fees, cutoff and final executable
  prices are still verified before each new buy.
- Reconnect waits are interruptible on shutdown. Pending stop persistence still
  retries through disconnections and rebounds. A stream wake-up is sent only
  after the associated stop-persistence attempt, and failed persistence keeps
  prices unavailable for trading. A latched stop can wake protective
  reconciliation even after the latest bid has recovered.

## Process and memory decisions

The gateway and native dashboard remain separate processes. Stream reception,
market discovery, display publishing and diagnostic recording run independently
of account I/O. Account reconciliation, decisions and order journaling keep a
single owner. The profile identified request pacing and repeated history scans;
more trading processes would duplicate reads and introduce write coordination,
without a demonstrated benefit after the history scan fix.

The worker's existing **500 MiB address-space cap** remains, as measured working
memory is about 32 MiB. The extra RAM is useful for bounded database caching and
Linux disk cache. Large quote queues or speculative account caches would add
latency/staleness; no such buffers were introduced. Credentials, official hosts,
firewall rules, key permissions, live-start confirmations, durable synchronous
commits and rate limits are unchanged. The independent gateway required no
restart because its protocol, allowlists and network code did not change.

## Verification and limits

- **176 offline tests passed**, including new bounded wake-up/broadcast tests,
  Pause ordering, updates during blocked discovery, durable-stop-before-notify,
  symmetric fresh-cash sizing and rejection of corrupt/duplicate/missing fills.
- The full mocked demo passed: both sides, a 98.4c NO ask using a 99c limit,
  two buys combined, complete first sale, latched 86c exit, rebound, reconnect,
  stale rejection, settlement and automatic rollover. Entry occurred with
  228.02 seconds remaining, within the preserved final-four-minute window.
- Hidden GUI integration, generated-key/isolation preflight and the disposable
  kernel-firewall test passed. Host and isolated-app health verified the unchanged
  production gateway and 240-second entry setting, with zero credentials or
  account requests. No host firewall change was needed for testing.
- Installed application files were updated without reading/changing credentials
  or opening the production journal. Reopen the app to load the update. It still
  starts paused in simulation; this work did not start a live session.

Official demo exchange acceptance and a prolonged real-network soak remain
unverified. Mandatory account, fee and cutoff reads still take several seconds
at the existing conservative rate. A fleeting opportunity may disappear during
those checks and is then skipped. No stale snapshot, increased API rate or
relaxed order validation was used to force a faster order. An 86c limit exit can
remain unfilled. Closed/suspended/offline computers cannot monitor continuously.

## Reproduce offline

Run from the project directory with the existing reviewed dependencies and
Linux isolation available. These use temporary generated credentials only:

```sh
TMPDIR=/dev/shm python3 -B -m unittest discover -s tests -p 'test_*.py'
TMPDIR=/dev/shm python3 -B tests/performance_pipeline.py --label current --samples 4 --output /tmp/kalshi-pipeline.json
TMPDIR=/dev/shm python3 -B tests/performance_reconcile.py --label current --output /tmp/kalshi-reconcile.json
TMPDIR=/dev/shm python3 -B tests/performance_idle.py --label current --output /tmp/kalshi-idle.json
TMPDIR=/dev/shm python3 -B tests/demo_pipeline.py --no-ask .984
TMPDIR=/dev/shm python3 -B tests/desktop_integration.py
```

Raw generated measurements: [pipeline before](performance-before.json),
[pipeline after](performance-after.json), [history before](performance-reconcile-before.json),
[history after](performance-reconcile-after.json), [background before](performance-idle-before.json),
[background after](performance-idle-after.json). Reconciliation artifacts include
cProfile summaries. The historical baseline was measured before runtime edits;
its quiet-background check used the previous installed snapshot before updating it.
