"""Entry point for the Fyers breakout strategy.

Quantity and lot size are read from the environment. Live mode is enabled
automatically only during the India-market weekday window.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from queue import Queue
from pathlib import Path
from threading import Event, Thread

from market_data_ws import FyersMarketDataSocket, MockMarketDataSocket
from trading import (
    BreakoutTrader,
    BrokerStateError,
    TradingAuthenticationError,
    FyersOrderExecutor,
    authenticated_client,
    authenticated_credentials,
    is_live_trading_window,
    parse_trade_call,
    resolve_option_symbol,
)
from telegram_trade_listener import TradeCallMessage, listen_for_trade_calls
from trade_lifecycle import TradeCommand, TradeRegistry


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


def read_quantity(value: int | None, label: str, env_name: str, default: int) -> int:
    raw = str(value) if value is not None else os.getenv(env_name)
    if raw is None:
        raw = str(default)
    return _positive_int(raw, label)


def telegram_call_to_signal(call: TradeCallMessage) -> str:
    """Convert a validated Telegram call to the trading parser's format."""

    targets = ", ".join(f"{target:g}" for target in call.targets)
    if call.trigger_range is not None:
        low, high = call.trigger_range
        entry_line = f"TRIGGER RANGE - {low:g} - {high:g}"
    else:
        if call.buy_above is None:
            raise ValueError("Telegram trade call has no entry condition")
        entry_line = f"BUY ABOVE - {call.buy_above:g}"
    return "\n".join(
        [
            call.instrument_name,
            entry_line,
            f"TARGET - {targets}",
            f"SL - {call.stoploss:g}",
        ]
    )


def _telegram_worker(
    chat: str,
    session: str,
    calls: Queue[TradeCallMessage | TradeCommand | BaseException],
    stop_event: Event,
    mock_market: bool = False,
) -> None:
    def on_trade_call(call: TradeCallMessage | TradeCommand) -> None:
        calls.put(call)

    try:
        if not mock_market:
            authenticated_credentials()
            print("Fyers authentication verified. Starting Telegram listener for NEW calls.", flush=True)
        asyncio.run(listen_for_trade_calls(chat, session, on_trade_call, stop_event))
    except BaseException as exc:  # Forward listener failures to the main thread.
        calls.put(exc)


def _mock_price_sequence(signal: object, raw_prices: str | None) -> list[float]:
    if raw_prices:
        try:
            prices = [float(value.strip()) for value in raw_prices.split(",") if value.strip()]
        except ValueError as exc:
            raise ValueError("--mock-prices must be comma-separated positive numbers") from exc
        if not prices or any(price <= 0 for price in prices):
            raise ValueError("--mock-prices must contain positive numbers")
        return prices

    trigger_range = getattr(signal, "trigger_range", None)
    entry = getattr(signal, "buy_above")
    targets = getattr(signal, "targets")
    start = trigger_range[0] if trigger_range else max(0.01, entry - 1)
    crossing_price = trigger_range[1] + 1 if trigger_range else entry + 1
    # Demonstrate the runner: T3, another high, then a trailing-stop retracement.
    high = targets[2] * 1.10 + 1
    return [start, crossing_price, targets[0] + 1, targets[1] + 1, targets[2] + 1, high, targets[2] - 0.1]


def _command_applies(command: TradeCommand, trade_id: str, source_message_ids: tuple[int, ...]) -> bool:
    if command.trade_id is not None:
        return command.trade_id == trade_id
    if command.reply_to_message_id is not None and source_message_ids:
        # Telegram often replies to the immediately preceding MODIFY ENTRY
        # message rather than to the original multi-message call. There is
        # only one active trade in this engine, so keep the command attached
        # to that active trade when the reply is outside its original chunks.
        return True
    return True


def _signal_with_command(signal: object, command: TradeCommand) -> str:
    if command.trigger_range is not None:
        entry = f"TRIGGER RANGE - {command.trigger_range[0]:g} - {command.trigger_range[1]:g}"
    elif command.buy_above is not None:
        entry = f"BUY ABOVE - {command.buy_above:g}"
    else:
        entry = f"BUY ABOVE - {getattr(signal, 'buy_above'):g}"
    targets = command.targets or getattr(signal, "targets")
    stoploss = command.stoploss if command.stoploss is not None else getattr(signal, "stoploss")
    return "\n".join([
        command.instrument_name or getattr(signal, "instrument_name"),
        entry,
        "TARGET - " + ", ".join(f"{value:g}" for value in targets),
        f"SL - {stoploss:g}",
    ])


def run_trade(
    args: argparse.Namespace,
    signal_text: str,
    *,
    event_queue: Queue[TradeCallMessage | TradeCommand | BaseException] | None = None,
    pending_signals: list[str] | None = None,
    trade_id: str = "manual",
    source_message_ids: tuple[int, ...] = (),
    registry: TradeRegistry | None = None,
) -> bool:
    """Return whether the trade is closed; an exhausted mock feed may leave a runner."""
    signal = parse_trade_call(signal_text)
    quantity = read_quantity(args.quantity, "Quantity", "FYERS_QUANTITY", default=100)
    lot_size = read_quantity(args.lot_size, "Lot size", "FYERS_LOT_SIZE", default=10)
    max_quantity = int(os.getenv("FYERS_MAX_QUANTITY", "10000"))
    if quantity > max_quantity:
        raise ValueError(f"Quantity {quantity} exceeds FYERS_MAX_QUANTITY={max_quantity}")
    if quantity % lot_size:
        raise ValueError(f"Quantity must be a multiple of the lot size ({lot_size})")

    if args.mock_market:
        live = False
        print("Mock market mode enabled; live orders are disabled.")
    else:
        live = is_live_trading_window()
        print(
            "Automatic session mode: "
            f"{'LIVE' if live else 'DRY RUN'} (weekdays, 09:00-15:40 IST)."
        )
    mode = "MOCK DRY RUN" if args.mock_market else ("LIVE" if live else "DRY RUN")
    print("\nTrade preflight")
    print(json.dumps({
        "trade_id": trade_id,
        "instrument": signal.instrument_name,
        "trigger_range": signal.trigger_range,
        "buy_above": signal.buy_above,
        "targets": signal.targets,
        "stoploss": signal.stoploss,
        "quantity": quantity,
        "lot_size": lot_size,
        "mode": mode,
    }, indent=2, default=str))

    client = None
    if args.mock_market:
        symbol = f"MOCK:{signal.instrument_name}"
        prices = _mock_price_sequence(signal, args.mock_prices)
        stream = MockMarketDataSocket(prices, interval=args.interval)
        print(f"Mock prices: {prices}")
    else:
        client_id, access_token = authenticated_credentials()
        client = authenticated_client((client_id, access_token))
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
    journal_snapshot = None

    def journal() -> None:
        nonlocal journal_snapshot
        snapshot = {
            "symbol": symbol,
            "remaining_quantity": trader.remaining_quantity,
            "confirmed_entry_quantity": trader.confirmed_entry_quantity,
            "active_stoploss": trader.active_stoploss,
            "trailing": trader.trailing,
            "closed": trader.closed,
            "orders": [
                {"id": order.response.get("id"), "side": order.side, "quantity": order.quantity,
                 "filled_quantity": order.applied_fill, "reason": order.reason,
                 "cancel_requested": order.cancel_requested, "status": order.status}
                for order in trader.order_history
            ],
        }
        if registry is not None and snapshot != journal_snapshot:
            try:
                registry.update(trade_id, "CLOSED" if trader.closed else "ACTIVE", **snapshot)
            except Exception as exc:
                raise BrokerStateError(f"Cannot persist broker lifecycle state: {exc}") from exc
            journal_snapshot = snapshot
    print(
        "Waiting for mock breakout ticks."
        if args.mock_market
        else "Waiting for a WebSocket breakout tick. Press Ctrl+C to stop monitoring."
    )

    try:
        while not trader.closed:
            trader.poll_orders()
            journal()
            if trader.closed:
                break
            if event_queue is not None:
                while True:
                    try:
                        event = event_queue.get_nowait()
                    except Exception:
                        break
                    if isinstance(event, BaseException):
                        raise event
                    if isinstance(event, TradeCallMessage):
                        if pending_signals is not None:
                            pending_signals.append(telegram_call_to_signal(event))
                        continue
                    if not _command_applies(event, trade_id, source_message_ids):
                        print(f"Ignoring control command for another trade: {event.raw_text}")
                        continue
                    if event.action == "MODIFY_ENTRY":
                        trader.modify_entry(trigger_range=event.trigger_range, buy_above=event.buy_above)
                        print(f"{trade_id}: entry modified")
                    elif event.action == "MODIFY_SL" and event.stoploss is not None:
                        trader.modify_stoploss(event.stoploss)
                        print(f"{trade_id}: stop-loss modified to {event.stoploss:g}")
                    elif event.action == "MODIFY_TARGETS" and event.targets is not None:
                        trader.modify_targets(event.targets)
                        print(f"{trade_id}: targets modified")
                    elif event.action == "CANCEL":
                        trader.cancel("Telegram cancellation")
                    elif event.action == "EXIT":
                        trader.manual_exit("Telegram manual exit")
                    elif event.action == "REENTRY" and pending_signals is not None:
                        trader.manual_exit("Telegram re-entry requested")
                        pending_signals.append(_signal_with_command(signal, event))
                    if trader.closed:
                        break
                journal()
                if trader.closed:
                    break
            try:
                message = stream.wait_for_update(
                    symbol, timeout=min(1.0, max(0.05, args.interval or 1.0))
                )
            except TimeoutError:
                continue
            price = float(message["ltp"])
            timestamp = message.get("timestamp")
            actions = trader.on_price(price, timestamp=timestamp)
            journal()
            print(
                f"{symbol} | LTP={price} | active SL={trader.active_stoploss:g} "
                f"| remaining={trader.remaining_quantity}"
            )
            for action in actions:
                print(f"ACTION: {action.reason} quantity={action.quantity} price={action.price}")
            if args.interval > 0:
                time.sleep(args.interval)
    except StopIteration as exc:
        print(f"Mock market test finished: {exc}")
    except KeyboardInterrupt:
        print("\nMonitoring stopped. Any broker position remains open; verify it in Fyers.")
        raise
    except Exception as exc:
        if live and (trader.entered or trader.entry_order is not None or trader.exit_order is not None):
            raise BrokerStateError(f"Monitoring failed with possible broker exposure: {exc}") from exc
        raise
    finally:
        try:
            journal()
        finally:
            close_stream = getattr(stream, "close", None)
            if close_stream is not None:
                try:
                    close_stream()
                except Exception as exc:
                    print(f"Market-data stream cleanup failed: {exc}")
    return trader.closed


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the simple Fyers trade assistant")
    parser.add_argument("--signal-file", type=Path, help="Optional file containing a trade call")
    parser.add_argument(
        "--telegram-chat",
        default=os.getenv("TELEGRAM_CHAT"),
        help="Continuously read completed trade calls from this Telegram chat",
    )
    parser.add_argument(
        "--telegram-session",
        default=os.getenv("TELEGRAM_SESSION", "~/.telegram/trade_listener"),
        help="Telethon session path for --telegram-chat",
    )
    parser.add_argument(
        "--mock-market",
        action="store_true",
        help="Use deterministic dry-run prices instead of Fyers market data",
    )
    parser.add_argument(
        "--mock-prices",
        help="Optional comma-separated mock prices, for example 160,168,150",
    )
    parser.add_argument(
        "--quantity",
        type=int,
        help="Optional quantity override; otherwise FYERS_QUANTITY (default: 100)",
    )
    parser.add_argument(
        "--lot-size",
        type=int,
        help="Optional lot-size override; otherwise FYERS_LOT_SIZE (default: 10)",
    )
    parser.add_argument("--interval", type=float, default=0.05, help="Minimum delay between processed ticks")
    args = parser.parse_args()

    telegram_thread: Thread | None = None
    telegram_stop = Event()
    calls: Queue[TradeCallMessage | TradeCommand | BaseException] | None = None
    pending_signals: list[str] = []
    registry = TradeRegistry(
        os.getenv("TRADE_REGISTRY_PATH", "~/.fyers/trade_lifecycle.sqlite3")
    )
    if args.telegram_chat:
        calls = Queue()
        telegram_thread = Thread(
            target=_telegram_worker,
            args=(args.telegram_chat, args.telegram_session, calls, telegram_stop, args.mock_market),
            name="telegram-trade-listener",
            daemon=True,
        )
        print(f"Preparing Telegram listener for chat {args.telegram_chat}; checking Fyers readiness first...")
        telegram_thread.start()

    try:
        while True:
            if calls is not None:
                if pending_signals:
                    item = None
                    signal_text = pending_signals.pop(0)
                    trade_id = "reentry"
                    source_message_ids = ()
                else:
                    item = calls.get()
                    if isinstance(item, BaseException):
                        raise item
                    if isinstance(item, TradeCommand):
                        print(f"Ignoring control command without an active trade: {item.raw_text}")
                        continue
                    signal_text = telegram_call_to_signal(item)
                    trade_id = registry.create(
                        {"instrument": item.instrument_name, "message_id": item.message_id}
                    )
                    source_message_ids = item.source_message_ids
                    print(f"Telegram trade call accepted as {trade_id}.")
            else:
                signal_text = read_signal(args.signal_file)
                trade_id = "manual"
                source_message_ids = ()

            try:
                if trade_id not in {"manual", "reentry"}:
                    registry.update(trade_id, "ACTIVE")
                completed = run_trade(
                    args,
                    signal_text,
                    event_queue=calls,
                    pending_signals=pending_signals,
                    trade_id=trade_id,
                    source_message_ids=source_message_ids,
                    registry=registry if trade_id not in {"manual", "reentry"} else None,
                )
                if trade_id not in {"manual", "reentry"}:
                    registry.update(trade_id, "MOCK_INCOMPLETE" if completed is False else "CLOSED")
                if completed is False:
                    print("Mock feed exhausted with an open simulated position; not a completed trade.")
                else:
                    print("Trade completed. Returning to Telegram listener for the next trade call.")
                if calls is None:
                    return
            except KeyboardInterrupt:
                raise
            except TradingAuthenticationError as exc:
                if trade_id not in {"manual", "reentry"}:
                    registry.update(trade_id, "AUTH_REQUIRED", error=str(exc))
                print(f"Trading stopped: authentication required. {exc}", flush=True)
                return
            except BrokerStateError as exc:
                if trade_id not in {"manual", "reentry"}:
                    registry.update(trade_id, "RECONCILIATION_REQUIRED", error=str(exc))
                print(f"Broker state requires reconciliation; no next trade will start: {exc}")
                return
            except Exception as exc:
                if trade_id not in {"manual", "reentry"}:
                    registry.update(trade_id, "ERROR", error=str(exc))
                print(f"Trade stopped before completion: {exc}")
                if calls is None:
                    raise
                print("Returning to Telegram listener for the next trade call.")
    except TradingAuthenticationError as exc:
        print(f"Trading NOT READY: {exc}", flush=True)
    except KeyboardInterrupt:
        print("\nTrading engine stopped.")
    finally:
        telegram_stop.set()
        if telegram_thread is not None:
            telegram_thread.join(timeout=3)


if __name__ == "__main__":
    main()
