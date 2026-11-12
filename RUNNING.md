# Fyers market-data MVP

## Afternoon sizing incident fix — affordable whole lots are the default

The Nov-12 13:22 SENSEX 81600 PE and 13:47 SENSEX 82100 CE calls were parsed
and reached breakout, but ceiling sizing rounded premium above available funds
and marked both SKIPPED_SIZING. The 11:55 SENSEX 81700 PE call did execute:
the journal and broker confirm the bot bought 60 units and subsequently exited.

Defaults now use floor across the sizing helper, FundsSizer, both CLIs and
environment template. For budget B, entry price P and contract lot size L:
`quantity = floor(B / (P * L)) * L`, subject to the existing max-quantity cap.
There is no rounding-driven skip when at least one whole lot is affordable.
Explicit ceil remains available for intentional reject-on-over-budget behavior.
Skip diagnostics now preserve funds, entry price, lot size, rounding and reason.

The 14:25 SENSEX 82000 CE call's first bot tick was 96.5, already above its 82
trigger. The observed-cross rule is preserved: the engine does not chase a
historical breakout using Telegram progress text. It now prints ENTRY WAIT
explicitly when starting above the trigger rather than appearing unresponsive.

## Automatic lot size and funds-based sizing

Real trades no longer read `FYERS_LOT_SIZE` or `FYERS_QUANTITY`. After resolving
the exact contract, both trading CLIs read its `minLotSize` from the daily
Fyers symbol master. Unknown/inactive contracts or invalid lot metadata stop
that signal; there is no fallback to a manual lot size.

At the observed breakout, and before a BUY request, the engine reads fresh
Fyers `fund_limit` data and selects the `Available Balance` row by title.
NSE/BSE use `equityAmount`; MCX uses `commodityAmount`. Entry sizing uses the
actual buffered/rounded limit price for range entries and that tick's LTP for
market entries, not the original Telegram trigger. Market fills can differ
from LTP, so estimated affordability does not guarantee broker acceptance.

```text
budget = Available Balance - FYERS_FUNDS_RESERVE
raw_lots = budget / (entry_price * contract_lot_size)
quantity = rounded_lots * contract_lot_size
```

`FYERS_SIZING_ROUNDING=floor` is the default and buys affordable whole lots.
Explicit `ceil` aligns upward to whole lots and skips results exceeding the
budget; it is not the default because most fractional budgets would skip.
Both modes retain the
`FYERS_MAX_QUANTITY` cap (rounded down to whole lots). The optional fixed
cash reserve defaults to zero; configure it to leave cash for fees/slippage.

Funds errors, insufficient budget for one lot, or invalid sizing mark registered
signals `SKIPPED_SIZING`; no BUY is submitted. The `ENTRY SIZING` output and
lifecycle journal record price, funds, lot size, lots and final quantity.
T1/T2 allocation and the T3 runner then operate on confirmed filled quantities.

`python live_trader.py --mock-market --quantity 100 --lot-size 10` remains
offline-safe. Those manual options are mock-only; real trading and the
standalone `trading.py` CLI use automatic sizing. Historical command examples
below mentioning fixed real quantities/lot sizes are superseded by this section.

Sources: [Fyers funds](https://fyers.in/api-docs/v3/api/account/funds/),
[symbol master](https://fyers.in/api-docs/v3/api/market-information/symbol-master/symbol-master-json/).

## Nov-12 missed-call investigation and split-message fixes

EC2 logs show the 09:27 SENSEX 82100 CE call was assembled correctly, then
authentication failed and the engine stopped. Its next startup was about
10:33 IST at message 55195, after the supplied calls. Logging in the previous
evening is not proof of valid credentials the next morning. Validate every
trading morning or privately configure the refresh PIN. Do not replay those
historical setups as new live orders.

The Nov-12 message formats also exposed two parser/state defects, now fixed:

- `MODIFY ENTRY` before initial targets/SL replaces the pending entry instead
  of becoming an ignored active-trade control. The recovery call uses range
  270–280 with targets 300/320/355 and SL 250, not the superseded 340 entry.
- `RE ENTRY - ABOVE - 355` retains the known instrument but waits for NEW
  targets/SL (375/400/435 and 335). It requests closure of an existing trade
  without queuing a new entry using stale risk levels. Once complete, the
  new call is handled normally. No known instrument means no invented call.
- A new instrument announcement clears an older incomplete setup; malformed
  risk levels are rejected without terminating the Telegram listener.

The single-active-trade queue policy is unchanged. Recognizing a call does
not mean every call executes concurrently or that an above-trigger first
price is treated as a confirmed upward crossing. Broker prices/fills, not
channel progress messages, drive entry/SL/target execution.

Validation: the complete local suite passes 64 tests, including Nov-12
signal formats and confirmed-close sequencing for split re-entry.

## Authentication readiness (2026-11-11)

Run `python auth.py` on EC2 each morning and wait for `Fyers authentication is
ready.` A nonempty token file is not proof of a valid token. On 2026-11-11 the
server successfully assembled both SENSEX calls but Fyers profile requests
returned code -16 (invalid/expired token), so no order could be submitted.

The engine now validates Fyers before starting Telegram consumption and again
before a trade. It refreshes an expired token non-interactively when a refresh
token, `secret_key`, and `pin` are available, validates the refreshed token, then
atomically updates the cache. A stale `FYERS_ACCESS_TOKEN` environment override
cannot hide a working cache token. No secrets are printed on refresh failure.
Without those refresh prerequisites, run the interactive `python auth.py` login.
Startup prints `Trading NOT READY` rather than appearing to listen successfully;
a later authentication failure marks the registered call `AUTH_REQUIRED` and
stops instead of discarding subsequent signals. Mock-market mode skips Fyers.

Use `python live_trader.py` to read Telegram settings directly from `.env`.
Passing `--telegram-session "$TELEGRAM_SESSION"` from a shell that has not exported
that variable overrides the configured path with an empty string.

The supplied 2026-11-11 message sequence is retained in the offline test fixture.
It emits exactly four initial calls plus the two explicit full-exit messages;
progress updates and duplicate event/poll deliveries do not create extra calls.
Historical calls are never automatically replayed as live orders after login.

## Current trading behavior (2026-11-10)

T1 and T2 still sell approximately 50% and 30% of the confirmed entry quantity,
rounded down to whole lots. T3 no longer sells the remainder: it activates a
runner with its initial active stop-loss at T3. On subsequent new highs:

```text
gap = max(high_watermark * FYERS_TRAILING_PERCENT / 100,
          current_one_minute_ATR * FYERS_TRAILING_ATR_MULTIPLE)
active_stoploss = max(previous_active_stoploss, T3, high_watermark - gap)
```

Defaults are 3% and 2x ATR. Until ATR is available, the percentage is used.
The stop never moves down, and only a new high can raise it. These are tunable
strategy defaults, not a guarantee against normal pullbacks or slippage.
T3 is the initial floor: if the first T3-crossing tick jumps far above T3, the
same trailing calculation immediately protects that high rather than waiting
for an even higher tick.
The quantity left after T1/T2 stays open until the trailing stop or an explicit
exit/cancel. With one lot, rounding may leave the whole position as the runner.

On a tick at or below the active stop, the engine submits a locally triggered
limit SELL slightly below that stop (0.1%, rounded down to `FYERS_PRICE_TICK`,
default 0.05). This is NOT a resting broker-native stop order: the engine must
remain running and receive market ticks. If price is at/below 98% of the active
stop, it uses a market exit instead. If a sell is already pending, the engine
requests cancellation, waits for confirmed terminal status, accounts for any
fills during cancellation, and market-sells only the remaining position.
Rejected cancellation with a still-pending order stops the engine for manual
reconciliation rather than risking a duplicate sell. A cancellation
acknowledgement alone never authorizes the replacement sell.

Live entry and exit acknowledgements are tracked by Fyers order ID. The engine
polls `orderbook(data={"id": order_id})`, default once per second per active order
(`FYERS_ORDER_POLL_SECONDS`), including while waiting for market ticks. Only
confirmed cumulative `filledQty` changes the position. Partial entry fills
count as actual exposure, but pending buy remainders are cancelled and confirmed
terminal before exits are allowed. Targets use the final confirmed entry size.
Pending target exits do not block target/stop observation; all crossed targets
advance protection, and T3 can activate while T1 is still pending.

Order IDs, statuses, fills, remaining quantity, and trailing state are journaled
for registered Telegram calls. Re-entry waits for the prior position to be
confirmed flat. Unknown broker outcomes, rejected exits, and unreconciled
cancellation errors stop the persistent engine with `RECONCILIATION_REQUIRED`;
they do not advance to another queued trade. Check Fyers before restarting.
Automatic startup reconciliation is still pending.

The default mock sequence now demonstrates T3, a new high, and a retracement.
If custom mock prices end while a runner is open, the run reports an incomplete
simulation instead of claiming the trade closed. Paper fills are simulated
immediately and are not evidence of live execution quality.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v -b
```

All original tests are preserved; additional tests cover fill transitions,
partial fills, cancellation races, T3 trailing, active-stop emergency exits,
polling without market ticks, journaling, and re-entry sequencing.

Broker schema references: [order lookup](https://fyers.in/api-docs/v3/api/trading/order-book/get-order-by-order-id/)
and [order status/filled quantities](https://fyers.in/api-docs/v3/api/trading/webhooks/webhook-response/).

## API setup

This service returns current Fyers quotes for NIFTY 50 and SENSEX, plus the
nearest call and put strikes from each index option chain.

Install and run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:FYERS_CLIENT_ID = "your_app_id-100"
$env:FYERS_ACCESS_TOKEN = "your_current_access_token"
.\.venv\Scripts\python.exe -m uvicorn app:app --reload
```

The service does not implement Fyers login/token generation; use a current
access token. Endpoints:

```text
GET http://127.0.0.1:8000/health
GET http://127.0.0.1:8000/market-data
GET http://127.0.0.1:8000/market-data?indices=NIFTY&strike_count=3
```

## Generate a Fyers access token

Install the dependencies, then run the authentication helper:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe auth.py
```

The script opens Chromium. Complete the Fyers login and OTP manually;
the script captures `auth_code` from the redirect URL, generates both tokens,
and caches them at `~/.fyers/token.json`. Add `pin=...` to `.env` to allow the
script to refresh an expired access token automatically. Fyers refresh tokens
are limited-lived, so the script falls back to browser login when refresh is
unavailable or rejected.

## Run a trade-call MVP

`trading.py` parses `BUY ABOVE` and `BUYING RANGE` calls. For a buying range,
the upper boundary is used as the breakout trigger. It buys only after an
observed upward cross, then exits 50%, 30%, and the remaining quantity at the
three targets. A downward stop-loss cross exits the remaining quantity.

Create `trade_call.txt`, for example:

```text
NIFTY 23450 CE
BUY ABOVE - 140
TARGET - 150, 163, 180
SL - 127
```

Test parsing without contacting Fyers:

```bash
python trading.py trade_call.txt --quantity 75 --lot-size 75 --parse-only
```

Run the dry-run strategy against live quotes; no orders are submitted by
default:

```bash
python trading.py trade_call.txt --quantity 75 --lot-size 75 --interval 2
```

Pass `--live` only after validating the symbol, quantity, lot size, product
type, and dry-run output:

```bash
python trading.py trade_call.txt --quantity 75 --lot-size 75 --live
```

Use the current exchange lot size for the selected contract. This MVP polls
quotes; a production version should use Fyers data WebSocket events and
reconcile orders and positions after restarts.
