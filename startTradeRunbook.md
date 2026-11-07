# Start Trading Runbook

Use this short checklist to start the Telegram-to-Fyers trading engine on EC2.

## 1. Start EC2 and connect

Start the EC2 instance in AWS Console and wait until it shows `Running` with
both status checks passed. Copy its current public IP, then connect from
Windows PowerShell:

```powershell
ssh -i "C:/Users/prabh/Downloads/aws-asus-option-trad-pem.pem" ec2-user@CURRENT_PUBLIC_IP
```

Replace `CURRENT_PUBLIC_IP` with the actual address.

## 2. Update and activate the project

```bash
cd ~/option-trading-aws-bot
git pull --ff-only origin main
source .venv/bin/activate
python -m pip check
```

If `git pull` reports local changes, stop and inspect them before continuing.

## 3. Check required settings

```bash
grep -E '^(TELEGRAM_CHAT|TELEGRAM_SESSION|FYERS_QUANTITY|FYERS_LOT_SIZE)=' .env
```

Confirm that `.env` contains the correct Telegram chat/session and trading
defaults, for example:

```text
FYERS_QUANTITY=100
FYERS_LOT_SIZE=10
```

The quantity must be a multiple of the lot size.

## 4. Check Fyers authentication

```bash
test -s ~/.fyers/token.json && echo "Fyers token found" || echo "Fyers token missing"
```

If the token is missing or expired:

```bash
python auth.py
```

## 5. Verify Telegram account once when needed

```bash
python telegram_trade_listener.py --list-dialogs
```

Confirm the displayed account and configured chat are correct. If the account
is wrong, use a new session path in `.env`, then run `--list-dialogs` again.

## 6. Start the live engine

Start it inside `tmux` so it continues running after SSH disconnects:

```bash
tmux new -s trader
cd ~/option-trading-aws-bot
source .venv/bin/activate
python live_trader.py --telegram-session "$TELEGRAM_SESSION"
```

Wait until the terminal shows:

```text
Automatic session mode: LIVE (weekdays, 09:00-15:40 IST).
Waiting for a WebSocket breakout tick.
```

Send a new complete trade call to the configured Telegram chat only after the
engine is waiting. The engine ignores trade calls sent before it starts.

Detach from `tmux` without stopping the engine:

```text
Ctrl+B, then D
```

## 7. Monitor the trade

Reconnect and view the terminal output:

```bash
tmux attach -t trader
```

When an order is submitted, verify the order and position in Fyers. A terminal
submission message does not guarantee that a limit order has filled.

The engine handles one trade call and exits after target 3 or stop-loss. Check
Fyers before restarting it for another trade.

## 8. Stop safely

```bash
tmux attach -t trader
```

Press `Ctrl+C`, verify that no unexpected open order or position remains in
Fyers, then stop the EC2 instance from AWS Console if the session is finished.
Never terminate the instance.

## Important

- `--mock-market` is for testing only and cannot place live orders.
- Automatic live mode is based on weekdays and IST time only; exchange holidays
  are not checked.
- Do not commit `.env`, Telegram sessions, token files, or private keys.
