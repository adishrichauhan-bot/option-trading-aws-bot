"""Parse trade calls and execute a cross/target/stop-loss strategy.

This module is dry-run outside the automatic IST weekday market window.
Live mode is selected automatically from Monday-Friday, 09:00-15:40 IST.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import time
from collections import deque
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv

from market_data_ws import FyersMarketDataSocket

load_dotenv()

UNDERLYING_SYMBOLS = {
    "NIFTY": "NSE:NIFTY50-INDEX",
    "NIFTY50": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
    "BANKEX": "BSE:BANKEX-INDEX",
}
NUMBER = r"\d+(?:\.\d+)?"
DASH = r"[-–—]"


@dataclass(frozen=True)
class TradeCall:
    instrument_name: str
    underlying: str
    strike: float | None
    option_type: str
    buy_above: float
    targets: tuple[float, float, float]
    stoploss: float
    trigger_range: tuple[float, float] | None = None
    fyers_symbol: str | None = None

    @property
    def underlying_symbol(self) -> str:
        if ":" in self.underlying:
            return self.underlying
        return UNDERLYING_SYMBOLS.get(
            self.underlying,
            f"{os.getenv('FYERS_DEFAULT_EXCHANGE', 'NSE')}:{self.underlying}-INDEX",
        )


def _match(text: str, pattern: str, label: str) -> re.Match[str]:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        raise ValueError(f"Could not parse {label} from trade call")
    return match


def parse_trade_call(text: str) -> TradeCall:
    """Parse BUY ABOVE or TRIGGER RANGE trade-call formats."""
    full_symbol_match = re.search(
        r"^\s*(?P<symbol>(?P<exchange>NSE|BSE|MCX):(?P<body>[A-Z0-9&._-]+(?:CE|PE)))\s*$",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    shorthand_match = re.search(
        rf"^\s*(?P<underlying>(?:(?:NSE|BSE|MCX):)?[A-Z0-9][A-Z0-9&._-]*)\s+"
        rf"(?P<strike>{NUMBER})\s+(?P<option_type>CE|PE)\s*$",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    fyers_symbol: str | None = None
    if full_symbol_match:
        fyers_symbol = full_symbol_match.group("symbol").upper()
        body = full_symbol_match.group("body").upper()
        option_type = body[-2:]
        # The exact Fyers symbol is retained and resolved directly. Its expiry
        # encoding makes strike extraction ambiguous without the symbol master.
        strike = None
        underlying = body[:-2]
        instrument_name = fyers_symbol
    elif shorthand_match:
        raw_underlying = shorthand_match.group("underlying").upper()
        underlying = raw_underlying
        strike = float(shorthand_match.group("strike"))
        option_type = shorthand_match.group("option_type").upper()
        instrument_name = f"{underlying} {strike:g} {option_type}"
    else:
        raise ValueError(
            "Instrument must be '<UNDERLYING> <STRIKE> CE/PE' or a full Fyers symbol"
        )

    buy_above_match = re.search(
        rf"BUY\s+ABOVE\s*{DASH}\s*(?P<price>{NUMBER})", text, flags=re.IGNORECASE
    )
    range_match = re.search(
        rf"(?:TRIGGER|BUYING)\s+RANGE\s*{DASH}\s*(?P<low>{NUMBER})\s*{DASH}\s*(?P<high>{NUMBER})",
        text,
        flags=re.IGNORECASE,
    )
    if buy_above_match:
        buy_above = float(buy_above_match.group("price"))
        trigger_range = None
    elif range_match:
        low = float(range_match.group("low"))
        high = float(range_match.group("high"))
        if low >= high:
            raise ValueError("Trigger-range low must be less than high")
        # The range is crossed when price moves above its upper boundary.
        buy_above = high
        trigger_range = (low, high)
    else:
        raise ValueError("Trade call must contain BUY ABOVE or TRIGGER RANGE")

    target_match = _match(
        text,
        rf"TARGETS?\s*{DASH}\s*(?P<t1>{NUMBER})\s*,\s*(?P<t2>{NUMBER})\s*,\s*(?P<t3>{NUMBER})",
        "three targets",
    )
    targets = tuple(float(target_match.group(name)) for name in ("t1", "t2", "t3"))
    if not buy_above < targets[0] < targets[1] < targets[2]:
        raise ValueError("Targets must be strictly above the entry and ascending")

    stoploss_match = _match(
        text,
        rf"(?:SL|STOP\s*LOSS|STOPLOSS)\s*{DASH}\s*(?P<stoploss>{NUMBER})",
        "stoploss",
    )
    stoploss = float(stoploss_match.group("stoploss"))
    if stoploss >= buy_above:
        raise ValueError("Stoploss must be below the buy-above price")

    return TradeCall(
        instrument_name=instrument_name,
        underlying=underlying,
        strike=strike,
        option_type=option_type,
        buy_above=buy_above,
        targets=targets,
        stoploss=stoploss,
        trigger_range=trigger_range,
        fyers_symbol=fyers_symbol,
    )


class OrderExecutor(Protocol):
    def market_order(self, symbol: str, quantity: int, side: int, reason: str) -> Any:
        ...

    def limit_order(
        self, symbol: str, quantity: int, side: int, limit_price: float, reason: str
    ) -> Any:
        ...


class FyersOrderExecutor:
    def __init__(self, client: Any, *, live: bool = False) -> None:
        self.client = client
        self.live = live
        self.product_type = os.getenv("FYERS_PRODUCT_TYPE", "INTRADAY")

    def _place_order(self, order: dict[str, Any], reason: str) -> Any:
        order_type = "LIMIT" if order["type"] == 1 else "MARKET"
        side = "BUY" if order["side"] == 1 else "SELL"
        limit_price = order.get("limitPrice", 0)
        price_text = f" limit_price={limit_price:g}" if order_type == "LIMIT" else ""
        print(
            f"ORDER SUBMIT: {order_type} {side} quantity={order['qty']} "
            f"symbol={order['symbol']}{price_text} reason={reason}",
            flush=True,
        )
        if not self.live:
            print(json.dumps({"dry_run": True, "reason": reason, "order": order}), flush=True)
            return {"s": "dry_run", "order": order}
        print(
            f"LIVE ORDER: {reason}: side={order['side']} "
            f"quantity={order['qty']} symbol={order['symbol']}"
        )
        print(f"LIVE ORDER PAYLOAD: {json.dumps(order, sort_keys=True)}")
        response = self.client.place_order(order)
        if isinstance(response, dict) and response.get("s") == "error":
            raise RuntimeError(f"Fyers rejected order: {response}")
        return response

    def market_order(self, symbol: str, quantity: int, side: int, reason: str) -> Any:
        order = {
            "symbol": symbol,
            "qty": quantity,
            "type": 2,
            "side": side,
            "productType": self.product_type,
            "limitPrice": 0,
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
            "stopLoss": 0,
            "takeProfit": 0,
            # Fyers orderTag accepts alphanumeric characters only.
            "orderTag": "mvpTrader",
            "isSliceOrder": False,
        }
        return self._place_order(order, reason)

    def limit_order(
        self, symbol: str, quantity: int, side: int, limit_price: float, reason: str
    ) -> Any:
        order = {
            "symbol": symbol,
            "qty": quantity,
            "type": 1,
            "side": side,
            "productType": self.product_type,
            "limitPrice": limit_price,
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
            "stopLoss": 0,
            "takeProfit": 0,
            "orderTag": "mvpTrader",
            "isSliceOrder": False,
        }
        return self._place_order(order, reason)

    def is_order_filled(self, response: Any) -> bool:
        """Return a conservative fill decision for an exit response.

        Fyers may acknowledge an order before it is filled. Missing status is
        therefore treated as pending in live mode so the emergency guard can
        protect the remaining position.
        """
        if not self.live:
            return True
        if not isinstance(response, dict):
            return False
        status = response.get("status", response.get("orderStatus"))
        if isinstance(status, str):
            return status.upper() in {"FILLED", "TRADED", "COMPLETE", "EXECUTED"}
        return status == 2


# India has a fixed UTC+05:30 offset and does not observe daylight saving time.
INDIA_TIMEZONE = dt.timezone(dt.timedelta(hours=5, minutes=30), name="IST")
LIVE_MARKET_START = dt.time(9, 0)
LIVE_MARKET_END = dt.time(15, 40)


def is_live_trading_window(now: dt.datetime | None = None) -> bool:
    """Return whether automatic live trading is allowed at the current IST time.

    The live window is Monday-Friday, 09:00:00 through 15:40:00 IST,
    inclusive. A datetime argument is supported for deterministic tests.
    """
    current = now or dt.datetime.now(INDIA_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=INDIA_TIMEZONE)
    current = current.astimezone(INDIA_TIMEZONE)
    return current.weekday() < 5 and LIVE_MARKET_START <= current.time() <= LIVE_MARKET_END


def ask_live_session() -> bool:
    """Backward-compatible name for the automatic live-session decision."""
    return is_live_trading_window()

@dataclass(frozen=True)
class OrderAction:
    reason: str
    quantity: int
    side: int
    price: float


class OneMinuteATR:
    """Build one-minute bars from LTP ticks and calculate ATR."""

    def __init__(self, period: int = 14) -> None:
        if period <= 0:
            raise ValueError("ATR period must be positive")
        self.period = period
        self._bucket: int | None = None
        self._high: float | None = None
        self._low: float | None = None
        self._close: float | None = None
        self._previous_close: float | None = None
        self._true_ranges: deque[float] = deque(maxlen=period)

    @property
    def value(self) -> float | None:
        if not self._true_ranges:
            return None
        return sum(self._true_ranges) / len(self._true_ranges)

    def update(self, price: float, timestamp: float | None = None) -> float | None:
        if price <= 0:
            raise ValueError("Price must be positive")
        current_timestamp = time.time() if timestamp is None else float(timestamp)
        if current_timestamp > 100_000_000_000:
            current_timestamp /= 1000.0
        bucket = int(current_timestamp // 60)

        if self._bucket is None:
            self._bucket = bucket
            self._high = self._low = self._close = price
            return self.value

        if bucket != self._bucket:
            assert self._high is not None and self._low is not None and self._close is not None
            if self._previous_close is None:
                true_range = self._high - self._low
            else:
                true_range = max(
                    self._high - self._low,
                    abs(self._high - self._previous_close),
                    abs(self._low - self._previous_close),
                )
            self._true_ranges.append(true_range)
            self._previous_close = self._close
            self._bucket = bucket
            self._high = self._low = self._close = price
            return self.value

        assert self._high is not None and self._low is not None
        self._high = max(self._high, price)
        self._low = min(self._low, price)
        self._close = price
        return self.value


class BreakoutTrader:
    """State machine for entry, staged exits, and a trailing stop by target."""

    def __init__(
        self,
        trade_call: TradeCall,
        symbol: str,
        quantity: int,
        lot_size: int,
        executor: OrderExecutor,
    ) -> None:
        if quantity <= 0 or lot_size <= 0 or quantity % lot_size:
            raise ValueError("quantity must be a positive multiple of lot_size")
        self.trade_call = trade_call
        self.symbol = symbol
        self.quantity = quantity
        self.lot_size = lot_size
        self.executor = executor
        self.previous_price: float | None = None
        self.entered = False
        self.closed = False
        self.remaining_quantity = 0
        self.active_stoploss = trade_call.stoploss
        self.targets_hit: set[int] = set()
        self.target_quantities = self._allocate_targets()
        self.atr = OneMinuteATR(
            period=int(os.getenv("FYERS_ATR_PERIOD", "14"))
        )
        self.pending_exit: tuple[Any, int, float] | None = None
        self.emergency_exit_sent = False

    def _allocate_targets(self) -> tuple[int, int, int]:
        first = math.floor(self.quantity * 0.50 / self.lot_size) * self.lot_size
        second = math.floor(self.quantity * 0.30 / self.lot_size) * self.lot_size
        third = self.quantity - first - second
        return first, second, third

    def modify_entry(
        self,
        *,
        trigger_range: tuple[float, float] | None = None,
        buy_above: float | None = None,
    ) -> None:
        if self.entered or self.closed:
            raise RuntimeError("entry cannot be modified after the trade has started or closed")
        if trigger_range is None and buy_above is None:
            raise ValueError("modified entry requires a range or BUY ABOVE price")
        if trigger_range is not None:
            low, high = trigger_range
            if low > high:
                raise ValueError("modified trigger range is invalid")
        self.trade_call = replace(self.trade_call, trigger_range=trigger_range, buy_above=buy_above)

    def modify_stoploss(self, stoploss: float) -> None:
        if stoploss <= 0 or (self.entered and stoploss >= self.previous_price):
            raise ValueError("modified stop-loss is invalid")
        self.active_stoploss = stoploss
        self.trade_call = replace(self.trade_call, stoploss=stoploss)

    def modify_targets(self, targets: tuple[float, ...]) -> None:
        if len(targets) != 3 or not all(first < second for first, second in zip(targets, targets[1:])):
            raise ValueError("exactly three ascending targets are required")
        if targets[0] <= self.active_stoploss:
            raise ValueError("first target must be above the active stop-loss")
        self.trade_call = replace(self.trade_call, targets=targets)  # type: ignore[arg-type]

    def _exit_order(self, quantity: int, reason: str, price: float) -> OrderAction | None:
        if quantity <= 0:
            return None
        response = self.executor.market_order(self.symbol, quantity, -1, reason)
        is_filled = getattr(self.executor, "is_order_filled", None)
        filled = True if is_filled is None else bool(is_filled(response))
        if filled:
            self.remaining_quantity = max(0, self.remaining_quantity - quantity)
        else:
            self.pending_exit = (response, quantity, self.active_stoploss)
        return OrderAction(reason, quantity, -1, price)

    def manual_exit(self, reason: str = "manual exit", price: float = 0.0) -> list[OrderAction]:
        action = self._exit_order(self.remaining_quantity, reason, price)
        if action is not None and self.pending_exit is None:
            self.closed = True
        return [] if action is None else [action]

    def cancel(self, reason: str = "trade cancelled", price: float = 0.0) -> list[OrderAction]:
        if not self.entered:
            self.closed = True
            return []
        return self.manual_exit(reason, price)

    def _check_pending_exit(self, price: float) -> list[OrderAction]:
        if self.pending_exit is None:
            return []
        response, quantity, stoploss = self.pending_exit
        is_filled = getattr(self.executor, "is_order_filled", None)
        if is_filled is not None and is_filled(response):
            self.remaining_quantity = 0
            self.pending_exit = None
            self.closed = True
            return []
        if price <= stoploss * 0.98 and not self.emergency_exit_sent:
            self.emergency_exit_sent = True
            emergency = self.executor.market_order(
                self.symbol, quantity, -1, "emergency market exit: stop-loss order pending >2%"
            )
            filled = True if is_filled is None else bool(is_filled(emergency))
            if filled:
                self.remaining_quantity = 0
                self.pending_exit = None
                self.closed = True
            else:
                self.pending_exit = (emergency, quantity, stoploss)
            return [OrderAction("emergency market exit: stop-loss order pending >2%", quantity, -1, price)]
        return []

    def on_price(self, price: float, timestamp: float | None = None) -> list[OrderAction]:
        if price <= 0:
            raise ValueError("Price must be positive")
        current_atr = self.atr.update(price, timestamp)
        pending_actions = self._check_pending_exit(price)
        if self.pending_exit is not None:
            self.previous_price = price
            return pending_actions
        if pending_actions and self.closed:
            self.previous_price = price
            return pending_actions
        if self.previous_price is None:
            self.previous_price = price
            return []

        previous = self.previous_price
        actions: list[OrderAction] = []
        if not self.entered:
            trigger_range = self.trade_call.trigger_range
            crossed_trigger_range = bool(
                trigger_range and previous <= trigger_range[1] < price
            )
            crossed_buy_above = (
                self.trade_call.buy_above is not None
                and previous <= self.trade_call.buy_above < price
            )
            if crossed_trigger_range:
                reason = "trigger range crossed"
                raw_buffer = max(1.0, 0.5 * (current_atr or 0.0))
                max_buffer = price * 0.02
                slippage_buffer = min(raw_buffer, max_buffer)
                limit_price = price + slippage_buffer
                print(
                    f"ENTRY PRICING: LTP={price:g} ATR_1m="
                    f"{current_atr if current_atr is not None else 0:g} "
                    f"buffer={slippage_buffer:g} limit={limit_price:g}"
                )
                self.executor.limit_order(
                    self.symbol, self.quantity, 1, limit_price, reason
                )
                self.entered = True
                self.remaining_quantity = self.quantity
                actions.append(OrderAction(reason, self.quantity, 1, limit_price))
            elif crossed_buy_above:
                reason = "buy-above crossed"
                self.executor.market_order(self.symbol, self.quantity, 1, reason)
                self.entered = True
                self.remaining_quantity = self.quantity
                actions.append(OrderAction(reason, self.quantity, 1, price))
            self.previous_price = price
            return actions

        if previous >= self.active_stoploss > price:
            quantity = self.remaining_quantity
            if quantity:
                reason = f"stoploss_{self.active_stoploss:g} crossed"
                exit_action = self._exit_order(quantity, reason, price)
                if exit_action is not None:
                    actions.append(exit_action)
            if self.pending_exit is None:
                self.remaining_quantity = 0
                self.closed = True
            self.previous_price = price
            return actions

        for index, target in enumerate(self.trade_call.targets):
            if index in self.targets_hit or not previous <= target < price:
                continue
            quantity = min(self.target_quantities[index], self.remaining_quantity)
            self.targets_hit.add(index)
            if index in {0, 1}:
                self.active_stoploss = target
            if quantity:
                exit_action = self._exit_order(quantity, f"target_{index + 1} crossed", price)
                if exit_action is not None:
                    actions.append(exit_action)

        if self.remaining_quantity == 0:
            self.closed = True
        self.previous_price = price
        return actions


def _option_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    data = response.get("data", response.get("d", {}))
    if isinstance(data, dict):
        rows = data.get("optionsChain", data.get("options_chain", []))
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _option_type(row: dict[str, Any]) -> str:
    return str(
        row.get("option_type", row.get("optionType", row.get("optType", "")))
    ).upper()


def _strike(row: dict[str, Any]) -> float | None:
    value = row.get("strike_price", row.get("strikePrice", row.get("strike")))
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _master_code(exchange: str) -> str:
    return {"NSE": "NSE_FO", "BSE": "BSE_FO", "MCX": "MCX_COM"}.get(
        exchange, "NSE_FO"
    )


def _load_symbol_master(exchange: str) -> dict[str, Any]:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("Install dependencies with: pip install -r requirements.txt") from exc

    master = _master_code(exchange)
    cache_dir = Path(os.path.expanduser("~/.fyers/sym_master"))
    cache_path = cache_dir / f"{master}.json"
    today = dt.date.today().isoformat()
    if cache_path.exists() and dt.datetime.fromtimestamp(
        cache_path.stat().st_mtime, tz=dt.timezone.utc
    ).date().isoformat() == today:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    try:
        response = requests.get(
            f"https://public.fyers.in/sym_details/{master}_sym_master.json",
            timeout=20,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not download the Fyers symbol master: {exc}") from exc
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected {master} symbol-master response")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _resolve_from_symbol_master(trade_call: TradeCall) -> str | None:
    exchange = trade_call.underlying.split(":", 1)[0] if ":" in trade_call.underlying else "NSE"
    underlying = trade_call.underlying.split(":", 1)[-1].upper()
    master = _load_symbol_master(exchange)
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    matches: list[tuple[float, str]] = []
    for key, raw_record in master.items():
        if not isinstance(raw_record, dict):
            continue
        symbol = str(raw_record.get("symTicker", key))
        if not symbol.upper().startswith(f"{exchange}:"):
            continue
        if _option_type(raw_record) != trade_call.option_type:
            continue
        if _strike(raw_record) != trade_call.strike:
            continue
        searchable = " ".join(
            [symbol]
            + [
                str(raw_record.get(field, ""))
            for field in ("underlyingSymbol", "underlying_symbol", "exSymName", "symDetails", "symbol")
            ]
        ).upper()
        if underlying not in searchable:
            continue
        try:
            expiry = float(raw_record.get("expiryDate", raw_record.get("expiry_date", 0)) or 0)
        except (TypeError, ValueError):
            expiry = 0
        if expiry and expiry < now:
            continue
        matches.append((expiry or float("inf"), symbol))
    if not matches:
        return None
    return min(matches, key=lambda item: item[0])[1]


def resolve_option_symbol(client: Any, trade_call: TradeCall) -> str:
    if trade_call.fyers_symbol:
        return trade_call.fyers_symbol
    response = client.optionchain(
        data={"symbol": trade_call.underlying_symbol, "strikecount": 50}
    )
    for row in _option_rows(response):
        if (
            _option_type(row) == trade_call.option_type
            and _strike(row) == trade_call.strike
        ):
            return str(row["symbol"])
    symbol = _resolve_from_symbol_master(trade_call)
    if symbol:
        return symbol
    raise ValueError(
        f"Could not resolve {trade_call.instrument_name}. Check the exchange, expiry, "
        "strike, and option type against the current Fyers symbol master."
    )


def authenticated_credentials() -> tuple[str, str]:
    # Keep Fyers imports out of parser-only usage.
    from auth import access_token_works, read_tokens

    client_id = os.getenv("FYERS_CLIENT_ID") or os.getenv("client_id")
    tokens = read_tokens()
    access_token = os.getenv("FYERS_ACCESS_TOKEN") or tokens.get("access_token")
    if not client_id or not access_token:
        raise RuntimeError("Run auth.py first to create a usable cached access token")
    if not access_token_works(client_id, access_token):
        raise RuntimeError(
            "Fyers access token is expired or invalid. Run `python auth.py` "
            "to refresh or regenerate it, then restart live_trader.py."
        )
    return client_id, access_token


def authenticated_client() -> Any:
    from auth import build_client

    client_id, access_token = authenticated_credentials()
    return build_client(client_id, access_token)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the breakout target/stop strategy")
    parser.add_argument("signal_file", type=Path, help="Text file containing the trade call")
    parser.add_argument(
        "--quantity",
        type=int,
        default=None,
        help="Optional quantity override; otherwise FYERS_QUANTITY (default: 100)",
    )
    parser.add_argument(
        "--lot-size",
        type=int,
        default=None,
        help="Optional lot-size override; otherwise FYERS_LOT_SIZE (default: 10)",
    )
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds")
    parser.add_argument(
        "--parse-only", action="store_true", help="Parse and print the signal without API access"
    )
    args = parser.parse_args()

    trade_call = parse_trade_call(args.signal_file.read_text(encoding="utf-8"))
    if args.parse_only:
        print(json.dumps(asdict(trade_call)))
        return

    quantity = int(os.getenv("FYERS_QUANTITY", "100")) if args.quantity is None else args.quantity
    lot_size = int(os.getenv("FYERS_LOT_SIZE", "10")) if args.lot_size is None else args.lot_size
    if quantity <= 0 or lot_size <= 0:
        raise ValueError("FYERS_QUANTITY and FYERS_LOT_SIZE must be greater than zero")
    live = is_live_trading_window()
    print(
        "Automatic session mode: "
        f"{'LIVE' if live else 'DRY RUN'} (weekdays, 09:00-15:40 IST)."
    )
    client_id, access_token = authenticated_credentials()
    client = authenticated_client()
    symbol = resolve_option_symbol(client, trade_call)
    print(json.dumps({"signal": trade_call.__dict__, "symbol": symbol, "live": live}, default=str))

    trader = BreakoutTrader(
        trade_call, symbol, quantity, lot_size, FyersOrderExecutor(client, live=live)
    )
    stream = FyersMarketDataSocket(client_id, access_token)
    stream.subscribe([symbol])
    print("Waiting for WebSocket ticks and a confirmed upward cross; no order is placed on the first tick.")
    while not trader.closed:
        message = stream.wait_for_update(symbol)
        price = float(message["ltp"])
        print(f"{symbol} price={price}")
        trader.on_price(price)
        if args.interval > 0:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
