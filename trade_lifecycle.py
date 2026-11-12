"""Trade identity, Telegram control-command parsing, and safety policies."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TradeCommand:
    """A control message that changes or closes an active trade."""

    action: str
    trade_id: str | None = None
    instrument_name: str | None = None
    trigger_range: tuple[float, float] | None = None
    buy_above: float | None = None
    targets: tuple[float, ...] | None = None
    stoploss: float | None = None
    exit_price: float | None = None
    raw_text: str = ""
    message_id: int | None = None
    reply_to_message_id: int | None = None
    awaiting_levels: bool = False


_NUMBER = r"\d+(?:\.\d+)?"
_TRADE_ID = re.compile(r"\bFB-\d{8}-\d{4,}\b", re.IGNORECASE)


def _numbers(value: str) -> tuple[float, ...]:
    return tuple(float(item) for item in re.findall(_NUMBER, value))


def parse_trade_command(
    text: str,
    *,
    message_id: int | None = None,
    reply_to_message_id: int | None = None,
) -> TradeCommand | None:
    """Parse explicit Telegram modifications, cancellations, and exits.

    The parser intentionally returns a command only for control language. A
    normal initial call is still assembled by ``telegram_trade_listener``.
    """

    raw = text.strip()
    if not raw:
        return None
    upper = re.sub(r"\s+", " ", raw.upper())
    trade_match = _TRADE_ID.search(upper)
    trade_id = trade_match.group(0).upper() if trade_match else None

    if re.search(r"\b(?:CANCEL|IGNORE|DON['’]?T BUY|DO NOT BUY)\b", upper):
        return TradeCommand("CANCEL", trade_id=trade_id, raw_text=raw,
                            message_id=message_id, reply_to_message_id=reply_to_message_id)
    if re.search(r"\b(?:EXIT|BOOK FULL|BOOK PROFIT|COST TO COST|EXIT AT COST)\b", upper):
        values = _numbers(upper)
        return TradeCommand("EXIT", trade_id=trade_id,
                            exit_price=values[0] if values else None, raw_text=raw,
                            message_id=message_id, reply_to_message_id=reply_to_message_id)

    is_reentry = bool(re.search(r"\bRE\s*-?\s*ENTRY\b|\bBUY\s+AGAIN\b", upper))
    is_entry_update = bool(re.search(r"\bMODIFY\s+ENTRY\b", upper))
    if is_reentry or is_entry_update:
        range_match = re.search(
            rf"(?:BUYING\s+RANGE|TRIGGER\s+RANGE|RANGE)\s*[-:]?\s*({_NUMBER})\s*(?:-|TO)\s*({_NUMBER})",
            upper,
        )
        above_match = re.search(rf"\b(?:BUY\s+)?ABOVE\s*[-:]?\s*({_NUMBER})", upper)
        trigger_range = None
        buy_above = None
        if range_match:
            trigger_range = (float(range_match.group(1)), float(range_match.group(2)))
        elif above_match:
            buy_above = float(above_match.group(1))
        else:
            values = _numbers(upper)
            if len(values) >= 2:
                trigger_range = (values[0], values[1])
            elif values:
                buy_above = values[0]
        target_match = re.search(r"TARGETS?\s*[-:]?\s*([\d., /-]+)", upper)
        targets = _numbers(target_match.group(1)) if target_match else None
        sl_match = re.search(rf"(?:SL|STOP\s*-?\s*LOSS|STOPLOSS)\s*[-:]?\s*({_NUMBER})", upper)
        return TradeCommand(
            "REENTRY" if is_reentry else "MODIFY_ENTRY",
            trade_id=trade_id,
            trigger_range=trigger_range,
            buy_above=buy_above,
            targets=targets,
            stoploss=float(sl_match.group(1)) if sl_match else None,
            raw_text=raw,
            message_id=message_id,
            reply_to_message_id=reply_to_message_id,
        )

    if re.search(r"\bMODIFY\s+(?:STOP\s*-?\s*LOSS|SL)\b", upper):
        match = re.search(rf"(?:SL|STOP\s*-?\s*LOSS|STOPLOSS)\s*[-:]?\s*({_NUMBER})", upper)
        if match:
            return TradeCommand("MODIFY_SL", trade_id=trade_id, stoploss=float(match.group(1)),
                                raw_text=raw, message_id=message_id,
                                reply_to_message_id=reply_to_message_id)

    # Plain TARGET fields belong to initial calls. The listener promotes a
    # standalone field to MODIFY only when no call is being assembled.
    target_update = re.search(r"\bMODIFY\s+TARGETS?\s*[-:]?\s*([\d., /-]+)", upper)
    if target_update:
        # Stop at the next field label; SL and trade IDs are not target prices.
        values = _numbers(target_update.group(1))
        if len(values) == 3:
            return TradeCommand("MODIFY_TARGETS", trade_id=trade_id, targets=values,
                                raw_text=raw, message_id=message_id,
                                reply_to_message_id=reply_to_message_id)
    return None


class TradeRegistry:
    """Small SQLite journal for stable IDs and recovery/audit information."""

    def __init__(self, path: str | Path = "trade_lifecycle.sqlite3") -> None:
        resolved = Path(path).expanduser()
        if resolved.parent != Path("."):
            resolved.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(resolved)
        self._lock = threading.RLock()
        with self._connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS trades (trade_id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def create(self, payload: dict[str, Any], *, now: datetime | None = None) -> str:
        current = now or datetime.now()
        prefix = f"FB-{current:%Y%m%d}-"
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT trade_id FROM trades WHERE trade_id LIKE ? ORDER BY trade_id DESC LIMIT 1",
                (prefix + "%",),
            ).fetchone()
            sequence = int(rows[0].rsplit("-", 1)[1]) + 1 if rows else 1
            trade_id = f"{prefix}{sequence:04d}"
            connection.execute(
                "INSERT INTO trades VALUES (?, ?, ?, ?)",
                (trade_id, "RECEIVED", json.dumps(payload, default=str), current.isoformat()),
            )
        return trade_id

    def update(self, trade_id: str, status: str, **payload: Any) -> None:
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT payload FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown trade ID: {trade_id}")
            current = json.loads(row[0])
            current.update(payload)
            connection.execute(
                "UPDATE trades SET status = ?, payload = ?, updated_at = ? WHERE trade_id = ?",
                (status, json.dumps(current, default=str), datetime.now().isoformat(), trade_id),
            )

    def get(self, trade_id: str) -> tuple[str, dict[str, Any]] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT status, payload FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()
        return None if row is None else (row[0], json.loads(row[1]))
