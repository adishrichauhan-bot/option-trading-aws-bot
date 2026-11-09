import threading
import unittest
from unittest.mock import Mock

from market_data_ws import FyersMarketDataSocket, MockMarketDataSocket


def make_stream() -> FyersMarketDataSocket:
    stream = FyersMarketDataSocket.__new__(FyersMarketDataSocket)
    stream._symbols = set()
    stream._prices = {}
    stream._events = {}
    stream._sequences = {}
    stream._lock = threading.RLock()
    stream._started = False
    stream._connected = False
    stream._error = None
    stream._socket = Mock()
    return stream


class MarketDataSocketTests(unittest.TestCase):
    def test_websocket_error_wakes_waiter_and_is_reported(self) -> None:
        stream = make_stream()
        stream._events["NSE:TEST"] = threading.Event()

        stream._on_error({"code": -99, "message": "Token is expired"})

        self.assertTrue(stream._events["NSE:TEST"].is_set())
        with self.assertRaisesRegex(RuntimeError, "Token is expired"):
            stream.wait_for_update("NSE:TEST", timeout=0.01)

    def test_close_closes_fyers_connection(self) -> None:
        stream = make_stream()

        stream.close()

        stream._socket.close_connection.assert_called_once_with()

    def test_mock_stream_returns_prices_and_supports_close(self) -> None:
        stream = MockMarketDataSocket([101.0])

        self.assertEqual(stream.wait_for_update("MOCK:TEST")["ltp"], 101.0)
        stream.close()


if __name__ == "__main__":
    unittest.main()
