"""Small HTTP API for Fyers index and ATM option market data."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query


INDEXES: dict[str, dict[str, str]] = {
    "NIFTY": {"symbol": "NSE:NIFTY50-INDEX", "name": "NIFTY 50"},
    "SENSEX": {"symbol": "BSE:SENSEX-INDEX", "name": "SENSEX"},
}


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _value(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if row.get(name) is not None:
            return row[name]
    return None


def _rows(response: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    """Extract rows from the common Fyers response envelopes."""
    for key in keys:
        value = response.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = _rows(value, *keys)
            if nested:
                return nested
    return []


def _quote(response: dict[str, Any], symbol: str) -> dict[str, Any]:
    rows = _rows(response, "d", "data", "quotes")
    for row in rows:
        if row.get("n") == symbol or row.get("symbol") == symbol:
            value = row.get("v", row)
            return value if isinstance(value, dict) else row
    if rows:
        value = rows[0].get("v", rows[0])
        return value if isinstance(value, dict) else rows[0]
    raise ValueError(f"No quote returned for {symbol}")


def _option_type(option: dict[str, Any]) -> str | None:
    value = str(_value(option, "option_type", "optionType", "type") or "").upper()
    if value in {"CE", "CALL", "1"}:
        return "CE"
    if value in {"PE", "PUT", "2"}:
        return "PE"
    symbol = str(option.get("symbol", "")).upper()
    if symbol.endswith(("-CE", "CE")):
        return "CE"
    if symbol.endswith(("-PE", "PE")):
        return "PE"
    return None


def _atm_options(response: dict[str, Any], spot: float) -> dict[str, dict[str, Any]]:
    options = _rows(response, "options_chain", "optionsChain", "d", "data")
    candidates: list[tuple[float, str, dict[str, Any]]] = []
    for option in options:
        strike = _number(_value(option, "strike_price", "strikePrice", "strike"))
        kind = _option_type(option)
        if strike is not None and kind:
            candidates.append((abs(strike - spot), kind, option))

    result: dict[str, dict[str, Any]] = {}
    for _, kind, option in sorted(candidates, key=lambda item: item[0]):
        if kind in result:
            continue
        result[kind] = {
            "symbol": option.get("symbol"),
            "strike": _number(_value(option, "strike_price", "strikePrice", "strike")),
            "ltp": _number(_value(option, "ltp", "lp", "last_price")),
            "bid": _number(_value(option, "bid", "bid_price", "bidPrice")),
            "ask": _number(_value(option, "ask", "ask_price", "askPrice")),
            "raw": option,
        }
    return result


class FyersMarketData:
    def __init__(self, client_id: str, access_token: str) -> None:
        try:
            from fyers_apiv3 import fyersModel
        except ImportError as exc:
            raise RuntimeError("Install dependencies with: pip install -r requirements.txt") from exc
        self.client = fyersModel.FyersModel(
            client_id=client_id, token=access_token, is_async=False, log_path=""
        )

    def fetch(self, names: list[str], strike_count: int) -> dict[str, Any]:
        symbols = ",".join(INDEXES[name]["symbol"] for name in names)
        quotes_response = self.client.quotes(data={"symbols": symbols})
        output: dict[str, Any] = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "indices": {},
        }
        for name in names:
            index = INDEXES[name]
            quote = _quote(quotes_response, index["symbol"])
            spot = _number(_value(quote, "lp", "ltp", "last_price"))
            if spot is None:
                raise ValueError(f"No last traded price returned for {index['symbol']}")
            chain_response = self.client.optionchain(
                data={"symbol": index["symbol"], "strikecount": strike_count, "timestamp": ""}
            )
            output["indices"][name] = {
                "name": index["name"],
                "symbol": index["symbol"],
                "spot": spot,
                "change": _number(_value(quote, "ch", "change")),
                "change_percent": _number(_value(quote, "chp", "change_percent")),
                "atm_options": _atm_options(chain_response, spot),
            }
        return output


app = FastAPI(title="Fyers Market Data MVP", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/market-data")
def market_data(
    indices: str = Query("NIFTY,SENSEX", description="Comma-separated values: NIFTY,SENSEX"),
    strike_count: int = Query(2, ge=1, le=10),
) -> dict[str, Any]:
    names = [item.strip().upper() for item in indices.split(",") if item.strip()]
    if not names or any(name not in INDEXES for name in names):
        raise HTTPException(status_code=400, detail="indices must contain NIFTY and/or SENSEX")
    client_id = os.getenv("FYERS_CLIENT_ID")
    access_token = os.getenv("FYERS_ACCESS_TOKEN")
    if not client_id or not access_token:
        raise HTTPException(status_code=500, detail="FYERS_CLIENT_ID and FYERS_ACCESS_TOKEN are required")
    try:
        return FyersMarketData(client_id, access_token).fetch(names, strike_count)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Fyers market-data request failed: {exc}") from exc
