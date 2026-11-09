Step-1 :
ssh -i "C:/Users/prabh/Downloads/aws-asus-option-trad-pem.pem" ec2-user@16.178.185.148

Step-2
cd ~/option-trading-aws-bot
source .venv/bin/activate
python -m pip check

Step-3 : Check Fyers auntication
test -s ~/.fyers/token.json && echo "Fyers token found" || echo "Fyers token missing"
If  token is missing error, run python auth.py

Step-4. 
Verify Telegram account once when needed
python telegram_trade_listener.py --list-dialogs

Step-5 : Start  live engine
run "tmux attach -t trader" if error then run "tmux new -s trader"
python live_trader.py --telegram-session "$TELEGRAM_SESSION"


Step-6.
Detach from `tmux` without stopping  engine:
press "Ctrl+B"
press only "D"

Optional Step : to make change in lot size, qty, other env variable :
Check .env
grep -E '^(TELEGRAM_CHAT|TELEGRAM_SESSION|FYERS_QUANTITY|FYERS_LOT_SIZE)=' .env
nano .env  # then make changes
ctrl+o     # save
enter      # confirm save
ctrl+x     # exit

Wait until the terminal shows that it is listening for Telegram trade calls.
After a complete call is received, it prints a unique ID such as
`FB-20261109-0001`, resolves the Fyers symbol, and starts the market-data
WebSocket for that trade.

After target 3, stop-loss, cancellation, manual exit, or an ordinary trade
error, the same process returns to Telegram listening for the next complete
trade call. Do not restart it after each trade.

While one trade is active, the listener also accepts `MODIFY ENTRY`, `MODIFY
SL`, target updates, `CANCEL`, `EXIT`, and `RE ENTRY` control messages. A
pending Fyers stop-loss order is monitored, and a market exit is submitted if
price moves more than 2% below that stop-loss.
