# Runbook

Offline validation, read-only troubleshooting, and repository maintenance for
Option Trading AWS Bot. See [README.md](./README.md) for architecture, current
limitations, review findings, historical evidence, and unresolved policies.

Consolidated 2026-11-25 against local baseline `7afebb9`. Commands below do not
start the real-order engine or renew broker authorization. Broker-backed startup,
order submission, live activation, and deployment procedures are outside this
runbook. Local checkout state does not establish what is installed on EC2.

## Before investigating

Record the checkout and runtime without exposing private configuration:

```bash
git status --short
git log -1 --oneline
```

Use an existing environment when available. Dependencies are declared in
[requirements.txt](./requirements.txt); do not assume the observed historical
FYERS SDK or Python version matches the current environment.

On Windows:

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip show fyers-apiv3
```

On Linux/EC2:

```bash
.venv/bin/python --version
.venv/bin/python -m pip check
.venv/bin/python -m pip show fyers-apiv3
```

Never paste tokens, PINs, login redirects, private keys, Telegram sessions, or raw
credential-bearing responses into issue reports. A token cache existing does not
prove current authentication readiness. Consult [.env.example](./.env.example)
for setting names without printing the private environment file.

## Offline environment setup

For a new local development environment only:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

Linux equivalent:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
```

Installing packages requires registry access. The subsequent guarded tests do
not require production credentials or a configured Telegram session. Do not
replace an existing private configuration or token cache for a test.

## Guarded validation

Run from the repository root on Windows:

```powershell
.\.venv\Scripts\python.exe -B tests/run_offline.py
```

Linux/EC2:

```bash
.venv/bin/python -B tests/run_offline.py
```

The [runner](./tests/run_offline.py) disables dotenv loading, uses temporary
home/token/journal state, rejects real broker-client construction and HTTP,
and audits external socket/DNS and protected-file access. These are Python
process guards, not an operating-system sandbox. Keep the runner's final guard
report; a swallowed forbidden request still counts as a violation.

Record commit, interpreter/SDK, exit status, test totals, expected failures,
unexpected failures/errors/successes/skips, and guard violations. Historical
counts in [README.md](./README.md#historical-evidence) are not today's results.
The known unread-tick and reconnect expected failures are unresolved defects;
remove a marker only when the underlying invariant is fixed and verified.

Mock feeds can exhaust with an open simulated runner. Report incomplete rather
than inventing a closed outcome. A normal broker-backed command may select real
orders automatically and is not an offline test command. Use the guarded runner
for isolation from production configuration and external services.

## Read-only process and terminal inspection

In an already authorized EC2 terminal, inspect rather than start processes:

```bash
pgrep -af '[l]ive_trader\.py|[m]orning_startup\.py run'
tmux list-sessions
tmux display-message -p '#S'
tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index} #{pane_current_command}'
```

An empty process result means no matching command was found at that instant.
A tmux session can contain an idle shell; its existence does not prove the bot
is running. A Python process alone does not establish successful authentication,
healthy market data, readiness, or position protection.

Switching tmux clients changes the terminal view only. Detaching with `Ctrl+B`,
then `D` leaves processes running. `Ctrl+C` interrupts the foreground process;
it does not close broker positions.

For an already installed service, inspect its state and recent logs:

```bash
systemctl is-active fyers-trader.service
systemctl show fyers-trader.service -p ActiveState -p SubState -p ExecMainStatus
sudo journalctl -u fyers-trader.service --since today -n 70 --no-pager
```

That service belongs to a separately installed managed-startup workflow; its
source is not present in this rolled-back baseline. A missing unit is therefore
possible. Inspecting it neither installs it nor changes the service.

To capture the existing terminal history, replace SESSION with a name returned
by the session list:

```bash
tmux capture-pane -p -J -t SESSION -S -10000
```

Keep captures private and redact secrets before sharing. Preserve log timestamps
and timezone. UTC converts to IST by adding 05:30; the next untimestamped log line
cannot be assumed to have occurred at the preceding message timestamp.

## Interpreting common output

| Output / symptom | Meaning and next diagnostic step |
| --- | --- |
| `Trade call incomplete; waiting for the next chunk` | Assembly lacks required fields; inspect the original chunks, ordering, and IDs; it is not itself a crash |
| `signal_message_received` / `TRADE DIAGNOSTIC` | Additional diagnostic development emitted these; absent from this rolled-back baseline; source/deployment version matters |
| `ENTRY_WINDOW_CLOSED` | Separately deployed scheduled-entry code ended its entry window; inspect timestamp and deployed policy |
| `TODAYS_AUTH_REQUIRED` | Managed launcher did not accept today's credential proof |
| `AUTH_WAIT_DEADLINE_EXCEEDED` | Managed launcher stopped waiting for that proof at its configured deadline |
| `START_CUTOFF_PASSED` | Managed-startup command was attempted after its configured start cutoff; repeating it does not change the deadline |
| Service exit code 2 | A refusal/failure outcome; read the reason instead of assuming every occurrence is an authentication error |
| FYERS WebSocket lost/closed | Connection ended; distinguish network, shutdown, and authentication evidence from timestamps and surrounding logs |
| `KeyboardInterrupt` following `^C` | Foreground process was interrupted; a threading-shutdown traceback can follow interruption during cleanup |
| `client_loop ... Connection reset` | SSH transport ended; it is not by itself evidence of broker state or bot termination |
| BUY ABOVE waiting | No qualifying observed crossing; provider progress text is not a broker quote |
| Range entry waiting | Ask may be outside the calculated cap, missing/stale, or the opportunity invalidated/expired |
| Sizing skip | Check recorded funds/lot/reserve/cap/rounding inputs; do not treat it as a fill |
| `OPEN_ORDERS` / `OPEN_POSITIONS` | Startup observed pending broker orders or exposure; nothing is automatically adopted or liquidated |
| `OWNERSHIP_UNAVAILABLE` | Another local owner or lock/storage access failure; lock-file existence alone is insufficient evidence |
| `RECOVERY_STORE_FAILED` | Startup audit or required journal evidence could not be read/written |
| Malformed/failed broker read | Unknown evidence, never an empty/flat account |
| Unknown submit/cancel or rejected exit | Preserve uncertainty and authoritative evidence; do not infer flatness from a missing acknowledgement |
| Friday warning | Current IST Friday startup/new-entry restriction; not evidence that existing exposure was closed |

The managed-startup reasons above explain older/deployed logs; they are not a
claim that those components currently exist in this checkout or are running.

## Journal evidence and legacy acknowledgement

[journal_report.py](./journal_report.py) opens SQLite in read-only mode and does
not create a database when a path is wrong. Against a reviewed offline copy:

```bash
.venv/bin/python journal_report.py --journal /absolute/path/to/journal-copy.sqlite3
```

Windows equivalent:

```powershell
.\.venv\Scripts\python.exe journal_report.py --journal C:/path/to/journal-copy.sqlite3
```

The report contains trade ID, status, last update, remaining quantity, and order
IDs/status/quantities. A snapshot is not full intent/fill history. CLOSED alone
does not prove execution, account flatness, or realized profit. The current
reporter does not have the previously stashed order-events option.

[reconcile_journal.py](./reconcile_journal.py) is a separate mutating maintenance
tool, not a read-only report. It requires explicit acknowledgement, holds account
ownership, validates fresh broker evidence, refuses pending/nonzero exposure,
and appends account/fingerprint/note/time evidence while retaining original
snapshots. It does not restore monitoring or resolve authentication. Its launch
procedure is outside this diagnostic runbook.

Do not edit/delete journal rows or lock files to turn unknown state into apparent
readiness. Mock state uses a separate registry path. Database migration/recovery
experiments must use synthetic copies and preserve original evidence.

## Incident evidence checklist

Collect only the information needed to distinguish receipt, parsing, eligibility,
submission, acknowledgement, fill, and closure:

1. Commit/build and command type, Python/SDK versions, process/service state.
2. Redacted source message IDs, reply relationships, original chunks, timestamps,
   parser result, and explicit rejection/non-entry reason.
3. Relevant observed price/quote times and data errors, without inventing missing
   historical asks or assuming subscription request means a healthy feed.
4. Read-only journal evidence and independently obtained broker identifiers/status
   where available. An action print or acknowledgement is not a confirmed fill.
5. Missing records, uncertain outcomes, and unperformed checks stated explicitly.

A successful API profile check now does not establish earlier credential health.
Likewise a current quote does not reconstruct an earlier executable ask. Do not
replay historical instructions as a diagnostic action against a real account.

## Maintenance and rollback

Review local changes before updating or consolidating documentation. Do not force
pull/reset over local work. Preserve private configuration, account sessions,
logs, journals, and externally installed service definitions.

The earlier development rollback is preserved in a local stash named
`User-requested rollback 2026-11-25 - preserve for future discussion`. Stash
indices can change; inspect metadata without applying it:

```bash
git stash list
git status --short
git diff --stat
git diff --check
```

Do not reapply that work without discussing scope. This documentation task does
not alter that stash. Ignored private/generated experiment files were backed up
separately; they are not guaranteed to be in a stash or a permanent archive.

A code checkout/pull/stash changes disk state, not an existing Python process or
external deployment. Reverting code is not a broker recovery operation. Newer
journal schemas require a separately tested compatibility/migration plan; never
erase uncertain external effects to make a rollback look successful.

For documentation edits, verify the two maintained documents, relative file links,
heading anchors, and whitespace. Application tests are needed when code behavior
changes, not simply to re-prove an unchanged implementation after prose edits.
Original pre-consolidation documents remain in Git history at `7afebb9`.
