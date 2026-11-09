"""Small API for Fyers index and ATM option market data."""

from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from market_data_ws import FyersMarketDataSocket
from trader_control import TraderProcessManager


load_dotenv()


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
    strikes = {
        strike
        for option in options
        if (strike := _number(_value(option, "strike_price", "strikePrice", "strike"))) is not None
        and _option_type(option) in {"CE", "PE"}
    }
    if not strikes:
        return {}

    atm_strike = min(strikes, key=lambda strike: abs(strike - spot))
    result: dict[str, dict[str, Any]] = {}
    for option in options:
        strike = _number(_value(option, "strike_price", "strikePrice", "strike"))
        kind = _option_type(option)
        if strike != atm_strike or kind is None:
            continue
        result[kind] = {
            "symbol": option.get("symbol"),
            "strike": strike,
            "ltp": _number(_value(option, "ltp", "lp", "last_price")),
            "change": _number(_value(option, "ltpch", "change")),
            "change_percent": _number(_value(option, "ltpchp", "change_percent")),
            "bid": _number(_value(option, "bid", "bid_price", "bidPrice")),
            "ask": _number(_value(option, "ask", "ask_price", "askPrice")),
            "open_interest": _number(_value(option, "oi", "open_interest")),
            "implied_volatility": _number(_value(option, "iv", "implied_volatility")),
        }
    return result


def _cached_access_token() -> str | None:
    path = Path(os.path.expanduser(os.getenv("token_file", "~/.fyers/token.json")))
    try:
        tokens = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    token = tokens.get("access_token") if isinstance(tokens, dict) else None
    return str(token) if token else None


class FyersMarketData:
    def __init__(self, client_id: str, access_token: str) -> None:
        try:
            from fyers_apiv3 import fyersModel
        except ImportError as exc:
            raise RuntimeError("Install dependencies with: pip install -r requirements.txt") from exc
        self.client = fyersModel.FyersModel(
            client_id=client_id, token=access_token, is_async=False, log_path=""
        )
        self.stream = FyersMarketDataSocket(client_id, access_token)

    def fetch(self, names: list[str], strike_count: int) -> dict[str, Any]:
        output: dict[str, Any] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "indices": {},
        }
        index_symbols = [INDEXES[name]["symbol"] for name in names]
        option_chains: dict[str, tuple[dict[str, Any], float]] = {}
        stream_symbols = set(index_symbols)
        for name in names:
            index = INDEXES[name]
            chain_response = self.client.optionchain(
                data={"symbol": index["symbol"], "strikecount": strike_count}
            )
            index_stream_quote = self.stream.latest(index["symbol"])
            spot = _number(_value(index_stream_quote or {}, "ltp", "lp", "last_price"))
            if spot is None:
                # The stream may not have delivered its first tick yet. Subscribe
                # and wait for the first SymbolUpdate instead of polling quotes().
                index_stream_quote = self.stream.wait_for(index["symbol"])
                spot = _number(_value(index_stream_quote, "ltp", "lp", "last_price"))
            if spot is None:
                raise ValueError(f"No last traded price returned for {index['symbol']}")
            atm = _atm_options(chain_response, spot)
            option_symbols = [str(option["symbol"]) for option in atm.values() if option.get("symbol")]
            stream_symbols.update(option_symbols)
            option_chains[name] = (chain_response, spot)

        self.stream.subscribe(stream_symbols)
        for name in names:
            index = INDEXES[name]
            chain_response, spot = option_chains[name]
            quote = self.stream.wait_for(index["symbol"])
            atm_options = _atm_options(chain_response, spot)
            for option_type, option in atm_options.items():
                option_symbol = option.get("symbol")
                if option_symbol:
                    option_quote = self.stream.wait_for(str(option_symbol))
                    option["ltp"] = _number(_value(option_quote, "ltp", "lp", "last_price"))
                    option["bid"] = _number(_value(option_quote, "bid_price", "bid"))
                    option["ask"] = _number(_value(option_quote, "ask_price", "ask"))
            output["indices"][name] = {
                "name": index["name"],
                "symbol": index["symbol"],
                "spot": spot,
                "change": _number(_value(quote, "ch", "change")),
                "change_percent": _number(_value(quote, "chp", "change_percent")),
                "atm_options": atm_options,
            }
        return output


app = FastAPI(title="Fyers Market Data MVP", version="0.1.0")
trader_manager = TraderProcessManager()


class TraderSettingsRequest(BaseModel):
    quantity: int = Field(gt=0)
    lot_size: int = Field(gt=0)


class TraderStartRequest(TraderSettingsRequest):
    telegram_chat: str | None = None
    telegram_session: str | None = None
    mock_market: bool = False


TRADER_UI = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Engine Control</title>
<style>
body{font:16px system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;background:#f5f7fb;color:#172033}
main{background:white;border:1px solid #dce2ec;border-radius:12px;padding:1.25rem;box-shadow:0 4px 18px #17203312}
label{display:block;margin:.75rem 0 .25rem}input{padding:.55rem;width:100%;box-sizing:border-box;border:1px solid #b8c2d1;border-radius:6px}
button{padding:.6rem 1rem;margin:1rem .5rem 1rem 0;border:0;border-radius:6px;background:#155eef;color:white;cursor:pointer}
button.stop{background:#c62828}button.secondary{background:#596579}pre{background:#101827;color:#dce7f7;padding:1rem;border-radius:8px;min-height:9rem;white-space:pre-wrap}
.note{color:#596579;font-size:.9rem}.state{font-weight:700}
</style></head><body><main><h1>Trading Engine Control</h1>
<p class="note">This page controls the local <code>live_trader.py</code> process. Quantity and lot-size changes apply when the engine is started next; they do not resize an active broker position. Stop only after checking Fyers; it does not close broker positions.</p>
<label>Trade quantity</label><input id="quantity" type="number" min="1" value="100">
<label>Lot size</label><input id="lot_size" type="number" min="1" value="10">
<label>Telegram chat (optional; .env is used when blank)</label><input id="telegram_chat" placeholder="-100..."><label>Telegram session (optional)</label><input id="telegram_session" placeholder="~/.telegram/trade_listener">
<label><input id="mock_market" type="checkbox" style="width:auto"> Mock market / dry run</label>
<button onclick="startEngine()">Start engine</button><button class="stop" onclick="stopEngine()">Stop engine</button><button class="secondary" onclick="refresh()">Refresh</button>
<p>Status: <span class="state" id="state">loading...</span></p><pre id="output"></pre>
<script>
async function call(path, options){const r=await fetch(path,{headers:{'Content-Type':'application/json'},...options});const d=await r.json();if(!r.ok)throw Error(d.detail||'request failed');return d}
function show(d){document.getElementById('state').textContent=d.running?'RUNNING (PID '+d.pid+')':'STOPPED';document.getElementById('output').textContent=(d.output||[]).join('\n')}
async function refresh(){try{show(await call('/trader/status'))}catch(e){document.getElementById('state').textContent=e}}
async function startEngine(){try{show(await call('/trader/start',{method:'POST',body:JSON.stringify({quantity:+quantity.value,lot_size:+lot_size.value,telegram_chat:telegram_chat.value||null,telegram_session:telegram_session.value||null,mock_market:mock_market.checked})}))}catch(e){alert(e)}}
async function stopEngine(){try{show(await call('/trader/stop',{method:'POST'}))}catch(e){alert(e)}}
refresh();setInterval(refresh,2000)
</script></main></body></html>"""


@app.get("/trader", response_class=HTMLResponse)
def trader_ui() -> str:
    return TRADER_UI


@app.get("/trader/status")
def trader_status() -> dict[str, Any]:
    return trader_manager.status()


@app.post("/trader/settings")
def trader_settings(request: TraderSettingsRequest) -> dict[str, Any]:
    try:
        return trader_manager.update_settings(request.quantity, request.lot_size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/trader/start")
def trader_start(request: TraderStartRequest) -> dict[str, Any]:
    try:
        return trader_manager.start(
            quantity=request.quantity,
            lot_size=request.lot_size,
            telegram_chat=request.telegram_chat,
            telegram_session=request.telegram_session,
            mock_market=request.mock_market,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409 if isinstance(exc, RuntimeError) else 400, detail=str(exc)) from exc


@app.post("/trader/stop")
def trader_stop() -> dict[str, Any]:
    return trader_manager.stop()


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
    client_id = os.getenv("FYERS_CLIENT_ID") or os.getenv("client_id")
    access_token = os.getenv("FYERS_ACCESS_TOKEN") or _cached_access_token()
    if not client_id or not access_token:
        raise HTTPException(
            status_code=500,
            detail="Run auth.py first or provide FYERS_CLIENT_ID and FYERS_ACCESS_TOKEN",
        )
    try:
        return FyersMarketData(client_id, access_token).fetch(names, strike_count)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Fyers market-data request failed: {exc}") from exc
