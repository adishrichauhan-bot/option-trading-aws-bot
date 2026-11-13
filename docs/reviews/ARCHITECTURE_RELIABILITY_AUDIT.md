# Independent architecture and reliability audit

Date: 2026-11-12. Baseline: `a4aac3a` on `main`.
Scope: technical correctness, recoverability, execution safety, operability and QA; not strategy profitability or regulatory certification.

## Executive assessment

The application is a functioning prototype with useful broker-fill safeguards, not a demonstrated unattended trading system. The principal weaknesses are not just message parsing: identity is lost between ingestion and execution, market updates are discarded, and recovery and protection depend on one process remaining healthy. Passing the existing suite does not establish safety across failures.

Do not infer that every supplied Telegram call was missed. Earlier incident investigations recorded an executed and closed 60-unit 11:55 trade, two ceiling-sizing skips, and an above-trigger initial tick for the 14:25 trade. These are historical findings from the conversation and repository notes, not freshly revalidated broker evidence in this audit. New defects below are independently supported by source and, where stated, offline reproduction; they are not retroactively asserted as the cause of each incident.

No application code, existing tests, configuration, broker state or server process was changed during this audit. Only this report was added. There was no live order replay or deployment.

## 1. Evidence inventory and limitations

| Requested artifact | Available evidence | Limitation |
| --- | --- | --- |
| Requirements/business rules | Supplied conversation, [HANDOFF](../history/HANDOFF.md), [RUNNING](../OPERATIONS.md#strategy-and-order-behavior), [preparation](../OPERATIONS.md#installation-and-configuration), [runbook](../OPERATIONS.md#daily-operation) | No approved, versioned acceptance specification; documents contradict themselves. Complete older conversation bodies are not available. |
| Architecture/detailed design | Eight application modules and narrative documentation | No authoritative component contracts, failure model, transition specification or architecture decision records. Architecture below is reconstructed from code. |
| Source/configuration | All eight top-level Python modules, 13 test modules, [dependencies](../../requirements.txt), [environment template](../../.env.example), [ignore rules](../../.gitignore), Git history | Secret configuration deliberately not dumped. No reproducible lockfile or CI configuration found in the tracked project inventory. |
| API specifications | Official Fyers order lookup, status/fill, funds and symbol-master documentation checked during audit | No exhaustive SDK/network conformance test, exchange capability certification or measured rate-limit behavior. |
| Database/state | [TradeRegistry](../../trade_lifecycle.py), lifecycle snapshots and tests | No production database copied or newly queried here; no migration, event ledger or recovery specification found. |
| Sample I/O | User's Nov-11/12 messages, replay fixtures, mocked order responses, mock price sequences | Synthetic prices are not historical exchange ticks; pasted messages omit some original IDs/reply relationships and delivery timing. |
| Logs/errors | Prior investigation results in conversation and documentation | No fresh EC2 log collection in this audit; no complete timestamp-aligned raw Telegram/tick/order trace to establish all historical causes. |
| Tests/results | Existing suite executed locally: **85 tests, all passing**, 0.382 seconds reported by unittest | Not proof of EC2 runtime parity today, live fills, concurrency safety, latency, fault tolerance, coverage percentage or profitability. |
| Independent review | Separate read-only reviews of broker/market data and ingestion/persistence; main review of authentication/API/operations | Reviews share the available repository; missing production evidence remains missing. |

The Python fact-grounded coding skill guided source-based verification and offline probes. No repository AGENTS.md or SKILL.md was found by the project search. Other installed skills are unrelated to this audit. Python environment helper requests did not promptly return; tests used the existing project virtualenv directly, without changing dependencies. No editor-interpreter claim is made.

### Official integration references

- [Order lookup](https://fyers.in/api-docs/v3/api/trading/order-book/get-order-by-order-id/): query the Fyers order ID, not the exchange order ID.
- [Status and cumulative fills](https://fyers.in/api-docs/v3/api/trading/webhooks/webhook-response/): statuses include cancelled, filled, transit, rejected, pending and expired; cumulative filled quantity carries partial fills.
- [Funds](https://fyers.in/api-docs/v3/api/account/funds/): identify Available Balance by title; the margin calculator is a separate capability.
- [Symbol master](https://fyers.in/api-docs/v3/api/market-information/symbol-master/symbol-master-json/): contract metadata includes activity and instrument details; it should supply constraints beyond lot size.

## 2. Reconstructed architecture and stack

The principal flow is Telegram user-session ingestion -> mutable multi-message assembler -> in-memory queue -> single active trade loop -> contract resolution/funds sizing -> WebSocket price observations -> strategy state machine -> synchronous Fyers requests -> cumulative-fill polling -> SQLite snapshot.

| Module | Current responsibility |
| --- | --- |
| [telegram_trade_listener.py](../../telegram_trade_listener.py) | Telethon events and two-second polling, text normalization, assembly, command emission, process-local deduplication |
| [live_trader.py](../../live_trader.py) | Thread orchestration, active-trade selection, command routing, sizing setup, live/paper selection, journaling and failure handling |
| [trading.py](../../trading.py) | Second parser, contract resolution, ATR, strategy, order adapter, broker-state reconciliation within one run, standalone CLI |
| [position_sizing.py](../../position_sizing.py) | Exact-symbol lot lookup, Decimal cash sizing, reserve and quantity cap |
| [market_data_ws.py](../../market_data_ws.py) | Fyers socket callbacks, latest-message cache, update waiters, deterministic mock |
| [trade_lifecycle.py](../../trade_lifecycle.py) | Command parser, trade IDs, one SQLite table storing mutable JSON payloads |
| [auth.py](../../auth.py) | Interactive authorization, cached credentials, profile validation and optional refresh |
| [app.py](../../app.py) | Separate FastAPI index/ATM market-data API; not the engine's readiness or control plane |
| [tests](../../tests) | Parser fixtures, mocked broker transitions, strategy and integration regressions |

Stack: Python, threads/queues, asyncio, SQLite, Fyers SDK, Telethon, requests, python-dotenv, Playwright for login, FastAPI/Uvicorn for the separate API. Dependencies use broad version ranges. Historical deployment evidence records Windows/Python 3.12 locally and EC2/Python 3.9 under tmux; this audit does not certify a current compatibility matrix.

## 3. Requirements and implementation alignment

| Rule | Implemented state | Unresolved design issue |
| --- | --- | --- |
| Split instrument/entry/targets/SL messages | Implemented with regression fixtures | One mutable assembly context; correlation, cancellation and edits incomplete |
| Buying range | Upper boundary treated as an upward-breakout threshold | Not equivalent to buying anywhere inside the range; owner must approve semantics |
| Above-trigger first price | Wait for a new observed crossing | Intentional no-chase behavior, not itself a defect; late subscription and discarded ticks can prevent observation |
| Lot size | Exact-symbol `minLotSize` lookup | Tick size, freeze limits and expiry selection need stronger contract validation |
| Quantity | Floor to affordable whole lots; reserve/cap; explicit ceil supported | Earlier ceiling request conflicts with affordability; latest fix deliberately selects floor. Fees/slippage/account-wide risk remain outside the formula |
| T1/T2 | Approximately 50%/30%, whole-lot rounding | Small sizes can allocate zero to either target; must be explicit acceptance behavior |
| T3 | Runner retained; T3 floor, percentage/ATR ratchet | No empirical basis supplied for calling defaults optimally safe |
| Stop breach | Local limit sell; 2% active-stop breach triggers cancel/reconcile/market logic | Requires functioning process and feed; not a broker-held protective stop |
| Order acknowledgement | Not treated as fill; cumulative broker fills used | No durable pre-submit intent or startup recovery |
| Multiple signals | One active trade, subsequent calls queued | No approved TTL, supersession, capacity or delayed-entry policy |
| Full-exit channel instruction | Parsed as EXIT, including BOOK FULL | Can override T3 runner; priority between channel commands and autonomous strategy needs an explicit rule |

## 4. Findings, ordered by consequence

Severity denotes plausible consequence, not a measured incident frequency. Critical means possible unmanaged exposure or duplicate execution after a failure; High means wrong/missed action or loss of essential traceability; Medium means operational/security/reproducibility weakness. Static findings identify supported failure paths, not claims that they occurred live.

### F01 — Critical: no startup reconciliation or exclusive execution ownership

Evidence: [main](../../live_trader.py:357) initializes a registry and starts new ingestion; [registry](../../trade_lifecycle.py:125) exposes create/update/get but no recovery traversal. Startup does not reconcile existing journal states against broker orders, fills and positions. No account-scoped single-writer lease or process lock exists in this execution path.

Consequence: restart loses strategy/position state; a second process can submit independently. A journal row is not proof that exposure is protected. Manual orders in the same account make attribution harder.

Required change: boot in RECOVERING, acquire exclusive account ownership, reconcile broker and durable intent state, and admit new orders only after reconciliation. Unknown exposure must block entries and surface an actionable incident, not silently be adopted or flattened.

### F02 — Critical: protection disappears with the process or feed

Evidence: [local stop submission](../../trading.py:536) occurs only after observed prices; [timeout handling](../../live_trader.py:313) loops on TimeoutError without a stale-feed deadline; other exceptions with exposure become BrokerStateError and end the engine. KeyboardInterrupt explicitly leaves broker positions open. [socket close callback](../../market_data_ws.py:68) changes a flag but does not establish a protection fallback.

Consequence: process death, disconnected feed, rejected exits, journal failure or an unrelated listener error can leave a position with no active local stop. Failing closed for NEW entries is not equivalent to protecting EXISTING exposure. The 2% rule is an escalation trigger, not a maximum-loss guarantee.

Required change: separate entry permission from exposure-management health. Evaluate broker-held protective orders against verified segment/product capabilities, keep reconciliation/protection alive during ingestion faults, and use independent alerts/watchdog. Do not add blind market retries or duplicate sell orders.

### F03 — High: unread ticks are discarded by the consumer API

Evidence: [wait_for_update](../../market_data_ws.py:122) snapshots the current producer sequence on every call and waits for something newer. [_on_message](../../market_data_ws.py:49) overwrites one latest-value slot. Ticks arriving during broker I/O, SQLite writes, printing or the loop sleep are not reliably consumed.

Offline reproduction: inject prices 79 and 84 before the next wait; the next wait times out despite two never-consumed messages. This was executed against the real wrapper with a mocked socket.

Consequence: a brief trigger crossing, stop breach or high-watermark observation can be missed. MockMarketDataSocket yields every price and therefore conceals this difference.

Required change: explicit consumer cursor and bounded ordered event queue, documented overflow behavior, exchange/receive timestamps and freshness checks. Latest-value coalescing may be suitable for display, not unqualified crossing detection. Test ticks received before and during strategy work.

### F04 — High: unrelated commands apply to the active trade

Evidence: [_command_applies](../../live_trader.py:140) returns true for any command without an explicit internal trade ID, including replies outside the active trade's source IDs.

Offline reproduction: EXIT replying to message 999 applies to a trade sourced from messages 1, 2, 3. Result: True.

Consequence: an old setup's EXIT/MODIFY SL or a queued setup's command can close or alter the current position. Single-active execution does not imply single-active conversation context.

Required change: persistent chat/message/reply-root-to-signal mapping, including modification descendants; reject/quarantine ambiguous commands. Instrument alone is insufficient when multiple setups use the same contract.

### F05 — High: cancellation does not invalidate pending assembly

Evidence: [_process_message](../../telegram_trade_listener.py:482) emits CANCEL and returns without clearing or invalidating the pending setup; the idle engine ignores control messages without an active trade.

Offline reproduction: instrument -> range -> CANCEL -> targets/SL emits both a cancellation command and a completed TradeCallMessage. The cancelled setup can become tradable later.

Required change: persist terminal cancellation at the signal identity level, including incomplete and queued setups; later chunks must not resurrect it. Do not indiscriminately clear unrelated assemblies.

### F06 — High: queued calls lose identity and bypass journaling

Evidence: [queue handling](../../live_trader.py:275) converts subsequent TradeCallMessage objects to strings; [pending selection](../../live_trader.py:413) labels all of them `reentry` with empty source IDs; registry is deliberately omitted for `manual` and `reentry`.

Consequence: ordinary queued signals become unjournaled trades, losing source dates, reply links and stable IDs. No TTL prevents a delayed call executing after its original context has expired. Pending work disappears on restart.

Required change: persist typed signal records before queueing; carry immutable IDs, revisions, timestamps and validity windows throughout execution. Every order-producing path must have the same durable lifecycle.

### F07 — High: invalid control messages can terminate protection

Evidence: [modify_entry/stoploss/targets](../../trading.py:665) raise on invalid changes. [run_trade](../../live_trader.py:283) applies them without per-command rejection isolation; its outer exception handler halts monitoring when exposure exists.

Consequence: a stale MODIFY ENTRY after entry, or invalid stop modification, can stop the whole engine while leaving a position open. A rejected command should not be treated as loss of broker-state integrity.

Required change: pure command validation and explicit CommandRejected events; continue exposure management with the last valid policy. Separate validation, ingestion, transport and reconciliation failure classes.

### F08 — High: durable state is written after external side effects

Evidence: [_place_order](../../trading.py:208) submits before a durable order-intent write; [journal](../../live_trader.py:236) later stores snapshots. [TradeRegistry.update](../../trade_lifecycle.py:164) overwrites JSON rather than appending transitions.

Consequence: a crash between broker acceptance and journal persistence leaves an unattributed order. Snapshots do not provide complete reconstruction of amendments, fill chronology or decisions. Unknown submission is correctly halted in-process, but cannot be resolved automatically after restart.

Required change: transactional durable intent/outbox before submission; stable client correlation where broker-supported; explicit UNKNOWN outcome and recovery lookup. Never promise exactly-once network submission: aim for durable intent, deduplicated handling and reconciliation.

### F09 — High: live/paper authority is selected once by wall clock

Evidence: [run_trade mode selection](../../live_trader.py:197) evaluates [weekday window](../../trading.py:340) once when a trade starts and retains executor.live. No exchange holiday/session service or per-entry cutoff exists in this path.

Consequence: a pre-open setup can remain paper after opening; a setup armed inside the window can submit later outside it. Time alone is also surprising authorization for manual CLI usage.

Required change: explicit execution mode, exchange-aware admission checked at submission, and separate policies for closing existing exposure and opening new exposure. Define overnight/expiry behavior rather than infer it.

### F10 — High: recovery ingestion and revisions are incomplete

Evidence: [_listen](../../telegram_trade_listener.py:559) starts at the newest ID; poll fallback fetches only the last 20 messages; seen IDs are memory-only; only NewMessage is registered. One global assembler is shared across event and polling deliveries.

Consequence: startup gaps are intentionally skipped; more than 20 missed messages can exceed fallback recovery; edits are ignored; interleaved setups are not correlated independently. Polling/event delivery order can affect assembly even though retained chunks are sorted.

Required change: durable inbox/cursor, paginated catch-up, revisions, reply-aware assemblies, bounded buffering and expiry. Catch up for audit/control recovery without automatically trading stale historical entries.

### F11 — High: contract constraints and risk budget are incomplete

Evidence: [ContractSpec](../../position_sizing.py:38) carries only symbol/lot; `contract_spec` treats missing tradeStatus as active. [symbol resolver](../../trading.py:950) takes the first matching chain strike/type, otherwise nearest nonexpired fallback. Tick size is an environment default, not per-contract metadata. Funds sizing consumes available cash less reserve (default zero), capped by units, not by stop risk or contract freeze limits.

Consequence: ambiguity about expiry, incorrect tick increments, oversized broker requests or insufficient cost/slippage buffer can reject orders. Cash affordability is not a portfolio risk limit.

Required change: versioned complete InstrumentSpec, explicit expiry policy, price/quantity constraints, account capital reservations and approved monetary risk limits. Validate any slicing through the order manager, not independent untracked orders. No specific investment risk percentage is prescribed by this audit.

### F12 — Medium: market-data API leaks socket lifecycle and misstates health

Evidence: [market_data endpoint](../../app.py:192) creates FyersMarketData per request; its fetch path subscribes sockets without closing them. [health](../../app.py:186) always returns ok and knows nothing about the trading worker.

Consequence: repeated requests can accumulate socket resources; a green API health response does not prove authenticated ingestion, fresh quotes or managed exposure. Error details also forward arbitrary exception text to clients.

Required change: application-lifespan-managed shared market-data adapter, bounded subscriptions, cleanup, redacted errors, and separate liveness/readiness/protection indicators. Actual resource growth was not load-tested here.

### F13 — Medium: operational and secret-handling controls are incomplete

Evidence: [auth.main](../../auth.py:237) prints raw profile and some raw error responses; manual auth input echoes pasted redirect credentials. Token temporary file permissions are restricted only after replacement. [ignore rules](../../.gitignore) do not generally exclude session files, PEM files, token JSON or SQLite journals. No committed service supervision/alerting/CI definitions were found. Dependencies are ranges without a lock. Documentation retains obsolete real-quantity flags, T3-exit instructions and conflicting sizing defaults.

Consequence: accidental credential/account-data exposure, inconsistent installs, unobserved outages and operator mistakes. This is not a claim that secrets are currently committed or infrastructure controls absent outside the repository.

Required change: redacted structured logging, non-echo credential entry, restrictive creation permissions, secret scanning/ignore rules, tested service configuration, dependency pinning and one current runbook with archived historical notes.

## 5. What should be retained

- Broker acknowledgement is distinct from fill; cumulative fill deltas are applied once and regressions rejected.
- Cancellation waits for terminal state before replacement sells; cancel/fill races have tests.
- Unknown submissions and rejected exits stop admission instead of blindly retrying.
- Exact-symbol lot metadata and Decimal whole-lot sizing improve consistency.
- Pure parser fixtures cover real split-call patterns; T3 trailing and partial-fill behavior have useful regression coverage.
- Mock mode disables real submission. Preserve this guarantee while replacing weak orchestration.

These are reusable building blocks, not evidence that recovery and orchestration are complete.

## 6. Proposed target architecture

Prefer a modular monolith with a durable local store and one execution owner initially. Microservices would add distributed failure modes without solving the current ownership problem.

1. **Ingestion adapter:** persist raw Telegram envelope, source IDs, reply parent, revision and source/receive timestamps before acknowledging local processing. A versioned parser emits normalized events, never orders.
2. **Signal coordinator:** correlate reply graphs, maintain independently keyed assemblies, apply amendments/cancellations, reject ambiguity, enforce validity windows. Store all queued setups with identity intact.
3. **Instrument/risk admission:** resolve an explicit contract and metadata version; validate session, freshness, funds reservation, quantity/price constraints and approved risk policy.
4. **Deterministic strategy reducer:** consumes ordered signal/price/fill events and returns proposed intents. No network calls inside the reducer. Inject a clock; consolidate duplicate parser/strategy branches.
5. **Durable order manager:** commit intents, dispatch to a broker adapter, track acknowledgements/fills/cancellations, reconcile unknown outcomes and account reservations. Normalize transport/schema errors centrally.
6. **Exposure/protection supervisor:** remains operational independently of Telegram. Tracks broker-confirmed positions and protection coverage, detects stale data and escalates unresolved exposure. Broker-held protection is an integration capability to validate, not assume.
7. **Recovery coordinator:** acquires exclusive ownership, loads nonterminal state, reconciles broker orders/trades/positions, restores protection or quarantines discrepancies before entries become READY.
8. **Operational plane:** structured redacted events, metrics, alerts, health endpoints, controlled deploy/drain procedure and account-scoped stop-new-entries control. Emergency flattening is a separate explicit operation.

### Suggested durable schema

| Entity | Essential fields/invariants |
| --- | --- |
| inbox_messages | chat_id, message_id, revision, reply_to, source/received time, raw text/hash; unique source revision |
| signals | signal_id, root message, revision, instrument, levels, validity, lifecycle, reason; immutable provenance |
| instrument_snapshots | symbol, expiry, lot/tick/freeze constraints, source timestamp/version |
| order_intents | intent_id, signal_id, side, quantity, type, price, reason, creation time, dispatch state; persisted before I/O |
| broker_orders/fills | broker ID, intent ID, cumulative quantity/status, execution IDs/prices/times; idempotent fill processing |
| position_allocations | account/product/symbol, bot allocation, confirmed buys/sells, protection state; manual activity distinguished |
| lifecycle_events | append-only sequence, aggregate version, event type, before/after references, correlation IDs |
| checkpoints/leases | ingestion cursor, account owner/fencing generation, reconciliation result |

SQLite with disciplined transactions, migrations, backup tests and single-writer ownership can be sufficient initially. PostgreSQL is an option when deployment/availability requirements justify it, not a prerequisite for fixing identity.

### Explicit state models

- Signal: ASSEMBLING -> VALIDATED -> ARMED -> ENTRY_PENDING -> OPEN -> EXIT_PENDING -> CLOSED; alternative terminal states CANCELLED, EXPIRED, REJECTED. Preserve independent order states for partial exposure.
- Order: INTENT_RECORDED -> SUBMITTING -> ACKNOWLEDGED -> PARTIAL/FILLED; CANCEL_REQUESTED -> CANCELLED or FILLED; rejection/expiry terminal. UNKNOWN requires reconciliation, not retry-as-new.
- Engine: RECOVERING -> READY; degraded ingestion can disable entries while protection continues. Unknown exposure moves to RECONCILIATION_REQUIRED, not an undifferentiated successful process exit.

### Required invariants

- No entry without current execution authority, fresh data, valid signal and reconciled account state.
- One executor owns the account; duplicate deliveries/restarts cannot create a second intent for the same accepted action.
- Confirmed position equals attributed buy fills minus sell fills; acknowledgements never change it.
- Replacement sells wait for reconciliation of existing sells and cannot oversell allocated exposure.
- Every tradable signal and order intent is durable before external side effects.
- Cancellation/expiry cannot be reversed by late chunks without an explicit new signal revision.
- Invalid commands cannot terminate otherwise healthy position protection.
- Missing ticks are detected; no silent assertion of continuous market observation.
- Every received signal has an inspectable decision reason, including waiting, rejected, expired and queued.

## 7. Verification results and QA redesign

Executed locally with the existing virtualenv and a unique temporary TRADE_REGISTRY_PATH:

```powershell
$env:TRADE_REGISTRY_PATH = Join-Path $env:TEMP ('trading-audit-' + [guid]::NewGuid().ToString() + '.sqlite3')
./.venv/Scripts/python.exe -m unittest discover -s tests -q -b
```

Result: 85 tests passed. Existing tests were preserved. Three separate in-memory probes reproduced F03, F04 and F05; no broker calls were made by these probes. Their scenarios and observed results are documented above; they were not added as permanent regression tests because this is an audit, not an implementation change.

Existing coverage includes split messages, sizing, auth readiness, trailing, partial fills, rejection, cancel races and polling without ticks. There is no measured coverage percentage. Passing mocks do not establish live SDK fidelity or network timing.

Required additions, in priority order:

1. Restart/crash matrix at each boundary: before intent commit, after commit, after broker accept/before ID save, after partial fill, during cancel, after sell fill/before journal update. Verify no new entry under unresolved state.
2. Multiple-process ownership and simultaneous startup tests; external/manual position discrepancy tests.
3. Tick bursts before/during waits, out-of-order timestamps, stale feed, reconnect, persistent socket error, missing/NaN LTP, queue overflow and slow broker calls.
4. Two interleaved setups with old/new replies, cancellation before completion, cancellation while queued, edits, duplicate/reordered delivery, catch-up exceeding 20 messages and expired signals.
5. Invalid amendments while a position is open must produce a rejected-command event while protection continues.
6. Contract tests using redacted captured Fyers payloads: eventual order visibility, timeouts, throttling, malformed status, fee/slippage constraints and segment-specific contract restrictions.
7. Property/state-machine tests for nonnegative position, monotonic cumulative fills, no oversell, trailing ratchet, idempotent events and cancellation terminality.
8. Replay recorded Telegram and market events through the same production adapters/reducer in offline mode. Include realistic latency and pending fills rather than immediate-fill-only mocks.
9. Deployment tests: boot recovery, backup restore, graceful drain, auth expiry, disk-full journal, clock/session boundaries and readiness behavior. Load-test the API's socket lifecycle separately.

Suggested operational measures: age of last source message/tick, queue age, signal decision latency, order confirmation latency, unresolved intent count, broker/journal position drift, protection coverage, auth readiness and deploy commit. Set numeric SLOs after measuring a baseline; do not invent achieved latency or availability.

## 8. Prioritized migration and exact next task

### P0 — Recovery and ownership foundation

**Exact next development task: implement a startup reconciliation gate and account-scoped single-executor ownership, with deterministic mocked Fyers orders/trades/positions tests.**

First iteration must refuse new entries if existing broker exposure, nonterminal orders or unresolved persisted state cannot be unambiguously reconciled. Do not attempt automatic adoption/flattening of manual positions. Persist the reconciliation result and clearly expose NOT READY. Introduce durable order intent identity alongside this foundation so later recovery can resolve submissions interrupted before acknowledgement persistence.

Acceptance cases: flat clean boot succeeds; pending/partial entry blocks or restores its exact intent; pending exit is not duplicated; unknown submission is not retried as new; manual/unattributed position blocks admission; unavailable/malformed broker responses fail closed for entries; second executor cannot acquire ownership; repeated reconciliation is idempotent. Existing 85 tests must remain green.

### P1 — Correct event ownership and input semantics

Fix F03–F07: durable typed signal queue, reply correlation, cancellation terminality, TTL, ordered market-event consumption and per-command rejection isolation. Add the three reproduced defects as permanent failing-before/fixed-after tests. Preserve no-chase semantics unless the owner explicitly changes them.

### P2 — Protection and execution resilience

Introduce independent exposure supervision, validated broker-side protective capabilities, bounded retry/reconciliation policy, feed health, account reservations and full contract constraints. Do not enable automatic restarts merely because a service manager is available; recovery must pass first.

### P3 — Operational hardening and controlled rollout

Consolidate current documentation, lock dependencies, add CI across supported runtimes, sanitize auth/logging, fix API resource lifetime and implement tested deployment/rollback/drain procedures. Run offline replay, then non-ordering shadow comparison, then an explicitly authorized controlled live rollout with measured acceptance gates.

Decisions requiring owner acceptance before changing business behavior: buying-range semantics; maximum signal age; first-tick/chase tolerance; simultaneous signals; command-vs-runner precedence; expiry selection; monetary risk allocation; end-of-session treatment; outage protection behavior. These decisions do not block the correctness fixes or startup gate described above.

## 9. Evidence needed to close remaining uncertainty

Obtain a redacted, timestamp-aligned incident bundle: original Telegram IDs/replies/revisions and reception times; market exchange/receive timestamps; exact deployed commit/effective nonsecret config; order acknowledgements/status/trade fills; registry snapshots and process lifecycle logs. Also obtain deployment/service definitions, supported-runtime policy, risk acceptance rules, broker API permissions and protection-order capability results.

Without that bundle, this report can establish reproducible implementation defects and design risks, but cannot quantify historical financial impact, guarantee that every supplied call was actionable, identify every missed-call cause, or certify unattended production reliability.
