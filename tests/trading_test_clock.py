"""Existing broker lifecycle fixtures use a deterministic permitted day."""
import datetime as dt
from unittest.mock import patch


def use_monday(test):
    clock = patch('trading_policy.now_ist',
                  return_value=dt.datetime.fromisoformat('2026-11-09T10:00:00+05:30'))
    clock.start()
    test.addCleanup(clock.stop)
