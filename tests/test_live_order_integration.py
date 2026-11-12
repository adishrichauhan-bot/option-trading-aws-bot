import sys
import unittest
from argparse import Namespace
from queue import Queue
from unittest.mock import Mock, patch

import live_trader
from trade_lifecycle import TradeCommand
from trading import BrokerStateError
from position_sizing import ContractSpec
from test_live_trader import FakeListenerThread


SIGNAL = "NIFTY 25000 CE\nTRIGGER RANGE - 100 - 101\nTARGET - 102, 103, 104\nSL - 98"


class LiveOrderIntegrationTests(unittest.TestCase):
    def run_scripted_trade(self, *, reentry=False, awaiting_levels=False):
        client = Mock()
        client.funds.return_value = {"s": "ok", "fund_limit": [{"title": "Available Balance", "equityAmount": 10250}]}
        client.place_order.side_effect = lambda order: {"s": "ok", "id": f"O{client.place_order.call_count}"}
        rows = {"O1": {"id": "O1", "status": 6, "filledQty": 0}}
        client.orderbook.side_effect = lambda data: {"s": "ok", "orderBook": [rows[data["id"]]]}
        stream = Mock()
        events = Queue()
        pending = []
        registry = Mock()
        ticks = iter((100, 101.5))
        stages = []

        def next_tick(symbol, timeout):
            try:
                return {"ltp": next(ticks)}
            except StopIteration:
                pass
            if not stages:
                rows["O1"] = {"id": "O1", "status": 2, "filledQty": 100}
                stages.append("entry filled without tick")
                if reentry:
                    events.put(TradeCommand("REENTRY", trigger_range=(100, 101), awaiting_levels=awaiting_levels))
                raise TimeoutError()
            if len(stages) == 1 and not reentry:
                stages.append("stop tick")
                rows["O2"] = {"id": "O2", "status": 6, "filledQty": 0}
                return {"ltp": 97.5}
            # Re-entry must NOT leave run_trade before the prior sell fills.
            self.assertEqual(client.place_order.call_count, 2)
            rows["O2"] = {"id": "O2", "status": 2, "filledQty": 100}
            stages.append("exit filled without tick")
            raise TimeoutError()

        stream.wait_for_update.side_effect = next_tick
        args = Namespace(mock_market=False, quantity=None, lot_size=None, interval=0)
        with (
            patch.dict("os.environ", {"FYERS_ORDER_POLL_SECONDS": "0"}),
            patch.object(live_trader, "is_live_trading_window", return_value=True),
            patch.object(live_trader, "authenticated_credentials", return_value=("fake", "fake")),
            patch.object(live_trader, "authenticated_client", return_value=client),
            patch.object(live_trader, "resolve_option_symbol", return_value="NSE:TEST"),
            patch.object(live_trader, "contract_spec", return_value=ContractSpec("NSE:TEST", 10)),
            patch.object(live_trader, "FyersMarketDataSocket", return_value=stream),
        ):
            closed = live_trader.run_trade(args, SIGNAL, event_queue=events, pending_signals=pending,
                                          trade_id="test", registry=registry)
        self.assertTrue(closed)
        stream.close.assert_called_once()
        self.assertEqual(registry.update.call_args.args[1], "CLOSED")
        self.assertEqual(registry.update.call_args.kwargs["remaining_quantity"], 0)
        self.assertEqual([order["id"] for order in registry.update.call_args.kwargs["orders"]], ["O1", "O2"])
        self.assertEqual(len(pending), int(reentry and not awaiting_levels))

    def test_orders_reconcile_during_market_data_timeouts_and_journal_ids(self):
        self.run_scripted_trade()

    def test_reentry_waits_for_confirmed_exit_before_returning(self):
        self.run_scripted_trade(reentry=True)

    def test_split_reentry_closes_old_position_without_queuing_old_risk_levels(self):
        self.run_scripted_trade(reentry=True, awaiting_levels=True)

    def test_broker_uncertainty_does_not_start_next_queued_trade(self):
        FakeListenerThread.instances.clear()
        run_trade = Mock(side_effect=BrokerStateError("unknown broker exposure"))
        with (
            patch.object(sys, "argv", ["live_trader.py", "--telegram-chat", "-100123"]),
            patch.object(live_trader, "Thread", FakeListenerThread),
            patch.object(live_trader, "TradeRegistry") as registry_type,
            patch.object(live_trader, "run_trade", run_trade),
        ):
            registry_type.return_value.create.return_value = "test-id"
            live_trader.main()
        self.assertEqual(run_trade.call_count, 1)
        self.assertEqual(registry_type.return_value.update.call_args.args[1], "RECONCILIATION_REQUIRED")


if __name__ == "__main__":
    unittest.main()
