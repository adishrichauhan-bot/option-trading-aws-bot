"""Parse trade calls and execute a cross/target/stop-loss strategy.

This module is dry-run by default. Pass ``--live`` only after validating the
signal, option symbol, quantity, and order responses in a non-production setup.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass
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
        if not self.live:
            print(json.dumps({"dry_run": True, "reason": reason, "order": order}))
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


@dataclass(frozen=True)
class OrderAction:
    reason: str
    quantity: int
    side: int
    price: float


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

    def _allocate_targets(self) -> tuple[int, int, int]:
        first = math.floor(self.quantity * 0.50 / self.lot_size) * self.lot_size
        second = math.floor(self.quantity * 0.30 / self.lot_size) * self.lot_size
        third = self.quantity - first - second
        return first, second, third

    def on_price(self, price: float) -> list[OrderAction]:
        if price <= 0:
            raise ValueError("Price must be positive")
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
            crossed_buy_above = previous <= self.trade_call.buy_above < price
            if crossed_trigger_range:
                reason = "trigger range crossed"
                self.executor.limit_order(
                    self.symbol, self.quantity, 1, trigger_range[1], reason
                )
                self.entered = True
                self.remaining_quantity = self.quantity
                actions.append(OrderAction(reason, self.quantity, 1, trigger_range[1]))
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
                self.executor.market_order(self.symbol, quantity, -1, reason)
                actions.append(OrderAction(reason, quantity, -1, price))
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
                self.executor.market_order(
                    self.symbol, quantity, -1, f"target_{index + 1} crossed"
                )
                actions.append(OrderAction(f"target_{index + 1} crossed", quantity, -1, price))
                self.remaining_quantity -= quantity

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
    from auth import read_tokens

    client_id = os.getenv("FYERS_CLIENT_ID") or os.getenv("client_id")
    tokens = read_tokens()
    access_token = os.getenv("FYERS_ACCESS_TOKEN") or tokens.get("access_token")
    if not client_id or not access_token:
        raise RuntimeError("Run auth.py first to create a usable cached access token")
    return client_id, access_token


def authenticated_client() -> Any:
    from auth import build_client

    client_id, access_token = authenticated_credentials()
    return build_client(client_id, access_token)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the breakout target/stop strategy")
    parser.add_argument("signal_file", type=Path, help="Text file containing the trade call")
    parser.add_argument("--quantity", type=int, required=True, help="Total option quantity")
    parser.add_argument("--lot-size", type=int, required=True, help="Contract lot size")
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds")
    parser.add_argument(
        "--parse-only", action="store_true", help="Parse and print the signal without API access"
    )
    parser.add_argument("--live", action="store_true", help="Enable real order placement")
    args = parser.parse_args()

    trade_call = parse_trade_call(args.signal_file.read_text(encoding="utf-8"))
    if args.parse_only:
        print(json.dumps(asdict(trade_call)))
        return

    client_id, access_token = authenticated_credentials()
    client = authenticated_client()
    symbol = resolve_option_symbol(client, trade_call)
    print(json.dumps({"signal": trade_call.__dict__, "symbol": symbol, "live": args.live}, default=str))

    trader = BreakoutTrader(
        trade_call, symbol, args.quantity, args.lot_size, FyersOrderExecutor(client, live=args.live)
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
