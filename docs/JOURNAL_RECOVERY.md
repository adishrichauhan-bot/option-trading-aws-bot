# Legacy journal recovery

After reviewing legacy records and confirming no exposure remains, run:

```bash
.venv/bin/python reconcile_journal.py --confirm-flat --note 'Operator confirmed historical trades are resolved and account is flat'
```

The command authenticates, holds the account execution lock, validates broker
orders/trades/positions, and refuses pending orders or nonzero positions. It
submits no orders. Acknowledgement is stored in `journal_reconciliation_v1`
with account, exact snapshot fingerprint, operator note and timestamp. Original
trade snapshots remain unchanged. Changed snapshots and other accounts still
block. Ordinary startup never automatically acknowledges unresolved records.

Then start `live_trader.py` normally. Authentication failures require separate
resolution. Inspect legacy evidence with `python journal_report.py`.

Mock runs use `MOCK_TRADE_REGISTRY_PATH` (default
`~/.fyers/mock_trade_lifecycle.sqlite3`) independently of the live journal.
