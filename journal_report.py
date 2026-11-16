"""Inspect legacy journal evidence without creating or changing a database."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3


def report(path):
    path = Path(path).expanduser().resolve()
    # mode=ro also prevents a typo from silently creating an empty journal.
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        rows = db.execute(
            'SELECT trade_id, status, payload, updated_at FROM trades ORDER BY trade_id'
        ).fetchall()
    result = []
    for trade_id, status, raw, updated_at in rows:
        payload = json.loads(raw)
        result.append({
            'trade_id': trade_id, 'status': status, 'updated_at': updated_at,
            'remaining_quantity': payload.get('remaining_quantity'),
            'orders': [
                {key: order.get(key) for key in ('id', 'status', 'quantity', 'filled_quantity')}
                for order in payload.get('orders', [])
            ],
        })
    return {'journal': str(path), 'trades': result}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal', default=os.getenv(
        'TRADE_REGISTRY_PATH', '~/.fyers/trade_lifecycle.sqlite3'))
    args = parser.parse_args()
    print(json.dumps(report(args.journal), indent=2))
