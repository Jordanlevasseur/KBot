# Standard prepared execution — November 13, 2026

Prepared execution now runs automatically for every desktop demo/live trading
session. The opt-in checkbox and CLI flag were removed. All ten side/price
variants are prepared in advance; stale or uncertain state blocks new entries
while asynchronous readers refresh it. Protective exits retain priority.

The normal launch path passed 285 offline tests, the hidden desktop test,
the isolated mock demo and both-side protective recovery scenarios. The latter
confirmed 50/50 synthetic contracts sold per side, partial fills, latched stops,
gateway and paused-worker restart, and zero uncertain-order replay submissions.
No real account or real-money orders were used.

The installed application was updated while closed. Reopen Kalshi Bitcoin Bot
normally; there is no preparation setting to enable. Simulation remains the
initial mode. Live connection and Start remain explicit user actions. Credentials,
production journals, services and firewall configuration were not changed.

See [latency measurements and limits](PREPARED_EXECUTION.md),
[raw timing samples](standard-prepared-latency.json), and
[verification record](standard-execution-verification.json).
