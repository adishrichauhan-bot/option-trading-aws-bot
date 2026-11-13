# Reliability implementation progress

Date: 2026-11-13. Starting commit: `62f190b`. User authorized implementation of the [review plan](reviews/ASTRA_REVIEW_APPROVAL.md). This records the first implementation batch; the whole roadmap is not complete. Results below describe validation before checkout synchronization. Pulling onto EC2 does not activate changes in an already running process.

## Completed in this batch

| Finding | Change | Evidence and limit |
| --- | --- | --- |
| TD11 startup containment | Both [integrated CLI](../live_trader.py) and [standalone CLI](../trading.py) authenticate, identify the account, acquire the same OS lock, and inspect broker orders/trades/positions plus legacy journal state before execution. | [Recovery tests](../tests/test_startup_recovery.py): flat/repeated startup, pending/partial/foreign order, long/short foreign position, failed/malformed reads, legacy uncertainty, persistence failure, both CLI boundaries, lock held during execution and released after exception/process death. Refusal prints NOT READY and exits 2. No automatic restoration. |
| TD09 strict fill quantities | Decoder rejects booleans, floats (including integral-looking floats), fractional strings, null/nonfinite and malformed values before accounting. Exact Python/JSON integers and legacy ASCII digit strings remain supported. Existing bounds and cumulative-delta safeguards remain. | Original fractional-fill expectedFailure removed because the invariant now passes; [additional boundaries](../tests/test_review_corrections.py) pass. Startup read contract is stricter and accepts JSON integers only. |
| TD07 narrow rejection isolation | Modifiers raise CommandRejected for their existing validation rejections; engine catches only that type at the amendment boundary and continues monitoring. | [Live-loop regression](../tests/test_live_order_integration.py) rejects entry/SL/target amendments while exposed, then confirms exactly one stop-exit order and flat completion. BrokerStateError handling remains intact. This does not solve TD06/08 domain-validation policy or isolate all ingestion/storage failures. |
| TD14 resource cleanup | Quote endpoint closes each successfully constructed request adapter on success/error. Cleanup failure does not mask primary result; HTTP failure details are sanitized. | [Cleanup regressions](../tests/test_review_corrections.py) pass. No shared-lifespan redesign or resource-load benchmark claimed. |
| TD16 scoped diagnostic privacy | Manual auth code uses hidden input; absence of a TTY refuses rather than echoing. Auth exchange/redirect/profile/refresh diagnostics no longer forward raw responses; profile is not printed. | Sentinel-response, transport, hidden-input and successful-profile-output regressions pass. This is not certification of every SDK log or a token-storage permissions migration. |
| F0 test isolation | Added [offline suite runner](../tests/run_offline.py). | Disables dotenv discovery, clears app settings, supplies temporary home/token/journal paths, rejects HTTP/real client construction and external socket/DNS activity. Guards record attempts, including those an application exception handler might swallow. Windows asyncio local socketpair and the synthetic child-process lock test remain supported. Guards are Python-process controls, not an OS sandbox. |

## Startup contract and schema checkpoint

[startup_recovery.py](../startup_recovery.py) implements **read contract v1**: successful `s=ok` envelopes, a usable exact profile `data.fy_id`, typed `orderBook`, `tradeBook`, and `netPositions` lists, recognized numeric order status and strict integer quantities. Pending/transit orders or nonzero net positions refuse startup. Optional position totals, when present, must agree with rows. Malformed/failed reads cannot mean flat. Sequential observations are not an atomic broker snapshot or continuous drift detection.

Account identity is normalized and SHA-256 hashed for the lock filename; raw profile/account data and tokens are not persisted. The lock lives under the OS user's home `.fyers/execution-locks`, independent of the configured journal path. Both CLIs retain it for their execution lifetime. The persistent file is not deleted: OS lock ownership, rather than file existence, excludes the second process and releases on death. All processes on the host must use the same OS home. Different hosts/OS users and manual trading are not fenced.

The only schema addition is an append-only table **startup_recovery_v1** with `run_id`, hashed `account_key`, `outcome`, allowlisted `reason`, redacted observation counts/read timestamps and UTC `recorded_at`. It is created and appended within one SQLite transaction before readiness is yielded. A failed audit write blocks startup. Existing `trades` schema/payloads are not migrated or rewritten. Unsupported/incompatible audit schema causes refusal through the storage error boundary. Profile/ownership failures before account lock acquisition do not create a reconciliation record.

Legacy journal traversal is conservative: unresolved statuses block even if broker reads look flat. CLOSED needs integer zero remaining quantity and terminal recorded orders; a sizing skip must have no recorded exposure/orders. Missing evidence is not restored, adopted, flattened, deleted or silently marked resolved. A clean broker account can still be refused because an old row remains unresolved; resolving that state is later engineering/operator work, not an automatic repair here.

This additive schema is a containment audit only. It is **not** the durable signal/intent/execution schema proposed for TD12, does not claim exactly-once submission, and cannot recover submissions that lacked pre-dispatch intent. Rolling back to older code removes startup enforcement even if the extra table is ignored; do not treat code rollback as a recovery decision or discard unresolved evidence.

The broker read fields were checked against official [profile](https://fyers.in/api-docs/v3/api/account/profile/), [positions](https://fyers.in/api-docs/v3/api/trading/positions/get-positions/) and [trade book](https://fyers.in/api-docs/v3/api/trading/trade-book/get-trades/) references. No authenticated broker diagnostics were performed. Strict response validation may expose a deployment SDK/schema mismatch as refusal; EC2 parity is not established.

## Validation manifest

Command from repository root, with its existing virtualenv:

```powershell
.\.venv\Scripts\python.exe -B tests/run_offline.py
```

Latest result: **119 tests: 117 passed, 2 expected failures; zero unexpected failures/errors/successes/skips; zero guard violations; exit 0**. Runtime Python 3.12.0, local FYERS SDK 3.2.1; unittest reported 1.286 seconds. Original baseline was 93 tests / 90 passing / 3 expected failures. All original test cases remain; three CLI orchestration fixtures now stub the separately tested startup boundary while retaining their original behavioral assertions. One new live-loop case checks invalid-command protection continuity. Additional regression modules cover recovery and narrow corrections. No dependencies added.

The initial recovery test failed because the required recovery module did not exist; narrow correction tests reproduced fill coercion, unclosed API adapters and raw/echoed auth diagnostics before their fixes. Focused recovery/correction/live-loop runs and the complete guarded suite passed after changes. Python 3.9/EC2 execution remains untested. No live orders, server restart, production credential/database access or deployment occurred.

## Remaining roadmap

1. TD12/05: concrete durable signal/intent/attempt/observation schema, transaction boundaries, legacy compatibility and crash matrix; integrate all order-producing paths before claiming recovery.
2. TD03/04: reply-descendant identity and terminal cancellation built on preserved signal identity; do not invent unthreaded-command policy.
3. TD01/02/FY08: ordered market consumption, explicit loss/overflow, connection generations and timestamp normalization. The unread-tick and reconnect expected failures remain unresolved. Live stale-backlog admission remains Q07-dependent.
4. TD06/08/10/13/15 and contract/accounting behavior remain subject to the named policy decisions in the review. Current entry/sizing/target/trailing/session policy was not changed in this batch.

Automatic restoration, durable pre-dispatch intents, continuous account reconciliation and protection independent of process/feed survival are not yet implemented. A passing startup gate does not establish these properties.
