"""Instrument metadata and entry-time, whole-lot cash sizing (no order submission)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from typing import Any


class SizingError(ValueError):
    """Skip the signal when contract metadata or usable funds cannot be established."""


def number(value: Any, label: str, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise SizingError(f"Invalid {label}") from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise SizingError(f"Invalid {label}")
    return result


def positive_integer(value: Any, label: str) -> int:
    parsed = number(value, label, positive=True)
    if parsed != parsed.to_integral_value():
        raise SizingError(f"{label} must be a positive whole number")
    return int(parsed)


@dataclass(frozen=True)
class ContractSpec:
    symbol: str
    lot_size: int


def contract_spec(symbol: str) -> ContractSpec:
    from trading import _load_symbol_master

    exchange, separator, _ = symbol.partition(":")
    if not separator or exchange not in {"NSE", "BSE", "MCX"}:
        raise SizingError("Unsupported or unqualified contract symbol")
    master = _load_symbol_master(exchange)
    record = master.get(symbol)
    if not isinstance(record, dict):
        record = next((row for row in master.values() if isinstance(row, dict) and row.get("symTicker") == symbol), None)
    if not isinstance(record, dict):
        raise SizingError(f"Exact contract {symbol} is missing from the symbol master")
    if record.get("symTicker", symbol) != symbol:
        raise SizingError(f"Conflicting symbol-master metadata for {symbol}")
    if str(record.get("tradeStatus", "1")) != "1":
        raise SizingError(f"Contract {symbol} is inactive")
    return ContractSpec(symbol, positive_integer(record.get("minLotSize"), "contract minLotSize"))


def available_balance(response: Any, symbol: str) -> Decimal:
    if not isinstance(response, dict) or response.get("s") != "ok":
        raise SizingError("Fyers available-funds request failed")
    rows = response.get("fund_limit")
    if not isinstance(rows, list):
        raise SizingError("Fyers funds response has no fund_limit list")
    matches = [row for row in rows if isinstance(row, dict) and str(row.get("title", "")).strip().casefold() == "available balance"]
    if len(matches) != 1:
        raise SizingError("Fyers funds response must contain one Available Balance row")
    field = "commodityAmount" if symbol.startswith("MCX:") else "equityAmount"
    return number(matches[0].get(field), field)


def calculate_quantity(funds: Any, price: Any, lot_size: int, *, max_quantity: int = 10000,
                       reserve: Any = 0, rounding: str = "ceil") -> int:
    budget = number(funds, "available funds") - number(reserve, "funds reserve")
    unit_price = number(price, "entry price", positive=True)
    lot_size = positive_integer(lot_size, "lot size")
    max_quantity = positive_integer(max_quantity, "maximum quantity")
    if budget <= 0:
        raise SizingError("No funds remain after the reserve")
    if rounding not in {"floor", "ceil"}:
        raise SizingError("Sizing rounding must be floor or ceil")
    lots = (budget / (unit_price * lot_size)).to_integral_value(
        rounding=ROUND_FLOOR if rounding == "floor" else ROUND_CEILING
    )
    quantity = min(int(lots) * lot_size, (max_quantity // lot_size) * lot_size)
    if quantity <= 0:
        raise SizingError("Available funds/quantity limit cannot cover one whole lot")
    if unit_price * quantity > budget:
        raise SizingError("Ceiling quantity exceeds available funds; trade skipped")
    return quantity


class FundsSizer:
    """Called once at the breakout, using its actual limit price or current LTP."""

    def __init__(self, client: Any, spec: ContractSpec, *, rounding: str = "ceil") -> None:
        if rounding not in {"floor", "ceil"}:
            raise SizingError("Sizing rounding must be floor or ceil")
        self.client, self.spec, self.rounding = client, spec, rounding
        self.max_quantity = positive_integer(os.getenv("FYERS_MAX_QUANTITY", "10000"), "FYERS_MAX_QUANTITY")
        self.reserve = number(os.getenv("FYERS_FUNDS_RESERVE", "0"), "FYERS_FUNDS_RESERVE")
        self.last_result: dict[str, Any] | None = None

    def __call__(self, entry_price: float) -> int:
        try:
            response = self.client.funds()
        except Exception:
            raise SizingError("Could not retrieve fresh Fyers funds; no entry submitted") from None
        funds = available_balance(response, self.spec.symbol)
        quantity = calculate_quantity(funds, entry_price, self.spec.lot_size,
                                      max_quantity=self.max_quantity, reserve=self.reserve,
                                      rounding=self.rounding)
        self.last_result = {
            "symbol": self.spec.symbol, "available_funds": str(funds), "reserve": str(self.reserve),
            "entry_price": entry_price, "lot_size": self.spec.lot_size,
            "lots": quantity // self.spec.lot_size, "quantity": quantity,
            "estimated_premium": str(Decimal(str(entry_price)) * quantity), "rounding": self.rounding,
        }
        print("ENTRY SIZING: " + json.dumps(self.last_result), flush=True)
        return quantity
