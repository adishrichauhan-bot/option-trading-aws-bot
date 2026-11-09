import sys
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

import live_trader
from telegram_trade_listener import TradeCallMessage


def trade_call(instrument: str) -> TradeCallMessage:
    return TradeCallMessage(
        instrument_name=instrument,
        trigger_range=(100.0, 101.0),
        targets=(102.0, 103.0, 104.0),
        stoploss=99.0,
        raw_text=instrument,
    )


class FakeListenerThread:
    instances = []

    def __init__(self, target, args, name, daemon):
        self.target = target
        self.args = args
        self.name = name
        self.daemon = daemon
        self.join_calls = []
        type(self).instances.append(self)

    def start(self) -> None:
        calls, _stop_event = self.args[2], self.args[3]
        calls.put(trade_call("NIFTY 25000 CE"))
        calls.put(trade_call("NIFTY 25100 CE"))
        calls.put(KeyboardInterrupt())

    def join(self, timeout=None) -> None:
        self.join_calls.append(timeout)


class PersistentLiveTraderTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeListenerThread.instances.clear()

    def test_main_processes_next_call_after_trade_error(self) -> None:
        args = ["live_trader.py", "--telegram-chat", "-100123"]
        run_trade = Mock(side_effect=[RuntimeError("order rejected"), None])

        with (
            patch.object(sys, "argv", args),
            patch.object(live_trader, "Thread", FakeListenerThread),
            patch.object(live_trader, "run_trade", run_trade),
        ):
            live_trader.main()

        self.assertEqual(run_trade.call_count, 2)
        self.assertEqual(
            [call.args[1].splitlines()[0] for call in run_trade.call_args_list],
            ["NIFTY 25000 CE", "NIFTY 25100 CE"],
        )
        self.assertEqual(FakeListenerThread.instances[0].join_calls, [3])

    def test_run_trade_mock_market_completes_and_returns(self) -> None:
        args = Namespace(
            mock_market=True,
            mock_prices="100,102,103,104,105",
            quantity=100,
            lot_size=10,
            interval=0,
        )
        signal = "NIFTY 25000 CE\nTRIGGER RANGE - 99 - 101\nTARGET - 102, 103, 104\nSL - 98"

        live_trader.run_trade(args, signal)

    def test_run_trade_mock_market_stoploss_returns(self) -> None:
        args = Namespace(
            mock_market=True,
            mock_prices="100,102,101,97",
            quantity=100,
            lot_size=10,
            interval=0,
        )
        signal = "NIFTY 25000 CE\nTRIGGER RANGE - 99 - 101\nTARGET - 102, 103, 104\nSL - 98"
        output = StringIO()

        with redirect_stdout(output):
            live_trader.run_trade(args, signal)

        self.assertIn("stoploss_98 crossed", output.getvalue())


if __name__ == "__main__":
    unittest.main()
