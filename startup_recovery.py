"""Conservative startup containment, not automatic position restoration.

Read contract v1: successful profile/order/trade/position envelopes with typed
collections. No remote writes, retries, inferred fills or adoption of exposure.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
from typing import Any


class RecoveryRequired(RuntimeError):
    """Admission refused; messages contain only local allowlisted reason codes."""


def _integer(value: Any) -> int:
    if type(value) is not int:
        raise RecoveryRequired('INVALID_BROKER_QUANTITY')
    return value


def _read(client: Any, method: str) -> dict:
    try:
        response = getattr(client, method)()
    except Exception:
        raise RecoveryRequired('BROKER_READ_FAILED') from None
    if not isinstance(response, dict) or response.get('s') != 'ok':
        raise RecoveryRequired('BROKER_READ_UNSUCCESSFUL')
    return response


def _rows(response: dict, key: str) -> list:
    rows = response.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RecoveryRequired('INVALID_BROKER_COLLECTION')
    return rows


class AccountLock:
    """OS-held single-host lock shared by both CLIs, independent of journal path.

    Never unlink the file: replacing a locked inode would allow another owner.
    Process death releases the OS lock; a stale file is harmless. The default
    home directory must be shared by execution processes on the deployment host.
    This cannot fence other hosts, different OS homes or manual trading.
    """
    def __init__(self, account_id: str, directory: Path | None = None):
        normalized = account_id.strip().upper()
        if not re.fullmatch(r'[A-Z0-9]+', normalized):
            raise RecoveryRequired('INVALID_ACCOUNT_IDENTITY')
        self.account_key = hashlib.sha256(normalized.encode('ascii')).hexdigest()
        self.path = (directory if directory is not None else Path.home()/'.fyers'/'execution-locks') / (self.account_key + '.lock')
        self._file = None

    def __enter__(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open('a+b')
            self._file = handle
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if self._file is not None:
                self._file.close()
                self._file = None
            raise RecoveryRequired('OWNERSHIP_UNAVAILABLE') from None
        return self

    def __exit__(self, *exc):
        if self._file is not None:
            # Closing the descriptor releases either OS lock, including on errors.
            self._file.close()
            self._file = None


@dataclass
class StartupPermit:
    account_key: str
    active: bool = True


@contextmanager
def startup_session(client: Any, registry: Any, *, lock_directory: Path | None = None):
    """Hold ownership through execution; yield only after durable flat decision.

    Read snapshots are sequential observations, not an atomic account snapshot.
    External/manual activity remains outside this first containment unit.
    """
    profile = _read(client, 'get_profile')
    data = profile.get('data')
    account = data.get('fy_id') if isinstance(data, dict) else None
    if not isinstance(account, str):
        raise RecoveryRequired('INVALID_ACCOUNT_IDENTITY')
    with AccountLock(account, lock_directory) as owner:
        observations = {}
        reason = None
        try:
            orders = _rows(_read(client, 'orderbook'), 'orderBook')
            observations['orders_at'] = datetime.now(timezone.utc).isoformat()
            trades = _rows(_read(client, 'tradebook'), 'tradeBook')
            observations['trades_at'] = datetime.now(timezone.utc).isoformat()
            positions_response = _read(client, 'positions')
            positions = _rows(positions_response, 'netPositions')
            observations['positions_at'] = datetime.now(timezone.utc).isoformat()
            pending = 0
            for order in orders:
                status = _integer(order.get('status'))
                qty, filled = _integer(order.get('qty')), _integer(order.get('filledQty'))
                if (not isinstance(order.get('id'), str) or not order['id'] or
                        status not in {1, 2, 4, 5, 6, 7} or qty <= 0 or
                        not 0 <= filled <= qty or (status == 2 and filled != qty)):
                    raise RecoveryRequired('INVALID_BROKER_ORDER')
                pending += status in {4, 6}
            for trade in trades:
                if (not isinstance(trade.get('symbol'), str) or not trade['symbol'] or
                        _integer(trade.get('tradedQty')) <= 0):
                    raise RecoveryRequired('INVALID_BROKER_TRADE')
            open_positions = 0
            for position in positions:
                if not isinstance(position.get('symbol'), str) or not position['symbol']:
                    raise RecoveryRequired('INVALID_BROKER_POSITION')
                open_positions += _integer(position.get('netQty')) != 0
            if 'overall' in positions_response:
                overall = positions_response['overall']
                if (not isinstance(overall, dict) or
                        _integer(overall.get('count_open')) != open_positions or
                        _integer(overall.get('count_total')) != len(positions)):
                    raise RecoveryRequired('INCONSISTENT_POSITION_TOTALS')
            try:
                unresolved = registry.unresolved_trade_ids()
            except Exception:
                raise RecoveryRequired('RECOVERY_STORE_FAILED') from None
            observations.update(order_count=len(orders), trade_count=len(trades),
                                position_count=len(positions), unresolved_count=len(unresolved))
            if pending:
                raise RecoveryRequired('OPEN_ORDERS')
            if open_positions:
                raise RecoveryRequired('OPEN_POSITIONS')
            if unresolved:
                raise RecoveryRequired('UNRESOLVED_JOURNAL')
        except RecoveryRequired as exc:
            reason = str(exc)
        try:
            registry.record_startup(owner.account_key, 'REFUSED' if reason else 'READY',
                                    reason or 'FLAT_CONFIRMED', observations)
        except Exception:
            raise RecoveryRequired('RECOVERY_STORE_FAILED') from None
        if reason:
            raise RecoveryRequired(reason)
        permit = StartupPermit(owner.account_key)
        try:
            yield permit
        finally:
            permit.active = False
