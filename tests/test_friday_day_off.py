"""Friday policy is measured in IST and never dispatches a forbidden BUY."""
from contextlib import redirect_stdout, nullcontext
import datetime as dt
from io import StringIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import live_trader
import trading
from argparse import Namespace
from trading_policy import TradingDayOff, ensure_trading_day, is_friday


THURSDAY = dt.datetime.fromisoformat('2026-11-12T23:59:59+05:30')
FRIDAY = dt.datetime.fromisoformat('2026-11-13T00:00:00+05:30')


class FridayDayOffTests(unittest.TestCase):
    def test_entire_friday_is_off_using_ist_including_utc_boundary(self):
        for now in (FRIDAY, dt.datetime(2026, 11, 13, 23, 59, 59),
                    dt.datetime.fromisoformat('2026-11-12T18:30:00+00:00')):
            with self.subTest(now=now):
                self.assertTrue(is_friday(now))
                with self.assertRaisesRegex(TradingDayOff, 'Friday.*trade off day'):
                    ensure_trading_day(now)
        for now in (THURSDAY, dt.datetime.fromisoformat('2026-11-13T18:30:00+00:00')):
            self.assertFalse(is_friday(now))
            ensure_trading_day(now)

    def test_live_window_excludes_friday_preserving_monday_thursday_hours(self):
        for day in (9, 10, 11, 12):
            self.assertTrue(trading.is_live_trading_window(dt.datetime(2026, 11, day, 10)))
        self.assertFalse(trading.is_live_trading_window(dt.datetime(2026, 11, 13, 10)))

    def test_integrated_manual_start_warns_before_auth_journal_or_listener(self):
        with (patch('trading_policy.now_ist', return_value=FRIDAY),
              patch.object(sys, 'argv', ['live_trader.py']),
              patch.object(live_trader, 'authenticated_client') as client,
              patch.object(live_trader, 'TradeRegistry') as registry,
              patch.object(live_trader, '_run_engine') as engine,
              redirect_stdout(StringIO()) as output):
            with self.assertRaises(SystemExit) as stopped:
                live_trader.main()
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn('Friday', output.getvalue())
        self.assertIn('trade off day', output.getvalue())
        client.assert_not_called()
        registry.assert_not_called()
        engine.assert_not_called()

    def test_standalone_start_warns_before_signal_file_or_broker_access(self):
        with (patch('trading_policy.now_ist', return_value=FRIDAY),
              patch.object(sys, 'argv', ['trading.py', 'nonexistent-signal.txt']),
              patch.object(trading, 'authenticated_credentials') as credentials,
              redirect_stdout(StringIO()) as output):
            with self.assertRaises(SystemExit) as stopped:
                trading.main()
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn('Friday', output.getvalue())
        credentials.assert_not_called()

    def test_explicit_mock_market_remains_available_on_friday(self):
        with (patch('trading_policy.now_ist', return_value=FRIDAY),
              patch.object(sys, 'argv', ['live_trader.py', '--mock-market']),
              patch.object(live_trader, 'TradeRegistry'),
              patch.object(live_trader, '_run_engine') as engine,
              patch.object(live_trader, 'authenticated_client') as client):
            live_trader.main()
        engine.assert_called_once()
        client.assert_not_called()

    def test_parse_only_remains_available_on_friday(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'signal.txt'
            path.write_text('NIFTY 25000 CE\nBUY ABOVE - 101\nTARGET - 110, 120, 130\nSL - 90')
            with (patch('trading_policy.now_ist', return_value=FRIDAY),
                  patch.object(sys, 'argv', ['trading.py', str(path), '--parse-only']),
                  patch.object(trading, 'authenticated_credentials') as credentials,
                  redirect_stdout(StringIO())):
                trading.main()
            credentials.assert_not_called()

    def test_queued_trade_cannot_silently_become_friday_paper_execution(self):
        with (patch('trading_policy.now_ist', return_value=FRIDAY),
              patch.object(live_trader, 'authenticated_credentials') as credentials,
              patch.object(live_trader, 'is_live_trading_window', return_value=False)):
            with self.assertRaises(TradingDayOff):
                live_trader.run_trade(Namespace(mock_market=False), 'unparsed signal')
        credentials.assert_not_called()

    def test_market_and_limit_buys_recheck_day_at_dispatch_not_construction(self):
        client = Mock()
        client.place_order.return_value = {'s': 'ok', 'id': 'O1'}
        with patch('trading_policy.now_ist', return_value=THURSDAY):
            executor = trading.FyersOrderExecutor(client, live=True)
        with patch('trading_policy.now_ist', return_value=FRIDAY):
            with self.assertRaises(TradingDayOff):
                executor.market_order('NSE:FAKE', 100, 1, 'entry')
            with self.assertRaises(TradingDayOff):
                executor.limit_order('NSE:FAKE', 100, 1, 102, 'entry')
        client.place_order.assert_not_called()

    def test_already_armed_thursday_cross_cannot_buy_on_friday(self):
        client = Mock()
        call = trading.parse_trade_call('NIFTY 25000 CE\nBUY ABOVE - 101\nTARGET - 110, 120, 130\nSL - 90')
        trader = trading.BreakoutTrader(call, 'NSE:FAKE', 100, 10,
                                       trading.FyersOrderExecutor(client, live=True))
        with patch('trading_policy.now_ist', return_value=THURSDAY):
            trader.on_price(100)
        with patch('trading_policy.now_ist', return_value=FRIDAY):
            with self.assertRaises(TradingDayOff):
                trader.on_price(102)
        client.place_order.assert_not_called()
        self.assertFalse(trader.entered)

    def test_midnight_between_order_preflight_and_dispatch_still_blocks(self):
        client = Mock()
        executor = trading.FyersOrderExecutor(client, live=True)
        with patch('trading_policy.now_ist', side_effect=[THURSDAY, FRIDAY]):
            with self.assertRaises(TradingDayOff):
                executor.market_order('NSE:FAKE', 100, 1, 'entry')
        client.place_order.assert_not_called()

    def test_rollover_refusal_stops_queue_without_fabricating_broker_uncertainty(self):
        from test_live_trader import FakeListenerThread
        from trading_policy import FRIDAY_WARNING
        with (patch('trading_policy.now_ist', return_value=THURSDAY),
              patch.object(sys, 'argv', ['live_trader.py', '--telegram-chat', 'fake']),
              patch.object(live_trader, 'Thread', FakeListenerThread),
              patch.object(live_trader, 'TradeRegistry') as registry,
              patch.object(live_trader, 'authenticated_client'),
              patch.object(live_trader, 'startup_session', return_value=nullcontext()),
              patch.object(live_trader, 'run_trade', side_effect=TradingDayOff(FRIDAY_WARNING)) as run,
              redirect_stdout(StringIO()) as output):
            registry.return_value.create.return_value = 'test-trade'
            with self.assertRaises(SystemExit) as stopped:
                live_trader.main()
        self.assertEqual(stopped.exception.code, 2)
        run.assert_called_once()
        self.assertEqual(registry.return_value.update.call_args.args[1], 'SKIPPED_DAY_OFF')
        self.assertNotIn('RECONCILIATION_REQUIRED', output.getvalue())

    def test_protective_sell_and_paper_orders_remain_available(self):
        client = Mock()
        client.place_order.return_value = {'s': 'ok', 'id': 'EXIT1'}
        with patch('trading_policy.now_ist', return_value=FRIDAY):
            live = trading.FyersOrderExecutor(client, live=True)
            live.market_order('NSE:FAKE', 100, -1, 'stoploss')
            live.limit_order('NSE:FAKE', 100, -1, 90, 'stoploss')
            paper = trading.FyersOrderExecutor(None, live=False)
            self.assertEqual(paper.market_order('MOCK:FAKE', 100, 1, 'test')['s'], 'dry_run')
        self.assertEqual(client.place_order.call_count, 2)


if __name__ == '__main__':
    unittest.main()
