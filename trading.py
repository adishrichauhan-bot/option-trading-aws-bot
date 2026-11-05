"""Parse trade calls and execute a cross/target/stop-loss strategy.

This module is dry-run by default. Pass ``--live`` only after validating the
signal, option symbol, quantity, and order responses in a non-production setup.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv

load_dotenv()

UNDERLYINGS = {
    "NIFTY": "NSE:NIFTY50-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
}
NUMBER = r"\d+(?:\.\d+)?"
DASH = r"[-–—]"


@dataclass(frozen=True)
class TradeCall:
    instrument_name: str
    underlying: str
    strike: float
    option_type: str
    buy_above: float
    targets: tuple[float, float, float]
    stoploss: float
    buying_range: tuple[float, float] | None = None

    @property
    def underlying_symbol(self) -> str:
        return UNDERLYINGS[self.underlying]


def _match(text: str, pattern: str, label: str) -> re.Match[str]:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        raise ValueError(f"Could not parse {label} from trade call")
    return match


def parse_trade_call(text: str) -> TradeCall:
    """Parse BUY ABOVE or BUYING RANGE trade-call formats."""
    instrument = _match(
        text,
        rf"^\s*(?P<underlying>NIFTY|SENSEX)\s+(?P<strike>{NUMBER})\s+(?P<option_type>CE|PE)\s*$",
        "instrument name",
    )
    underlying = instrument.group("underlying").upper()
    strike = float(instrument.group("strike"))
    option_type = instrument.group("option_type").upper()
    instrument_name = f"{underlying} {strike:g} {option_type}"

    buy_above_match = re.search(
        rf"BUY\s+ABOVE\s*{DASH}\s*(?P<price>{NUMBER})", text, flags=re.IGNORECASE
    )
    range_match = re.search(
        rf"BUYING\s+RANGE\s*{DASH}\s*(?P<low>{NUMBER})\s*{DASH}\s*(?P<high>{NUMBER})",
        text,
        flags=re.IGNORECASE,
    )
    if buy_above_match:
        buy_above = float(buy_above_match.group("price"))
        buying_range = None
    elif range_match:
        low = float(range_match.group("low"))
        high = float(range_match.group("high"))
        if low >= high:
            raise ValueError("Buying-range low must be less than high")
        # A range becomes a breakout trigger at its upper boundary.
        buy_above = high
        buying_range = (low, high)
    else:
        raise ValueError("Trade call must contain BUY ABOVE or BUYING RANGE")

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
        buying_range=buying_range,
    )


class OrderExecutor(Protocol):
    def market_order(self, symbol: str, quantity: int, side: int, reason: str) -> Any:
        ...


class FyersOrderExecutor:
    def __init__(self, client: Any, *, live: bool = False) -> None:
        self.client = client
        self.live = live
        self.product_type = os.getenv("FYERS_PRODUCT_TYPE", "INTRADAY")

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
            "orderTag": "mvp-trader",
            "isSliceOrder": False,
        }
        if not self.live:
            print(json.dumps({"dry_run": True, "reason": reason, "order": order}))
            return {"s": "dry_run", "order": order}
        print(f"LIVE ORDER: {reason}: {side=} {quantity=} {symbol=}")
        return self.client.place_order(order)


@dataclass(frozen=True)
class OrderAction:
    reason: str
    quantity: int
    side: int
    price: float


class BreakoutTrader:
    """State machine that reacts only to observed price crossings."""

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
            if previous <= self.trade_call.buy_above < price:
                self.executor.market_order(self.symbol, self.quantity, 1, "buy-above crossed")
                self.entered = True
                self.remaining_quantity = self.quantity
                actions.append(OrderAction("buy-above crossed", self.quantity, 1, price))
            self.previous_price = price
            return actions

        if previous >= self.trade_call.stoploss > price:
            quantity = self.remaining_quantity
            if quantity:
                self.executor.market_order(self.symbol, quantity, -1, "stoploss crossed")
                actions.append(OrderAction("stoploss crossed", quantity, -1, price))
            self.remaining_quantity = 0
            self.closed = True
            self.previous_price = price
            return actions

        for index, target in enumerate(self.trade_call.targets):
            if index in self.targets_hit or not previous <= target < price:
                continue
            quantity = min(self.target_quantities[index], self.remaining_quantity)
            self.targets_hit.add(index)
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


def resolve_option_symbol(client: Any, trade_call: TradeCall) -> str:
    response = client.optionchain(
        data={"symbol": trade_call.underlying_symbol, "strikecount": 50}
    )
    for row in _option_rows(response):
        if (
            row.get("option_type") == trade_call.option_type
            and float(row.get("strike_price", -1)) == trade_call.strike
        ):
            return str(row["symbol"])
    raise ValueError(
        f"Could not resolve {trade_call.instrument_name} in the nearest Fyers option chain"
    )


def quote_price(client: Any, symbol: str) -> float:
    response = client.quotes(data={"symbols": symbol})
    rows = response.get("d", [])
    if not rows:
        raise RuntimeError(f"No quote returned for {symbol}")
    value = rows[0].get("v", rows[0])
    price = value.get("lp") if isinstance(value, dict) else None
    if price is None:
        raise RuntimeError(f"Quote has no last price for {symbol}: {response}")
    return float(price)


def authenticated_client() -> Any:
    # Keep Fyers imports out of parser-only usage.
    from auth import build_client, read_tokens

    client_id = os.getenv("FYERS_CLIENT_ID") or os.getenv("client_id")
    tokens = read_tokens()
    access_token = os.getenv("FYERS_ACCESS_TOKEN") or tokens.get("access_token")
    if not client_id or not access_token:
        raise RuntimeError("Run auth.py first to create a usable cached access token")
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

    client = authenticated_client()
    symbol = resolve_option_symbol(client, trade_call)
    print(json.dumps({"signal": trade_call.__dict__, "symbol": symbol, "live": args.live}, default=str))

    trader = BreakoutTrader(
        trade_call, symbol, args.quantity, args.lot_size, FyersOrderExecutor(client, live=args.live)
    )
    print("Waiting for a confirmed upward cross; no order is placed on the first price tick.")
    while not trader.closed:
        price = quote_price(client, symbol)
        print(f"{symbol} price={price}")
        trader.on_price(price)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
