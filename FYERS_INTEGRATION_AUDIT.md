# Python and FYERS integration audit

Baseline: `a4aac3a`; reviewed 2026-11-12. Application code unchanged. This technical review precedes test additions and proposed implementation corrections.

## 1. Exact local integration inventory

Local installed distribution: **fyers_apiv3 3.2.1**, verified from `.venv/Lib/site-packages/fyers_apiv3-3.2.1.dist-info/METADATA`. [requirements](./requirements.txt) permits `>=3.1,<4`; it does not pin this version. EC2 version was not inspected in this audit and must not be inferred from local installation. Package metadata pins requests2.31.0, aiohttp3.9.3 and websocket-client1.6.1 among other dependencies; installed transitive compatibility was not fully certified.

Endpoint values below are inspected in installed SDK `fyersModel.Config` and method bodies, not guessed from names. Base REST: `https://api-t1.fyers.in/api/v3`; data: `https://api-t1.fyers.in/data`.

| Application operation | Local SDK/HTTP interface | Application response assumptions |
| --- | --- | --- |
| Browser authorization | SessionModel.generate_authcode -> `/generate-authcode` | Redirect query/fragment contains auth_code/code, optional state checked |
| Token exchange | POST `/validate-authcode` | access_token required; refresh_token optional; error response handling varies by path |
| Refresh | Direct requests POST `/validate-refresh-token`, timeout30 | grant_type=refresh_token, appIdHash, refresh_token, pin; refreshed token validated by trading path |
| Readiness | get_profile -> GET `/profile` | s=ok or code200/201 accepted by auth helper |
| Funds | funds -> GET `/funds` | s=ok, fund_limit list; unique Available Balance title; equityAmount/commodityAmount |
| Place order | place_order -> POST `/orders/sync` | Returned id mandatory; acknowledgement is not fill; explicit error or absent id raises BrokerStateError |
| Cancel | cancel_order -> DELETE `/orders/sync` | s=ok is request acknowledgement only; terminal status must be polled |
| Status | orderbook(data={id}) -> GET `/orders?id=...` | orderBook list, matching id, status and filledQty required |
| Option discovery | optionchain -> GET data `/options-chain-v3` | Nested options_chain/optionsChain/d/data rows; symbol, strike and type aliases; first matching strike/type |
| Symbol metadata | requests GET `https://public.fyers.in/sym_details/{segment}_sym_master.json`, timeout20 | Mapping of exact symbol to metadata; minLotSize, symTicker, tradeStatus; expiry/strike/type fields for fallback resolution |
| Market stream | FyersDataSocket, SDK endpoint `wss://socket.fyers.in/hsm/v1-5/prod`; symbol-token helper `/data/symbol-token` | subscribe SymbolUpdate; callbacks on_connect/message/error/close; application needs symbol/ltp and optionally timestamp |

Application market socket options: litemode=False, write_to_file=False, reconnect=True; token passed as client_id:access_token. SDK helper symbol-token request has timeout10. Local SDK map includes last_traded_time and exch_feed_time; application reads generic timestamp and otherwise ATR uses local time. No adapter explicitly maps those SDK timestamps. Exact timestamp choice/units must be contract-tested against captured SymbolUpdate data; generic timestamp availability must not be assumed.

The installed SDK also provides FyersOrderSocket at `wss://socket.fyers.in/trade/v3`, with on_orders/on_trades/on_positions/on_general and OnOrders/OnTrades/OnPositions subscriptions. **The application does not instantiate this interface.** There is consequently no implemented REST-versus-order-WebSocket merge, gap recovery or ordering contract to verify. No webhook receiver is implemented either.

## 2. Official documentation cross-check

- [Order lookup](https://fyers.in/api-docs/v3/api/trading/order-book/get-order-by-order-id/) confirms querying with FYERS id, distinct from exchange order ID. Current lookup follows that distinction.
- [Status/fill reference](https://fyers.in/api-docs/v3/api/trading/webhooks/webhook-response/) documents statuses1 cancelled,2 filled,4 transit,5 rejected,6 pending,7 expired and cumulative filledQty. Application mapping matches these numeric values. Its additional textual aliases are compatibility assumptions, not demonstrated FYERS guarantees. Webhook envelope is not presumed identical to REST.
- [Funds](https://fyers.in/api-docs/v3/api/account/funds/) confirms fund_limit headings and title-based Available Balance selection. Affordability estimate is not margin approval or guaranteed fill price.
- [Symbol master](https://fyers.in/api-docs/v3/api/market-information/symbol-master/symbol-master-json/) documents active/inactive tradeStatus and metadata. Treating a missing activity field as active is application policy, not a documented guarantee.
- [General socket](https://fyers.in/api-docs/v3/api/websockets/general-socket/overview/) describes separate order/trade/position callbacks and SDK/raw key mappings. Normalize each envelope separately if added; do not feed raw socket payloads into REST decoder.
- [Order events](https://fyers.in/api-docs/v3/api/websockets/general-socket/general-socket-order-events/) describes subscription and order identifiers; it does not establish application recovery or exactly-once processing.
- [Data socket overview](https://fyers.in/api-docs/v3/api/websockets/data-socket/) confirms market-stream separation from account events.
- [Auth-code exchange](https://fyers.in/api-docs/v3/api/authentication/individual-app/validate-auth-code/) confirms token-exchange API role.

Specific guessed URLs for detailed SymbolUpdate and refresh documentation were inaccessible through the browser tool; those two detailed contracts were not fully verified against their pages. Endpoint/source facts remain verified locally, while complete auth/data schema conformance remains an evidence gap. No claims about token duration, replay guarantees, cancellation deadlines, broker idempotency or quota numbers are inferred.

## 3. Authentication and transport behavior

[auth](./auth.py:35) checks environment token then cache, validates with profile, optionally refreshes with secret/PIN/refresh token, validates refresh and atomically replaces cache. Interactive CLI obtains authorization code through browser or echoed manual input. State is checked only if returned; expected state comes from configuration, not a per-login random value. Redirect origin/state enforcement merits hardening; no exploit is asserted here.

Installed SDK synchronous `FyersService.post_call/get_call/delete_call` invokes requests.Session methods without an explicit timeout. SessionModel.generate_token also omits timeout. These differ from application refresh/master requests with explicit timeouts. SDK catches many exceptions and can return an error-shaped result, so applications must consider both raised transport errors and returned error envelopes. Inspecting these bodies confirms an unbounded-call design risk; an actual production hang was not measured.

[executor](./trading.py:208) halts on uncertain submit. It does not retry blindly: good containment. But an SDK error response is labelled “rejected” even where a transport-level outcome may be unknown; no durable intent exists to resolve it after termination. Poll/cancel failure can stop active protection. poll_interval is a cache interval, not a network deadline or rate-limit budget.

## 4. Python execution and synchronization

- Main thread owns BreakoutTrader, polls REST, processes commands, waits for ticks and journals synchronously. Blocking REST/storage/logging delays all these responsibilities.
- Telegram daemon thread runs asyncio.run; event callbacks and polling share one assembler/seen set. Current message routing has no internal await through its mutation sequence, so do not invent simultaneous coroutine mutation there; arrival/interleaving/correlation still matter.
- Thread-safe Queue carries calls, commands and exceptions. A BaseException from listener becomes an engine error; it is not isolated from existing position protection. Queue/pending list/seen IDs have no application capacity/retention control.
- SDK socket callbacks mutate latest-price/sequence/event dictionaries under RLock. This protects memory operations, not delivery ordering. wait_for_update snapshots producer sequence at read time, dropping unread updates between reads.
- Reconnect does not clear _error. Close marks connection false but does not create a stale-feed escalation. Timeout retries can continue indefinitely without price-based protection.
- Registry lock is per instance. SQLite primary key does not enforce account execution ownership or transactional read/merge/write across multiple independent writers. Specific cross-process interleavings remain scenarios to test.
- Finally closes the market stream, including after an invalid command stops monitoring. API requests create separate streams without corresponding cleanup.

## 5. State transitions, cross-source consistency and reconstruction

OrderState lists recognized statuses and a terminal predicate; TrackedOrder retains applied_fill/status/cancel flag. This is **not a complete transition table**. Unknown statuses, decreasing cumulative fills, overfills and FILLED quantity mismatch are rejected. Nonterminal status regression such as PENDING -> TRANSIT with unchanged fills is not checked against a documented transition graph. Whether that particular observation can be legitimate must be resolved from broker contract, not assumed.

Repeated cumulative fills are idempotent within a run; partial exposure is recorded even if the buy remains pending. Cancellation ACK does not authorize replacement before terminal reconciliation. Existing tests cover these strengths. Fractional filledQty is truncated by int(), a confirmed validation defect. Terminal observations clear active order tracking; later changes are not reconciled through an account ledger.

REST snapshots, future socket observations and executions must have separate decoders with common normalized IDs, observation times and cumulative/execution semantics. A newer arrival need not be newer broker truth. Merge cannot double-count a socket execution and REST cumulative change. Until a merge contract exists, adding an order socket alone does not improve accounting safety.

[journal](./live_trader.py:240) persists selected last-state fields after external effects. Queued/manual/reentry paths omit it; source revisions, full ATR/trailing history, executed prices/charges and intent chronology are incomplete. Startup does not load/reconcile prior nonterminal trades against orders/trades/positions. Thus internal state cannot be completely reconstructed from current persistence. Broker net positions alone cannot distinguish bot versus manual ownership.

No P&L report engine exists. ACTION prices are decision/request context, not confirmed execution prices; estimated premium is not realized cost. Financial correctness remains unverified until execution/charge attribution and accounting policy exist.

## 6. Issue classifications

| ID / classification | Evidence / effect | Severity and correction direction |
| --- | --- | --- |
| FY01 Confirmed implementation defect | market_data_ws wait discards unread ticks; prior reproduced | High: consumer cursor/ordered bounded queue |
| FY02 Confirmed implementation defect | _on_connect never clears transient _error; prior reproduced | High: validated connection-generation recovery |
| FY03 Confirmed implementation defect | order_state int(1.9) becomes1; prior reproduced | High: strict integral schema validation |
| FY04 Architectural weakness | Local SDK sync HTTP omits explicit deadlines; main loop blocked by calls | Critical exposure consequence: bounded adapter I/O, classified timeout and independent protection health |
| FY05 Architectural weakness | No durable pre-submit intent/startup/account reconciliation | Critical: ownership/recovery gate and durable workflow |
| FY06 Confirmed implementation defect | Unrelated replies accepted; incomplete cancellation survives; queued provenance lost | High: persistent typed signal/reply lifecycle; technical register TD03–05 |
| FY07 Architectural weakness | Status validation is not explicit transition/observation merge model | High: normalized events, guarded reducer, reconciliation on contradiction |
| FY08 Unverified assumption | Generic timestamp availability, textual status aliases, full refresh/schema contract | High where used for decisions: captured redacted fixtures and official contract validation |
| FY09 Missing requirement | Freshness/cancel/visibility deadlines, manual allocation policy, accounting method | Define acceptance policy; do not invent thresholds or auto-liquidation |
| FY10 Potential failure scenario | Two executors or manual activity conflict with pending bot exit | Critical: exclusivity plus broker allocation checks; concurrency tests required |
| FY11 Confirmed implementation defect | API socket cleanup absent; interactive auth raw/echoed sensitive output | Medium: scoped lifecycle and consistent redaction |
| FY12 Architectural weakness | Log correlation/time/source fields inconsistent; snapshot history incomplete | High: append-only normalized event trail and output manifests |
| FY13 Unverified assumption | EC2 SDK equals local3.2.1 or future allowed install behaves identically | Pin tested versions and record deployment manifest; remote version not inspected |

Logs currently contain some order IDs, prices and reasons but not universal correlation IDs, UTC timestamps, event source and before/after state. Noninteractive refresh redacts failures, whereas interactive paths/profile output and arbitrary response exceptions are less controlled. Logging privacy is not certified by SDK log_path being empty; SDK logging configuration and generated files require separate redaction verification.

## 7. Proposed boundaries (no production modifications in this audit)

Retain existing arithmetic/fill/cancel tests. Introduce typed BrokerPort for submission/cancel/status/trades/positions, MarketPort for ordered timestamped events, CredentialProvider, InstrumentRepository, Clock, durable IntentStore and pure StrategyReducer. Put SDK/schema/deadline knowledge only in adapters, not strategy.

ValidationError rejects input without killing protection; ReadUnavailable uses bounded read policy; UnknownWrite forbids retry-as-new; AuthUnavailable disables admission and invokes approved recovery; PersistenceUnavailable blocks uncommitted effects; ReconciliationRequired quarantines conflicting allocations. Trade cancellation and process shutdown are not synonyms.

Recovery: exclusive owner -> durable unresolved intents -> successful broker snapshots -> cumulative/execution reconciliation -> restore supported state or quarantine -> admit new entries only when permitted. If broker information cannot uniquely resolve an unknown order, do not invent a result. Keep one writer even if market/order sockets run asynchronously.

## 8. Executable audit tests

Add a separate mocked unittest module after this review. Passing tests check maintained safeguards; expected-failure tests express desired invariants for known unfixed defects. **Expected failures are not fixes or passing safety criteria.** An unexpected success forces review/removal of the marker when fixed. Fixtures are synthetic; no real FYERS model/socket instantiated, no funds/auth/network requests. Network socket connections are guarded during test execution.

Planned executable cases: pending ACK versus fill, duplicate cumulative observations, fractional-fill rejection, malformed order lookup, timeout after possible acceptance, unknown status, tick buffered-before-read, reconnect recovery. Results will be appended only after execution.

### Executed results

Added [executable audit tests](./tests/test_fyers_integration_audit.py). Focused run: **8 tests, 5 passed, 3 expected failures**. Full suite: **93 tests, 90 passed, 3 expected failures**, no unexpected failures (0.419 seconds reported). Editor diagnostics reported no errors in the added module. Application source and existing tests were not modified.

The three expected failures reproduce desired-but-unmet behavior FY01 unread tick delivery, FY02 reconnect recovery and FY03 fractional-fill rejection. They are explicitly unresolved defects, not successful validation. Remove their expectedFailure markers when implementing fixes; unexpected successes cause unittest failure and demand review.

Run using the project virtualenv, with an isolated registry:

```powershell
$env:TRADE_REGISTRY_PATH = Join-Path $env:TEMP ('fyers-audit-' + [guid]::NewGuid().ToString() + '.sqlite3')
./.venv/Scripts/python.exe -m unittest discover -s tests -p test_fyers_integration_audit.py -v -b
./.venv/Scripts/python.exe -m unittest discover -s tests -q -b
```

Audit tests instantiate only mock broker/socket interfaces, with socket connection guards during each test. They do not construct a real FYERS client or submit real orders. Full suite used its existing mocked/offline paths and temporary registry; no live broker validation was performed. These tests do not yet prove cross-process ownership, full restart reconstruction, log redaction or SDK transport deadline enforcement; those remain specified follow-up work.
