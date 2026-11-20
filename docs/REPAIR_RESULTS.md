# Connection repair results — 2026-11-19

**Deployment update:** The user subsequently approved installation. It is now installed and passed read-only production checks. See [DEPLOYMENT_RESULTS.md](DEPLOYMENT_RESULTS.md). The source-only statements below describe the earlier repair stage.

Source repairs are tested. The installed desktop and gateway have **not** been changed or restarted. Automatic trading was not enabled; all submitted test orders used a synthetic exchange. Live readiness remains conditional on deployment and a subsequent read-only check of the deployed code.

## Confirmed defects and repairs

| File / function | Defect | Repair and benefit |
|---|---|---|
| `strategy.py`, `VolatilityTracker` | Repeated full-history scans exceeded the 100 ms calculation interval at 900 observations. | Monotonic range queues preserve the original results, including gaps, duplicate timestamps and inclusive endpoints. Prior measured batch cost fell from 153.02 to 6.75 ms. This reduces scheduler pressure; it does not prove the hotspot caused no losses. |
| `prepared.py`, `PreparedEngine.execute` | A normal entry-window message could overwrite the real unreconciled-account blocker. | Explicit readiness reporting before new-entry checks. Existing position and protective-order paths retain priority. No reconciliation bypass. |
| `streams.py`, `relay` | The receive timeout handler also caught timeouts from local writes and verification pings after control frames. A failed Pong could initiate a second Ping, and a downstream write timeout could be mislabeled. | Only the receive await is inside the inactivity handler. Pong failures and write failures now leave through their correct recovery path. No failed Pong produces a verified heartbeat. |
| `streams.py`, `relay` | Handshake/subscription phases were not distinguished; protocol exceptions did not use the approved-address fallback. Cleanup was not uniform across error types. | Explicit phase labels, protocol-exception handling, and socket cleanup in `finally`. Only the existing pinned addresses are attempted. |
| `streams.py`, `safe_stream_failure`; `gateway.py`, handler | Unbounded retry warnings could flood logs; precise failures were difficult to correlate. | One warning per phase per 30 seconds. Logs contain only phase, exception class and validated numeric HTTP status—never exception bodies or signed headers. |
| `connection_status.py`, `ConnectionHealth` | A failed historical request could keep showing disconnected even after newer authenticated account requests succeeded. | Show **degraded** when the transport is demonstrably reachable but requests remain unresolved. Retain the request warning. Authentication rejection remains blocking, and success expires after 15 seconds. This passive display state never authorizes an order. |

The saved strategy, sizing, order routes, duplicate protection, five-second book freshness, Linux isolation, API allowlists, credentials and firewall configuration were not changed.

Meaningful installed-to-source differences: [repair-source-vs-installed.diff](repair-source-vs-installed.diff). This is not a Git diff: the project has no Git history.

## Test repair, rather than changing strategy to make tests pass

The old suite mixed several historical strategies without configuring them. The same synthetic exchange recognized 63-cent sells, filled at 71 cents, while tests expected 86 cents and runtime defaults were 92 cents. This made results misleading.

`tests/strategy_fixture.py` now explicitly sets each historical fixture's policy and restores it after its module. The synthetic exchange derives the sell side and price from the configured rule and actual order limit; it still rejects oversells, overlapping orders and below-limit fills. Crash subprocesses receive the same explicit fixture policy. Saved user settings are never loaded or edited by these tests.

Fixture updates: `test_closing_blackout.py`, `test_contract_sizing.py`, `test_core.py`, `test_decisions.py`, `test_entry_checks.py`, `test_exchange_rejections.py`, `test_execution.py`, `test_fill_history.py`, `test_full_position_exit.py`, `test_missed_no.py`, `test_prepared.py`, `test_price_sides.py`, `test_protective_sells.py`, `test_purchase_cap.py`, `test_stop_63.py`, `test_trader.py`.

`test_online.py` recognizes the existing public strategy-version health field. `test_screen_lock.py` tests the existing asynchronous power monitor instead of an obsolete Tk callback; it also verifies that failing screen-lock inhibition does not interrupt sleep protection. `test_connection_recovery.py` keeps unchanged credentials, fee, cash and signing hashes and verifies runtime order calls, while intentionally repaired relay behavior is covered by behavioral tests instead of freezing obsolete whole-file hashes. Historical checksum documents were retained.

New tests: `test_stream_failures.py` verifies timeout classification, fallback, cleanup and sanitized bounded logging. `test_strategy_matrix.py` verifies both sides across 92–99-cent entry settings and 63/71/86/92-cent exits where valid, including exact trigger, just-above hold, full quantities, rebound and duplicate protection. `test_connection_status.py` covers degraded reporting without granting account readiness.

## Results

- Complete offline suite: **424 tests, zero failures, zero errors**, 36.278 seconds. Temporary databases used `/dev/shm`; local socket tests ran with the necessary OS permissions. No external exchange requests are made by this suite.
- All application and test Python files passed syntax parsing.
- Eight isolated demo-worker lifecycle runs passed: connect and reconcile while paused, Start, eligible YES or NO, confirmed synthetic fill, Pause, Quit. Start preserved the established book connection in every run.
- Generated RSA keys only. No real test orders. The mock reference relay now accepts its actual keyword argument, removing the previous fixture TypeError without inventing an executable reference price.
- Receipt-to-gateway submission: **median 11.38 ms, p95 16.82 ms, worst 17.19 ms**. Worker RSS 33.80–33.95 MiB. No balance GET, sizing calculation or full reconciliation ran on the critical submission path.
- These eight local measurements exclude Internet latency, exchange acknowledgment and fills. They are not a guarantee of live latency and are not directly comparable to the earlier lifecycle benchmark, which enabled trading before waiting for readiness.
- Detailed run data: [repair-lifecycle-test.json](repair-lifecycle-test.json).

## Read-only verification and remaining limits

`tests/read_only_connection_audit.py` uses the existing isolated gateway and a GET-only client. It mounts production history read-only, backs it up to a disposable database, and performs independent reconciliation on that copy while streaming market data. It does not instantiate an order broker or invoke pending-order recovery. It prints aggregate health, not credentials or account amounts. Its production result is recorded separately in `repair-read-only-audit.json`.

Actual result: **120.16 seconds, 10 completed account cohorts, zero account read errors, successful independent reconciliation**. Stream connected at 0.60 seconds, with no subsequent disconnects; 99.49% of samples were healthy including startup. The temporary journal contained 36 historical verification records. None were written back to production. This used the repaired source client with the **unchanged installed gateway**, so it is a point-in-time connectivity/account check, not proof that the new gateway has been deployed or that intermittent failures are eliminated. [Aggregate evidence](repair-read-only-audit.json).

The earlier observation that Start is followed by a disconnect remains a live correlation, not a proven direct teardown. Source tracing and the eight isolated lifecycle tests establish that successful Start does not recreate the feed. Actual production Pong/handshake delays require observation of the deployed repair; no timeout has been increased to conceal them.

The earlier handoff's two-buy maximum / 95-cent minimum conflicts with saved settings and the existing uncapped-entry implementation. This repair does not silently resolve that strategy discrepancy. Legacy uncapped and configurable-policy tests reflect the existing implementation; they are not proof of a two-buy cap.

## Deployment and rollback

Do not install while a trading worker has positions or pending orders. Deployment requires explicit approval under the handoff instructions. Prepare a dated backup of the installed files, then copy only the five production modules listed above. Desktop copies belong in `~/.local/share/kalshi-bitcoin-bot/kalshi_bot`; gateway copies belong in `/opt/kalshi-bot/kalshi_bot`. Preserve ownership and permissions. The gateway actually imports `streams.py`, `gateway.py`, and shared policy code from `/opt`, not the source directory.

After approval, stop only the verified inactive gateway service, install the reviewed modules, restart it, compare hashes, run the read-only audit, and reopen the desktop paused. Do not automatically enable trading. Rollback restores the backed-up files to their original paths and restarts the gateway under the same inactive-worker condition. Do not modify, restore or delete trading databases as part of code rollback.

Exact commands **for the approved maintenance window only**, after fresh confirmation that the worker is closed with no orders/positions:

```bash
cd /home/mint/Documents/Codex/2026-11-11/codex-master-prompt-build-kalshi-bitcoin
repair_backup=$(mktemp -d "$HOME/kalshi-connection-backup.XXXXXX")
mkdir "$repair_backup/desktop" "$repair_backup/gateway"
for module in strategy.py prepared.py streams.py gateway.py connection_status.py; do
    cp "$HOME/.local/share/kalshi-bitcoin-bot/kalshi_bot/$module" "$repair_backup/desktop/$module"
    sudo cp "/opt/kalshi-bot/kalshi_bot/$module" "$repair_backup/gateway/$module"
done
sudo systemctl stop kalshi-production-gateway.socket kalshi-production-gateway.service
for module in strategy.py prepared.py streams.py gateway.py connection_status.py; do
    install -m 644 "kalshi_bot/$module" "$HOME/.local/share/kalshi-bitcoin-bot/kalshi_bot/$module"
    sudo install -o root -g root -m 644 "kalshi_bot/$module" "/opt/kalshi-bot/kalshi_bot/$module"
done
sudo systemctl start kalshi-production-gateway.socket kalshi-production-gateway.service
for module in strategy.py prepared.py streams.py gateway.py connection_status.py; do
    cmp "kalshi_bot/$module" "$HOME/.local/share/kalshi-bitcoin-bot/kalshi_bot/$module"
    cmp "kalshi_bot/$module" "/opt/kalshi-bot/kalshi_bot/$module"
done
TMPDIR=/dev/shm python3 -B tests/read_only_connection_audit.py --seconds 120 --output docs/deployed-read-only-audit.json
printf 'Rollback backup: %s\n' "$repair_backup"
```

Rollback, using the saved `repair_backup` path and the same maintenance conditions:

```bash
sudo systemctl stop kalshi-production-gateway.socket kalshi-production-gateway.service
for module in strategy.py prepared.py streams.py gateway.py connection_status.py; do
    install -m 644 "$repair_backup/desktop/$module" "$HOME/.local/share/kalshi-bitcoin-bot/kalshi_bot/$module"
    sudo install -o root -g root -m 644 "$repair_backup/gateway/$module" "/opt/kalshi-bot/kalshi_bot/$module"
done
sudo systemctl start kalshi-production-gateway.socket kalshi-production-gateway.service
```

These commands were documented, not executed. A failed copy/hash/audit must stop the maintenance procedure rather than proceeding to live trading.

**Status: offline regression PASS; live deployment/readiness not yet verified.**
