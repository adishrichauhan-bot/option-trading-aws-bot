# Option Trading Bot Preparation

> 2026-11-10 update: older sections below are historical notes. For current T3
> runner behavior, confirmed broker fills, active-stop 2% breach handling, and
> re-entry sequencing, see [Current trading behavior](./RUNNING.md#current-trading-behavior-2026-11-10).
> T3 no longer closes the remaining position. The runner stays open until its
> trailing stop or an explicit exit. Startup reconciliation is still pending.

This file records the development and EC2 setup completed so far. Never commit real credentials, PEM files, access tokens, or `.env` values.

## Project files

- [`auth.py`](./auth.py): Fyers authorization-code flow and token cache.
- [`app.py`](./app.py): FastAPI service for NIFTY, SENSEX, and ATM options.
- [`trading.py`](./trading.py): trade-call parser and trading state machine.
- [`live_trader.py`](./live_trader.py): persistent Telegram-to-Fyers trading entry point.
- [`telegram_trade_listener.py`](./telegram_trade_listener.py): Telegram message assembler and control-command parser.
- [`trade_lifecycle.py`](./trade_lifecycle.py): unique trade IDs and lifecycle journal.
- [`tests/`](./tests/): automated WebSocket, parser, lifecycle, and trade-safety tests.
- [`requirements.txt`](./requirements.txt): Python dependencies.
- [`RUNNING.md`](./RUNNING.md): run instructions.
- [`.env.example`](./.env.example): environment-variable template.

## Local and EC2 setup

Repository path: `C:\Users\prabh\WorkSpace\option-trading-bot-aws\option-trading-aws-bot`.

Local PowerShell commands:

```powershell
cd "C:\Users\prabh\WorkSpace\option-trading-bot-aws\option-trading-aws-bot"
\.venv\Scripts\Activate.ps1
python --version
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m pip check
```

EC2 commands:

```bash
cd ~/option-trading-aws-bot
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python --version
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
```

EC2 uses Python 3.9. `app.py` uses `timezone.utc` instead of `datetime.UTC`, because `datetime.UTC` is unavailable in Python 3.9.

## Environment and authentication

Create a private `.env` from [`.env.example`](./.env.example). Configure the Fyers client ID, secret, redirect URI, grant type, response type, and state. The token cache is normally `~/.fyers/token.json`.

On Windows:

```powershell
Copy-Item .env.example .env
code .env
```

On EC2:

```bash
cd ~/option-trading-aws-bot
cp .env.example .env
chmod 600 .env
nano .env
```

Run these commands on EC2:

```bash
source .venv/bin/activate
python auth.py
ls -ld ~/.fyers
ls -l ~/.fyers/token.json
python -m pip check
```

The script reads the authorization code from the redirect, generates access and refresh tokens, and caches them. Run `python auth.py` again when the cache is missing or expired.

On minimal EC2 images, install missing Playwright browser libraries or use the manual/non-browser authorization path in `auth.py`.

## EC2 and GitHub SSH

Connect from Windows PowerShell:

```powershell
ssh -i "C:/path/to/ec2-key.pem" ec2-user@PUBLIC_IP
```

Keep the EC2 private key on the local computer. Do not copy it to EC2 or commit it to Git.

For private GitHub access, run on EC2:

```bash
ssh-keygen -t ed25519 -C "ec2-option-trading-bot" -f ~/.ssh/github_ed25519
cat ~/.ssh/github_ed25519.pub
```

Add the public-key output to GitHub, then run:

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/github_ed25519
ssh-keyscan github.com >> ~/.ssh/known_hosts
ssh -T git@github.com
git clone git@github.com:OWNER/REPOSITORY.git option-trading-aws-bot
cd option-trading-aws-bot
```

## Dependency issue fixed

`fyers-apiv3` requires `websocket-client==1.6.1`, while newer Selenium versions require `websocket-client>=1.8.0`. Selenium was removed from the dependency set and Playwright is used instead.

If dependencies are changed, reinstall and verify with:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
python -m pip freeze > /tmp/option-trading-requirements.txt
```

## FastAPI market data

The configured index symbols are `NSE:NIFTY50-INDEX` and `BSE:SENSEX-INDEX`.
Start with:

```bash
cd ~/option-trading-aws-bot
source .venv/bin/activate
uvicorn app:app --host 127.0.0.1 --port 8000
```

From a second EC2 terminal, test with:

```bash
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/market-data
curl -i "http://127.0.0.1:8000/market-data?indices=NIFTY&strike_count=3"
```

The API binds to localhost and is not directly exposed publicly. If Uvicorn was started in the background, inspect it with `ps -ef | grep uvicorn`.

## Trade-call MVP

Supported format: `NIFTY 23450 CE`, `BUY ABOVE - 140`, `TARGET - 150, 163, 180`, `SL - 127`.

Trigger ranges are supported as bounded trigger bands. For example,
`TRIGGER RANGE - 377 - 380` means that when price crosses above 380, the
strategy submits a limit order with `limitPrice=380`; it will never use the
current LTP such as 416 as the limit price. The old `BUYING RANGE` label is
still accepted as an input alias. A `BUY ABOVE` value remains a market-order
breakout trigger. The strategy exits
approximately 50%, 30%, and the remaining quantity at the targets, and exits
the full remaining quantity when price crosses below stop-loss. Whole-lot
rules may prevent exact percentages.

The active stop-loss also advances during the trade: it starts at the original
`SL`, changes to `target_1` after target 1 is crossed, and changes to `target_2`
after target 2 is crossed. A fall below the current active stop-loss exits the
remaining quantity.

The integrated live engine also supports these controls while one trade is
active:

```text
MODIFY ENTRY - BUYING RANGE - 220 - 230
MODIFY ENTRY - BUY ABOVE - 320
MODIFY SL - 290
MODIFY TARGET - 305, 320, 380
CANCEL
EXIT
RE ENTRY - 135 - 145
```

Each Telegram call receives a persistent ID such as `FB-20261109-0001`.
Lifecycle records are stored in `~/.fyers/trade_lifecycle.sqlite3` by default.
The engine runs one active trade at a time; new complete calls received during
an active trade are queued and processed after it closes.

If Fyers acknowledges an exit order without reporting it as filled, the engine
continues monitoring the remaining position. If price moves more than 2% below
the active stop-loss, it submits an emergency market exit. Fyers order and
position state remains authoritative and must be verified.

Instrument parsing accepts generic names such as `BANKNIFTY 55000 CE`,
`FINNIFTY 24000 PE`, and `MCX:CRUDEOIL 7000 CE`. For an equity option or an
exact contract, use the exchange-qualified Fyers underlying or full symbol,
for example `NSE:RELIANCE-EQ 1400 CE` or `NSE:NIFTY2632423050CE`. Full Fyers
symbols are passed through directly; validate them against the daily symbol
master before live order placement.

Create a signal file:

```bash
cd ~/option-trading-aws-bot
vi trade_call.txt
```

Test parsing without API access:

```bash
python trading.py trade_call.txt --parse-only
```

Run dry-run trading:

```bash
python trading.py trade_call.txt --interval 2
```

Live mode is automatic: it is enabled Monday-Friday from 09:00 through 15:40
IST, inclusive. At all other times the order executor uses dry-run mode:

```bash
python trading.py trade_call.txt --interval 2
```

## End-of-day shutdown

1. Stop Uvicorn with `Ctrl+C` when it is running in the foreground.
2. Optionally run `deactivate`.
3. In AWS Console choose **Instance state → Stop instance**.
4. Never choose **Terminate instance**.

Stopping preserves the disk and project files. The public IP can change after restart unless an Elastic IP is attached.

## Next-day startup

1. Start the instance in AWS Console and wait for `Running` and `2/2 status checks passed`.
2. Check the current public IP and SSH into it.
3. Run `cd ~/option-trading-aws-bot`, `git pull origin main`, `source .venv/bin/activate`, and `python -m pip check`.
4. Verify `ls -l ~/.fyers/token.json`; run `python auth.py` if the token is missing or expired.
5. Start Uvicorn and test `/health`.

## Git safety

Commit source and documentation only: `git status`, `git add app.py auth.py trading.py requirements.txt RUNNING.md preparation.md`, `git commit -m "Document trading bot preparation"`, and `git push origin main`.

Never commit `.env`, PEM files, token caches, browser profiles, or logs containing credentials.

## Complete command reference

Foreground Uvicorn: press `Ctrl+C`.

Background Uvicorn: run `kill -TERM $(cat uvicorn.pid)`, then `rm -f uvicorn.pid`, `curl --max-time 3 http://127.0.0.1:8000/health`, and `deactivate`.

Then use AWS Console: **Instance state -> Stop instance**. Do not terminate the instance.

After starting EC2 and waiting for status checks, connect with `ssh -i "C:/path/to/ec2-key.pem" ec2-user@NEW_PUBLIC_IP`.

On EC2 run, in order: `cd ~/option-trading-aws-bot`; `git pull origin main`; `source .venv/bin/activate`; `python -m pip check`; `ls -l ~/.fyers/token.json`; `python auth.py` only if the token is missing or expired; and `uvicorn app:app --host 127.0.0.1 --port 8000`.

From a second EC2 terminal run: `curl -i http://127.0.0.1:8000/health` and `curl -i http://127.0.0.1:8000/market-data`.

To commit these notes run: `git status`; `git add preparation.md`; `git diff --cached --stat`; `git commit -m "Expand preparation command reference"`; and `git push origin main`.

## Simple live-trader entry point

Use [`live_trader.py`](./live_trader.py) as the single entry point. It prompts
for the trade call, reads quantity and lot size from `.env`, resolves the exact
Fyers option symbol, prints a preflight summary, and starts the existing
WebSocket strategy.

Start in dry-run mode:

```bash
cd ~/option-trading-aws-bot
source .venv/bin/activate
python live_trader.py
```

Alternatively, provide a signal file and values directly:

```bash
python live_trader.py --signal-file trade_call.txt
```

Live mode is selected automatically using the Monday-Friday 09:00-15:40 IST
window; no live-session confirmation is requested:

```bash
python live_trader.py --signal-file trade_call.txt
```

Optional environment defaults are `FYERS_QUANTITY`, `FYERS_LOT_SIZE`,
`FYERS_PRODUCT_TYPE`, and `FYERS_MAX_QUANTITY`. The default maximum quantity
is 10,000; quantity and lot-size defaults are 100 and 10. Pressing `Ctrl+C` stops monitoring; it does not silently close a
broker position, so verify any open position in Fyers.

## Fyers WebSocket market data

Live prices are received through `market_data_ws.py` using the official Fyers
`FyersDataSocket` and `SymbolUpdate` feed. `app.py` uses this stream for index
and ATM-option LTP/bid/ask values. `trading.py` uses the same stream and no
longer calls the REST `quotes()` endpoint in a polling loop.

The option-chain lookup is still performed through the Fyers SDK because it
discovers the contract symbol and strike. Order submission is also still made
through the supported Fyers order API; the order WebSocket is for order/trade/
position updates, not for submitting orders.

Run the API normally; the WebSocket starts when the first market-data request
is made:

```bash
source .venv/bin/activate
python -m pip check
uvicorn app:app --host 127.0.0.1 --port 8000
```

Run the strategy normally; it subscribes to the resolved option symbol and
waits for new WebSocket ticks:

```bash
python trading.py trade_call.txt --quantity 750 --lot-size 75 --interval 0
```

If the WebSocket reports an authentication or connection error, verify the
cached token and app ID:

```bash
ls -l ~/.fyers/token.json
python auth.py
python -m pip show fyers-apiv3
```

## Telegram trade-call listener

`telegram_trade_listener.py` was added as a standalone Telegram user-account
listener. It parses the instrument, `BUYING RANGE` or `TRIGGER RANGE`, three
targets, and stop-loss. It also supports calls delivered as multiple Telegram
message chunks: chunks are appended in message-ID order, decorative characters
are removed, and JSON is emitted only after the complete call is assembled.

Install the Telegram dependency:

```bash
python -m pip install telethon
python -m pip check
```

Configure Telegram credentials privately in `.env`:

```text
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_PHONE=+91XXXXXXXXXX
TELEGRAM_CHAT=your_actual_chat_id
TELEGRAM_SESSION=/home/ec2-user/.telegram/trade_listener_ec2
```

Never use the literal value `CORRECT_CHAT_ID`; replace it with the actual chat
ID returned by `--list-dialogs`.

List chats for the currently authenticated Telegram account:

```bash
python telegram_trade_listener.py --list-dialogs
```

Test recent messages from one chat:

```bash
python telegram_trade_listener.py \
  --chat "$TELEGRAM_CHAT" \
  --once \
  --limit 20
```

Run the listener continuously:

```bash
python telegram_trade_listener.py
```

The listener prints the logged-in account, target chat, chat ID, received
chunks, and completed parsed JSON. A Telegram session is account-specific; use
a new `TELEGRAM_SESSION` path when switching accounts.

## Telegram listener and trading-engine integration

The listener is integrated with `live_trader.py`. The engine waits for the
first completed Telegram call, converts it into the existing `TradeCall`
format, resolves the Fyers option symbol, and starts the breakout state
machine. The listener itself does not place orders.

### Integration review

The live path is:

```text
Telegram message chunks
  -> TradeCallAssembler
  -> validated TradeCallMessage
  -> live_trader.py conversion
  -> trading.py parse_trade_call
  -> Fyers option-symbol resolution
  -> Fyers market-data WebSocket
  -> BreakoutTrader state machine
  -> FyersOrderExecutor
```

The parser and engine were syntax-checked after the integration changes. The
following functional and technical risks remain and should be understood
before live use:

1. **High — limit-order fill is not confirmed.** For a trigger-range entry,
   the engine marks the trade as entered after Fyers accepts the limit-order
   request. It does not poll the order status or confirm a fill. If the order
   remains pending or is partially filled, target and stop-loss actions may not
   match the actual broker position.
2. **High — one active trade at a time.** New calls are queued, not traded
   concurrently. The persistent listener remains alive after target 3,
   stop-loss, cancellation, manual exit, or an ordinary trade error.
3. **High — only new Telegram messages are consumed after startup.** The
   listener records the latest message ID when it starts, so a call sent before
   startup is intentionally ignored. Start the engine before the signal is
   published.
4. **High — no restart reconciliation.** The engine does not restore an open
   position after a crash or reboot. Check Fyers positions and open orders
   before restarting after any interruption to avoid duplicate entries or
   exits.
5. **High — automatic live mode is time-only.** Weekdays from 09:00 through
   15:40 IST are treated as live, including exchange holidays and unusual
   sessions. Outside that window the engine is dry-run; `--mock-market` is
   always dry-run.
6. **Medium — market-data timeout stops the process.** If no newer WebSocket
   tick arrives within the socket timeout, the exception is not recovered by
   the main loop. Confirm the process is still running and inspect the last
   terminal output.
7. **Medium — Telegram sessions are account-specific.** Changing Telegram
   credentials while reusing an old session can keep the old account logged
   in. Use a new `TELEGRAM_SESSION` path and verify the account with
   `--list-dialogs`.
8. **Medium — a live order error stops the engine.** A rejected order raises
   an exception. Treat the broker order book and positions as authoritative
   and perform a manual safety check before any restart.

Do not treat a terminal line saying that an order was submitted as proof that
the position is filled. Verify the order and position in Fyers.

## Morning live-market startup — beginner runbook

Use this sequence each trading morning. The commands below assume Amazon Linux,
the repository at `~/option-trading-aws-bot`, and a configured `.env`.

### 1. Start EC2 and connect

1. In AWS Console, start the EC2 instance.
2. Wait for `Running` and both status checks to pass.
3. Copy the current public IPv4 address. It may change after a stop/start.
4. From Windows PowerShell, connect using the current address:

```powershell
ssh -i "C:/Users/prabh/Downloads/aws-asus-option-trad-pem.pem" ec2-user@CURRENT_PUBLIC_IP
```

Replace `CURRENT_PUBLIC_IP`; do not type the placeholder literally.

### 2. Update the code and activate Python

Run these commands after SSH connects:

```bash
cd ~/option-trading-aws-bot
git status --short
git pull --ff-only origin main
source .venv/bin/activate
python -m pip check
```

If `git status --short` shows local edits, stop and inspect them before
pulling. Do not overwrite them blindly.

### 3. Check the non-secret configuration

Confirm that the values exist without printing Fyers secrets:

```bash
grep -E '^(TELEGRAM_CHAT|TELEGRAM_SESSION|FYERS_QUANTITY|FYERS_LOT_SIZE)=' .env
```

The expected trading defaults are:

```text
FYERS_QUANTITY=100
FYERS_LOT_SIZE=10
```

Change them in `.env` only when required, and make sure quantity is a multiple
of lot size. Do not commit `.env`.

### 4. Verify Fyers authentication

Check whether the token cache exists:

```bash
test -s ~/.fyers/token.json && echo "Fyers token cache found" || echo "Fyers token cache missing"
```

If it is missing or expired, run:

```bash
python auth.py
```

Complete the displayed login flow and confirm that the token cache is created
before continuing.

### 5. Verify the Telegram account and chat

Run:

```bash
python telegram_trade_listener.py --list-dialogs
```

Check the `Logged in as:` line and confirm that the configured chat appears.
If the account is wrong, use a new session path before continuing:

```bash
export TELEGRAM_SESSION="$HOME/.telegram/trade_listener_correct_account"
python telegram_trade_listener.py --list-dialogs
```

Use the exact chat ID or visible chat configured in `TELEGRAM_CHAT`. Never use
`CORRECT_CHAT_ID` as a literal value.

### 6. Run a safe end-to-end mock test

Start the integrated engine with mock market data:

```bash
python live_trader.py \
  --telegram-session "$TELEGRAM_SESSION" \
  --mock-market \
  --interval 0.5
```

Now send a fresh complete test trade call to the configured Telegram chat.
The process should show the parsed call, preflight values, mock entry, staged
exits, and dry-run orders. This mode cannot place a Fyers order.

Press `Ctrl+C` after the mock test if it is still waiting. The next live run
must be started before the real Telegram trade call is sent.

### 7. Start the live engine in tmux

If tmux is not installed, install it once:

```bash
sudo dnf install -y tmux || sudo yum install -y tmux
```

Start a new session:

```bash
tmux new -s trader
cd ~/option-trading-aws-bot
source .venv/bin/activate
python live_trader.py --telegram-session "$TELEGRAM_SESSION"
```

Confirm the terminal shows:

```text
Automatic session mode: LIVE (weekdays, 09:00-15:40 IST).
Waiting for a WebSocket breakout tick.
```

The engine is now waiting for a **new** complete trade call. Send the trade
call only after this message appears. When the trigger crosses, inspect the
printed `ORDER SUBMIT` line and verify the order in Fyers.

Detach without stopping the engine using `Ctrl+B`, then `D`.

### 8. Monitor or stop the engine

Reattach during the session:

```bash
tmux attach -t trader
```

After a completed trade, review the terminal output and Fyers order/position
screens. The same process should continue listening for the next signal; only
restart it after a deliberate shutdown, crash, token failure, or reconciliation
check.

To stop it safely, reattach and press `Ctrl+C`, then verify in Fyers that no
unexpected position or open order remains:

```bash
tmux attach -t trader
```

After verification, leave the EC2 process stopped or stop the EC2 instance
from AWS Console. Never terminate the instance.

Because Fyers authentication, symbol resolution, market data, and order
execution are EC2 dependencies, run the integrated process on EC2:

```bash
cd ~/option-trading-aws-bot
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check

python live_trader.py \
  --telegram-session "$TELEGRAM_SESSION"
```

`live_trader.py` reads `TELEGRAM_CHAT` automatically. Do not pass a literal
placeholder such as `CORRECT_CHAT_ID`.

The first EC2 Telegram run may request the phone number, login code, and 2FA
password. Keep the generated `.session` file private.

## EC2 background execution with tmux

Install `tmux` on Amazon Linux:

```bash
sudo dnf install -y tmux
```

If `dnf` is unavailable:

```bash
sudo yum install -y tmux
```

Start a persistent session:

```bash
tmux new -s trader
```

Run the integrated engine inside tmux:

```bash
cd ~/option-trading-aws-bot
source .venv/bin/activate
python live_trader.py \
  --telegram-session "$TELEGRAM_SESSION"
```

Detach without stopping the process with `Ctrl+B`, then `D`. Reattach with:

```bash
tmux attach -t trader
```

## Offline mock-market validation

When the market is closed, use `--mock-market`. This mode skips Fyers
authentication and the Fyers WebSocket, uses a mock symbol, forces dry-run
orders, and runs the same `BreakoutTrader` state machine with deterministic
prices.

Run Telegram plus mock-market testing on EC2:

```bash
cd ~/option-trading-aws-bot
source .venv/bin/activate

python live_trader.py \
  --telegram-session "$TELEGRAM_SESSION" \
  --mock-market \
  --interval 0.5
```

Send a new complete trade call after the process starts. The default mock
sequence performs:

```text
start below trigger -> cross trigger -> target 1 -> target 2 -> target 3
```

Expected dry-run exits for quantity 100 are 50, 30, and 20 units.

To test the stop-loss path, use prices matching the trade call's trigger and
stop-loss values. For a trigger range of `160 - 167` and stop-loss `150`:

```bash
python live_trader.py \
  --telegram-session "$TELEGRAM_SESSION" \
  --mock-market \
  --mock-prices "160,168,149"
```

Expected behavior is entry after the trigger crossing followed by a full
dry-run exit below stop-loss. Mock mode never asks for live confirmation and
cannot place a real Fyers order.

## Latest EC2 test sequence

After code changes are pushed from local:

```powershell
git add telegram_trade_listener.py live_trader.py trading.py market_data_ws.py requirements.txt preparation.md
git commit -m "Document Telegram EC2 integration and mock testing"
git push
```

On EC2:

```bash
cd ~/option-trading-aws-bot
git pull --ff-only
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
python telegram_trade_listener.py --list-dialogs
```

Use the actual chat ID from the dialog list, verify the displayed Telegram
account, then run the mock-market command above. Only after the Telegram
listener, parser, entry, target exits, and stop-loss behavior have been
validated should the normal live command be run.

## Trigger-range slippage-buffer pricing

For a trigger-range entry, the engine no longer uses the upper trigger-range
value as the limit price. It builds one-minute ATR values from timestamped LTP
ticks and calculates:

```text
raw_buffer = max(1.0, 0.5 * current_1m_ATR)
buffer = min(raw_buffer, 0.02 * current_option_price)
limit price = current option price + buffer
```

The current option price is the LTP at the trigger crossing. Until a completed
one-minute bar is available, ATR is unavailable and the minimum buffer of `1.0`
is used, still subject to the 2% cap. `BUY ABOVE` entries remain market orders.

The engine prints the calculation at entry:

```text
ENTRY PRICING: LTP=168 ATR_1m=... buffer=... limit=...
```

The ATR period can be configured with `FYERS_ATR_PERIOD`; the default is 14
completed one-minute bars.

When an entry is submitted, the terminal prints an explicit order line such as:

```text
ORDER SUBMIT: LIMIT BUY quantity=100 symbol=... limit_price=380.5 reason=trigger range crossed
```

This makes the exact limit price visible for both dry-run and live order
submissions. The following JSON line also contains the broker payload.

## Display recent Telegram messages

To display all of the latest 100 messages from the default chat configured in
`TELEGRAM_CHAT`:

```bash
python telegram_trade_listener.py --once --limit 100 --show-all
```

To display the latest 100 messages from a particular chat:

```bash
python telegram_trade_listener.py \
  --chat "ACTUAL_CHAT_ID" \
  --once \
  --limit 100 \
  --show-all
```

Replace `ACTUAL_CHAT_ID` with the numeric ID from
`python telegram_trade_listener.py --list-dialogs`. Messages are printed in
chronological order as JSON lines containing `message_id`, `chat_id`,
`message_date`, and `raw_text`. Without `--show-all`, `--once` prints only
recognized completed trade calls.

To display an inclusive message-ID range from the default `TELEGRAM_CHAT`:

```bash
python telegram_trade_listener.py \
  --once \
  --message-id-range 100-150 \
  --show-all
```

To display an inclusive message-ID range from a particular chat:

```bash
python telegram_trade_listener.py \
  --chat "ACTUAL_CHAT_ID" \
  --once \
  --message-id-range 100-150 \
  --show-all
```

`--message-id-range` accepts `START-END` (or `START:END`) and requires
`--once`. The endpoints are included. Omit `--show-all` if only completed
trade calls should be printed.
