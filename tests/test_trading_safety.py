import unittest

from trading import BreakoutTrader, TradeCall


class PendingExitExecutor:
    def __init__(self) -> None:
        self.orders = []

    def market_order(self, symbol, quantity, side, reason):
        self.orders.append((symbol, quantity, side, reason))
        return {"id": f"O{len(self.orders)}"}

    def limit_order(self, symbol, quantity, side, limit_price, reason):
        self.orders.append((symbol, quantity, side, reason))
        return {"id": f"O{len(self.orders)}"}

    def is_order_filled(self, response):
        return False


def make_call() -> TradeCall:
    return TradeCall(
        instrument_name="NIFTY 25000 CE",
        underlying="NSE:NIFTY50-INDEX",
        strike=25000.0,
        option_type="CE",
        buy_above=101.0,
        targets=(102.0, 103.0, 104.0),
        stoploss=98.0,
    )


class TradingSafetyTests(unittest.TestCase):
    def test_updates_are_applied_before_entry(self) -> None:
        executor = PendingExitExecutor()
        trader = BreakoutTrader(make_call(), "NSE:TEST", 100, 10, executor)
        trader.modify_entry(trigger_range=(110.0, 111.0))
        trader.modify_stoploss(109.0)
        trader.modify_targets((112.0, 113.0, 114.0))
        self.assertEqual(trader.trade_call.trigger_range, (110.0, 111.0))
        self.assertEqual(trader.active_stoploss, 109.0)
        self.assertEqual(trader.trade_call.targets, (112.0, 113.0, 114.0))

    def test_pending_stoploss_gets_emergency_market_order_after_two_percent_breach(self) -> None:
        executor = PendingExitExecutor()
        trader = BreakoutTrader(make_call(), "NSE:TEST", 100, 10, executor)
        trader.on_price(100.0)
        trader.on_price(102.0)  # entry
        trader.on_price(97.0)   # normal stop-loss order is acknowledged but pending
        self.assertFalse(trader.closed)
        actions = trader.on_price(95.9)  # below 98 * 0.98
        self.assertTrue(any("emergency market exit" in action.reason for action in actions))
        self.assertEqual(len(executor.orders), 3)  # entry, normal exit, emergency exit
        self.assertFalse(trader.closed)  # fake broker deliberately leaves it pending

    def test_cancel_before_entry_closes_without_order(self) -> None:
        executor = PendingExitExecutor()
        trader = BreakoutTrader(make_call(), "NSE:TEST", 100, 10, executor)
        trader.cancel()
        self.assertTrue(trader.closed)
        self.assertEqual(executor.orders, [])


if __name__ == "__main__":
    unittest.main()
