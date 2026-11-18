"""Replay the supplied afternoon setups with simulated funds/ticks; no live orders."""

import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

from live_trader import telegram_call_to_signal
from position_sizing import ContractSpec, FundsSizer, SizingError, calculate_quantity
from telegram_trade_listener import TradeCallAssembler
from trading import BreakoutTrader, FyersOrderExecutor, parse_trade_call
from trade_lifecycle import parse_trade_command


class AfternoonSizingRegressionTests(unittest.TestCase):
    def make_trader(self, instrument, entry, targets, stoploss):
        assembler = TradeCallAssembler()
        self.assertIsNone(assembler.append(1, "Trade Upcoming Be Ready\n\n" + instrument))
        self.assertIsNone(assembler.append(2, entry))
        call = assembler.append(3, f"TARGET - {targets}\n\nSL - {stoploss}")
        parsed = parse_trade_call(telegram_call_to_signal(call))
        client = Mock()
        # Synthetic budget deliberately not an exact whole-lot premium.
        client.funds.return_value = {"s": "ok", "fund_limit": [{"title": "Available Balance", "equityAmount": 10000}]}
        symbol = "BSE:TEST"
        with patch.dict("os.environ", {"FYERS_FUNDS_RESERVE": "0", "FYERS_MAX_QUANTITY": "10000"}):
            sizer = FundsSizer(client, ContractSpec(symbol, 20))
        trader = BreakoutTrader(parsed, symbol, 20, 20, FyersOrderExecutor(None), entry_sizer=sizer)
        return trader, sizer

    def test_default_floor_preserves_affordable_trades_with_fractional_lot_budgets(self):
        self.assertEqual(calculate_quantity(10000, 94, 20), 40)
        with self.assertRaises(SizingError):
            calculate_quantity(10000, 94, 20, rounding="ceil")

    def test_supplied_setups_obey_range_reward_risk_and_affordability(self):
        cases = [
            ("SENSEX 81700 PE", "BUYING RANGE - 100 - 110", "130, 150, 190", 80, 100, True),
            ("SENSEX 81600 PE", "BUYING RANGE - 80 - 88", "104, 125, 150", 60, 84, False),
            ("SENSEX 82100 CE", "BUYING RANGE - 82 - 90", "110, 130, 160", 62, 88, False),
            ("SENSEX 82000 CE", "BUYING RANGE - 74 - 82", "100, 120, 155", 58, 74, True),
        ]
        for instrument, entry, targets, stoploss, price, eligible in cases:
            with self.subTest(instrument=instrument):
                trader, sizer = self.make_trader(instrument, entry, targets, stoploss)
                trader.on_price(price)
                if not eligible:
                    self.assertFalse(trader.entered)
                    self.assertEqual(trader.order_history, [])
                    self.assertIsNone(sizer.last_result)
                    continue
                self.assertTrue(trader.entered)
                self.assertGreater(trader.remaining_quantity, 0)
                self.assertEqual(trader.remaining_quantity % 20, 0)
                self.assertLessEqual(float(sizer.last_result["estimated_premium"]), 5000)
                self.assertEqual(sizer.last_result["rounding"], "floor")
                self.assertEqual(len(trader.order_history), 1)

    def test_skip_diagnostics_preserve_budget_price_lot_and_reason(self):
        trader, sizer = self.make_trader("SENSEX 81600 PE", "BUYING RANGE - 80 - 88", "120, 130, 150", 60)
        sizer.rounding = "ceil"
        with self.assertRaises(SizingError):
            trader.on_price(84)
        self.assertEqual(sizer.last_result["status"], "SKIPPED")
        self.assertEqual(sizer.last_result["available_funds"], "10000")
        self.assertEqual(sizer.last_result["lot_size"], 20)
        self.assertIn("Ceiling", sizer.last_result["reason"])
        self.assertEqual(trader.order_history, [])

    def test_second_call_accepts_modified_stop_after_affordable_entry(self):
        trader, _ = self.make_trader("SENSEX 81600 PE", "BUYING RANGE - 80 - 88", "120, 130, 150", 60)
        trader.on_price(84)
        command = parse_trade_command("MODIFY SL - 48")
        trader.modify_stoploss(command.stoploss)
        self.assertEqual(trader.active_stoploss, 48)
        trader.on_price(47)
        self.assertTrue(trader.closed)
        self.assertEqual(trader.remaining_quantity, 0)

    def test_first_tick_96_5_waits_for_return_to_calculated_zone_without_breakout(self):
        trader, sizer = self.make_trader("SENSEX 82000 CE", "BUYING RANGE - 74 - 82", "100, 120, 155", 58)
        output = StringIO()
        with redirect_stdout(output):
            trader.on_price(96.5)
        self.assertFalse(trader.entered)
        self.assertIsNone(sizer.last_result)
        trader.on_price(80)
        self.assertFalse(trader.entered)
        trader.on_price(74)
        self.assertTrue(trader.entered)
