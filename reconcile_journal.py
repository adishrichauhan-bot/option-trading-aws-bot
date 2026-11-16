"""One-time operator acknowledgement of legacy snapshots; submits no orders."""
import argparse
import os

from trading import authenticated_client
from startup_recovery import startup_session
from trade_lifecycle import TradeRegistry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-flat', action='store_true', required=True,
                        help='Confirm legacy records have been reviewed and no exposure remains')
    parser.add_argument('--note', required=True)
    args = parser.parse_args()
    if not args.note.strip():
        parser.error('--note must be nonempty')
    registry = TradeRegistry(os.getenv('TRADE_REGISTRY_PATH', '~/.fyers/trade_lifecycle.sqlite3'))
    with startup_session(authenticated_client(), registry, reconciliation_note=args.note):
        print('Legacy journal reconciled; broker flatness confirmed. No orders submitted.')


if __name__ == '__main__':
    main()
