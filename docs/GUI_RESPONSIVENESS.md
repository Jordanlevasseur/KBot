# Desktop freeze repair

The two-second power callback synchronously queried D-Bus and could start or
close inhibitors. The sleep handshake waits up to five seconds; holder cleanup
can wait seven seconds; D-Bus name-owner lookup and session connection can wait
on the session service. Multiple sequential operations delayed all Tk events.
Limiting Activity to 100 rows did not address these waits.

`PowerMonitor` now creates, checks, repairs and releases both inhibitors on one
daemon thread. That thread owns its private D-Bus connection throughout its
lifetime. It reports loss before attempting recovery and serializes all repairs.
Tk polls plain messages. Loss requests an entry pause (retried if the worker pipe
is full), blocks Start, and leaves the worker and protective exits running.
Recovery never sends Start. Startup cannot connect a worker before sleep
protection succeeds. Power cleanup never joins a stuck service call on Tk.

Other potentially blocking GUI paths were audited:

- Worker stdin write/flush could block indefinitely if its reader stalled. Small
  commands now use atomic nonblocking pipe writes and report backpressure.
- Gateway health HTTP, sandbox path preparation, and worker process creation
  now run in background jobs. The existing gateway checks and isolated command
  are preserved. Completion callbacks are queued and consumed only by Tk.
- Strategy persistence (including fsync), guide reads, and log emission now run
  off the UI thread. Strategy persistence serializes saves and blocks Start and
  Connect until completion. Strategy validation and the trading rules are unchanged.
- Worker stop already waited in a thread, but its final post-kill wait was
  unbounded. It now has a timeout. Reader threads own output-stream cleanup,
  avoiding closing a stream while another thread holds its blocking read lock.
  The GUI also has a 15-second close deadline. A delayed process launch checks
  closing and stops itself if shutdown raced with creation.
- Profit/database reading and gateway installation already run in background
  threads. The profit reader's destroy hook has a bounded 0.2-second join.
- Tk queue draining now yields after 15 ms or 100 worker messages. Individual
  render callbacks can still exceed that budget. Activity remains capped at 100
  display rows with a one-second refresh; the latest pending rows flush even if
  no further state arrives. Trade history is uncapped and can cost CPU/Tk time
  for large histories; table equality checks avoid unchanged redraws.
- Intentional modal confirmations/file choosers remain on Tk. Credential
  selection/stat/chmod and credential validation/save remain synchronous and
  unchanged to preserve authentication behavior. Startup path creation, saved
  strategy loading, logger construction and instance locking also still perform
  local filesystem operations before the event loop. These are residual manual
  or startup latency sources, not the periodic power freeze.

Simulation `Engine.stop()` still cancels active entry orders and preserves
protective sells. It records a control event only when running changes or an
active buy is canceled. Existing history is never deleted or rewritten.

Tests use mocks, synthetic workers and temporary databases/configuration.
No installed application, production database, credentials, authentication,
gateway isolation, reconciliation rules or trading strategy is changed.

## Validation in this workspace

- Final targeted run: 28 tests passed (desktop protocol/isolation, power lifecycle,
  callback threading, shutdown deadlines, backpressure, pending Activity refresh,
  mode switching, stop-event idempotency and protective-order preservation).
- Python compilation passed for all four modified application modules.
- Full core module run: 31 tests, five failures and one error. Replacing only
  `Engine.stop` in memory with its original implementation and excluding the new
  regression reproduced the same five failures and one error across 30 tests.
  Existing tests expect older 86-cent protective sells or allow entry sizing below
  the configured threshold. Trading strategy was deliberately preserved.
- Whole-repository discovery was attempted and interrupted after widespread
  failures/errors; it is not a passing full-suite result.
- GUI smoke command was run with fresh temporary XDG state/config directories.
  It exited with “A graphical desktop session is required.” Tk could not access
  a graphical display here. Real GUI rendering remains unverified in this
  environment; the headless callback regressions passed.
