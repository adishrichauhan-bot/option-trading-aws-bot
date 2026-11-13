"""Startup containment uses synthetic broker state and isolated journals only."""
import os
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from trade_lifecycle import TradeRegistry
from startup_recovery import AccountLock, RecoveryRequired, startup_session
from trading_test_clock import use_monday


def flat_client():
    client = Mock(spec=['get_profile', 'orderbook', 'tradebook', 'positions'])
    client.get_profile.return_value = {'s': 'ok', 'data': {'fy_id': 'TEST123'}}
    client.orderbook.return_value = {'s': 'ok', 'orderBook': []}
    client.tradebook.return_value = {'s': 'ok', 'tradeBook': []}
    client.positions.return_value = {'s': 'ok', 'netPositions': []}
    return client


class StartupRecoveryTests(unittest.TestCase):
    def setUp(self):
        use_monday(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.registry = TradeRegistry(self.root / 'journal.sqlite3')
        self.client = flat_client()

    def session(self, **kwargs):
        return startup_session(self.client, self.registry, lock_directory=self.root / 'locks', **kwargs)

    def test_clean_flat_boot_is_repeatable_and_lock_held_until_exit(self):
        for _ in range(2):
            with self.session() as permit:
                self.assertTrue(permit.active)
                with self.assertRaises(RecoveryRequired):
                    with AccountLock('TEST123', self.root / 'locks'):
                        self.fail('second owner admitted')
            self.assertFalse(permit.active)
        with self.registry._connection() as db:
            rows = db.execute('SELECT outcome FROM startup_recovery_v1').fetchall()
        self.assertEqual(rows, [('READY',), ('READY',)])

    def test_pending_partial_and_foreign_orders_refuse_startup(self):
        for status, filled in ((4, 0), (6, 0), (6, 20)):
            with self.subTest(status=status, filled=filled):
                self.client.orderbook.return_value['orderBook'] = [
                    {'id': 'FOREIGN', 'status': status, 'qty': 100, 'filledQty': filled}]
                with self.assertRaisesRegex(RecoveryRequired, 'OPEN_ORDERS'):
                    with self.session():
                        self.fail('pending order admitted')

    def test_long_short_and_manual_positions_refuse_without_adoption(self):
        for quantity in (20, -20):
            self.client.positions.return_value['netPositions'] = [{'symbol': 'NSE:FAKE', 'netQty': quantity}]
            with self.assertRaisesRegex(RecoveryRequired, 'OPEN_POSITIONS'):
                with self.session():
                    self.fail('foreign exposure admitted')

    def test_failed_missing_or_malformed_broker_reads_never_mean_flat(self):
        for method, key in (('orderbook', 'orderBook'), ('tradebook', 'tradeBook'), ('positions', 'netPositions')):
            for response in ({'s': 'error', 'message': 'PRIVATE'}, {'s': 'ok'}, {'s': 'ok', key: [None]}):
                with self.subTest(method=method, response=response):
                    client = flat_client()
                    getattr(client, method).return_value = response
                    with self.assertRaises(RecoveryRequired) as caught:
                        with startup_session(client, self.registry, lock_directory=self.root/'locks'):
                            self.fail('malformed response admitted')
                    self.assertNotIn('PRIVATE', str(caught.exception))
        self.client.positions.side_effect = TimeoutError('PRIVATE')
        with self.assertRaisesRegex(RecoveryRequired, 'BROKER_READ_FAILED'):
            with self.session():
                self.fail('unavailable broker admitted')

    def test_malformed_quantities_and_unknown_status_block(self):
        for value in (None, True, 0.1, float('nan'), '0'):
            self.client.positions.return_value['netPositions'] = [{'symbol': 'NSE:FAKE', 'netQty': value}]
            with self.assertRaises(RecoveryRequired):
                with self.session():
                    self.fail('malformed quantity admitted')
        self.client.positions.return_value['netPositions'] = []
        self.client.orderbook.return_value['orderBook'] = [{'id': 'O1', 'status': 999, 'qty': 100, 'filledQty': 0}]
        with self.assertRaises(RecoveryRequired):
            with self.session():
                self.fail('unknown status admitted')

    def test_terminal_orders_and_closed_positions_do_not_block_clean_account(self):
        self.client.orderbook.return_value['orderBook'] = [
            {'id': 'O1', 'status': 2, 'qty': 100, 'filledQty': 100},
            {'id': 'O2', 'status': 1, 'qty': 100, 'filledQty': 20}]
        self.client.positions.return_value['netPositions'] = [{'symbol': 'NSE:FAKE', 'netQty': 0}]
        with self.session():
            pass

    def test_legacy_unresolved_rows_are_preserved_and_block_even_if_broker_flat(self):
        trade_id = self.registry.create({'instrument': 'FAKE'})
        self.registry.update(trade_id, 'RECONCILIATION_REQUIRED', remaining_quantity=20)
        before = self.registry.get(trade_id)
        for _ in range(2):
            with self.assertRaisesRegex(RecoveryRequired, 'UNRESOLVED_JOURNAL'):
                with self.session():
                    self.fail('legacy exposure admitted')
        self.assertEqual(self.registry.get(trade_id), before)

    def test_closed_label_with_exposure_or_nonterminal_order_is_not_proof(self):
        trade_id = self.registry.create({})
        for fields in ({'remaining_quantity': 20, 'orders': []},
                       {'remaining_quantity': 0, 'orders': [{'status': 'PENDING'}]}):
            self.registry.update(trade_id, 'CLOSED', **fields)
            with self.assertRaisesRegex(RecoveryRequired, 'UNRESOLVED_JOURNAL'):
                with self.session():
                    self.fail('false closed admitted')

    def test_record_failure_prevents_readiness_and_releases_lock(self):
        self.registry.record_startup = Mock(side_effect=OSError('PRIVATE'))
        with self.assertRaisesRegex(RecoveryRequired, 'RECOVERY_STORE_FAILED'):
            with self.session():
                self.fail('unrecorded readiness admitted')
        with AccountLock('TEST123', self.root/'locks'):
            pass

    def test_profile_without_exact_account_identity_blocks(self):
        for response in ({'s': 'error'}, {'s': 'ok', 'data': {}}, {'s': 'ok', 'data': {'fy_id': '*MASKED*'}}):
            self.client.get_profile.return_value = response
            with self.assertRaises(RecoveryRequired):
                with self.session():
                    self.fail('unknown identity admitted')

    def test_os_lock_excludes_another_process_and_releases_after_process_death(self):
        script = (
            'from startup_recovery import AccountLock\n'
            'from pathlib import Path\n'
            'import sys,time\n'
            'with AccountLock("TEST123", Path(sys.argv[1])):\n'
            ' print("LOCKED", flush=True)\n'
            ' time.sleep(30)\n'
        )
        child = subprocess.Popen([sys.executable, '-B', '-c', script, str(self.root/'locks')],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 cwd=str(Path(__file__).resolve().parents[1]))
        try:
            self.assertEqual(child.stdout.readline().strip(), 'LOCKED')
            with self.assertRaises(RecoveryRequired):
                with AccountLock('TEST123', self.root/'locks'):
                    self.fail('second process admitted')
        finally:
            child.kill()
            child.communicate(timeout=10)
        with AccountLock('TEST123', self.root/'locks'):
            pass

    def test_integrated_cli_refuses_before_listener_and_execution(self):
        import live_trader
        self.client.positions.return_value['netPositions'] = [{'symbol': 'NSE:FAKE', 'netQty': 20}]
        with (patch.dict(os.environ, {'TRADE_REGISTRY_PATH': str(self.root/'journal.sqlite3')}),
              patch.object(sys, 'argv', ['live_trader.py', '--telegram-chat', 'fake']),
              patch.object(live_trader, 'authenticated_client', return_value=self.client),
              patch('startup_recovery.Path.home', return_value=self.root),
              patch.object(live_trader, 'Thread') as worker,
              patch.object(live_trader, 'run_trade') as run,
              redirect_stdout(StringIO()) as output):
            with self.assertRaises(SystemExit) as stopped:
                live_trader.main()
            self.assertEqual(stopped.exception.code, 2)
        worker.assert_not_called()
        run.assert_not_called()
        self.assertIn('OPEN_POSITIONS', output.getvalue())

    def test_integrated_cli_holds_lock_during_engine_and_releases_on_failure(self):
        import live_trader
        def engine(*args):
            with self.assertRaises(RecoveryRequired):
                with AccountLock('TEST123', self.root/'.fyers'/'execution-locks'):
                    self.fail('owner missing during execution')
            raise RuntimeError('synthetic engine failure')
        with (patch.dict(os.environ, {'TRADE_REGISTRY_PATH': str(self.root/'journal.sqlite3')}),
              patch.object(sys, 'argv', ['live_trader.py']),
              patch.object(live_trader, 'authenticated_client', return_value=self.client),
              patch('startup_recovery.Path.home', return_value=self.root),
              patch.object(live_trader, '_run_engine', side_effect=engine)):
            with self.assertRaisesRegex(RuntimeError, 'synthetic engine'):
                live_trader.main()
        with AccountLock('TEST123', self.root/'.fyers'/'execution-locks'):
            pass

    def test_standalone_cli_refuses_before_resolution_or_orders(self):
        import trading
        signal = self.root/'signal.txt'
        signal.write_text('NIFTY 25000 CE\nBUY ABOVE - 101\nTARGET - 110, 120, 130\nSL - 90')
        self.client.orderbook.return_value['orderBook'] = [{'id': 'O1', 'status': 6, 'qty': 100, 'filledQty': 20}]
        with (patch.dict(os.environ, {'TRADE_REGISTRY_PATH': str(self.root/'journal.sqlite3')}),
              patch.object(sys, 'argv', ['trading.py', str(signal)]),
              patch.object(trading, 'authenticated_credentials', return_value=('fake', 'fake')),
              patch.object(trading, 'authenticated_client', return_value=self.client),
              patch('startup_recovery.Path.home', return_value=self.root),
              patch.object(trading, '_run_standalone') as run,
              redirect_stdout(StringIO()) as output):
            with self.assertRaises(SystemExit) as stopped:
                trading.main()
            self.assertEqual(stopped.exception.code, 2)
        run.assert_not_called()
        self.assertIn('OPEN_ORDERS', output.getvalue())

    def test_standalone_cli_holds_same_account_lock(self):
        import trading
        signal = self.root/'signal.txt'
        signal.write_text('NIFTY 25000 CE\nBUY ABOVE - 101\nTARGET - 110, 120, 130\nSL - 90')
        def run(*args):
            with self.assertRaises(RecoveryRequired):
                with AccountLock('TEST123', self.root/'.fyers'/'execution-locks'):
                    self.fail('standalone missing ownership')
        with (patch.dict(os.environ, {'TRADE_REGISTRY_PATH': str(self.root/'journal.sqlite3')}),
              patch.object(sys, 'argv', ['trading.py', str(signal)]),
              patch.object(trading, 'authenticated_credentials', return_value=('fake', 'fake')),
              patch.object(trading, 'authenticated_client', return_value=self.client),
              patch('startup_recovery.Path.home', return_value=self.root),
              patch.object(trading, '_run_standalone', side_effect=run) as execute):
            trading.main()
        execute.assert_called_once()


if __name__ == '__main__':
    unittest.main()
