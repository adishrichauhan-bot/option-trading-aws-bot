import unittest
import sys
from contextlib import nullcontext
from queue import Queue
from threading import Event
from unittest.mock import Mock, patch

import auth
import live_trader
import trading
from trading_test_clock import use_monday


class TradingCredentialTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict("os.environ", {"client_id": "test-app", "secret_key": "test-secret", "pin": "test-pin"}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_expired_access_refreshes_validates_and_caches_before_use(self):
        with (
            patch.object(auth, "read_tokens", return_value={"access_token": "old", "refresh_token": "refresh"}),
            patch.object(auth, "access_token_works", side_effect=[False, True]) as validate,
            patch.object(auth, "refresh_access_token", return_value={"access_token": "new", "refresh_token": "refresh"}) as refresh,
            patch.object(auth, "write_tokens") as write,
        ):
            self.assertEqual(trading.authenticated_credentials(), ("test-app", "new"))
        refresh.assert_called_once_with("test-app", "test-secret", "refresh", "test-pin")
        self.assertEqual(validate.call_args_list[-1].args, ("test-app", "new"))
        write.assert_called_once_with({"access_token": "new", "refresh_token": "refresh"})

    def test_valid_cache_needs_no_refresh(self):
        with (patch.object(auth, "read_tokens", return_value={"access_token": "valid"}),
              patch.object(auth, "access_token_works", return_value=True),
              patch.object(auth, "refresh_access_token") as refresh):
            self.assertEqual(auth.trading_credentials(), ("test-app", "valid"))
            refresh.assert_not_called()

    def test_stale_environment_override_does_not_hide_new_cache(self):
        with (patch.dict("os.environ", {"FYERS_ACCESS_TOKEN": "stale-override"}),
              patch.object(auth, "read_tokens", return_value={"access_token": "fresh-cache"}),
              patch.object(auth, "access_token_works", side_effect=[False, True]),
              patch.object(auth, "refresh_access_token") as refresh):
            self.assertEqual(auth.trading_credentials(), ("test-app", "fresh-cache"))
            refresh.assert_not_called()

    def test_missing_pin_requires_interactive_login_without_prompting_in_worker(self):
        with (patch.dict("os.environ", {"pin": ""}),
              patch.object(auth, "read_tokens", return_value={"access_token": "expired", "refresh_token": "refresh"}),
              patch.object(auth, "access_token_works", return_value=False),
              patch.object(auth, "refresh_access_token") as refresh):
            with self.assertRaisesRegex(trading.TradingAuthenticationError, "python auth.py"):
                trading.authenticated_credentials()
            refresh.assert_not_called()

    def test_failed_refresh_never_leaks_response_secrets(self):
        with (patch.object(auth, "read_tokens", return_value={"access_token": "old", "refresh_token": "refresh"}),
              patch.object(auth, "access_token_works", return_value=False),
              patch.object(auth, "refresh_access_token", side_effect=RuntimeError("DO_NOT_PRINT_SECRET")),
              patch.object(auth, "write_tokens") as write):
            with self.assertRaises(auth.FyersAuthenticationError) as caught:
                auth.trading_credentials()
            self.assertNotIn("DO_NOT_PRINT_SECRET", str(caught.exception))
            write.assert_not_called()

    def test_invalid_refreshed_token_does_not_replace_cache(self):
        with (patch.object(auth, "read_tokens", return_value={"access_token": "old", "refresh_token": "refresh"}),
              patch.object(auth, "access_token_works", return_value=False),
              patch.object(auth, "refresh_access_token", return_value={"access_token": "bad"}),
              patch.object(auth, "write_tokens") as write):
            with self.assertRaises(auth.FyersAuthenticationError):
                auth.trading_credentials()
            write.assert_not_called()


class ListenerReadinessTests(unittest.TestCase):
    def setUp(self):
        use_monday(self)

    def test_auth_failure_prevents_starting_telegram(self):
        events = Queue()
        with (patch.object(live_trader, "authenticated_credentials", side_effect=trading.TradingAuthenticationError("login required")),
              patch.object(live_trader, "listen_for_trade_calls") as listen):
            live_trader._telegram_worker("chat", "session", events, Event())
        self.assertIsInstance(events.get_nowait(), trading.TradingAuthenticationError)
        listen.assert_not_called()

    def test_mock_listener_does_not_require_fyers(self):
        with (patch.object(live_trader, "authenticated_credentials") as credentials,
              patch.object(live_trader, "listen_for_trade_calls", new=Mock(return_value="coroutine-placeholder")),
              patch.object(live_trader.asyncio, "run") as run):
            live_trader._telegram_worker("chat", "session", Queue(), Event(), mock_market=True)
        credentials.assert_not_called()
        run.assert_called_once_with("coroutine-placeholder")

    def test_midday_auth_failure_marks_call_and_stops_before_next_queued_signal(self):
        from test_live_trader import FakeListenerThread
        FakeListenerThread.instances.clear()
        with (patch.object(sys, "argv", ["live_trader.py", "--telegram-chat", "test-chat"]),
              patch.object(live_trader, "Thread", FakeListenerThread),
              patch.object(live_trader, "authenticated_client", return_value=Mock()),
              patch.object(live_trader, "startup_session", return_value=nullcontext()),
              patch.object(live_trader, "TradeRegistry") as registry,
              patch.object(live_trader, "run_trade", side_effect=trading.TradingAuthenticationError("login required")) as run):
            registry.return_value.create.return_value = "test-trade"
            live_trader.main()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(registry.return_value.update.call_args.args[1], "AUTH_REQUIRED")
