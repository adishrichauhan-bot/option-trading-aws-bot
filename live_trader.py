"""Simple interactive entry point for the Fyers breakout strategy.

Dry-run is the default. The program asks whether this is a live trading
session before connecting to the order path.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

from market_data_ws import FyersMarketDataSocket
from trading import (
    BreakoutTrader,
    FyersOrderExecutor,
    ask_live_session,
    authenticated_client,
    authenticated_credentials,
    parse_trade_call,
    resolve_option_symbol,
)


def _positive_int(value: str, label: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a whole number") from exc
    if number <= 0:
        raise ValueError(f"{label} must be greater than zero")
    return number


def read_signal(signal_file: Path | None) -> str:
    if signal_file:
        return signal_file.read_text(encoding="utf-8")

    print("Enter the trade call. Press Enter after each line.")
    instrument = input("Instrument (example: NIFTY 23450 CE): ")
    buy_line = input("Buy line (BUY ABOVE or TRIGGER RANGE): ").strip()
    target_line = input("Targets (example: TARGET - 150, 163, 180): ").strip()
    stoploss_line = input("Stop-loss (example: SL - 127): ").strip()

    return normalize_interactive_signal([instrument, buy_line, target_line, stoploss_line])


def normalize_interactive_signal(lines: list[str]) -> str:
    """Convert shorthand prompt values into the parser's explicit format."""
    instrument, buy_line, target_line, stoploss_line = lines
    # Accept the short values users naturally type at the interactive prompts.
    # File-based signals remain explicit and are parsed unchanged.
    if re.fullmatch(r"\s*\d+(?:\.\d+)?\s*[,/]\s*\d+(?:\.\d+)?\s*", buy_line):
        low, high = re.split(r"\s*[,/]\s*", buy_line)
        buy_line = f"TRIGGER RANGE - {low} - {high}"
    if re.fullmatch(
        r"\s*\d+(?:\.\d+)?\s*,\s*\d+(?:\.\d+)?\s*,\s*\d+(?:\.\d+)?\s*",
        target_line,
    ):
        target_line = f"TARGET - {target_line}"
    if re.fullmatch(r"\s*\d+(?:\.\d+)?\s*", stoploss_line):
        stoploss_line = f"SL - {stoploss_line}"

    return "\n".join([instrument, buy_line, target_line, stoploss_line])


def read_quantity(value: int | None, label: str, env_name: str) -> int:
    raw = str(value) if value is not None else os.getenv(env_name)
    if raw is None:
        raw = input(f"{label}: ").strip()
    return _positive_int(raw, label)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the simple Fyers trade assistant")
    parser.add_argument("--signal-file", type=Path, help="Optional file containing a trade call")
    parser.add_argument("--quantity", type=int, help="Total quantity; otherwise prompt or read FYERS_QUANTITY")
    parser.add_argument("--lot-size", type=int, help="Lot size; otherwise prompt or read FYERS_LOT_SIZE")
    parser.add_argument("--interval", type=float, default=0.05, help="Minimum delay between processed ticks")
    args = parser.parse_args()

    signal = parse_trade_call(read_signal(args.signal_file))
    quantity = read_quantity(args.quantity, "Quantity", "FYERS_QUANTITY")
    lot_size = read_quantity(args.lot_size, "Lot size", "FYERS_LOT_SIZE")
    max_quantity = int(os.getenv("FYERS_MAX_QUANTITY", "10000"))
    if quantity > max_quantity:
        raise ValueError(f"Quantity {quantity} exceeds FYERS_MAX_QUANTITY={max_quantity}")
    if quantity % lot_size:
        raise ValueError(f"Quantity must be a multiple of the lot size ({lot_size})")

    live = ask_live_session()
    mode = "LIVE" if live else "DRY RUN"
    print("\nTrade preflight")
    print(json.dumps({
        "instrument": signal.instrument_name,
        "trigger_range": signal.trigger_range,
        "buy_above": signal.buy_above,
        "targets": signal.targets,
        "stoploss": signal.stoploss,
        "quantity": quantity,
        "lot_size": lot_size,
        "mode": mode,
    }, indent=2, default=str))

    client_id, access_token = authenticated_credentials()
    client = authenticated_client()
    symbol = resolve_option_symbol(client, signal)
    print(f"Resolved Fyers symbol: {symbol}")

    stream = FyersMarketDataSocket(client_id, access_token)
    stream.subscribe([symbol])
    trader = BreakoutTrader(
        signal,
        symbol,
        quantity,
        lot_size,
        FyersOrderExecutor(client, live=live),
    )
    print("Waiting for a WebSocket breakout tick. Press Ctrl+C to stop monitoring.")

    try:
        while not trader.closed:
            message = stream.wait_for_update(symbol)
            price = float(message["ltp"])
            actions = trader.on_price(price)
            print(
                f"{symbol} | LTP={price} | active SL={trader.active_stoploss:g} "
                f"| remaining={trader.remaining_quantity}"
            )
            for action in actions:
                print(f"ACTION: {action.reason} quantity={action.quantity} price={action.price}")
            if args.interval > 0:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nMonitoring stopped. Any broker position remains open; verify it in Fyers.")


if __name__ == "__main__":
    main()
