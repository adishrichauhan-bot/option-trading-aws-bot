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
grep -E '^(TELEGRAM_CHAT|TELEGRAM_SESSION|FYERS_QUANTITY|FYERS_LOT_SIZE)=' .env

Step-4. 
Check Fyers auntication
test -s ~/.fyers/token.json && echo "Fyers token found" || echo "Fyers token missing"
If  token is missing or expired, run python auth.py

Step-5. 
Verify Telegram account once when needed
python telegram_trade_listener.py --list-dialogs

Step-6. 
Start  live engine
tmux new -s trader
or
tmux attach -t trader
source .venv/bin/activate
python live_trader.py --telegram-session "$TELEGRAM_SESSION"

Wait until  terminal shows:
Waiting for a WebSocket breakout tick.

After a trade reaches target 3, stop-loss, or an ordinary trade error, the same
process returns to Telegram listening for the next complete trade call. Do not
restart it after each trade.

Step-7.
Detach from `tmux` without stopping  engine:
Ctrl+B, then D
