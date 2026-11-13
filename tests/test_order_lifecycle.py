import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from trading import BreakoutTrader, BrokerStateError, FyersOrderExecutor
from test_trading_safety import make_call
from trading_test_clock import use_monday


class BrokerLifecycleTests(unittest.TestCase):
    def setUp(self):
        use_monday(self)
        self.env = patch.dict("os.environ", {"FYERS_ORDER_POLL_SECONDS": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = Mock()
        self.client.cancel_order.return_value = {"s": "ok"}
        self.client.place_order.side_effect = lambda order: {"s": "ok", "id": f"O{self.client.place_order.call_count}"}
        self.rows = {}
        self.client.orderbook.side_effect = lambda data: {"s": "ok", "orderBook": [self.rows[data["id"]]]}
        self.executor = FyersOrderExecutor(self.client, live=True)
        self.trader = BreakoutTrader(replace(make_call(), trigger_range=(100, 101)), "NSE:TEST", 100, 10, self.executor)

    def state(self, order_id, status, filled):
        self.rows[order_id] = {"id": order_id, "status": status, "filledQty": filled}

    def enter(self):
        self.state("O1", 2, 100)
        self.trader.on_price(100)
        self.trader.on_price(101.5)
        self.trader.poll_orders()

    def test_ack_is_not_entry_and_pending_does_not_resubmit(self):
        self.state("O1", 6, 0)
        self.trader.on_price(100)
        self.trader.on_price(101.5)
        self.assertFalse(self.trader.entered)
        self.assertEqual(self.trader.remaining_quantity, 0)
        self.trader.on_price(101.6)
        self.assertFalse(self.trader.entered)
        self.assertEqual(self.client.place_order.call_count, 1)
        self.state("O1", 2, 100)
        self.trader.poll_orders()
        self.assertTrue(self.trader.entered)
        self.assertEqual(self.trader.remaining_quantity, 100)

    def test_partial_entry_is_counted_once_and_then_fully_confirmed(self):
        self.state("O1", 6, 30)
        self.trader.on_price(100)
        self.trader.on_price(101.5)
        self.trader.poll_orders()
        self.assertTrue(self.trader.entered)
        self.assertEqual(self.trader.remaining_quantity, 30)
        self.trader.poll_orders()
        self.assertEqual(self.trader.remaining_quantity, 30)
        self.state("O1", 2, 100)
        self.trader.poll_orders()
        self.assertEqual(self.trader.remaining_quantity, 100)

    def test_rejected_entry_closes_without_a_position(self):
        self.state("O1", 5, 0)
        self.trader.on_price(100)
        self.trader.on_price(101.5)
        self.trader.poll_orders()
        self.assertTrue(self.trader.closed)
        self.assertFalse(self.trader.entered)
        self.assertEqual(self.trader.remaining_quantity, 0)

    def test_partial_target_exit_keeps_actual_remainder_and_does_not_close_trade(self):
        self.enter()
        self.state("O2", 6, 20)
        self.trader.on_price(102.5)
        self.trader.poll_orders()
        self.assertEqual(self.trader.remaining_quantity, 80)
        self.trader.poll_orders()
        self.assertEqual(self.trader.remaining_quantity, 80)
        self.state("O2", 2, 50)
        self.trader.poll_orders()
        self.assertEqual(self.trader.remaining_quantity, 50)
        self.assertFalse(self.trader.closed)

    def test_exit_rejection_halts_instead_of_claiming_flat(self):
        self.enter()
        self.state("O2", 5, 0)
        self.trader.on_price(102.5)
        with self.assertRaises(BrokerStateError):
            self.trader.poll_orders()
        self.assertEqual(self.trader.remaining_quantity, 100)
        self.assertFalse(self.trader.closed)

    def test_stop_breach_cancels_pending_sell_before_emergency_for_unsold_balance(self):
        self.enter()
        self.state("O2", 6, 20)
        self.trader.on_price(97.5)
        self.trader.on_price(95)
        self.client.cancel_order.assert_called_once_with(data={"id": "O2"})
        self.assertEqual(self.client.place_order.call_count, 2)
        # Cancellation races with another 10 units filled.
        self.state("O2", 1, 30)
        self.trader.on_price(94)
        self.assertEqual(self.client.place_order.call_count, 3)
        self.assertEqual(self.client.place_order.call_args.args[0]["qty"], 70)
        self.assertEqual(self.client.place_order.call_args.args[0]["type"], 2)
        self.assertFalse(self.trader.closed)
        self.state("O3", 2, 70)
        self.trader.poll_orders()
        self.assertTrue(self.trader.closed)
        self.assertEqual(self.trader.remaining_quantity, 0)

    def test_full_fill_during_cancel_prevents_duplicate_emergency_sell(self):
        self.enter()
        self.state("O2", 6, 0)
        self.trader.on_price(97.5)
        self.trader.on_price(95)
        self.state("O2", 2, 100)
        self.trader.on_price(94)
        self.assertEqual(self.client.place_order.call_count, 2)
        self.assertTrue(self.trader.closed)

    def test_missing_order_status_halts_without_new_order(self):
        self.trader.on_price(100)
        self.trader.on_price(101.5)
        with self.assertRaises(BrokerStateError):
            self.trader.poll_orders()
        self.assertFalse(self.trader.entered)
        self.assertEqual(self.client.place_order.call_count, 1)

    def test_cancelled_partial_entry_uses_confirmed_position_for_targets(self):
        self.state("O1", 6, 40)
        self.trader.on_price(100)
        self.trader.on_price(101.5)
        self.trader.on_price(102.5)
        self.client.cancel_order.assert_called_once_with(data={"id": "O1"})
        self.assertEqual(self.client.place_order.call_count, 1)
        self.state("O1", 1, 40)
        self.trader.on_price(102.6)
        self.assertEqual(self.trader.remaining_quantity, 40)
        self.assertEqual(self.client.place_order.call_args.args[0]["qty"], 20)

    def test_cancel_pending_buy_waits_for_final_fill_then_exits_only_that_fill(self):
        self.state("O1", 6, 20)
        self.trader.on_price(100)
        self.trader.on_price(101.5)
        self.trader.cancel()
        self.assertFalse(self.trader.closed)
        self.assertEqual(self.client.place_order.call_count, 1)
        self.state("O1", 1, 30)
        self.trader.poll_orders()
        self.assertEqual(self.client.place_order.call_args.args[0]["qty"], 30)
        self.state("O2", 2, 30)
        self.trader.poll_orders()
        self.assertTrue(self.trader.closed)

    def test_target_pending_does_not_prevent_t3_stop_or_high_watermark_updates(self):
        self.enter()
        self.state("O2", 6, 20)
        self.trader.on_price(105)
        self.assertEqual(self.trader.active_stoploss, 104)
        self.assertTrue(self.trader.trailing)
        self.trader.on_price(120)
        self.assertAlmostEqual(self.trader.active_stoploss, 116.4)
        self.trader.on_price(113)
        self.client.cancel_order.assert_called_once_with(data={"id": "O2"})
        self.state("O2", 1, 20)
        self.trader.poll_orders()
        self.assertEqual(self.client.place_order.call_args.args[0]["qty"], 80)
        self.assertEqual(self.client.place_order.call_args.args[0]["type"], 2)

    def test_rejected_cancellation_halts_rather_than_waiting_forever_or_double_selling(self):
        self.enter()
        self.state("O2", 6, 0)
        self.trader.on_price(97.5)
        self.client.cancel_order.return_value = {"s": "error", "message": "not cancellable"}
        with self.assertRaises(BrokerStateError):
            self.trader.on_price(95)
        self.assertEqual(self.client.place_order.call_count, 2)
        self.assertEqual(self.trader.remaining_quantity, 100)

    def test_cancel_error_after_full_fill_is_not_a_duplicate_exit(self):
        self.enter()
        self.state("O2", 6, 0)
        self.trader.on_price(97.5)
        def cancel(data):
            self.state("O2", 2, 100)
            return {"s": "error", "message": "already traded"}
        self.client.cancel_order.side_effect = cancel
        self.trader.on_price(95)
        self.trader.poll_orders()
        self.assertTrue(self.trader.closed)
        self.assertEqual(self.client.place_order.call_count, 2)

    def test_regressing_fill_halts_instead_of_corrupting_position(self):
        self.state("O1", 6, 40)
        self.trader.on_price(100)
        self.trader.on_price(101.5)
        self.trader.poll_orders()
        self.state("O1", 6, 20)
        with self.assertRaises(BrokerStateError):
            self.trader.poll_orders()
        self.assertEqual(self.trader.remaining_quantity, 40)

    def test_unknown_submission_outcome_requires_reconciliation(self):
        self.client.place_order.side_effect = TimeoutError("request timed out")
        self.trader.on_price(100)
        with self.assertRaises(BrokerStateError):
            self.trader.on_price(101.5)
        self.assertFalse(self.trader.entered)

    def test_pending_market_entry_is_also_not_a_fill(self):
        self.trader.trade_call = make_call()
        self.state("O1", 6, 0)
        self.trader.on_price(100)
        self.trader.on_price(101.5)
        self.trader.poll_orders()
        self.assertFalse(self.trader.entered)
        self.assertEqual(self.client.place_order.call_args.args[0]["type"], 2)

    def test_trailing_stop_limit_breach_uses_raised_sl_and_only_unsold_runner(self):
        self.enter()
        self.state("O2", 2, 50)
        self.trader.on_price(102.5)
        self.state("O3", 2, 30)
        self.trader.on_price(103.5)
        self.trader.on_price(104.5)
        self.trader.on_price(120)
        self.assertAlmostEqual(self.trader.active_stoploss, 116.4)
        self.assertEqual(self.trader.remaining_quantity, 20)
        self.state("O4", 6, 10)
        self.trader.on_price(116)
        self.assertEqual(self.client.place_order.call_args.args[0]["type"], 1)
        self.trader.on_price(113)
        self.client.cancel_order.assert_called_once_with(data={"id": "O4"})
        self.state("O4", 1, 10)
        self.trader.poll_orders()
        self.assertEqual(self.client.place_order.call_args.args[0]["qty"], 10)
        self.assertEqual(self.client.place_order.call_args.args[0]["type"], 2)
        self.assertEqual(self.trader.remaining_quantity, 10)
        self.state("O5", 2, 10)
        self.trader.poll_orders()
        self.assertEqual(self.trader.remaining_quantity, 0)
        self.assertTrue(self.trader.closed)


if __name__ == "__main__":
    unittest.main()
