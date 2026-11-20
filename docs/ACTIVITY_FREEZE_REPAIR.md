# Activity Log freeze and historical-order status repair

## Exact freeze cause

The dashboard registered a global mouse-wheel handler. For an event outside its canvas (including Activity Log and Trade History), it walked up widget parents. At the root, `winfo_parent()` returns an empty string, and `nametowidget('')` returns that same root. The loop never ended, blocking Tk's event thread and all buttons, including Close. This was not the database loading too much history.

Reproduced in a separate real Tk window: switching to Activity Log and generating Button-5 hung until the test timeout. The captured Python stack was `dashboard_wheel` → `nametowidget`. The live GUI was using approximately 44% CPU while its independent worker continued running.

`desktop.py` now stops at an empty parent, handles destroyed widgets, and detects unexpected cycles. Wheel events outside the dashboard return normally to the target widget's scroll handler.

## Why “Known order absent” appeared

The account's latest orders listing omits some old canceled orders. The worker independently verifies finalized historical markets before allowing entries. During startup those checks legitimately block entries. Inspection found 36 saved verification records and recent successful reconciled snapshots even while the frozen UI still showed earlier warnings. Successful reconciliation had no corresponding activity-log entry, leaving failure records as the newest visible audit entries.

`prepared.py` now records a reconciliation-success event when readiness transitions from false to true. It does not repeat the event on every successful cohort.

A second verified defect caused periodic interruptions: `MissingOrderVerifier` only requested new evidence after its ten-minute lifetime expired. It now starts renewing after five minutes, one background job at a time. A transient network/rate-limit/server failure may retain existing evidence only until its original expiry. Schema/auth failures revoke it. Expired evidence still blocks trading; the 600-second validity limit and fresh account reconciliation are unchanged. Startup still requires actual exchange evidence; historical records are not fabricated or deleted.

## Validation and installation

- Full offline suite: **431 tests passed**, zero failures/errors, 41.951 seconds.
- Real Tk test: **276 wheel events across four tabs**, completed and closed normally in 0.332 seconds.
- Tests cover root ancestry, destroyed widgets, unexpected cycles, Dashboard descendants, successful reconciliation log transitions, renewal before expiry, transient refresh failure, expiry, and schema rejection.
- A fresh authenticated read-only check confirmed zero positions and no nonterminal orders before stopping the old paused worker. The worker exited gracefully; the spinning desktop was then terminated.
- Installed `desktop.py`, `prepared.py`, and `missing_order.py` into the desktop installation, matching tested source hashes. These execute in the GUI/worker, not the gateway; no gateway restart was needed. Backup and hashes: `scroll-repair-deployment.json`.
- No strategy settings, credentials, production history, or order-validation rules were changed. No live orders were submitted or canceled by this maintenance.

The fix removes the reproduced event-loop hang. It cannot promise uninterrupted Internet service. Historical validation can still legitimately block entries if fresh evidence is unavailable or contradictory; the repair does not bypass that protection.
