"""Owner-approved trading-day policy, evaluated in India Standard Time."""
from __future__ import annotations

import datetime as dt


INDIA_TIMEZONE = dt.timezone(dt.timedelta(hours=5, minutes=30), name='IST')
FRIDAY_WARNING = (
    "WARNING: It's Friday, a trade off day (IST). "
    "Trading startup and new entries are disabled."
)


class TradingDayOff(RuntimeError):
    """A known pre-dispatch policy refusal, not an uncertain broker outcome."""


def now_ist() -> dt.datetime:
    return dt.datetime.now(INDIA_TIMEZONE)


def is_friday(now: dt.datetime | None = None) -> bool:
    current = now if now is not None else now_ist()
    if current.tzinfo is None:
        current = current.replace(tzinfo=INDIA_TIMEZONE)
    return current.astimezone(INDIA_TIMEZONE).weekday() == 4


def ensure_trading_day(now: dt.datetime | None = None) -> None:
    if is_friday(now):
        raise TradingDayOff(FRIDAY_WARNING)
