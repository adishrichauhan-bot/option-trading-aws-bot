"""Offline API resource-lifecycle and error-redaction regressions."""
from contextlib import redirect_stdout
from io import StringIO
import os
import unittest
from unittest.mock import Mock, patch

import app


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


if __name__ == "__main__":
    unittest.main()
