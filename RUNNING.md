# Fyers market-data MVP

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
