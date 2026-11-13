"""Offline audit checks. Expected failures expose known, unfixed defects."""
import threading
import unittest
from unittest.mock import Mock, patch

from market_data_ws import FyersMarketDataSocket
from trading import BrokerStateError, BreakoutTrader, FyersOrderExecutor, TradeCall


class FyersIntegrationAuditTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'socket.create_connection'):
            guard = patch(target, side_effect=AssertionError('Network forbidden in audit'))
            guard.start()
            self.addCleanup(guard.stop)
        env = patch.dict('os.environ', {'FYERS_ORDER_POLL_SECONDS': '0'})
        env.start()
        self.addCleanup(env.stop)
        self.client = Mock(spec=['place_order', 'orderbook', 'cancel_order'])
        self.client.place_order.return_value = {'s': 'ok', 'id': 'O1'}
        self.executor = FyersOrderExecutor(self.client, live=True)

    def observation(self, status, filled):
        self.client.orderbook.return_value = {'s': 'ok', 'orderBook': [
            {'id': 'O1', 'status': status, 'filledQty': filled}]}

    def trader(self):
        call = TradeCall('NIFTY 25000 CE', 'NIFTY', 25000, 'CE',
                         101, (110, 120, 130), 90)
        return BreakoutTrader(call, 'NSE:FAKE', 100, 10, self.executor)

    @staticmethod
    def stream():
        stream = FyersMarketDataSocket.__new__(FyersMarketDataSocket)
        stream._symbols = set()
        stream._prices = {}
        stream._events = {}
        stream._sequences = {}
        stream._lock = threading.RLock()
        stream._started = True  # No SDK/socket thread can be started.
        stream._connected = False
        stream._error = None
        stream._socket = Mock()
        return stream

    def test_ack_pending_partial_duplicate_and_full(self):
        trader = self.trader()
        self.observation(6, 0)
        trader.on_price(100)
        trader.on_price(102)
        self.assertFalse(trader.entered)
        self.assertEqual(trader.remaining_quantity, 0)
        for fill, expected in ((30, 30), (30, 30), (100, 100)):
            self.observation(2 if fill == 100 else 6, fill)
            trader.poll_orders()
            self.assertEqual(trader.remaining_quantity, expected)
        self.client.place_order.assert_called_once()
        self.client.orderbook.assert_called_with(data={'id': 'O1'})

    def test_unknown_submit_timeout_is_not_retried(self):
        self.client.place_order.side_effect = TimeoutError('synthetic lost response')
        with self.assertRaises(BrokerStateError):
            self.executor.market_order('NSE:FAKE', 100, 1, 'audit')
        self.client.place_order.assert_called_once()
        self.client.cancel_order.assert_not_called()

    def test_missing_order_row_is_uncertain_not_zero_fill(self):
        self.client.orderbook.return_value = {'s': 'ok', 'orderBook': []}
        with self.assertRaises(BrokerStateError):
            self.executor.order_state({'id': 'O1'}, 100)

    def test_unknown_status_is_rejected(self):
        self.observation(999, 0)
        with self.assertRaises(BrokerStateError):
            self.executor.order_state({'id': 'O1'}, 100)

    def test_regressing_cumulative_fill_does_not_reduce_position(self):
        trader = self.trader()
        self.observation(6, 30)
        trader.on_price(100)
        trader.on_price(102)
        trader.poll_orders()
        self.observation(6, 20)
        with self.assertRaises(BrokerStateError):
            trader.poll_orders()
        self.assertEqual(trader.remaining_quantity, 30)

    def test_fy03_fractional_fill_must_be_rejected(self):
        self.observation(6, 1.9)
        with self.assertRaises(BrokerStateError):
            self.executor.order_state({'id': 'O1'}, 100)

    @unittest.expectedFailure
    def test_fy01_unread_tick_must_be_delivered(self):
        stream = self.stream()
        stream.subscribe(['NSE:FAKE'])
        stream._on_message({'symbol': 'NSE:FAKE', 'ltp': 84})
        self.assertEqual(stream.wait_for_update('NSE:FAKE', timeout=0.001)['ltp'], 84)

    @unittest.expectedFailure
    def test_fy02_reconnect_must_allow_fresh_tick(self):
        stream = self.stream()
        stream.subscribe(['NSE:FAKE'])
        stream._on_error('synthetic transient failure')
        stream._on_connect()
        # Deliver a tick after the consumer establishes its baseline.
        stream._events['NSE:FAKE'].wait = lambda timeout: stream._on_message(
            {'symbol': 'NSE:FAKE', 'ltp': 85})
        self.assertEqual(stream.wait_for_update('NSE:FAKE', timeout=0.1)['ltp'], 85)


if __name__ == '__main__':
    unittest.main()
