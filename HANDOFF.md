# Option Trading AWS Bot — Development Handoff

Last reviewed: 2026-11-10 (Asia/Calcutta)

## Automatic sizing implementation

[position_sizing.py](./position_sizing.py) now obtains the exact contract's
`minLotSize` from the Fyers symbol master and reads fresh Available Balance
at the breakout. Real trades ignore FYERS_LOT_SIZE/FYERS_QUANTITY; explicit
quantity/lot-size options are mock-only. Both entry points size against the
actual limit price or market-entry LTP through BreakoutTrader's entry_sizer
hook, before submission. Existing confirmed-fill/exit/trailing logic remains.

The original ceiling request is available as `FYERS_SIZING_ROUNDING=ceil`
(CLI default); an over-budget ceiling result is SKIPPED_SIZING, never an
unaffordable live order. `floor` selects affordable whole lots. The rounding
choice was raised with the user because ceiling normally exceeds a fully
allocated cash budget. See current deployment/chat status for the selected
production mode. FYERS_MAX_QUANTITY remains a cap and FYERS_FUNDS_RESERVE
defaults to zero. Local sizing regression tests cover both rules, metadata,
funds lookup, price/funds changes and no-entry failure behavior.

## Nov-12 incident and continuation update

Production evidence: the first 09:27 SENSEX 82100 CE call parsed correctly,
but its trade was marked authentication-required and the engine exited.
The user logged in again and restarted around 10:33 IST; listener baseline
55195 excluded the prior calls intentionally. A profile check during this
investigation succeeded, but unattended refresh was still not configured.
Yesterday's successful login does not ensure next-morning authentication.

Implemented pending-entry replacement before initial targets/SL, and split
re-entry assembly with a remembered instrument and fresh targets/SL. A
re-entry waiting for risk levels closes an active prior trade but cannot
queue an entry with stale levels. New instrument headers reset incomplete
state; invalid calls are rejected without killing Telegram consumption.
Regression cases cover the supplied 340 buy-above, 390–400 range, 350–360
range, 355 re-entry with SL 335, and 270–280 recovery modification.

Local validation: 64 tests passed; existing broker lifecycle/trailing tests
remain green. No historical live orders were replayed. Broker diagnostics
reported an existing open position, so a live-engine restart requires position
reconciliation; do not assume the bot restores it. Startup reconciliation
remains the next separate development task.

## Authentication incident and readiness fix — 2026-11-11

After deploying the Telegram routing fix, EC2 logs confirmed correct assembly
of SENSEX 82000 PE (330 entry) and SENSEX 82100 PE (380 entry). Both failed
before symbol resolution because Fyers rejected the cached access token.
A read-only profile check confirmed code -16, invalid/expired token. EC2 had
a refresh token and secret but no configured `pin`, so unattended refresh was
not possible. The listener restarted at message 55100, after the morning NIFTY
calls; it intentionally does not backfill old calls.

The trading path now validates/refreshes credentials before Telegram startup
and each trade, with no interactive browser prompt inside the listener.
Missing refresh prerequisites require `python auth.py` on EC2. Authentication
failures stop consumption visibly instead of returning to apparent readiness.
The updated runbook validates authentication rather than token-file existence.

An offline fixture reproduces all four user-supplied calls, commentary, media
placeholders, and exits. The replay emits exactly four complete calls, survives
the engine parser, and de-duplicates event/poll delivery. Local validation:
57 tests passed. Live recovery still requires the account holder's interactive
login or private PIN configuration; never request credentials in chat and never
replay the historical messages into the live engine.

## Telegram signal-routing fix — 2026-11-11

Server message 55091 (`TARGET - 174, 186, 202` followed by `SL - 152`)
was misclassified as a target modification and discarded without an active
trade. Plain target fields now remain initial-call chunks while assembly is
pending; only explicit `MODIFY TARGET` text is a direct command. Standalone
target/SL updates remain supported when no call is being assembled. Target
modification parsing stops at the next field label and requires three prices,
so SL cannot become a fourth target.

[Routing regression tests](./tests/test_telegram_message_routing.py) cover the
server chunk, split targets/SL, a complete single-message call, explicit updates,
standalone updates, and duplicate delivery. Seven tests failed before the fix;
all eight passed afterwards. Full suite: 47 tests passed on local Python 3.12.
This fix does not replay missed signals or restart/deploy the server.

## Implementation update — 2026-11-10

This update supersedes older descriptions below of T3 liquidation and missing
order confirmation. See [current execution details](./RUNNING.md#current-trading-behavior-2026-11-10).

- Implemented T3 runner: retain the remainder after T1/T2, start SL at T3, then
  ratchet on new highs using the larger of a 3% gap or 2x one-minute ATR. Both
  parameters are configurable, and the stop cannot be lowered by a command once
  trailing is active.
- Implemented Fyers order-ID polling with cumulative fill accounting for market
  and trigger-range limit entries and all exits. An acknowledgement is not a
  fill. Partial fills update actual exposure exactly once.
- Active-stop limit exits have the 2% breach market fallback. Cancellation must
  reach a confirmed terminal status before a replacement market sell; late
  fills are deducted first. Direct gaps below 98% of active SL use market exits.
- All crossed target levels advance SL even if an earlier target sale is pending.
- Registered Telegram trades journal order IDs/status/fills and remaining size.
  Re-entry waits for confirmed closure; uncertain broker exposure stops the
  listener from advancing to another trade.
- Added [broker lifecycle tests](./tests/test_order_lifecycle.py),
  [trailing tests](./tests/test_trailing_runner.py), and
  [live-loop integration tests](./tests/test_live_order_integration.py).
  All original test files remain unchanged.
- Validation: 39 tests passed using Python 3.12 locally; no live orders or live
  broker/Telegram/EC2 validation performed. Python 3.9 runtime validation remains
  unverified.
- Exact next task is now **startup reconciliation of Fyers open orders and
  positions before accepting any signal**, using the journal plus authoritative
  broker state to prevent duplicate entries/exits after restarts. This is not
  implemented by this update.

The original audit below is retained as historical context. Its initial Git
state and test counts refer to handoff creation, not the current worktree.

## Scope and evidence

This handoff is based on the repository contents, existing project documentation, the current Git state, the available local session metadata, and the test run recorded below.

The historical chats associated with this project are visible locally as session metadata, but their complete message bodies are not exposed to this assistant. Therefore, claims below are based on code and documentation unless explicitly marked as inferred or unverified. The accessible current chat contains the request to prepare this handoff; no prior complete transcript was available.

There are no project-specific skill files in this repository. The external coding-agent skills available in the current environment are listed in [Existing skills](#existing-skills); they are not application dependencies.

## 1. Project purpose and architecture

This project is an AWS/EC2-hosted Python options-trading bot for Fyers. Its current product surface has two related paths:

1. A FastAPI market-data MVP exposing NIFTY 50, SENSEX, and nearest-at-the-money option information.
2. A persistent Telegram-to-Fyers trading engine that assembles trade calls, resolves the Fyers option symbol, receives live WebSocket prices, executes a breakout/target/stop-loss state machine, and accepts control messages while a trade is active.

The main live flow is:

```text
Telegram user-account messages
  -> TradeCallAssembler / control-command parser
  -> validated TradeCallMessage
  -> live_trader.py
  -> trading.py TradeCall parser
  -> Fyers option-chain or symbol-master resolution
  -> Fyers market-data WebSocket
  -> BreakoutTrader state machine
  -> FyersOrderExecutor
  -> Fyers order API
  -> TradeRegistry SQLite lifecycle journal
```

The standalone API flow is:

```text
HTTP request -> app.py -> Fyers option-chain REST lookup + FyersMarketDataSocket
             -> JSON index/ATM-option response
```

The system is deliberately single-trade-at-a-time in the integrated engine. New complete Telegram calls are queued while an active trade is running.

## 2. Technology stack and dependencies

- Python: local virtual environment is Python 3.12.0; project documentation says the EC2 deployment uses Python 3.9, so Python 3.9 compatibility must be preserved.
- FastAPI and Uvicorn: HTTP API and local service hosting.
- Fyers API v3 (`fyers-apiv3`): authentication, option-chain lookup, market-data WebSocket, and order submission.
- Playwright: interactive Chromium login fallback for Fyers authentication.
- Telethon: Telegram user-account listener and session management.
- `requests`: Fyers refresh-token and public symbol-master downloads.
- `python-dotenv`: `.env` configuration loading.
- Standard library: `sqlite3`, `threading`, `queue`, `argparse`, `dataclasses`, `unittest`, and related modules.

Declared dependency ranges are in [requirements.txt](./requirements.txt). The installed local environment was observed with: `fastapi 0.162.2`, `fyers-apiv3 3.2.1`, `playwright 1.64.1`, `python-dotenv 1.2.4`, `requests 2.31.0`, `telethon 1.45.0`, `uvicorn 0.54.0`, and `websocket-client 1.6.1`.

The project intentionally does not use Selenium because the preparation notes record a dependency conflict: Fyers requires `websocket-client==1.6.1`, while newer Selenium versions require a newer websocket-client. Playwright is used instead.

## 3. Completed features and implementation details

### Authentication and token handling — completed

Implemented in [auth.py](./auth.py):

- Fyers authorization-code flow using the configured redirect URI, client ID, secret, grant type, response type, and state.
- Browser-based login with Playwright/Chromium when available.
- Manual URL/code paste fallback for headless EC2 or environments without `DISPLAY`.
- Redirect-state validation and login-error handling.
- Cached access and refresh tokens at `~/.fyers/token.json` by default, with atomic replacement and restrictive permissions where supported.
- Access-token validation through a Fyers profile request.
- Optional refresh-token flow using `pin`.
- Optional `token_file`, `FYERS_ACCESS_TOKEN`, and environment-based overrides.

### FastAPI market-data MVP — completed, live integration dependent

Implemented in [app.py](./app.py):

- `GET /health` returns `{"status":"ok"}`.
- `GET /market-data` accepts `indices=NIFTY,SENSEX` and `strike_count` from 1 through 10.
- Supports NIFTY 50 (`NSE:NIFTY50-INDEX`) and SENSEX (`BSE:SENSEX-INDEX`).
- Uses the Fyers option chain to identify the nearest strike to the streamed spot price.
- Returns spot, change, change percentage, and ATM CE/PE metadata including symbol, LTP, bid, ask, open interest, and implied volatility when supplied by Fyers.
- Uses the shared market-data WebSocket rather than polling the REST quotes endpoint.
- Returns clear HTTP errors for invalid indices, missing credentials, and Fyers failures.

### Thread-safe market-data WebSocket — completed

Implemented in [market_data_ws.py](./market_data_ws.py):

- Wraps the official Fyers `FyersDataSocket` SymbolUpdate feed.
- Supports lazy connection on first subscription, dynamic subscriptions, latest-price lookup, first-message waiting, and newer-tick waiting.
- Uses locks/events/sequences so callers do not process the same tick twice.
- Propagates WebSocket errors to waiting callers.
- Provides `MockMarketDataSocket` for deterministic offline tests.

### Trade-call parsing and symbol resolution — completed

Implemented in [trading.py](./trading.py):

- Parses shorthand instruments such as `NIFTY 23450 CE`, generic index names, exchange-qualified underlyings, and full Fyers symbols.
- Accepts `BUY ABOVE` and both `TRIGGER RANGE` and legacy `BUYING RANGE` formats.
- Requires three ascending targets and a stop-loss below the entry trigger.
- Resolves option symbols from the Fyers option chain, then falls back to the daily public Fyers symbol master cached under `~/.fyers/sym_master`.
- Supports NSE, BSE, and MCX master-code selection.

### Breakout/target/stop-loss strategy — completed, with live-order limitations

Implemented in [trading.py](./trading.py):

- Requires positive quantity and quantity divisible by lot size.
- Does not enter on the first price; it requires an observed upward cross.
- `BUY ABOVE` submits a market buy on the upward cross.
- A trigger range crosses when price moves above its upper boundary and submits a limit order using the current LTP plus an ATR-derived buffer capped at 2% of price.
- Builds one-minute ATR from timestamped ticks; default period is 14 completed bars and is configurable with `FYERS_ATR_PERIOD`.
- Exits approximately 50%, 30%, and the remainder at targets, rounded according to lot size.
- Advances the active stop-loss to target 1 and then target 2.
- Exits remaining quantity when price crosses below the active stop-loss.
- Supports manual exit, cancellation, entry modification, stop-loss modification, and target modification.
- Tracks pending exits and sends one emergency market exit if price reaches more than 2% below the stop-loss associated with the pending exit.
- Automatically selects LIVE only Monday-Friday, 09:00–15:40 IST; otherwise it uses dry-run mode. This is a time-window rule, not an exchange-calendar rule.

### Fyers order execution — completed

Implemented in [trading.py](./trading.py):

- Builds Fyers market and limit order payloads with configurable product type (`FYERS_PRODUCT_TYPE`, default `INTRADAY`).
- Prints explicit order lines and JSON payloads.
- Suppresses broker submission in dry-run mode.
- Treats missing live order status as pending rather than assuming a fill.

### Telegram listener and message assembly — completed, operationally dependent

Implemented in [telegram_trade_listener.py](./telegram_trade_listener.py):

- Uses a Telethon user session, not a bot token.
- Lists dialogs and verifies the authenticated account.
- Reads a configured chat continuously or in one-shot mode.
- Assembles multi-message trade calls in message-ID order.
- Cleans decorative text and parses instrument, entry, three targets, and stop-loss.
- Emits parsed calls as JSON lines.
- Supports displaying recent messages and inclusive message-ID ranges.
- Parses control commands including `MODIFY ENTRY`, `MODIFY SL`, target updates, `CANCEL`, `EXIT`, and `RE ENTRY` through [trade_lifecycle.py](./trade_lifecycle.py).

### Persistent integrated engine — completed, with recovery gaps

Implemented in [live_trader.py](./live_trader.py):

- Reads a signal file or waits for Telegram calls.
- Validates quantity, lot size, and maximum quantity (`FYERS_MAX_QUANTITY`, default 10,000).
- Assigns persistent IDs such as `FB-YYYYMMDD-0001`.
- Stores lifecycle data in SQLite, defaulting to `~/.fyers/trade_lifecycle.sqlite3`.
- Runs one active trade and queues later calls.
- Keeps the Telegram listener alive after completion, cancellation, manual exit, or an ordinary trade error.
- Applies in-trade control messages to the active state machine.
- Supports deterministic `--mock-market` and `--mock-prices` runs without Fyers authentication or real orders.

### Documentation and operational runbooks — completed but still evolving

Existing operational material is in [RUNNING.md](./RUNNING.md), [preparation.md](./preparation.md), and [startTradeRunbook.md](./startTradeRunbook.md). It covers local setup, EC2 setup, Fyers authentication, Telegram verification, tmux operation, mock testing, live startup, shutdown, and credential safety.

## 4. Features currently being developed or only partially complete

No source file contains an explicit in-progress marker. Based on the implementation and the documented risks, these are partially complete rather than production-complete:

- Live order lifecycle: order submission exists, but trigger-range entry is marked entered after broker acknowledgement without confirming that the limit order filled or handling partial fills.
- Restart recovery: lifecycle records are persisted, but an open broker position/order is not reconstructed into the strategy after a process crash or EC2 restart.
- Exchange-calendar awareness: live mode uses weekday/time only and does not exclude holidays or special sessions.
- Runtime resilience: a market-data timeout or some WebSocket failures can stop the engine instead of reconnecting/retrying through a supervised recovery path.
- Concurrent trading: intentionally unsupported; only one active trade is processed at a time.
- Production deployment: the documented execution model is manual EC2 plus tmux; no service unit, supervisor, health monitor, or automated deployment is present in this repository.

## 5. Pending tasks and known bugs/risks

The following risks are explicitly documented in [preparation.md](./preparation.md) or are directly evident from the current code:

1. Confirm live limit-order fills and partial fills before treating a trigger-range entry as active.
2. Reconcile Fyers positions and open orders on startup/restart before accepting another entry.
3. Add exchange-holiday/session-calendar handling before relying on automatic LIVE mode.
4. Add recovery/reconnect behavior for market-data timeouts and WebSocket disconnects.
5. Decide and implement a safe policy for live order errors; currently an order exception stops the active trade, then the persistent wrapper returns to Telegram listening.
6. Keep the one-active-trade policy explicit, or design and test concurrency if the product requires it.
7. Verify Fyers symbol-master parsing against current daily symbol-master formats and expiry semantics before live use.
8. Add integration tests with mocked Fyers responses for authentication, option-chain resolution, order rejection, fill transitions, and restart reconciliation.
9. Review the manually edited [startTradeRunbook.md](./startTradeRunbook.md) before committing; it is currently modified in the worktree and was not changed by this handoff.
10. Treat `.env`, Telegram session files, token caches, PEM keys, and logs containing credentials as private. The current `.gitignore` excludes `.env`, virtual environments, caches, and temporary files, but not every possible credential filename.

## 6. Important technical decisions and rationale

- WebSocket market data is used for strategy prices because the documented MVP moved away from REST quote polling and needs newer-tick semantics.
- Playwright replaced Selenium because of the recorded `websocket-client` dependency conflict with `fyers-apiv3`.
- Authentication tokens live outside the repository to reduce credential exposure and to support both local and EC2 workflows.
- Dry-run is the default safety posture; LIVE is selected automatically only within the weekday IST window, except `--mock-market`, which always forces dry-run.
- Entry is cross-based rather than first-tick-based to avoid entering on a stale or already-above-trigger initial quote.
- Trigger-range entries use current price plus a capped ATR buffer instead of blindly using the range ceiling, to improve the chance of a fill while limiting the extra price.
- Exits are market orders and are divided approximately 50/30/20, with lot-size rounding so broker quantities remain valid.
- The stop-loss trails to target 1 and target 2 to protect realized gains.
- SQLite is used for a small local lifecycle journal and stable daily trade IDs; it is not yet a broker-state recovery database.
- Telegram uses a user session because the current workflow listens to an existing account/chat and may require account login/2FA.
- Fyers remains authoritative for actual order and position state. Terminal output saying an order was submitted is not proof of a fill.
- The FastAPI service binds to localhost in the documented EC2 commands, avoiding direct public exposure.

## 7. File and folder structure

```text
.
├── app.py                       FastAPI health and market-data endpoints
├── auth.py                      Fyers login, refresh, validation, token cache
├── live_trader.py               Persistent Telegram-integrated trading entry point
├── market_data_ws.py            Thread-safe live/mock market-data adapters
├── telegram_trade_listener.py   Telethon listener, assembler, parser, CLI
├── trade_lifecycle.py           Control-command parser and SQLite trade registry
├── trading.py                   Trade-call parser, symbol resolver, strategy, orders
├── requirements.txt             Version-ranged Python dependencies
├── .env.example                 Non-secret configuration template
├── .gitignore                   Excludes environment, cache, and temporary files
├── README.md                    Minimal project title only
├── RUNNING.md                   Basic local/API/auth/trading commands
├── preparation.md               Detailed development, EC2, Telegram, and risk notes
├── startTradeRunbook.md         Short EC2/tmux live-start runbook
├── HANDOFF.md                   This continuation document
├── tests/
│   ├── test_live_trader.py      Persistent engine and mock-market tests
│   ├── test_market_data_ws.py   WebSocket wrapper/mock tests
│   ├── test_trade_lifecycle.py  Command parsing and SQLite ID tests
│   └── test_trading_safety.py   State updates, pending exit, emergency exit tests
├── .venv/                       Local virtual environment; ignored
├── __pycache__/                 Python bytecode; ignored
└── .env                         Local secrets; ignored and must not be committed
```

## 8. Existing skills

There are no application-specific skills under the repository. The current coding environment exposes these external skills:

- `add-python-version-support`: prepare/test support for a newer upper-bound Python version.
- `drop-python-version-support`: raise the minimum Python version and modernize accordingly.
- `pylance-docs`: consult official Pylance behavior, settings, diagnostics, and troubleshooting.
- `pylance-python-profiling`: profile Python CPU, call, or memory behavior with Pylance-supported tools.
- `pylance-refactoring`: perform Pylance-guided Python refactoring.
- `python-add-type-annotations`: add Python annotations using code-grounded type information.
- `python-fact-grounded-coding`: require repository/interpreter evidence before changing or reporting Python behavior.
- `python-type-inference`: determine a specific Python symbol type from real code.
- `python-write-stubs`: create or improve Python `.pyi` stubs.
- `typescript-setup` and `typescript-update`: TypeScript project setup and TypeScript version upgrades; neither is currently applicable to this Python project.
- `imagegen`: generate/edit raster images; not applicable to the current trading engine.
- `openai-docs`: documentation for OpenAI/Codex products; not an application dependency.
- `review-agent`, `skill-creator`, and `skill-installer`: agent review, skill authoring, and skill installation workflows.

Use a skill only when the next task matches its stated scope. Do not add skills or dependencies merely because they are available.

## 9. Environment setup and execution commands

### Local PowerShell setup

```powershell
cd "C:\Users\prabh\WorkSpace\option-trading-bot-aws\option-trading-aws-bot"
\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m pip check
```

Copy [.env.example](./.env.example) to `.env` and fill in secrets privately. Never print or commit `.env`.

### API

```powershell
python -m uvicorn app:app --reload
```

Endpoints:

```text
GET http://127.0.0.1:8000/health
GET http://127.0.0.1:8000/market-data
GET http://127.0.0.1:8000/market-data?indices=NIFTY&strike_count=3
```

### Authentication

```powershell
python auth.py
```

This may open Chromium or request a manually pasted redirect URL. It writes the token cache outside the repository.

### Parser-only and mock strategy checks

```powershell
python trading.py trade_call.txt --parse-only
python live_trader.py --mock-market --mock-prices "160,168,170,180"
```

### Integrated Telegram engine on EC2

```bash
cd ~/option-trading-aws-bot
source .venv/bin/activate
python -m pip check
python auth.py                         # only when the Fyers token is missing/expired
python telegram_trade_listener.py --list-dialogs
tmux new -s trader                    # or: tmux attach -t trader
python live_trader.py --telegram-session "$TELEGRAM_SESSION"
```

Detach with `Ctrl+B`, then `D`. Reattach with `tmux attach -t trader`. Verify Fyers orders and positions independently.

### Useful environment variables

Authentication: `redirect_uri`, `client_id`, `secret_key`, `grant_type`, `response_type`, `state`, optional `auth_code`, `pin`, `token_file`, `FYERS_ACCESS_TOKEN`.

Trading: `FYERS_QUANTITY`, `FYERS_LOT_SIZE`, optional `FYERS_MAX_QUANTITY`, `FYERS_PRODUCT_TYPE`, `FYERS_ATR_PERIOD`, `FYERS_DEFAULT_EXCHANGE`, `TRADE_REGISTRY_PATH`.

Telegram: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE`, `TELEGRAM_CHAT`, `TELEGRAM_SESSION`.

## 10. Testing procedures and results

The repository uses Python `unittest` discovery; there is no `pytest` configuration or test runner configuration in the repository.

Validated command on 2026-11-10:

```text
<project>/.venv/Scripts/python.exe -m unittest discover -s tests -v
Ran 11 tests in 0.111s
OK
```

All 11 tests passed:

- 3 persistent live-trader/mock-market tests.
- 3 market-data WebSocket/mock tests.
- 2 lifecycle parser/SQLite tests.
- 3 trading-safety tests.

The run exercised mock entry, staged 50/30/20 exits, stop-loss handling, listener continuation after a trade error, WebSocket error propagation, cancellation, lifecycle ID persistence, command updates, and emergency exit behavior.

The VS Code test provider was also queried, but it discovered no matching test items for these files. That is a tooling/discovery limitation, not a failed test result; the direct `unittest` command above is the authoritative validation performed here.

No live Fyers, live Telegram, broker-fill, EC2, or production-network test was performed.

## 11. Constraints and instructions for the next coding assistant

- Preserve Python 3.9 compatibility because the documented EC2 environment uses Python 3.9. In particular, do not replace `timezone.utc` with `datetime.UTC` without raising the deployment floor and updating all support documentation.
- Never expose, commit, or copy `.env`, Fyers tokens, Telegram session files, PEM keys, or credential-bearing logs.
- Do not place real orders during development. Use parser-only, mock-market, and mocked executor tests first.
- Before changing order behavior, inspect both Fyers order status and position semantics; broker state is authoritative.
- Preserve the dry-run default and the explicit `--mock-market` hard safety behavior.
- Keep quantity a positive multiple of lot size and respect `FYERS_MAX_QUANTITY`.
- Preserve the observed-cross requirement unless a deliberate product decision changes it.
- Do not assume an accepted order is filled. Any fix must address pending, rejected, and partial-fill paths.
- Do not assume restart safety exists. Check open orders and positions before restarting the live engine.
- Preserve the current one-active-trade behavior unless concurrency is explicitly designed and tested.
- Run `python -m pip check` after dependency changes and the complete `unittest` command after code changes.
- Inspect `git status` before editing. At handoff time, [startTradeRunbook.md](./startTradeRunbook.md) was already modified; that change belongs to the existing worktree and must not be discarded.
- Keep documentation claims synchronized with code. [README.md](./README.md) is currently only a title; the detailed instructions live in the other Markdown files.
- Do not modify application functionality merely to update this handoff.

## 12. Exact next development task

No exact next task was recorded in the accessible conversation history. The highest-priority concrete task identified by the repository’s own risk notes is:

> Implement broker-order lifecycle confirmation for trigger-range entries and exits: track the returned Fyers order ID, poll or subscribe to order status until filled/rejected/partially filled, adjust `BreakoutTrader.remaining_quantity` to the confirmed filled quantity, and add deterministic tests for pending, filled, rejected, and partial-fill transitions. Do not mark a trigger-range trade as entered solely because `place_order` returned an acknowledgement.

Before implementing it, preserve the current tests and add mocked Fyers responses. After implementation, run the full `unittest` suite and perform a manual review of the emergency-stop behavior. The next task after that should be startup reconciliation of Fyers open orders/positions so a restart cannot duplicate an entry or exit.

## Current Git state at handoff

- Branch: `main`.
- HEAD: `b0e334e` (`ui changes revert`).
- Upstream: `origin/main`; no recorded ahead/behind divergence at review time.
- Existing worktree modification: `startTradeRunbook.md` only.
- This handoff file is new and is the only file added by the handoff preparation.

