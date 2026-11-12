Step-1 :
ssh -i "C:/Users/prabh/Downloads/aws-asus-option-trad-pem.pem" ec2-user@16.178.185.148

Step-2
cd ~/option-trading-aws-bot
source .venv/bin/activate
python -m pip check

Step-3 : Validate Fyers authentication every morning
python auth.py
Do this BEFORE the first trade each day, even if login succeeded yesterday.
The Nov-12 09:27 signal was parsed, but yesterday's token had expired and
the process stopped. Restarting at 10:33 cannot recover the earlier calls.
Wait for "Fyers authentication is ready." before continuing.
A token file existing does not mean the access token is still valid.
Complete the displayed browser/redirect login when requested.
For unattended refresh, privately configure pin in .env together with the
existing Fyers app secret and cached refresh token. Never share these values.

Step-4. 
Verify Telegram account once when needed
python telegram_trade_listener.py --list-dialogs

Step-5 : Start  live engine
run "tmux attach -t trader" if error then run "tmux new -s trader"
python live_trader.py
The engine reads TELEGRAM_CHAT and TELEGRAM_SESSION from .env itself.
Do not pass an empty shell TELEGRAM_SESSION override.
Wait for "Fyers authentication verified" and the logged-in Telegram account.
If it prints "Trading NOT READY", complete Step-3 and start the engine again.
Before restarting, check existing Fyers positions/open orders. Do not assume
the engine restores or manages an already open broker position after restart.


Step-6.
Detach from `tmux` without stopping  engine:
press "Ctrl+B"
press only "D"

Optional Step : to make change in lot size, qty, other env variable :
Lot size and real trade quantity are now automatic; old FYERS_LOT_SIZE and
FYERS_QUANTITY settings are ignored. Lot size comes from the exact symbol's
minLotSize. Fresh Available Balance and the breakout entry price determine size.
FYERS_SIZING_ROUNDING=floor is the default and buys affordable whole lots.
Ceiling rounding caused the Nov-12 afternoon SKIPPED_SIZING failures; do not
set FYERS_SIZING_ROUNDING=ceil unless those over-budget skips are intentional.
FYERS_MAX_QUANTITY caps size and FYERS_FUNDS_RESERVE optionally preserves cash.
Check .env
grep -E '^(TELEGRAM_CHAT|TELEGRAM_SESSION|FYERS_SIZING_ROUNDING|FYERS_MAX_QUANTITY|FYERS_FUNDS_RESERVE)=' .env
nano .env  # then make changes
ctrl+o     # save
enter      # confirm save
ctrl+x     # exit

Wait until the terminal shows that it is listening for Telegram trade calls.
After a complete call is received, it prints a unique ID such as
`FB-20261109-0001`, resolves the Fyers symbol, and starts the market-data
WebSocket for that trade.

After target 3, the remaining quantity continues with a trailing stop; the
trade does not finish just because T3 was crossed. After confirmed closure,
the same process returns to Telegram listening. Authentication failures and
uncertain broker exposure stop the engine instead of silently losing signals.
Only new messages after listener startup are consumed: do not replay old calls.

While one trade is active, the listener also accepts `MODIFY ENTRY`, `MODIFY
SL`, target updates, `CANCEL`, `EXIT`, and `RE ENTRY` control messages. A
pending Fyers stop-loss order is monitored, and a market exit is submitted if
price moves more than 2% below that stop-loss.
