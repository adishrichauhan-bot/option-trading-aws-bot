# Option Trading Bot Preparation

This file records the development and EC2 setup completed so far. Never commit real credentials, PEM files, access tokens, or `.env` values.

## Project files

- [`auth.py`](./auth.py): Fyers authorization-code flow and token cache.
- [`app.py`](./app.py): FastAPI service for NIFTY, SENSEX, and ATM options.
- [`trading.py`](./trading.py): trade-call parser and trading state machine.
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

Buying ranges are supported; the upper range value is the breakout trigger. The strategy buys after an observed upward cross, exits approximately 50%, 30%, and the remaining quantity at the targets, and exits the full remaining quantity when price crosses below stop-loss. Whole-lot rules may prevent exact percentages.

The active stop-loss also advances during the trade: it starts at the original
`SL`, changes to `target_1` after target 1 is crossed, and changes to `target_2`
after target 2 is crossed. A fall below the current active stop-loss exits the
remaining quantity.

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
python trading.py trade_call.txt --quantity 750 --lot-size 75 --parse-only
```

Run dry-run trading:

```bash
python trading.py trade_call.txt --quantity 750 --lot-size 75 --interval 2
```

Real orders require explicit `--live` after validating the symbol, lot size, quantity, product type, and dry-run output:

```bash
python trading.py trade_call.txt --quantity 750 --lot-size 75 --interval 2 --live
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

Use [`live_trader.py`](./live_trader.py) as the single interactive entry
point. It prompts for the trade call, quantity, and lot size, resolves the
exact Fyers option symbol, prints a preflight summary, and starts the existing
WebSocket strategy.

Start in dry-run mode:

```bash
cd ~/option-trading-aws-bot
source .venv/bin/activate
python live_trader.py
```

Alternatively, provide a signal file and values directly:

```bash
python live_trader.py --signal-file trade_call.txt --quantity 750 --lot-size 75
```

Real order placement requires both the `--live` flag and typing `LIVE` at the
confirmation prompt:

```bash
python live_trader.py --signal-file trade_call.txt --quantity 750 --lot-size 75 --live
```

Optional environment defaults are `FYERS_QUANTITY`, `FYERS_LOT_SIZE`,
`FYERS_PRODUCT_TYPE`, and `FYERS_MAX_QUANTITY`. The default maximum quantity
is 10,000. Pressing `Ctrl+C` stops monitoring; it does not silently close a
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
