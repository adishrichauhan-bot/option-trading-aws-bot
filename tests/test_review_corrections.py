"""Reproducing regressions for TD09, TD14 and TD16."""
from contextlib import redirect_stdout
from io import StringIO
import os
import getpass
import unittest
import warnings
from unittest.mock import Mock, patch

import app
import auth
from trading import FyersOrderExecutor, BrokerStateError


class StrictFillTests(unittest.TestCase):
    def test_malformed_quantity_is_never_coerced(self):
        client = Mock()
        executor = FyersOrderExecutor(client, live=True)
        for value in (True, False, 1.9, 1.0, None, float('nan'), float('inf'), '1.9', '-1', {}, []):
            client.orderbook.return_value = {'s': 'ok', 'orderBook': [{'id': 'O1', 'status': 6, 'filledQty': value}]}
            with self.subTest(value=value), self.assertRaises(BrokerStateError):
                executor.order_state({'id': 'O1'}, 100, force=True)

    def test_exact_integer_and_compatibility_digit_string_are_supported(self):
        client = Mock()
        executor = FyersOrderExecutor(client, live=True)
        for value in (0, 30, '30'):
            client.orderbook.return_value = {'s': 'ok', 'orderBook': [{'id': 'O1', 'status': 6, 'filledQty': value}]}
            self.assertEqual(executor.order_state({'id': 'O1'}, 100, force=True).filled_quantity, int(value))


class ApiCleanupTests(unittest.TestCase):
    def invoke(self, adapter):
        with (patch.dict(os.environ, {'FYERS_CLIENT_ID': 'fake', 'FYERS_ACCESS_TOKEN': 'fake'}),
              patch.object(app, 'FyersMarketData', return_value=adapter)):
            return app.market_data('NIFTY', 2)

    def test_success_closes_request_stream(self):
        adapter = Mock()
        adapter.fetch.return_value = {'indices': {}}
        self.assertEqual(self.invoke(adapter), {'indices': {}})
        adapter.close.assert_called_once()

    def test_failure_closes_stream_without_exposing_exception(self):
        adapter = Mock()
        adapter.fetch.side_effect = RuntimeError('PRIVATE_ACCESS_TOKEN')
        with self.assertRaises(app.HTTPException) as caught:
            self.invoke(adapter)
        adapter.close.assert_called_once()
        self.assertEqual(caught.exception.status_code, 502)
        self.assertNotIn('PRIVATE_ACCESS_TOKEN', caught.exception.detail)

    def test_cleanup_failure_does_not_replace_success_or_primary_failure(self):
        adapter = Mock()
        adapter.close.side_effect = RuntimeError('PRIVATE_CLOSE_TOKEN')
        adapter.fetch.return_value = {'indices': {}}
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(self.invoke(adapter), {'indices': {}})
        self.assertNotIn('PRIVATE_CLOSE_TOKEN', output.getvalue())
        adapter.fetch.side_effect = RuntimeError('PRIVATE_PRIMARY')
        with self.assertRaises(app.HTTPException) as caught:
            self.invoke(adapter)
        self.assertEqual(caught.exception.status_code, 502)


class AuthRedactionTests(unittest.TestCase):
    def test_hidden_input_refuses_echo_fallback_without_a_tty(self):
        def unavailable(prompt):
            warnings.warn('Cannot disable echo', getpass.GetPassWarning)
            self.fail('must not continue to echo fallback')
        with patch('getpass.getpass', side_effect=unavailable), redirect_stdout(StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'interactive terminal'):
                auth.manual_auth_code('https://example.invalid/login', 'state')

    def test_manual_code_uses_non_echo_input(self):
        with (patch('getpass.getpass', return_value='PRIVATE_CODE') as hidden,
              patch('builtins.input', side_effect=AssertionError('echo input forbidden')),
              redirect_stdout(StringIO()) as output):
            self.assertEqual(auth.manual_auth_code('https://example.invalid/login', 'state'), 'PRIVATE_CODE')
        hidden.assert_called_once()
        self.assertNotIn('PRIVATE_CODE', output.getvalue())

    def test_redirect_error_does_not_forward_external_description(self):
        with self.assertRaises(RuntimeError) as caught:
            auth.extract_auth_code('https://example.invalid/?error=bad&description=PRIVATE_CODE', 'state')
        self.assertNotIn('PRIVATE_CODE', str(caught.exception))

    def test_exchange_failure_does_not_forward_response_or_transport(self):
        session = Mock()
        for result in ({'s': 'error', 'message': 'PRIVATE_CODE'}, None):
            session.generate_token.return_value = result
            with self.assertRaises(RuntimeError) as caught:
                auth.exchange_auth_code(session, 'fake')
            self.assertNotIn('PRIVATE_CODE', str(caught.exception))
        session.generate_token.side_effect = RuntimeError('PRIVATE_CODE')
        with self.assertRaises(RuntimeError) as caught:
            auth.exchange_auth_code(session, 'fake')
        self.assertNotIn('PRIVATE_CODE', str(caught.exception))

    def test_interactive_main_never_prints_profile(self):
        client = Mock()
        client.get_profile.return_value = {'s': 'ok', 'data': {'email': 'PRIVATE_PROFILE'}}
        with (patch.dict(os.environ, {'redirect_uri':'fake', 'client_id':'fake', 'secret_key':'fake',
                                     'grant_type':'fake', 'response_type':'fake', 'state':'fake'}, clear=True),
              patch.object(auth, 'read_tokens', return_value={'access_token':'fake'}),
              patch.object(auth, 'access_token_works', return_value=True),
              patch.object(auth, 'build_client', return_value=client),
              patch.object(auth.fyersModel, 'SessionModel'),
              redirect_stdout(StringIO()) as output):
            self.assertIs(auth.main(), client)
        self.assertNotIn('PRIVATE_PROFILE', output.getvalue())


if __name__ == '__main__':
    unittest.main()
