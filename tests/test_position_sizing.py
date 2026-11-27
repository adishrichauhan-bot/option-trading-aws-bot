
from contextlib import redirect_stdout
from io import StringIO
from live_trader import telegram_call_to_signal
from telegram_trade_listener import TradeCallAssembler
from trade_lifecycle import parse_trade_command
from trading import parse_trade_call
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from position_sizing import ContractSpec, FundsSizer, SizingError, available_balance, calculate_quantity, contract_spec
from trading import BreakoutTrader, FyersOrderExecutor
from test_trading_safety import make_call


def funds(amount, commodity=0):
    return {"s": "ok", "fund_limit": [
        {"title": "Total Balance", "equityAmount": 999999},
        {"id": 10, "title": "Available Balance", "equityAmount": amount, "commodityAmount": commodity},
    ]}


class QuantityCalculationTests(unittest.TestCase):
    def test_floor_is_affordable_and_a_whole_lot(self):
        self.assertEqual(calculate_quantity(10000, 120, 25, rounding="floor"), 75)
        self.assertEqual(calculate_quantity(10000, 94, 20), 100)
        self.assertEqual(calculate_quantity(10000, 100, 1), 95)

    def test_ceiling_rule_rejects_unaffordable_result(self):
        with self.assertRaisesRegex(SizingError, "Ceiling"):
            calculate_quantity(10000, 120, 25, rounding="ceil")
        with self.assertRaises(SizingError):
            calculate_quantity(10000, 94, 20, rounding="ceil")

    def test_ceiling_exact_whole_lots_can_trade(self):
        self.assertEqual(calculate_quantity(10000, 95, 25, rounding="ceil"), 100)

    def test_not_even_one_lot_skips(self):
        with self.assertRaises(SizingError):
            calculate_quantity(1000, 120, 25)

    def test_max_quantity_rounds_down_to_lots(self):
        self.assertEqual(calculate_quantity(100000, 100, 65, max_quantity=200), 195)

    def test_reserve_reduces_usable_budget(self):
        self.assertEqual(calculate_quantity(10000, 100, 20, reserve=2500), 60)
        self.assertEqual(calculate_quantity(10000, 100, 20, reserve=7000), 20)

    def test_decimal_boundary_does_not_lose_a_lot(self):
        self.assertEqual(calculate_quantity("0.20", "0.01", 1), 19)

    def test_invalid_inputs_fail_closed(self):
        for amount, price, lot in [("NaN", 10, 20), (1000, "Infinity", 20), (1000, 0, 20),
                                   (-1, 10, 20), (1000, 10, 0), (1000, 10, 2.5)]:
            with self.subTest(amount=amount, price=price, lot=lot):
                with self.assertRaises(SizingError):
                    calculate_quantity(amount, price, lot)


class FundsAndMetadataTests(unittest.TestCase):
    def test_uses_available_balance_not_total_or_list_order(self):
        self.assertEqual(available_balance(funds(1234), "BSE:TEST"), Decimal(1234))
        self.assertEqual(available_balance(funds(1234, 987), "MCX:TEST"), Decimal(987))

    def test_missing_failed_or_duplicate_balances_rejected(self):
        for response in ({"s": "error"}, {"s": "ok", "fund_limit": []},
                         {"s": "ok", "fund_limit": funds(10)["fund_limit"] * 2}):
            with self.subTest(response=response):
                with self.assertRaises(SizingError):
                    available_balance(response, "NSE:TEST")

    def test_exact_instrument_metadata_is_authoritative_not_env(self):
        with (patch.dict("os.environ", {"FYERS_LOT_SIZE": "999", "FYERS_QUANTITY": "999999"}),
              patch("trading._load_symbol_master", return_value={"BSE:TEST": {"minLotSize": 20, "tradeStatus": 1}})):
            self.assertEqual(contract_spec("BSE:TEST").lot_size, 20)

    def test_missing_inactive_or_invalid_contract_rejected(self):
        for master in ({}, {"NSE:TEST": {"minLotSize": 0}}, {"NSE:TEST": {"minLotSize": 65, "tradeStatus": 0}}):
            with self.subTest(master=master):
                with patch("trading._load_symbol_master", return_value=master):
                    with self.assertRaises(SizingError):
                        contract_spec("NSE:TEST")


class EntrySizingTests(unittest.TestCase):
    def test_market_entry_uses_breakout_ltp_and_never_resizes_exits(self):
        client = Mock()
        client.funds.return_value = funds(10200)
        with patch.dict("os.environ", {"FYERS_FUNDS_RESERVE": "0", "FYERS_MAX_QUANTITY": "10000"}):
            sizer = FundsSizer(client, ContractSpec("NSE:TEST", 10), rounding="floor")
        trader = BreakoutTrader(make_call(), "NSE:TEST", 10, 10, FyersOrderExecutor(None), entry_sizer=sizer)
        trader.on_price(100)
        trader.on_price(102)
        self.assertEqual(sizer.last_result["entry_price"], 102)
        self.assertEqual(trader.order_history[0].quantity, 90)
        trader.on_price(102.5)
        self.assertEqual(trader.remaining_quantity, 50)
        client.funds.assert_called_once()

    def test_range_funds_queried_only_at_eligible_ask_and_limit_price_used(self):
        from dataclasses import replace
        client = Mock()
        client.funds.return_value = funds(10000)
        with patch.dict("os.environ", {"FYERS_FUNDS_RESERVE": "0", "FYERS_MAX_QUANTITY": "10000"}):
            sizer = FundsSizer(client, ContractSpec("NSE:TEST", 10), rounding="floor")
        call = replace(make_call(), trigger_range=(100, 110), buy_above=110,
                       targets=(130, 140, 150), stoploss=90)
        trader = BreakoutTrader(call, "NSE:TEST", 10, 10, FyersOrderExecutor(None), entry_sizer=sizer)
        trader.on_price(99)
        client.funds.assert_not_called()
        trader.on_price(102)
        client.funds.assert_called_once()
        self.assertEqual(sizer.last_result["entry_price"], 102)
        self.assertEqual(trader.quantity, 90)
        self.assertEqual(trader.remaining_quantity, 90)
        self.assertEqual(trader.target_quantities, (40, 20, 30))

    def test_each_new_trade_rechecks_changed_funds_and_market_price(self):
        client = Mock()
        client.funds.side_effect = [funds(10200), funds(5100)]
        with patch.dict("os.environ", {"FYERS_FUNDS_RESERVE": "0", "FYERS_MAX_QUANTITY": "10000"}):
            sizer = FundsSizer(client, ContractSpec("NSE:TEST", 10), rounding="floor")
        self.assertEqual(sizer(102), 90)
        self.assertEqual(sizer(102), 40)
        self.assertEqual(client.funds.call_count, 2)

    def test_funds_failure_or_insufficient_budget_submits_no_entry(self):
        client = Mock()
        client.funds.return_value = funds(1)
        with patch.dict("os.environ", {"FYERS_FUNDS_RESERVE": "0", "FYERS_MAX_QUANTITY": "10000"}):
            sizer = FundsSizer(client, ContractSpec("NSE:TEST", 20))
        executor = FyersOrderExecutor(None)
        executor.market_order = Mock()
        trader = BreakoutTrader(make_call(), "NSE:TEST", 20, 20, executor, entry_sizer=sizer)
        trader.on_price(100)
        with self.assertRaises(SizingError):
            trader.on_price(102)
        executor.market_order.assert_not_called()
        self.assertFalse(trader.entered)


class SignalSizingRegressionTests(unittest.TestCase):
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
                self.assertLessEqual(float(sizer.last_result["estimated_premium"]), 9500)
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


if __name__ == "__main__":
    unittest.main()
