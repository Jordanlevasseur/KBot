# Kalshi Bitcoin Bot — desktop application

**Missed-buy repair installed:** account preparation now retains confirmed closed
fills and refreshes current risk independently, with verified gateway timing and
bounded TLS reuse. [Diagnosis and verification](docs/MISSED_BUY_REPAIR.md).

**Prepared execution is standard for demo and live trading.** Quantities, fees,
order payloads and reserved buying power are continuously prepared before an
eligible quote appears. No opt-in checkbox or command-line switch is required.
Freshness checks, protective-exit priority and explicit live Connect/Start
controls remain. See [implementation and verification](docs/PREPARED_EXECUTION.md).


A native Linux desktop application plus terminal tools for the requested
15-minute Bitcoin strategy: offline simulation, demo trading, read-only account
monitoring, and explicitly enabled live trading.
Simulation uses Python 3.10+ and SQLite with no third-party dependencies. The
account monitor also requires `cryptography` (distribution package
`python3-cryptography`). No browser, telemetry, or background updates.

**Current setup and controls:** see [ONLINE.md](docs/ONLINE.md) and
[repair/validation report](docs/REPAIR_REPORT.md).
The simulator displays simulated money; the separate account monitor displays
actual API-reported account data only after explicit `--connect` authorization.

**Price rule updated:** only submitted buy limits must be 95c–99c. Cheaper fills
are allowed, as confirmed by the user. The former fill-floor blocker is resolved.

The trading worker includes V2 order writes and persistent execution recovery.
It is disabled by default. Official demo acceptance remains separate from the
automated mocked-exchange verification. The account-monitor modes remain read-only.

Current cash handling uses the selected Bitcoin market’s spendable exchange
balance rather than combined account cash. Purchases target **floor(available
Bitcoin cash in dollars / 2) whole contracts**: $26.87 targets 13 contracts.
Fees are reserved separately from remaining cash; total cost cannot exceed that
market balance. This replaces dollar-budget sizing. See [contract-count sizing](docs/CONTRACT_SIZING.md)
and [cash scope fix](docs/CASH_SCOPE_FIX.md).

Strategy checks now run on an independent **250 ms** schedule, including while
account requests are slow. The optional three-second desktop trading pause is
removed. Orders still wait for fresh exchange validation. See [decision cadence](docs/DECISION_CADENCE.md)
for measurements, the dashboard indicator and remaining execution limits.

Mocked entry response was measured at **1.43 s**, down from **4.34 s**, with
all fresh account/price/fee checks retained. See [entry latency measurements](docs/ENTRY_LATENCY.md)
for conditions, rate limits and real-exchange timing limits.

## Laptop app

The desktop app automatically prevents laptop sleep/lid-close suspend and
automatic idle screen locking while open. Manual locking and minimized operation
remain supported; normal power/idle behavior returns when it closes. See
[power behavior](docs/DESKTOP.md#screen-locking-and-laptop-sleep) and
[persistent connection/status fixes](docs/PERSISTENT_CONNECTION.md).

Install the reviewed snapshot with `python3 deploy/install-desktop.py`, then launch
**Kalshi Bitcoin Bot** from the Applications menu or desktop shortcut. It opens in
offline simulation with new entries paused. Click **Start simulation**. No terminal,
browser, account key, or network connection is needed for simulation. See the
[desktop guide](docs/DESKTOP.md) for dashboard controls, local account setup,
private storage, and demo-trading limitations. Live order submission requires an explicit live-mode connection and a separate Start confirmation.
Use [ONLINE.md](docs/ONLINE.md) for the current read/write-key connection steps. No account authentication or new entries are enabled automatically.

## Install and start

Use your Linux distribution's approved packages for `python3` and `bubblewrap`.
On Debian/Ubuntu these packages are named `python3` and `bubblewrap`. No package
installation, host firewall changes, or automatic startup was performed for you.
Keep the application in its own directory; run these commands from this directory:

```sh
./deploy/run-sandboxed.sh --check-isolation
./deploy/run-sandboxed.sh
```

The application opens with **entries stopped**. Type `s` then Enter to start
simulated entries. The demonstration starts at virtual second 775 of a 900-second
market. The default runs five virtual seconds per display refresh.

- `s`: start simulated entries.
- `p`: stop new entries and cancel residual simulated buys. Protective exits continue.
- `h`: show confirmed simulated fills.
- `l`: review persisted events, warnings, and recovery messages.
- `d` / `c`: simulate feed disconnection / reconnection.
- `q` or Ctrl-C: stop and exit. Exit stops all processing until the next launch.

A headless demonstration:

```sh
./deploy/run-sandboxed.sh --start --steps 150
```

Replay progress and positions persist between launches in `state/simulation.sqlite3`.
A restart pauses new entries; existing stop instructions remain latched and resume
protective processing. `--start` explicitly enables **simulated** entries on launch.
For a fresh simulation, move the entire `state` directory aside while the bot is
stopped. Do not edit the database while the bot is running.

`--mode demo` and `--mode live` exit with an error before opening a database or
making a connection. There is no switch that enables external trading.

## Strategy and accounting

Only the simulated `KXBTC15M` series is accepted. Entry eligibility uses the supplied
cutoff, with `0 < cutoff - now <= 240`. A previously recorded cutoff can move earlier,
never later. These are **synthetic cutoffs**, not fetched official market data.

The simulator chooses YES first if both sides qualify. It sizes one order using
floor(available cash in dollars / 2) whole contracts at order creation. Actual cost
is quantity times price plus fees and may exceed half the cash. Total available
cash remains a hard cap. The simulation fee
is conservatively rounded up **per contract** from `0.07 × p × (1-p)` dollars.
This is a test fee model, not a claim about actual current fees for an account or
series. A conservative 2c fee reserve per contract covers the simulation fee for cheaper fills.
Price improvement, including fills below 95c, may leave more cash
unused. Order retries and partial fills do not count as additional buys. A buy is
counted on its first confirmed fill and remains counted if its remainder is canceled.

There is at most one active order and one held position across all markets. Only a
complete liquidation permits another entry, and no market can exceed two filled
buy orders. Partial entries finish filling unless canceled, stopped, or expired;
activation of the stop cancels any residual buy before creating a sell.

An observed held-side executable bid at or below 86c permanently latches the stop.
The simulator creates a limit sell at 86c, executes only at bids >=86c, tracks
partial fills, and preserves a valid order. A failed attempt does not increment the
buy count. Canceled/rejected sell orders can be retried without a configured limit.
The cancellation API here is synchronous simulation; it does not model real
exchange cancellation ambiguity. Missing bids do not fabricate a trigger price.

The synthetic scenario falls through the stop, rebounds, allows a second entry,
and settles YES. These prices and outcomes are deterministic demonstration data,
not Bitcoin forecasts or a profitability backtest. Untriggered positions wait for
settlement; closure alone never fabricates a sale. Fees, remaining cost basis,
sale proceeds, settlement payouts, and realized P/L are persisted in cents.

## Security

The supported launcher creates fresh Linux user, process, mount, and network
namespaces through bubblewrap, drops all capabilities, clears inherited environment
variables, and mounts only the application package, Python runtime, devices, and
its private writable state directory. Home directories, browser profiles, SSH
material, host credentials, and unrelated projects are not mounted. No network
interface other than isolated loopback is available. There are no shared host
listeners, DNS configuration, or external routes. This blocks inbound and outbound
traffic at the operating-system level without modifying your computer's firewall.

Startup checks visible interfaces, personal paths, effective capabilities, and
external routing. If isolation is unavailable, the launcher/application refuses to
run. These checks complement the namespace setup; they are not a general proof
against a compromised kernel. Runtime address space is capped at 500 MiB.

The state directory is the only writable host mount. Do not put unrelated files
or secrets there. The runtime `/usr` mount is read-only; avoid storing personal
secrets under `/usr`. State files are private (umask 077, database mode 0600).
Simulation mode never asks for or reads API credentials. The separate account
monitor provides a local credential configuration command and signed GET requests
through a dedicated Unix-socket gateway. Neither mode implements money transfers
or external order writes. See the separate account setup/security guide.

A hardened optional [systemd template](deploy/kalshi-simulation.service) is supplied
for a finite headless run under a dynamic account with a 500 MB memory limit and
20% CPU quota. It is not installed or enabled automatically. The interactive
bubblewrap launcher is the verified path; the systemd template has not been
executed here and may require distribution-specific adaptation. Do not loosen
isolation as a workaround for launch failures.

## Persistence, logs, and recovery

SQLite WAL with synchronous FULL makes each tick and its simulated exchange
operations one durable transaction. A process lock prevents duplicate bot instances.
Restart checks database integrity, buy-count consistency, and active exit quantities
before accepting start commands. Uncommitted work rolls back after process death.
A replayed or out-of-order tick has no effect. Restart pauses the virtual clock
while the process is down; UI disconnection advances it without retroactive trading.
Missed synthetic settlements are reconciled at reconnection.

Tables hold account cash/P&L, markets/cutoffs/buy counts, remaining positions and
cost basis, stop state, orders, confirmed fills, balance snapshots, settlements,
and events. Events are the application log and accessible through `l`; the last ten
are included in headless JSON output. CLI/configuration failures are printed to
stderr. No passwords or key material are logged because none are accepted.

Back up state only while stopped, or use SQLite's backup API. Preserve the database
and any WAL files together. On corruption or a reconciliation failure, stop, retain
a copy, and investigate; do not delete state to resume a real account. There is no
automatic repair, log pruning, or retention policy yet. Monitor disk use for very
long simulations. The virtual replay is single-threaded and sleeps between UI ticks.

## Tests and performance

Protective selling was verified on both sides, including partial fills and
uncertain submissions. See the [protective sell report](docs/PROTECTIVE_SELL_REPORT.md)
for 221 tests, repeated randomized cases, fixes and the read-only trade audit.
Live protective execution remains unverified.

The ThinkPad performance update uses immediate stream wake-ups, faster dashboard
refresh and indexed full-history reconciliation. See [before/after measurements](docs/PERFORMANCE.md).
The four-minute entry window and all order safety checks remain unchanged.

Developer tests use temporary offline databases and do not access an account:

```sh
python3 -m unittest discover -s tests -v
PYTHONPATH=. python3 tests/benchmark.py
```

The test harness calls the simulator core directly outside its launch sandbox;
the runtime CLI itself requires isolation. Tests cover prices, window boundaries,
fee-inclusive sizing, partial buys/sells, sequential re-entry after complete sales, retries, persistent
stops, rebounds, disconnections, process death, replay protection, settlement,
private files, inconsistent recovery, and disabled demo/live modes. Run the
sandbox self-check separately using the first installation command.

See [validation results](docs/VALIDATION.md) for measured resource usage and limits.

## Troubleshooting

- `bwrap ... Operation not permitted`: user/network namespaces may be blocked by
  your distribution or an enclosing container. Ask your administrator to permit
  the required isolation; the application intentionally does not fall back.
- `Isolation verification failed`: start through `deploy/run-sandboxed.sh`. The
  direct Python CLI is intentionally blocked on an ordinary host.
- `Another bot owns this database`: stop the existing process. Do not delete its
  lock file; the lock is released automatically when the process exits.
- No buys: press `s`, wait for the final 240 virtual seconds, inspect price/cash,
  and check the latest decision and any outstanding position or unresolved order.
- Stop sell remains open: no executable bid at 86c or above, or no simulated
  liquidity. A limit order cannot guarantee execution.
- Entries paused after restart: intentional; verify the visible position and
  active order, then press `s` if you want more simulated entries.

The entry window excludes the closing 30 seconds: new buys require more than 30 and at most 240 seconds remaining. Unfilled buys expire at close minus 30 seconds; protective selling continues until close. See docs/CLOSING_PURCHASE_CUTOFF.md.
