Step-1. 
Start EC2
ssh -i "C:/Users/prabh/Downloads/aws-asus-option-trad-pem.pem" ec2-user@16.178.185.148

Step-2. 
activate  project
cd ~/option-trading-aws-bot
source .venv/bin/activate
python -m pip check

Step-3. 
Check .env
grep -E '^(TELEGRAM_CHAT|TELEGRAM_SESSION|FYERS_QUANTITY|FYERS_LOT_SIZE|TRADE_REGISTRY_PATH)=' .env

Step-4. 
Check Fyers auntication
test -s ~/.fyers/token.json && echo "Fyers token found" || echo "Fyers token missing"
If  token is missing or expired, run python auth.py

Step-5. 
Verify Telegram account once when needed
python telegram_trade_listener.py --list-dialogs

Step-6. 
Start the local control UI in a separate tmux window or SSH session:
uvicorn app:app --host 127.0.0.1 --port 8000

Open `http://127.0.0.1:8000/trader` through an SSH tunnel. The page can start
or stop `live_trader.py`, set quantity and lot size, select mock-market mode,
and display recent engine output. Quantity and lot-size changes apply to the
next engine start; they never resize an active broker position.

For local access from Windows, create the tunnel in a separate PowerShell
window:
ssh -i "C:/Users/prabh/Downloads/aws-asus-option-trad-pem.pem" -L 8000:127.0.0.1:8000 ec2-user@CURRENT_PUBLIC_IP
Then browse to `http://127.0.0.1:8000/trader`.

For a simpler Windows desktop control panel, run locally from PowerShell:

```powershell
cd "C:/Users/prabh/WorkSpace/option-trading-bot-aws/option-trading-aws-bot"
\.venv\Scripts\python.exe desktop_control.py
```

Use **Save .env** first. Fill in `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`,
`TELEGRAM_PHONE`, `TELEGRAM_CHAT`, `TELEGRAM_SESSION`, and the Fyers fields.
Then use **Fyers login/auth**, **List Telegram chats**, and finally **Start
engine**. Authentication opens in a separate console window when interaction
is required.

Step-7.
Start  live engine
tmux new -s trader
or
tmux attach -t trader
source .venv/bin/activate
python live_trader.py --telegram-session "$TELEGRAM_SESSION"

Wait until the terminal shows that it is listening for Telegram trade calls.
After a complete call is received, it prints a unique ID such as
`FB-20261109-0001`, resolves the Fyers symbol, and starts the market-data
WebSocket for that trade.

After target 3, stop-loss, cancellation, manual exit, or an ordinary trade
error, the same process returns to Telegram listening for the next complete
trade call. Do not restart it after each trade.

While one trade is active, the listener also accepts control messages such as
`MODIFY ENTRY - BUYING RANGE - 220 - 230`, `MODIFY SL - 290`, `MODIFY TARGET - 305, 320, 380`, `CANCEL`, `EXIT`, and `RE ENTRY - 135 - 145`. An explicit ID such as `FB-20261109-0001` can be included when needed. A pending Fyers stop-loss order is monitored, and a market exit is submitted if price moves more than 2% below that stop-loss.

The lifecycle journal is stored at `~/.fyers/trade_lifecycle.sqlite3` by
default. Set `TRADE_REGISTRY_PATH` to use another private path.

Step-8.
Detach from `tmux` without stopping  engine:
Ctrl+B, then D
