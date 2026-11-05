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
