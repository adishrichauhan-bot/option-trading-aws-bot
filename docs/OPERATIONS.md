# Operating guide

Updated 2026-11-13. This consolidates the former preparation, running and startup
guides. Use it for current commands; baseline-specific assessments and incidents
are linked from the [documentation index](./README.md). Phase 1 scope and
validation are in [implementation progress](./IMPLEMENTATION_PROGRESS.md).

## Application layout

| Component | Responsibility |
| --- | --- |
| [live_trader.py](../live_trader.py) | Main Telegram/manual trading CLI |
| [trading.py](../trading.py) | Parser, strategy, order lifecycle and standalone CLI |
| [startup_recovery.py](../startup_recovery.py) | Startup refusal gate and local account ownership |
| [telegram_trade_listener.py](../telegram_trade_listener.py) | Account session, message assembly and controls |
| [trade_lifecycle.py](../trade_lifecycle.py) | SQLite trade snapshots and startup audit |
| [position_sizing.py](../position_sizing.py) | Exact-contract lot metadata and funds sizing |
| [market_data_ws.py](../market_data_ws.py) | FYERS SymbolUpdate and mock feeds |
| [auth.py](../auth.py) | Interactive authorization and cached-token validation/refresh |
| [app.py](../app.py) | Separate market-data API |

The main flow is Telegram chunks -> complete call -> symbol resolution -> market
ticks -> strategy -> broker requests -> cumulative fill polling -> journal.
One trade is active; subsequent calls wait in memory.

## Installation and configuration

### Local Windows

From the repository root, run creation/copy commands only for a new setup:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
Copy-Item .env.example .env
```

Retain an existing environment and private configuration. Edit the copy using
the [environment template](../.env.example). Never commit credentials, keys,
token caches, Telegram sessions or credential-bearing logs.

### EC2

Connect from your local terminal with the current instance address:

```powershell
ssh -i "C:/path/to/ec2-key.pem" ec2-user@CURRENT_PUBLIC_IP
```

The established checkout is `~/option-trading-aws-bot`. For a first-time setup,
clone this project's GitHub repository there, then:

```bash
cd ~/option-trading-aws-bot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
cp .env.example .env
chmod 600 .env
```

Do not recreate/copy over an existing environment or configuration during an
update. GitHub access uses the server's own authorized deploy key/credential
helper; keep the EC2 login private key on your local computer. Dependencies are
in [requirements.txt](../requirements.txt). Playwright supplies optional browser
login; Selenium's conflicting websocket-client dependency is not used.

EC2 was observed running Python 3.9.25; initial local Phase 1 validation used
Python 3.12.0. Inspect the actual interpreter/SDK rather than assuming parity:

```bash
.venv/bin/python --version
.venv/bin/python -m pip show fyers-apiv3
```

### Settings

Configure these privately; add Telegram settings to the template for ingestion.

| Setting | Meaning/default |
| --- | --- |
| `client_id` or `FYERS_CLIENT_ID`, `secret_key`, `redirect_uri`, `grant_type`, `response_type`, `state` | FYERS app authorization |
| `token_file` | Default `~/.fyers/token.json` |
| `pin`, app secret and refresh token | Optional unattended refresh prerequisites |
| `browser_headless=true` | Use terminal redirect/code entry instead of Chromium |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE` | Telegram user-account login |
| `TELEGRAM_CHAT` | Actual intended chat ID/name |
| `TELEGRAM_SESSION` | Private account-specific path, e.g. `/home/ec2-user/.telegram/trade_listener_ec2` |
| `TRADE_REGISTRY_PATH` | Default `~/.fyers/trade_lifecycle.sqlite3` |
| `FYERS_SIZING_ROUNDING` | `floor`; explicit `ceil` skips over-budget results |
| `FYERS_MAX_QUANTITY`, `FYERS_FUNDS_RESERVE` | Unit cap 10000; cash reserve 0 |
| `FYERS_PRODUCT_TYPE` | `INTRADAY` |
| `FYERS_PRICE_TICK`, `FYERS_ATR_PERIOD` | Configured tick 0.05; ATR period 14 |
| `FYERS_TRAILING_PERCENT`, `FYERS_TRAILING_ATR_MULTIPLE` | Runner gap 3% and 2x ATR |
| `FYERS_ORDER_POLL_SECONDS` | Per-order polling cache interval 1 second |

Real quantity/lot size are automatic. `FYERS_QUANTITY` and `FYERS_LOT_SIZE`
are obsolete real-sizing controls. CLI `--quantity`/`--lot-size` are mock-only.

## Authentication

From the activated environment, run `python auth.py` each trading morning and
wait for `Fyers authentication is ready.` A token file existing is insufficient.
Login/OTP is interactive if refresh is unavailable. Manual login prints a URL
and accepts the redirect/code with hidden input; it refuses an echoed fallback
and therefore requires an interactive terminal. Install the optional browser
with `python -m playwright install chromium` when using browser login.

The trading path validates cached/environment token candidates and can refresh
with secret, refresh token and PIN, validates the result and updates the cache.
Keep all credentials private. Login readiness is not a guarantee of uninterrupted
authentication throughout an open position.

## Validation and input examples

Run the guarded suite from the root of either checkout:

```bash
.venv/bin/python -B tests/run_offline.py
```

On Windows use `.\.venv\Scripts\python.exe -B tests/run_offline.py`.
It supplies temporary state and blocks production configuration and external
connections. It does not start Telegram or a real broker client. The two
expected failures are unresolved tick-delivery/reconnect invariants.

Create a local signal file:

```text
NIFTY 23450 CE
BUY ABOVE - 140
TARGET - 150, 163, 180
SL - 127
```

Parse without broker access, then optionally run a deterministic mock:

```bash
python trading.py trade_call.txt --parse-only
python live_trader.py --signal-file trade_call.txt --telegram-chat "" \
  --mock-market --quantity 100 --lot-size 10 --interval 0
```

Empty chat explicitly selects file/manual input even if configuration selects a
Telegram chat. Mock-market disables FYERS auth/feed/orders, but can still contact
Telegram when a chat is selected. Use the guarded suite for isolated validation.
Custom `--mock-prices "139,141,126"` exercises this example's entry and stop.
The default sequence demonstrates T3, a higher high and retracement. An exhausted
feed with an open runner is an incomplete simulation, not a confirmed closure.

A range may replace BUY ABOVE: `TRIGGER RANGE - 137 - 140` or the `BUYING RANGE`
alias. CLI parsing accepts supported generic underlyings/exchanges and exact
FYERS symbols; Telegram syntax is narrower.

## Daily operation

### Update the checkout

Inspect changes/processes first. Preserve local configuration, sessions, signal
files and logs. With a clean tracked worktree, pull the reviewed commit:

```bash
cd ~/option-trading-aws-bot
git status --short
git pull --ff-only origin main
source .venv/bin/activate
python -m pip check
python -B tests/run_offline.py
git log -1 --oneline
```

Install dependencies again only when the dependency set changes or environment
validation requires it. Pull updates disk files; it does not restart a running
Python process. An older process predates the Phase 1 lock, so do not launch
another engine alongside it. Activation requires a deliberate handoff after
checking its broker state.

### Verify Telegram and start

Use `python telegram_trade_listener.py --list-dialogs` when verifying the account
or selecting the actual chat. First login may request phone/code/2FA. Changing
accounts requires the correct account-specific session. Do not pass an empty
shell variable as `--telegram-session`, which overrides the configured path.

After authentication and broker-state checks, inspect the existing session:

```bash
tmux ls
tmux attach -t trader
```

If no session/process exists, create it with `tmux new -s trader`. Inside it,
activate the checkout environment and run `python live_trader.py`. The engine
reads Telegram configuration directly. File/manual broker-backed execution can
use `--telegram-chat ""` and `--signal-file trade_call.txt`.

Normal broker-backed commands can submit live orders automatically on weekdays
09:00–15:40 IST, inclusive. Mode is selected once per trade, without a holiday or
submission-time gate. Outside that window execution is paper but still uses
real auth/quotes. There is no supported `--live` flag; a normal command is not
an inherently offline or dry-run test.

Both broker-backed CLIs acquire the local account lock and check broker
orders/trades/positions plus legacy journal before execution. The integrated
listener starts after this succeeds. Expected output includes `Startup
reconciliation passed; local account ownership acquired.` and the authenticated
Telegram/listening messages.

`Trading NOT READY` with exit 2 means startup refused:

| Reason | Meaning |
| --- | --- |
| `OPEN_ORDERS` / `OPEN_POSITIONS` | Existing broker orders/exposure; nothing is adopted or liquidated |
| `UNRESOLVED_JOURNAL` | Local legacy evidence is not conclusively terminal/flat |
| `OWNERSHIP_UNAVAILABLE` | Another local owner or lock/storage access problem |
| Broker read/identity/schema errors | Usable successful account evidence was not obtained |
| `RECOVERY_STORE_FAILED` | Startup decision cannot be persisted |

Check the reason; authentication is not the answer to every refusal. Do not
delete/edit journal or lock files to bypass it. OS ownership releases on death;
the lock filename may remain. Processes must share an OS user's home; the lock
cannot fence manual trades, other homes/hosts or an older running binary.

Only new messages after the listener baseline are consumed. Start before the
signal; never replay historical calls as new live instructions. Detach using
`Ctrl+B`, then `D`, and reattach to inspect.

### Shutdown

`Ctrl+C` stops local monitoring; it does not close broker positions. Verify broker
orders/exposure before stopping or restarting the engine or EC2. If stopping EC2
through AWS Console, choose **Stop instance**, not **Terminate**. Disk persists;
the public IP can change after restart unless fixed. The startup gate refuses
unresolved exposure and does not restore management of an old position.

## Strategy and order behavior

- Entry requires previous price at/below trigger followed by a price strictly
  above it. A first tick above waits for a fresh cross. Range entry uses the upper
  boundary, not buying anywhere inside the range.
- BUY ABOVE submits market entry. A range submits a limit at crossing LTP plus
  `min(max(1, 0.5 * ATR), 0.02 * LTP)`, rounded up to the configured tick. It is
  not fixed at the range upper bound.
- Exact-symbol `minLotSize` and fresh Available Balance determine whole-lot size
  at entry. Budget is balance minus reserve; floor is default and quantity is
  capped in lots. Explicit ceil skips over-budget results. Fees/slippage and
  order-specific margin can differ from the estimate.
- Acknowledgement is not fill. Broker IDs are polled; only confirmed cumulative
  fill deltas change exposure. Duplicate observations do not add twice;
  malformed fill quantities are rejected.
- T1/T2 book approximately 50%/30% of confirmed entry, rounded down to whole
  lots. Small sizes can allocate zero to either. Strict crossings advance SL.
  T3 retains the remainder and activates the runner with a T3 floor.
- On new highs, runner SL is `max(previous_SL, T3, high - gap)`, where
  `gap = max(high * trailing_percent / 100, ATR * trailing_ATR_multiple)`.
  Defaults are 3% and 2x ATR; SL never lowers after trailing starts.
- At/below SL, a local limit sell is placed 0.1% below SL, tick-rounded down.
  At/below 98% of active SL the emergency path uses market exit. Pending sells
  must reach confirmed terminal cancellation first; late fills reduce the
  replacement quantity. Cancel ACK is insufficient. This is not a resting
  broker-held stop or a guarantee of a maximum 2% loss.
- Reentry waits for confirmed closure and fresh complete risk levels. Unknown
  submit, rejected exit and uncertain cancellation stop progression instead of
  blindly resubmitting or starting the next trade.

Controls include `MODIFY ENTRY - BUY ABOVE - 320`, `MODIFY ENTRY - BUYING RANGE
- 220 - 230`, `MODIFY SL - 290`, `MODIFY TARGET - 305, 320, 380`, `CANCEL`,
`EXIT`, and `RE ENTRY - 135 - 145`. Levels must apply to the intended setup.
Phase 1 isolates existing typed amendment rejections; durable reply correlation,
pending cancellation and full domain-validation policy remain open.

The journal contains registered-trade snapshots and Phase 1 startup decisions.
Queued/manual/reentry paths do not yet have complete durable intent history.
Unread-tick loss, latched reconnect errors, stale-feed handling, automatic
restoration, independent protection and financial accounting remain unfinished.
See [review decisions](./reviews/ASTRA_REVIEW_APPROVAL.md) and the
[historical handoff](./history/HANDOFF.md) for policy/evidence boundaries.

## Telegram diagnostics

The standalone listener prints/parses messages and does not place broker orders:

```bash
python telegram_trade_listener.py --once --limit 100 --show-all
python telegram_trade_listener.py --once --message-id-range 100-150 --show-all
```

It uses configured chat/session; add `--chat ACTUAL_CHAT_ID` with the real desired
ID if needed. ID ranges are inclusive, accept `START:END` too and require
`--once`. Omit `--show-all` to print only recognized calls. Diagnostics are not
an instruction to trade historical messages.

## Market-data API

Run independently from the trading engine:

```bash
uvicorn app:app --host 127.0.0.1 --port 8000
```

From another terminal:

```bash
curl -i http://127.0.0.1:8000/health
curl -i "http://127.0.0.1:8000/market-data?indices=NIFTY,SENSEX&strike_count=3"
```

The API uses FYERS chain discovery and WebSocket quotes for NIFTY/SENSEX and
nearest ATM CE/PE. It requires usable credentials and closes request streams on
success/error. `/health` is API liveness only, not trading readiness, quote age
or protection health. Keep localhost binding unless exposure/access control is
separately configured. Stop foreground Uvicorn with `Ctrl+C`.

## Troubleshooting and maintenance

| Symptom | Check |
| --- | --- |
| Entry waits above trigger | `ENTRY WAIT`: no fresh cross observed; channel progress is not a broker tick |
| Sizing skip | Diagnostic funds/lot/reserve/cap/rounding; real manual quantity flags are unsupported |
| Authentication failure | Validate credentials/refresh; cache presence is insufficient |
| Wrong Telegram account | Correct private session, dialogs and chat; no empty shell overrides |
| Rejected protective exit / unknown order | Authoritative broker state; never assume flat or start another trade |
| Feed/process stops with exposure | Local protection is degraded/stopped; startup cannot take over automatically |
| Expected test failures | Known market-delivery/reconnect defects; remove markers only when fixed |

Use `git status` before updates/commits and inspect staged paths. Never force a
pull/reset over local edits. Keep current commands here and historical findings
in the review/history folders; do not duplicate operating procedures.
