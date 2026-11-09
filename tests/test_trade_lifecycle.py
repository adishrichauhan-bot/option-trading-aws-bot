import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from trade_lifecycle import TradeRegistry, parse_trade_command


class TradeLifecycleTests(unittest.TestCase):
    def test_observed_modify_and_cancel_formats(self) -> None:
        entry = parse_trade_command("MODIFY ENTRY - BUYING RANGE - 220 - 230", message_id=7)
        self.assertEqual(entry.action, "MODIFY_ENTRY")
        self.assertEqual(entry.trigger_range, (220.0, 230.0))
        self.assertEqual(entry.message_id, 7)

        stop = parse_trade_command("MODIFY SL - 290", reply_to_message_id=7)
        self.assertEqual(stop.action, "MODIFY_SL")
        self.assertEqual(stop.stoploss, 290.0)
        self.assertEqual(stop.reply_to_message_id, 7)

        cancel = parse_trade_command("Don't buy now - cancel your orders")
        self.assertEqual(cancel.action, "CANCEL")

        reentry = parse_trade_command("RE ENTRY - 135 - 145")
        self.assertEqual(reentry.action, "REENTRY")
        self.assertEqual(reentry.trigger_range, (135.0, 145.0))

    def test_trade_ids_are_persistent_and_sequential(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trades.sqlite3"
            first = TradeRegistry(path).create({"instrument": "NIFTY 25000 PE"}, now=datetime(2026, 11, 9))
            second_registry = TradeRegistry(path)
            second = second_registry.create({"instrument": "NIFTY 24900 CE"}, now=datetime(2026, 11, 9))
            self.assertEqual(first, "FB-20261109-0001")
            self.assertEqual(second, "FB-20261109-0002")
            second_registry.update(second, "ENTERED", fyers_order_id="OID-2")
            self.assertEqual(second_registry.get(second)[0], "ENTERED")
            self.assertEqual(second_registry.get(second)[1]["fyers_order_id"], "OID-2")


if __name__ == "__main__":
    unittest.main()
