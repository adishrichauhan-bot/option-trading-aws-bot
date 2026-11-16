"""Buy-zone execution uses executable asks and preserves broker fill ownership."""
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from trading import BreakoutTrader, FyersOrderExecutor
from test_trading_safety import make_call
from trading_test_clock import use_monday


class RangeEntryTests(unittest.TestCase):
    def setUp(self):
        use_monday(self)
        settings = patch.dict('os.environ', {
            'FYERS_ORDER_POLL_SECONDS': '0',
            'FYERS_PRICE_TICK': '0.05',
            'FYERS_ENTRY_MIN_RR': '1.5',
            'FYERS_ENTRY_TTL_SECONDS': '60',
            'FYERS_ENTRY_MAX_QUOTE_AGE_SECONDS': '5',
        })
        settings.start()
        self.addCleanup(settings.stop)
        self.now = 100.0
        clock = patch('trading.time.monotonic', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.client = Mock()
        self.client.place_order.side_effect = lambda order: {
            's': 'ok', 'id': f'O{self.client.place_order.call_count}'}
        self.client.cancel_order.return_value = {'s': 'ok'}
        self.rows = {'O1': {'id': 'O1', 'status': 6, 'filledQty': 0}}
        self.client.orderbook.side_effect = lambda data: {
            's': 'ok', 'orderBook': [self.rows[data['id']]]}
        self.quote(104)
        self.executor = FyersOrderExecutor(self.client, live=True)
        self.call = replace(make_call(), buy_above=110, trigger_range=(100, 110),
                            stoploss=90, targets=(130, 140, 150))
        self.trader = BreakoutTrader(self.call, 'NSE:TEST', 100, 10, self.executor)

    def quote(self, ask):
        self.client.depth.return_value = {
            's': 'ok', 'd': {'NSE:TEST': {'ask': [{'price': ask, 'volume': 1000}]}}}

    def tick(self, price, *, ask=None):
        self.now += 1
        if ask is not None:
            self.quote(ask)
        return self.trader.on_price(price)

    def test_first_tick_enters_at_ask_inside_zone_without_breakout(self):
        self.trader.on_price(103)
        order = self.client.place_order.call_args.args[0]
        self.assertEqual(order['limitPrice'], 104)
        self.assertEqual(order['type'], 1)
        self.assertEqual(order['side'], 1)
        self.assertFalse(self.trader.entered)  # Acknowledgement is not a fill.
        self.assertEqual(self.trader.remaining_quantity, 0)
        self.client.depth.assert_called_with(data={'symbol': 'NSE:TEST', 'ohlcv_flag': 0})

    def test_ask_above_reward_risk_cap_waits_then_enters_on_pullback(self):
        # (130 + 1.5*90) / 2.5 = 106, below the range's high of 110.
        self.tick(104, ask=107)
        self.client.place_order.assert_not_called()
        self.tick(105, ask=105)
        self.assertEqual(self.client.place_order.call_args.args[0]['limitPrice'], 105)

    def test_range_high_caps_entry_even_when_reward_risk_allows_more(self):
        self.trader.modify_targets((200, 210, 220))
        self.tick(111, ask=111)
        self.client.place_order.assert_not_called()
        self.tick(110, ask=110)
        self.assertEqual(self.client.place_order.call_args.args[0]['limitPrice'], 110)

    def test_ask_below_range_waits_for_recovery_into_zone(self):
        self.tick(99, ask=99)
        self.client.place_order.assert_not_called()
        self.tick(100, ask=100)
        self.assertEqual(self.client.place_order.call_args.args[0]['limitPrice'], 100)

    def test_tick_rounding_cannot_raise_limit_above_cap(self):
        self.tick(105.9, ask=106.01)
        self.client.place_order.assert_not_called()
        self.tick(105.9, ask=105.96)
        self.assertEqual(self.client.place_order.call_args.args[0]['limitPrice'], 106)

    def test_missing_bad_or_empty_live_depth_does_not_fall_back_to_ltp(self):
        malformed = [
            {'s': 'error'}, {'s': 'ok', 'd': {}},
            {'s': 'ok', 'd': {'NSE:TEST': {'ask': []}}},
        ]
        for value in (True, None, 0, -1, float('nan'), float('inf')):
            malformed.append({'s': 'ok', 'd': {'NSE:TEST': {
                'ask': [{'price': value, 'volume': 100}]}}})
        for response in malformed:
            with self.subTest(response=response):
                self.client.depth.return_value = response
                self.tick(104)
                self.client.place_order.assert_not_called()

    def test_empty_or_invalid_ask_size_is_not_executable_liquidity(self):
        for volume in (0, -1, None, True, float('nan')):
            with self.subTest(volume=volume):
                self.client.depth.return_value = {'s': 'ok', 'd': {'NSE:TEST': {
                    'ask': [{'price': 104, 'volume': volume}]}}}
                self.tick(104)
                self.client.place_order.assert_not_called()

    def test_best_available_ask_is_used_without_relying_on_level_order(self):
        self.client.depth.return_value = {'s': 'ok', 'd': {'NSE:TEST': {'ask': [
            {'price': 105, 'volume': 100}, {'price': 104, 'volume': 100},
        ]}}}
        self.tick(103)
        self.assertEqual(self.client.place_order.call_args.args[0]['limitPrice'], 104)

    def test_unavailable_or_slow_depth_never_submits(self):
        self.client.depth.side_effect = TimeoutError('synthetic quote timeout')
        self.tick(104)
        self.client.place_order.assert_not_called()
        def slow_depth(data):
            self.now += 6
            return {'s': 'ok', 'd': {'NSE:TEST': {'ask': [{'price': 104, 'volume': 100}]}}}
        self.client.depth.side_effect = slow_depth
        self.tick(104)
        self.client.place_order.assert_not_called()

    def test_quote_is_revalidated_after_sizing_before_submission(self):
        def size(price):
            self.quote(107)
            return 100
        self.trader.entry_sizer = size
        self.tick(104)
        self.client.place_order.assert_not_called()

    def test_slow_sizing_cannot_use_a_stale_ltp_with_fresh_depth(self):
        def size(price):
            self.now += 6
            return 100
        self.trader.entry_sizer = size
        self.tick(104)
        self.assertEqual(self.client.depth.call_count, 2)
        self.client.place_order.assert_not_called()

    def test_sizing_crossing_opportunity_deadline_prevents_dispatch(self):
        def size(price):
            self.now = 161
            return 100
        self.trader.entry_sizer = size
        self.tick(104)
        self.client.place_order.assert_not_called()
        self.assertTrue(self.trader.closed)

    def test_ask_increase_within_cap_still_cannot_overrun_sized_limit(self):
        sized_prices = []
        def size(price):
            sized_prices.append(price)
            self.quote(105)  # Still under cap106, but affordability used104.
            return 100
        self.trader.entry_sizer = size
        self.tick(104)
        self.assertEqual(sized_prices, [104])
        self.client.place_order.assert_not_called()

    def test_fractional_reward_risk_ceiling_rounds_down_to_tick(self):
        self.trader.modify_targets((130.03, 140, 150))  # Raw ceiling106.012.
        self.tick(105.9, ask=106.01)
        self.client.place_order.assert_not_called()
        self.tick(106, ask=106)
        self.assertEqual(self.client.place_order.call_args.args[0]['limitPrice'], 106)

    def test_zone_without_a_valid_tick_never_dispatches(self):
        self.trader.modify_entry(trigger_range=(100.01, 100.02))
        self.tick(100.01, ask=100.01)
        self.client.place_order.assert_not_called()
        self.now = 161
        self.trader.poll_orders()
        self.assertTrue(self.trader.closed)

    def test_pending_ack_does_not_reprice_or_submit_another_entry(self):
        self.tick(104)
        self.tick(105, ask=105)
        self.tick(105.5, ask=105.5)
        self.assertEqual(self.client.place_order.call_count, 1)
        self.client.modify_order.assert_not_called()

    def test_waiting_opportunity_expires_without_a_tick(self):
        self.now = 161
        self.trader.poll_orders()
        self.assertTrue(self.trader.closed)
        self.client.place_order.assert_not_called()

    def test_amending_range_does_not_restart_the_entry_expiry_clock(self):
        self.now = 150
        self.trader.modify_entry(trigger_range=(101, 110))
        self.now = 161
        self.tick(104)
        self.client.place_order.assert_not_called()
        self.assertTrue(self.trader.closed)

    def test_pending_entry_expires_and_waits_for_confirmed_cancellation(self):
        self.tick(104)
        self.now = 161
        self.trader.poll_orders()
        self.client.cancel_order.assert_called_once_with(data={'id': 'O1'})
        self.assertFalse(self.trader.closed)
        self.rows['O1'] = {'id': 'O1', 'status': 1, 'filledQty': 0}
        self.trader.poll_orders()
        self.assertTrue(self.trader.closed)
        self.assertEqual(self.client.place_order.call_count, 1)

    def test_stale_market_ticks_cancel_unfilled_entry(self):
        self.tick(104)
        self.now += 6
        self.trader.poll_orders()
        self.client.cancel_order.assert_called_once_with(data={'id': 'O1'})

    def test_expiry_retains_confirmed_partial_position_for_management(self):
        self.tick(104)
        self.rows['O1'] = {'id': 'O1', 'status': 6, 'filledQty': 40}
        self.now = 161
        self.trader.poll_orders()
        self.assertEqual(self.trader.remaining_quantity, 40)
        self.client.cancel_order.assert_called_once()
        self.rows['O1'] = {'id': 'O1', 'status': 1, 'filledQty': 40}
        self.trader.poll_orders()
        self.assertEqual(self.trader.remaining_quantity, 40)
        self.assertFalse(self.trader.closed)
        self.assertEqual(self.client.place_order.call_count, 1)

    def test_confirmed_full_fill_wins_over_expiry_and_stale_tick_cancellation(self):
        self.tick(104)
        self.rows['O1'] = {'id': 'O1', 'status': 2, 'filledQty': 100}
        self.now = 161
        self.trader.poll_orders()
        self.client.cancel_order.assert_not_called()
        self.assertIsNone(self.trader.entry_order)
        self.assertTrue(self.trader.entered)
        self.assertFalse(self.trader.closed)
        self.assertEqual(self.trader.remaining_quantity, 100)
        self.assertEqual(self.client.place_order.call_count, 1)

    def test_sl_or_first_target_before_entry_invalidates_signal(self):
        for price in (90, 130):
            with self.subTest(price=price):
                trader = BreakoutTrader(self.call, 'NSE:TEST', 100, 10, self.executor)
                self.quote(price)
                trader.on_price(price)
                self.assertTrue(trader.closed)
                self.quote(104)
                self.now += 1
                trader.on_price(104)
                self.client.place_order.assert_not_called()

    def test_pending_entry_cancels_on_sl_and_reconciles_racing_fill_before_exit(self):
        self.tick(104)
        self.rows['O1'] = {'id': 'O1', 'status': 6, 'filledQty': 20}
        self.tick(89, ask=89)
        self.client.cancel_order.assert_called_once_with(data={'id': 'O1'})
        self.assertEqual(self.client.place_order.call_count, 1)
        self.rows['O1'] = {'id': 'O1', 'status': 1, 'filledQty': 30}
        self.trader.poll_orders()
        exit_order = self.client.place_order.call_args.args[0]
        self.assertEqual(exit_order['side'], -1)
        self.assertEqual(exit_order['qty'], 30)

    def test_pending_entry_cancels_when_first_target_is_reached(self):
        self.tick(104)
        self.tick(130, ask=130)
        self.client.cancel_order.assert_called_once_with(data={'id': 'O1'})
        self.assertEqual(self.client.place_order.call_count, 1)

    def test_manual_cancel_reconciles_final_entry_fill_and_exits_once(self):
        self.tick(104)
        self.rows['O1'] = {'id': 'O1', 'status': 6, 'filledQty': 20}
        self.trader.poll_orders()
        self.trader.cancel('operator cancellation')
        self.assertEqual(self.client.place_order.call_count, 1)
        self.client.cancel_order.assert_called_once_with(data={'id': 'O1'})
        self.rows['O1'] = {'id': 'O1', 'status': 1, 'filledQty': 30}
        self.rows['O2'] = {'id': 'O2', 'status': 6, 'filledQty': 0}
        self.trader.poll_orders()
        exit_order = self.client.place_order.call_args.args[0]
        self.assertEqual(exit_order['side'], -1)
        self.assertEqual(exit_order['qty'], 30)
        self.trader.poll_orders()
        self.assertEqual(self.client.place_order.call_count, 2)
        self.rows['O2'] = {'id': 'O2', 'status': 2, 'filledQty': 30}
        self.trader.poll_orders()
        self.trader.poll_orders()
        self.assertTrue(self.trader.closed)
        self.assertEqual(self.trader.remaining_quantity, 0)
        self.assertEqual(self.client.place_order.call_count, 2)

    def test_amendment_cannot_leave_an_order_above_the_revised_ceiling(self):
        self.tick(104)
        self.trader.modify_targets((120, 130, 140))  # New cap = 102.
        self.trader.poll_orders()
        self.client.cancel_order.assert_called_once_with(data={'id': 'O1'})

    def test_buy_above_still_requires_an_observed_upward_cross(self):
        trader = BreakoutTrader(make_call(), 'NSE:TEST', 100, 10, self.executor)
        trader.on_price(102)  # First tick already above 101: wait.
        self.client.place_order.assert_not_called()
        self.client.depth.assert_not_called()
        trader.on_price(100)
        trader.on_price(101.5)
        self.assertEqual(self.client.place_order.call_args.args[0]['type'], 2)
        self.client.depth.assert_not_called()

    def test_paper_range_entry_uses_ltp_as_simulated_ask(self):
        trader = BreakoutTrader(self.call, 'MOCK:TEST', 100, 10, FyersOrderExecutor(None))
        trader.on_price(104)
        self.assertTrue(trader.entered)
        self.assertEqual(trader.order_history[0].response['order']['limitPrice'], 104)


if __name__ == '__main__':
    unittest.main()
