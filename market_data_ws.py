"""Thread-safe Fyers market-data WebSocket stream."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from typing import Any


class FyersMarketDataSocket:
    """Maintain live SymbolUpdate messages and expose the latest prices."""

    def __init__(self, client_id: str, access_token: str) -> None:
        try:
            from fyers_apiv3.FyersWebsocket import data_ws
        except ImportError as exc:
            raise RuntimeError("Install dependencies with: pip install -r requirements.txt") from exc

        self._data_ws = data_ws
        self._symbols: set[str] = set()
        self._prices: dict[str, dict[str, Any]] = {}
        self._events: dict[str, threading.Event] = {}
        self._sequences: dict[str, int] = {}
        self._lock = threading.RLock()
        self._started = False
        self._connected = False
        self._socket = data_ws.FyersDataSocket(
            access_token=f"{client_id}:{access_token}",
            log_path="",
            litemode=False,
            write_to_file=False,
            reconnect=True,
            on_connect=self._on_connect,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

    def _on_connect(self) -> None:
        with self._lock:
            self._connected = True
            symbols = sorted(self._symbols)
        if symbols:
            self._socket.subscribe(symbols=symbols, data_type="SymbolUpdate")
        self._socket.keep_running()

    def _on_message(self, message: dict[str, Any]) -> None:
        symbol = message.get("symbol")
        if not symbol:
            return
        with self._lock:
            self._prices[str(symbol)] = message
            self._sequences[str(symbol)] = self._sequences.get(str(symbol), 0) + 1
            event = self._events.setdefault(str(symbol), threading.Event())
            event.set()

    def _on_error(self, message: Any) -> None:
        print(f"Fyers market-data WebSocket error: {message}")

    def _on_close(self, message: Any) -> None:
        with self._lock:
            self._connected = False
        print(f"Fyers market-data WebSocket closed: {message}")

    def subscribe(self, symbols: Iterable[str]) -> None:
        requested = {str(symbol) for symbol in symbols if symbol}
        if not requested:
            return
        with self._lock:
            new_symbols = requested - self._symbols
            self._symbols.update(requested)
            for symbol in requested:
                self._events.setdefault(symbol, threading.Event())
                self._sequences.setdefault(symbol, 0)
            should_start = not self._started
            if should_start:
                self._started = True
            connected = self._connected

        if should_start:
            threading.Thread(target=self._socket.connect, name="fyers-data-ws", daemon=True).start()
        elif new_symbols and connected:
            self._socket.subscribe(symbols=sorted(new_symbols), data_type="SymbolUpdate")

    def latest(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            return self._prices.get(symbol)

    def wait_for(self, symbol: str, timeout: float = 10.0) -> dict[str, Any]:
        self.subscribe([symbol])
        with self._lock:
            message = self._prices.get(symbol)
            event = self._events[symbol]
        if message is not None:
            return message
        if not event.wait(timeout):
            raise TimeoutError(f"No WebSocket market-data message received for {symbol}")
        with self._lock:
            message = self._prices.get(symbol)
        if message is None:
            raise TimeoutError(f"No WebSocket market-data message received for {symbol}")
        return message

    def wait_for_update(self, symbol: str, timeout: float = 10.0) -> dict[str, Any]:
        """Wait for a newer tick so callers do not process the same tick twice."""
        self.subscribe([symbol])
        with self._lock:
            sequence = self._sequences[symbol]
            event = self._events[symbol]
            event.clear()
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                if self._sequences.get(symbol, 0) > sequence:
                    return self._prices[symbol]
            if time.monotonic() >= deadline:
                raise TimeoutError(f"No new WebSocket market-data message received for {symbol}")
            event.wait(timeout=min(0.25, max(0.0, deadline - time.monotonic())))
            event.clear()
