"""Run unittest with temporary state and guards against production access.

Usage from the repository root: .venv/Scripts/python.exe -B tests/run_offline.py
These Python-process guards are not an operating-system network sandbox.
"""
from contextlib import ExitStack
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    production_home = str(Path.home()).replace('\\', '/').lower()
    sys.path.insert(0, str(root))
    violations = []
    internal_pair = False
    original_pair = socket.socketpair

    def socket_pair(*args, **kwargs):
        nonlocal internal_pair
        internal_pair = True
        try:
            # Windows asyncio creates a local socket pair internally.
            return original_pair(*args, **kwargs)
        finally:
            internal_pair = False

    def audit(event, args):
        if event in ('socket.connect', 'socket.getaddrinfo') and not internal_pair:
            violations.append(event)
            raise AssertionError('External network forbidden in offline suite')
        if event == 'open' and isinstance(args[0], (str, bytes)):
            path = os.fsdecode(args[0]).replace('\\', '/').lower()
            if (path.endswith('/.env') or path.startswith(production_home+'/.fyers/') or
                    path.startswith(production_home+'/.telegram/')):
                violations.append('protected-file')
                raise AssertionError('Production configuration/state forbidden')

    def forbidden(label):
        def reject(*args, **kwargs):
            violations.append(label)
            raise AssertionError(label + ' forbidden in offline suite')
        return reject

    sys.addaudithook(audit)
    with tempfile.TemporaryDirectory(prefix='offline-trading-tests-') as tmp, ExitStack() as stack:
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() in {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'COMSPEC'}}
        environment.update(HOME=tmp, USERPROFILE=tmp, PYTHON_DOTENV_DISABLED='1',
                           TRADE_REGISTRY_PATH=str(Path(tmp)/'journal.sqlite3'),
                           token_file=str(Path(tmp)/'synthetic-token.json'))
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        stack.enter_context(patch('socket.socketpair', socket_pair))
        stack.enter_context(patch('dotenv.load_dotenv', return_value=False))
        stack.enter_context(patch('requests.sessions.Session.request', side_effect=forbidden('HTTP')))
        stack.enter_context(patch('fyers_apiv3.fyersModel.FyersModel', side_effect=forbidden('real FYERS client')))
        suite = unittest.defaultTestLoader.discover(str(root/'tests'))
        result = unittest.TextTestRunner(verbosity=1, buffer=True).run(suite)
        print('OFFLINE VALIDATION:', {
            'python': sys.version.split()[0], 'tests': result.testsRun,
            'failures': len(result.failures), 'errors': len(result.errors),
            'expected_failures': len(result.expectedFailures),
            'unexpected_successes': len(result.unexpectedSuccesses),
            'skipped': len(result.skipped), 'guard_violations': violations,
        })
        return 0 if result.wasSuccessful() and not violations else 1


if __name__ == '__main__':
    raise SystemExit(main())
