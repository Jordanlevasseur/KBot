> Current connection instructions: [ONLINE.md](ONLINE.md). Version 0.4 adds explicit
> Live trading mode, automatic fee verification, streamed books, a 1× elapsed-time
> clock, and the in-app administrator gateway installer. Earlier locked/read-only
> descriptions below refer to version 0.3 and are superseded by ONLINE.md.

# Laptop application

Launch **Kalshi Bitcoin Bot** from your Applications menu or desktop shortcut.
The native Tk interface needs no browser, GPU, local web server, or AI model.
The reviewed source snapshot is installed under `~/.local/share/kalshi-bitcoin-bot`.

## Screen locking and laptop sleep

The open app automatically prevents sleep/lid-close suspend and automatic idle
screen locking. It holds an app-owned desktop-session inhibitor; global lock and
power settings are not edited. Closing the app releases its inhibitors. Manual
screen locking is still available, and the app never unlocks an already locked
screen. The power status line reports whether protection is available.

If the desktop idle-inhibitor service is unavailable or restarts, the app reports
this and retries without pausing trading. The existing sleep-inhibitor safeguards
remain. Actual suspend, hibernation, shutdown, power loss or logout still stops
local execution. See [persistent connections and power](PERSISTENT_CONNECTION.md).

## Start a simulation

The application always starts in **Simulation • offline**, with new entries paused.
Click **Start simulation**. It uses synthetic markets and simulated money. A sample
starts inside the final four-minute entry window so you can observe both the stop and
second-buy sequence. Adjust replay speed to 1×, 5×, or 15×. After restart it restores
its own simulation history and pauses new entries.

**Pause new buys** stops entries and cancels residual simulated buys; protective
exits keep running. Closing the application ends monitoring. The app warns before
closing a held position. Trade history and activity log tabs show recorded fills
and recovery events. A simulation is not a profitability backtest.

## Profit in trade history

The PROFIT column shows net realized dollars on BUY fills, including entry/exit
fees. Open and partial exits are marked. Settlement updates appear automatically;
SELL rows are accounted for on the BUY rows. Missing cost information shows a dash.
See [calculation and validation](TRADE_PROFIT.md).

## Local account configuration

Account setup has separate buttons for a demo key and a live read-only key. The file
chooser lets you select an existing local PEM file; the private key's contents are
never displayed or logged. Source files must be owned by you and private (mode 600).
The key ID is hidden as you type. Configuration validates locally and stores files
with mode 600 in a private environment-specific folder under
`~/.config/kalshi-bitcoin-bot`. Existing credentials are never overwritten.

Choose an account mode, then click **Check credentials offline**. This validates
Linux isolation and key format without contacting Kalshi. No key is needed for simulation.

The **Connect** button in account modes asks for explicit authorization. The app
never reconnects an account automatically at startup. Live account mode is read-only.
It does not adopt or protect your existing personal positions.

An administrator-reviewed gateway installation is required before any account can
connect. This desktop installer does not create system users, install services, or
change the host firewall. See ACCOUNT_SETUP.md for the gateway deployment. The GUI
credential directories differ from the older command-line examples: use the GUI's
`~/.config/kalshi-bitcoin-bot/demo` and `production` paths for this application.

## Demo trading candidate

Choose **Demo trading • test funds** only after reading TRADING.md and provisioning
the demo gateway with trading enabled. Enter a verified fee reserve in Account setup,
connect explicitly, then click **Start demo trading** to enable entries. It submits
demo orders only, never production orders. No fee value is guessed for you.

The current candidate uses REST polling and can miss brief stop crossings between
polls. Official demo acceptance, fees, account netting, historical recovery, and
privileged gateway deployment require verification. **Production order submission
is deliberately absent from the desktop UI.** Packaging it as an app does not make
the trading candidate production validated.

Pausing demo entries keeps protective processing enabled while the worker remains
open. Closing or switching away from a held demo position stops that processing;
existing exchange orders may remain. Never remove the journal to fix an account
mismatch. Read activity logs and reconcile instead.

## Storage and isolation

Desktop state lives under `~/.local/state/kalshi-bitcoin-bot` (or the corresponding
XDG state location). Each mode has its own persistent database. This application
does not modify the earlier terminal simulator's database in the source project.
Desktop logs rotate at 1 MB with two backups. The UI prevents duplicate instances.

The native desktop frontend uses your display and file chooser like a normal Linux
application. It has no Internet transport; it launches the account/strategy worker
inside a separate Linux mount, process, user, and network namespace. The worker gets
only the application package, its mode's state directory, and—after your selection—
the dedicated credential directory and optional Unix gateway socket. It gets no
GUI display socket, home directory, browser profiles, SSH files, or inherited secrets.
The worker address space is capped at 500 MiB. There is no insecure fallback if
bubblewrap or namespace support is unavailable.

## Install or update a reviewed snapshot

Prerequisites from your Linux distribution: Python 3, Tk (`python3-tk`), bubblewrap,
and optionally `python3-cryptography` for account modes. No package is downloaded by
the app. Run `python3 deploy/install-desktop.py` from a reviewed checkout to install
or update the per-user app. No sudo is needed. The installer copies only application
code, launcher scripts, guides, and its icon; it never copies credentials or state.
The app performs no background update checks. Updating does not erase account history.

The menu shortcut requires no terminal. Some desktop environments require right-click
**Allow Launching** on a newly created desktop shortcut; the Applications-menu entry
works normally. If the GUI cannot open, run `~/.local/bin/kalshi-bitcoin-bot` from a
terminal to see the display-session error.

To uninstall, first close the application. Remove only its exact app directory,
`~/.local/bin/kalshi-bitcoin-bot`, the matching `.desktop` file under
`~/.local/share/applications`, and its desktop shortcut. Retain or back up the private
state directory. Revoke API keys at Kalshi before removing their local credential
files. Uninstalling this GUI does not remove a separately installed gateway.

Purchase counts now display the number completed, without a /2 maximum. New entries remain sequential: the previous position must be completely sold and reconciled. The four-minute window, sizing, fees, and 86-cent protective rule remain unchanged.

Trade history includes TIME (LOCAL): the confirmed fill date/time through seconds and the local timezone. Historical exchange fills populate automatically; unavailable times show an em dash. See TRADE_TIMES.md.

New purchases stop at exactly 30 seconds remaining. The dashboard displays the final-30-second purchase block, while protective selling remains active until market close. Earlier unfilled buy quantities expire at the purchase deadline. See CLOSING_PURCHASE_CUTOFF.md.
