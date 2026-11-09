import unittest
from dataclasses import replace
from unittest.mock import patch

from trading import BreakoutTrader, FyersOrderExecutor
from test_trading_safety import make_call


class TrailingRunnerTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"FYERS_TRAILING_PERCENT": "3", "FYERS_TRAILING_ATR_MULTIPLE": "2"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.trader = BreakoutTrader(make_call(), "MOCK:TEST", 100, 10, FyersOrderExecutor(None))

    def reach_t3(self):
        for price in (100, 101.5, 102.5, 103.5, 104.5):
            self.trader.on_price(price, timestamp=1000)

    def test_t3_retains_runner_and_starts_stop_at_t3(self):
        self.reach_t3()
        self.assertTrue(self.trader.trailing)
        self.assertFalse(self.trader.closed)
        self.assertEqual(self.trader.remaining_quantity, 20)
        self.assertEqual(self.trader.active_stoploss, 104)
        self.assertEqual([order.quantity for order in self.trader.order_history], [100, 50, 30])

    def test_new_high_raises_stop_and_retracement_does_not_lower_it(self):
        self.reach_t3()
        self.trader.on_price(120, timestamp=1000)
        self.assertAlmostEqual(self.trader.active_stoploss, 116.4)
        self.trader.on_price(118, timestamp=1000)
        self.assertAlmostEqual(self.trader.active_stoploss, 116.4)
        self.assertFalse(self.trader.closed)
        self.trader.on_price(116.4, timestamp=1000)
        self.assertTrue(self.trader.closed)
        self.assertEqual(self.trader.remaining_quantity, 0)

    def test_atr_can_give_runner_more_room_without_loosening_existing_stop(self):
        self.reach_t3()
        self.trader.atr._true_ranges.append(10)
        self.trader.on_price(130, timestamp=1000)
        self.assertEqual(self.trader.active_stoploss, 110)
        self.trader.atr._true_ranges.clear()
        self.trader.atr._true_ranges.append(30)
        self.trader.on_price(131, timestamp=1000)
        self.assertEqual(self.trader.active_stoploss, 110)

    def test_gap_below_raised_stop_exits_runner_by_market(self):
        self.reach_t3()
        self.trader.on_price(120, timestamp=1000)
        self.trader.on_price(113, timestamp=1000)
        self.assertTrue(self.trader.closed)
        order = self.trader.order_history[-1]
        self.assertEqual(order.quantity, 20)
        self.assertEqual(order.response["order"]["type"], 2)
        self.assertIn("emergency market exit", order.reason)

    def test_single_lot_retains_entire_position_until_trailing_stop(self):
        trader = BreakoutTrader(make_call(), "MOCK:TEST", 10, 10, FyersOrderExecutor(None))
        for price in (100, 101.5, 102.5, 103.5, 104.5):
            trader.on_price(price, timestamp=1000)
        self.assertEqual(trader.remaining_quantity, 10)
        self.assertTrue(trader.trailing)

    def test_range_entry_delayed_fill_above_target_does_not_miss_target(self):
        # Price-level target checks, rather than another cross, are necessary after fill confirmation.
        self.trader.trade_call = replace(self.trader.trade_call, trigger_range=(100, 101))
        self.trader.on_price(100, timestamp=1000)
        self.trader.on_price(102.5, timestamp=1000)
        self.trader.on_price(102.6, timestamp=1000)
        self.assertEqual(self.trader.remaining_quantity, 50)

    def test_manual_command_cannot_lower_a_runner_stop(self):
        self.reach_t3()
        self.trader.on_price(120, timestamp=1000)
        with self.assertRaises(ValueError):
            self.trader.modify_stoploss(110)
        self.assertAlmostEqual(self.trader.active_stoploss, 116.4)

    def test_jump_straight_above_t3_protects_jump_gain_before_retracement(self):
        for price in (100, 101.5, 120):
            self.trader.on_price(price, timestamp=1000)
        self.assertTrue(self.trader.trailing)
        self.assertAlmostEqual(self.trader.active_stoploss, 116.4)
        self.assertEqual(self.trader.remaining_quantity, 20)
        self.trader.on_price(110, timestamp=1000)
        self.assertTrue(self.trader.closed)


if __name__ == "__main__":
    unittest.main()
