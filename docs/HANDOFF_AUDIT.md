# Enable Trading and connection audit — 2026-11-19

Follow-up repairs and corrected full-suite results are in [REPAIR_RESULTS.md](REPAIR_RESULTS.md). The findings below are the earlier audit snapshot, retained for traceability.

Readiness: **FAIL / not established**. Source-only repairs are staged. No production deployment, restart, live enablement, journal deletion, or live order was performed during this audit.

## Findings and evidence

1. `Application.start()` sends `start` to the attached worker. `prepared_runtime.run.poll()` sets `journal.running=True`; neither recreates the gateway, monitor, nor BookHub. Failed IPC can enter `connect()` and the normal stop/relaunch path. A direct lifecycle teardown on a successful Start command was not found.
2. Saved production records: Start at 18:37:04 Alaska time; missing historical-order reconciliation block at +0.60 seconds; first recorded stream TimeoutError at +30.18 seconds. Reconciliation blocks also occurred before Start. This supports correlation, not proof that Start caused a transport failure.
3. Gateway journal at 18:52:24 explicitly reports `phase=peer-verification error=TimeoutError`: an upstream Pong deadline failed. The service remained active. This is more precise than the earlier generic streaming-phase diagnosis. Whether network latency, exchange behavior, or scheduling delayed the Pong remains unresolved. The historical handshake failures have not been reproduced in this pass.
4. Volatility calculations were quadratic: four window queries plus a snapshot took approximately 153.02 ms with 900 samples despite a 100 ms runtime calculation interval. There is a second monitor-side tracker. This is a confirmed processing bottleneck and a possible contributor to scheduling delays; it is not proven as the sole connection-loss cause.
5. `PreparedEngine.execute()` could show entry-window waiting while account reconciliation was incomplete. The next iteration replaced the previous blocked status. The new explicit readiness guard preserves the actual account blocker. Existing submission guards already blocked unconfirmed accounts.
6. Historical verification is no longer zero: the read-only journal inspection found **24 verification records** and a successful snapshot about 21 minutes old at inspection, with the app closed. The latest paused diagnostic showed no position or pending order, but this is historical evidence, not a fresh exchange position check. No fabricated proof or record rewrite was used.
7. No Git repository is present. Backups exist, but do not provide a complete original baseline. Claims that previous patches preserved all original routes cannot be established from Git.

## Deployment inventory

Source, desktop and `/opt` copies matched for desktop, desktop_runtime, prepared_runtime, prepared, missing_order, streams, gateway and strategy before this audit. `execution.py` differed in the clearer journal-lock error message. `/opt/connection_status.py` differed from source/desktop. The active system service executes Python from `/opt/kalshi-bot`; desktop workers bind the user installation into `/app`.

Saved settings are buy minimum **96**, sell **86**, entry **240 seconds**, closing block **30 seconds**, volatility limits **25/20/15**. The code permits configurable minima and removed the purchase cap in an earlier change. Thus the handoff's 95-cent minimum and two-buy maximum do **not** match the installed policy. These strategy differences were not silently changed by this repair.

## Previous patches assessed

- Refresh `None.notify()` repair: justified by code and repeated crash locations.
- Separate market and account status and treating historical 404 as a request outcome: justified; does not establish reconciliation.
- Bounded background missing-order verifier: produces evidence now; earlier claims that it remained entirely broken are outdated.
- Gateway HTTP 200: only local acceptance. BookFeed prices require a valid sequenced snapshot and freshness, so HTTP 200 alone does not authorize buys.
- Socket idle timeout changed from 4 to 6 seconds: five-second quote freshness remains independent. Successful short probes did not establish long-term reliability; genuine peer-verification timeouts persisted.
- Error logging: sanitized class and phase are useful, but current warnings are not rate-limited as the older handoff logger was. Bounded diagnostic logging remains follow-up work.
- Prior assertions that an invisible process was an obsolete verifier were unsupported: the restricted process namespace hid the actual worker.

## Source repairs and meaningful diffs

`kalshi_bot/strategy.py`: replace repeated full-history high/low comprehensions with monotonic min/max queues. Inclusive endpoints, duplicate timestamps, gaps, unordered observations and existing averaging definitions are preserved. Same microbenchmark after repair: 60 samples 0.49 ms; 300 samples 1.72 ms; 900 samples 6.75 ms (before: 1.79/28.72/153.02 ms). These are development-host measurements, not guaranteed laptop latency.

`kalshi_bot/prepared.py`: after the existing paused/position/order handling, return `Account reconciliation incomplete; new buys blocked` whenever the journal is not ready. Protective handling retains priority.

`tests/test_volatility_equivalence.py`: deterministic comparison against the original range definitions and a stale/unconfirmed account reporting regression.

## Verification

- 43 focused tests passed: volatility equivalence, connection reporting, market pipeline, historical-order repair.
- 3 final targeted tests passed after the readiness-reporting guard.
- Real isolated demo-worker lifecycle with a synthetic gateway: two samples, YES and NO; generated RSA credentials, three contracts each, no production credentials or real orders. Connect/Start/fill/Pause/Quit succeeded. Receipt-to-gateway latency 148.55–286.77 ms; median 217.66 ms. Logs included `Stream closed: error=TypeError` from the synthetic reference subscription; lifecycle completion is not proof of all-stream correctness. Details: `handoff-lifecycle-test.json`.
- Full discovery suite: **417 tests ran in 18.343 seconds; FAILED with 234 failure entries and 845 error entries (including subtests)**. No valid passed-test count can be inferred by subtracting subtest counts from 417. Examples include fixtures assuming 92–95-cent eligibility against a different configured minimum, fixtures expecting a 71-cent sell while the active test policy uses 92 cents, and old source-hash invariants. Other failures remain untriaged. These results are a release blocker; they were not suppressed or rewritten to force green. The initial disk-backed run was interrupted after prolonged filesystem-journal waits; the completed run used `/dev/shm` only for disposable test databases.

## Security and remaining work

No credential material was printed. Production records were opened read-only. API allowlists, pinned destinations, firewall rules, credential mounts, five-second freshness checks and order authorization were not edited. The source repair changes calculation cost and reporting, not the trading policy.

Still required: longer production read-only observation under simultaneous account workload; handshake-stage reproduction; complete full-suite review; explicit resolution of the strategy mismatch; verified fresh account reconciliation in an isolated dry-run harness with all write methods disabled; bounded stream-failure logging; and a controlled activation test after deployment approval. A healthy systemd service or passing unit tests alone is insufficient.

## Deployment and rollback (approval required; not executed)

The source repair affects only `strategy.py` and `prepared.py`. Gateway files need no new deployment for these repairs. First confirm the bot is paused, flat, and closed using current evidence. Save the installed files in a fresh backup directory, then install the reviewed source files:

```bash
cd /home/mint/Documents/Codex/2026-11-11/codex-master-prompt-build-kalshi-bitcoin
backup_dir=$(mktemp -d "$HOME/kalshi-repair-backup.XXXXXX")
cp /home/mint/.local/share/kalshi-bitcoin-bot/kalshi_bot/strategy.py "$backup_dir/strategy.py"
cp /home/mint/.local/share/kalshi-bitcoin-bot/kalshi_bot/prepared.py "$backup_dir/prepared.py"
install -m 644 kalshi_bot/strategy.py kalshi_bot/prepared.py /home/mint/.local/share/kalshi-bitcoin-bot/kalshi_bot/
printf 'Rollback backup: %s\n' "$backup_dir"
```

Reopen in simulation and verify before connecting production. Do not start real trading automatically. To roll back, close the app and install the two saved files from the printed backup directory into the same installed package directory. Preserve production databases throughout. The `/opt` copies should be brought into parity only as part of a separately reviewed deployment; replacing all files through the broad setup installer is unnecessary for these two changes.
