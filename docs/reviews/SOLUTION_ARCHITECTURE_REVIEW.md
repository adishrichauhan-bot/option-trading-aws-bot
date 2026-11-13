# 3. Solution architecture review

Date: 2026-11-12. Reviewed source baseline: `a4aac3a`.

## Verdict

The architecture supports a small, single-process trading prototype and deterministic tests of individual strategy transitions. It does **not** yet provide predictable end-to-end event handling, safe unattended restart, or adequate isolation of exposure management from unrelated failures.

Retain the confirmed-fill accounting, cancellation-race handling, whole-lot sizing and existing regression fixtures. Refactor orchestration, identity, persistence and protection boundaries before expanding functionality. A modular monolith with explicit ownership and durable state is proportionate; distributing the current components into microservices would not fix their contracts.

This is a documentation-only review. No application code, existing reports, configuration, orders or server processes were changed. The Python fact-grounded coding skill informed the source/evidence approach. This review reuses the previous audit's 85 passing tests and three offline reproductions; those checks were not rerun here. New observations below are static source findings, not newly reproduced production incidents.

Related evidence: [architecture/reliability audit](ARCHITECTURE_RELIABILITY_AUDIT.md), [functional traceability matrix](FUNCTIONAL_REQUIREMENTS_AUDIT.md), [operator runbook](../OPERATIONS.md#daily-operation).

## 1. Current component architecture

| Component | Actual responsibilities | Boundary assessment |
| --- | --- | --- |
| [Telegram adapter/assembler](../../telegram_trade_listener.py:87) | Session/login, events and polling, parsing, mutable assembly, deduplication, command recognition, printing | Transport, syntax and signal lifecycle are mixed. Reply relationships are read but do not establish independent keyed assemblies. |
| [Live orchestration](../../live_trader.py:168) | Mode selection, credentials, symbol/sizing setup, strategy construction, commands, pending signals, order polling, market consumption, snapshots, cleanup | Central failure domain; too many independent policies share one loop and exception boundary. |
| [Trading module](../../trading.py:421) | Domain object/parser, ATR, strategy, broker adapter, symbol cache/resolver, auth wrapper and standalone CLI | Pure calculations and external side effects are colocated; separate CLI and compatibility paths increase behavioral divergence. |
| [Position sizing](../../position_sizing.py:34) | Metadata access, funds lookup, arithmetic, environment policy, diagnostic output | Decimal calculation is reusable, but metadata and configuration are not cleanly injected. |
| [Market-data wrapper](../../market_data_ws.py:11) | Callback thread synchronization, subscriptions, latest-value cache, sequence/event wait API, connection state | Locking protects memory access but does not preserve the market event sequence required by crossing logic. |
| [Lifecycle module](../../trade_lifecycle.py:125) | Command regex parsing and SQLite snapshot registry | Unrelated concerns coexist; registry is neither a durable workflow queue nor a recovery engine. |
| [Authentication](../../auth.py:35) | Profile validation, token refresh/cache, browser/manual authorization | Useful noninteractive trading path; operational and interactive paths have inconsistent redaction and lifecycle behavior. |
| [Market-data API](../../app.py:192) | Independent index/ATM REST facade using Fyers/socket | Separate from trading orchestration, not a trading readiness endpoint. Request-owned sockets lack explicit cleanup. |

### Data flow versus control flow

Current data movement:

```text
Telegram events + recent-message polling
    -> shared mutable assembler / command parser
    -> Queue[TradeCallMessage | TradeCommand | BaseException]
    -> live trade loop
         -> later calls converted to untyped signal strings
         -> contract lookup + funds sizing
         -> market-price observations -> strategy -> synchronous broker requests
         -> order status polling -> cumulative-fill updates
         -> SQLite snapshot + console output

Separate HTTP request -> option chain + new market socket -> API response
```

The control loop first polls orders and journals, drains queued messages, waits for a new quote, applies the strategy, journals/prints, then optionally sleeps. Consequently, blocking broker calls and storage/logging latency delay price consumption and control-message processing. There is no measured end-to-end latency budget.

An order acknowledgement controls tracking, not exposure. Confirmed cumulative fills control remaining quantity. This is a sound distinction. However, persistence follows external submission, so broker state can advance without a durable matching intent.

## 2. Review of the twelve requested dimensions

Severity is architectural consequence, not incident frequency. **Critical** denotes possible unmanaged/duplicate exposure; **High** wrong or missed behavior and recovery/traceability failure; **Medium** operability, security-hardening or maintenance weakness. Production occurrence is not inferred from a possible path.

| Dimension | Evidence and weakness | Impact and assessment | Recommended boundary |
| --- | --- | --- | --- |
| Component architecture | [run_trade](../../live_trader.py:168) assembles most infrastructure and runs all execution concerns | High: changing one policy can affect many failure paths; multiple entrypoints do not share one application lifecycle | One composition root; explicit ingestion, signal coordinator, strategy, order manager and recovery modules |
| Separation of responsibilities | [trading](../../trading.py), [sizing](../../position_sizing.py:39), [registry/command module](../../trade_lifecycle.py) mix parsing, infrastructure and domain concerns | Medium: harder to reason about side effects and unit boundaries | Domain models/reducer depend only on typed ports; adapters own SDK, storage and transport details |
| Data/control flow | [queue conversion](../../live_trader.py:283) drops source metadata; [command routing](../../live_trader.py:139) accepts unrelated replies | High: source identity and causality are not preserved end to end | Immutable signal/event envelopes with stable IDs, revisions, source/reply references and timestamps |
| State management | [BreakoutTrader](../../trading.py:424) uses multiple flags, quantities, order references and target sets; [registry](../../trade_lifecycle.py:164) stores partial snapshots | High: legal transitions and complete recovery state are implicit | Explicit transition model, invariant checks, versioned events/snapshots and one state owner |
| Event processing | [market waiter](../../market_data_ws.py:122) discards unread updates; [listener](../../telegram_trade_listener.py:559) uses memory dedup and last20 poll fallback | High: outcome depends on timing and delivery order, not just event contents | Ordered consumer cursors/queues, durable inbox, pagination, revision handling, freshness/overflow policy |
| Error handling | [outer trade handler](../../live_trader.py:336) turns exposure-time errors into stop; [queue drain](../../live_trader.py:280) catches any Exception as empty | Critical for protection continuity: invalid amendments/ingestion errors can stop monitoring; broad catches blur diagnoses | Typed validation, transport, auth, persistence and reconciliation errors; local recovery decisions |
| External APIs | [executor](../../trading.py:197) uses synchronous Fyers SDK; [auth](../../auth.py:35), [resolver](../../trading.py:950) and [sizer](../../position_sizing.py:103) have separate handling | High: latency/errors consume the same control loop; no unified timeout/retry/rate-budget contract | Broker/market/auth adapters with typed schemas and bounded policies; never blindly retry unknown order submissions |
| Persistence/recovery | [startup](../../live_trader.py:353) does not reconcile broker state; [snapshot](../../live_trader.py:240) occurs after submission and excludes queued/manual paths | Critical: restart cannot prove ownership or protection of exposure | Durable pre-submit intent, universal identity, startup reconciliation, account ownership gate |
| Concurrency/synchronization | Thread-safe queue/RLocks exist; no process-wide/account lease; registry lock is per instance | High: memory safety does not provide business ordering or single executor ownership | One account execution owner, transactionally serialized updates, explicit producer/consumer contracts |
| Logging/observability | Console text/JSON mixed; mutable journal; [API health](../../app.py:186) always ok | High: cannot reliably distinguish connected, armed, exposed, protected, stale and halted | Structured correlated events, separate liveness/admission/protection readiness, metrics and alerts |
| Security boundaries | External channel text controls orders; broad commands; [auth output](../../auth.py:237) exposes profile/error details; API has no application-level auth | High for command authority; Medium hardening: ambiguous input can cross into execution; actual network exposure unknown | Approved chat/account provenance, strict command correlation, redaction, restricted secrets and explicit API exposure policy |
| Failure isolation | Telegram faults, journal faults, validation errors and broker faults converge in one engine shutdown path | Critical: stopping new orders also stops local protection of existing exposure | Separate entry admission from exposure supervision; independent alerting and verified broker-side protection capability |

## 3. State, persistence and recovery analysis

### State ownership today

| State | Location | Durable/recoverable? |
| --- | --- | --- |
| Raw messages, dedup set, last polling ID | Listener memory | No durable cursor/inbox; startup intentionally chooses newest baseline |
| Incomplete setup and remembered last call | Assembler memory | Lost on restart; not independently keyed by reply root |
| Pending complete calls | In-memory Queue, then list of strings | No; identity/timestamps lost during conversion |
| Strategy flags, ATR, high watermark, target state | BreakoutTrader memory | Snapshot captures only a subset; no reconstruction path |
| Submitted order tracking and cached polls | Executor/strategy memory | Some IDs/fills copied to registered trade snapshot after side effects |
| Trade lifecycle | SQLite trades row with JSON | Durable last snapshot, not complete transition history or broker truth |
| Actual orders, fills, account positions | Broker | Authoritative exposure evidence, not reconciled at startup |

The current registry must not be described as recovery simply because it persists JSON. Its public API is create/update/get. It lacks nonterminal traversal, recovery decisions and restoration. Snapshot omissions include sufficient historical event chronology and several strategy details needed for exact continuation. Queued and manual paths do not consistently journal at all.

The dangerous crash interval is broker acceptance before tracking/journaling. A transport exception is correctly treated as unknown rather than retried, but there is no durable intent identifier established before submission to support restart resolution. An outbox cannot make a remote order API transactional; it makes uncertainty explicit and recoverable.

Recommended recovery ordering:

1. Acquire exclusive execution ownership for the configured account.
2. Enter RECOVERING with new entries disabled.
3. Load nonterminal signals/intents and broker orders, trades and positions.
4. Reconcile IDs, cumulative fills and attributed position quantities; identify manual/unowned discrepancies.
5. Restore or explicitly quarantine exposure/protection state. Do not silently adopt or liquidate external positions.
6. Enable admission only when the reconciliation policy permits it; record the result durably.

Broker APIs failing during startup must not be interpreted as an empty account. Automatic process restart is not a recovery solution until this sequence exists.

## 4. Event ordering and concurrency

The architecture has a main execution thread, a Telegram thread running asyncio, and SDK/socket callback threads. Queue and RLock use are helpful, but synchronization correctness has several separate levels:

- **Memory access:** locks protect the socket dictionaries and per-registry-instance operations.
- **Delivery:** event and polling channels can deliver duplicate or differently ordered messages. Memory dedup avoids some duplicates, not recovery across restarts or edits.
- **Consumption:** the market waiter establishes its baseline at read time rather than last consumption. Producer updates between reads can disappear even without a data race.
- **Business ordering:** later setups and their commands must remain correlated. The single-active-trade rule does not establish which setup an incoming reply references.
- **Account ownership:** two processes can both trade. A Python lock does not exclude another process, host or manual actor.

The Telegram callbacks perform their current message-routing work synchronously within the event loop; this review does not assert simultaneous mutation inside that non-awaiting section. The supported concern is event arrival/interleaving and shared assembly context, not an invented threading race.

The unbounded Queue, unbounded seen-ID set and pending list have no admission/backpressure policy. Draining every queued event before processing the next price can delay protection under a sustained backlog. This is a static starvation risk, not a measured load-test result.

[TradeRegistry.create](../../trade_lifecycle.py:148) computes the next ID by SELECT then INSERT under an instance-local lock. Concurrent registry instances may contend or select the same candidate; the primary key prevents duplicate stored IDs but not independent broker activity, and no retry/serialization contract is defined. Likewise, read/merge/write snapshots can lose logical changes across writers. These interleavings were not executed in this review.

On reconnect, [_on_connect](../../market_data_ws.py:41) does not clear an earlier `_error`; consumers continue raising when that field remains set. Merely configuring SDK reconnect=True therefore does not prove application-level recovery. Define connection generations, readiness reset and stale-cache invalidation explicitly.

## 5. Coupling, hidden dependencies and unnecessary complexity

| Dependency/problem | Concrete evidence | Why it matters | Simplification |
| --- | --- | --- | --- |
| Circular module dependency | [trading imports sizing](../../trading.py:24); [sizing lazily imports private master loader](../../position_sizing.py:40) | Import succeeds through deferral, but direction of dependency is inverted and metadata reuse depends on trading internals | Dedicated instrument repository port/adapter |
| Import-time environment side effects | [trading](../../trading.py:26), [auth](../../auth.py:25), [API](../../app.py:17) load dotenv; live CLI relies on imported modules | Effective behavior depends on import order, process environment and working directory | Parse/validate one immutable settings object at startup and inject it |
| Dual parsers | [Telegram parser](../../telegram_trade_listener.py:288) and [trading parser](../../trading.py:70) differ on range/SL rules | A signal accepted upstream can fail downstream; tests may validate only one ingress | One normalized domain validator plus separate text adapters |
| Dual strategy paths | [on_price](../../trading.py:752) switches based on executor confirmation capability | Legacy test executors can exercise behavior different from actual Fyers/paper path | One fill-event model; mocks implement same port and lifecycle |
| Strategy executes infrastructure | [entry sizing/submission](../../trading.py:607) invokes funds/network/order operations during price handling | Pure policy cannot be replayed independently of I/O scheduling | Reducer emits intents; effect handler executes them and feeds normalized results back |
| Coupling via strings and introspection | reason.startswith(target_) and executor capability checks in [confirmed path](../../trading.py:564) | Human-facing wording can affect control decisions; protocols do not express full lifecycle contract | Typed reason/order-purpose enums and complete adapter protocol |
| Two startup/control lifecycles | [live main](../../live_trader.py:353) and [standalone trading main](../../trading.py:988) | Recovery/journaling protections can diverge by entrypoint | Shared application service; CLI differences limited to input adapters |
| Implicit clocks | Wall-clock session selection, registry local datetime, ATR timestamps and monotonic polling | Tests and timezone boundaries can diverge; source time and receipt time are not uniformly represented | Inject clocks; UTC storage with explicit exchange-session timezone |
| Auth bundled with browser dependency | [auth imports](../../auth.py:20) include Playwright in the trading credentials module | Headless credential validation imports interactive tooling; dependency availability affects readiness | Separate credential service from interactive provisioning CLI |
| Historical documentation overlays | [running guide](../OPERATIONS.md#strategy-and-order-behavior), [handoff](../history/HANDOFF.md), [preparation](../OPERATIONS.md#installation-and-configuration) retain superseded instructions | Operators can select incompatible behavior despite current source being correct | One versioned current contract/runbook; separate historical incident archive |

There is no evidence that SQLite or a monolith is itself the problem. Missing transaction semantics, ownership, recovery contracts and failure boundaries are the problem. Introducing Kafka, containers or multiple services is not a substitute for those contracts.

## 6. Failure isolation and single points of failure

| Failure/dependency | Current response / single point | Required architectural response |
| --- | --- | --- |
| Main process/EC2 interruption | In-memory strategy lost; no broker-held stop is placed by current local-stop design | Durable recovery plus separately verified exposure protection; no automatic blind restart trading |
| Market feed stops without fatal error | Timeout retries continue; no fresh stop evaluation | Explicit stale-feed state; disable entries and execute approved exposure-management/alert policy |
| Market callback reports error | Persistent error causes consumer exception; active trade stops | Generation-aware reconnect and resubscription; protect exposure while resolving connectivity |
| Telegram listener fails | Exception travels through same queue into active trade; may stop monitoring | Degrade ingestion/admission without inherently terminating protection |
| Invalid amendment | Modifier throws; outer handler can halt with exposure | Reject command locally and retain last valid strategy state |
| Broker submit timeout | Correctly marked unknown; engine halts | Durable unknown intent, no retry-as-new, broker reconciliation and incident reporting |
| Broker order lookup/cancel failure | BrokerStateError; monitoring/admission stops | Separate uncertain-order state from process death; bounded reconciliation and alerts |
| SQLite failure | Snapshot failure can stop engine; subsequent error-status write may fail again | Explicit persistence-degraded state; entry block and documented protection continuity; do not claim journal success |
| Token expiry/refresh unavailable | Startup gate exists, but long-lived active execution has no independent auth recovery | Credential lifecycle monitoring and approved protection response |
| Repeated API requests | New sockets constructed per request without corresponding close | Application-lifetime ownership or deterministic request cleanup |
| Operator/runbook error | Manual tmux procedure and contradictory historical instructions | Tested deploy/drain procedure, version/readiness display, recovery gate and alerts |

The Fyers account/API, Telegram source, host, token material and local disk are external or operational dependencies. Their uptime and redundancy were not measured. The repository contains no demonstrated failover protection for them. Failover itself requires fencing to avoid a second executor trading simultaneously.

## 7. Logging, observability and security boundaries

Current prints show some useful prices, order payloads, reasons and sizing calculations. The lifecycle journal also supplies useful snapshots. Nevertheless, log lines do not form a consistent, versioned event schema; not all trades carry stable IDs; process exit can be the only failure signal. A successful `/health` response belongs to the separate API process and cannot establish that the trading loop is alive or exposure protected.

Minimum proposed events: signal received/assembled/rejected/expired, command accepted/rejected with mapping reason, entry armed/waiting, intent recorded/submitted/unknown, broker fill/cancel update, protection change, reconciliation result and readiness change. Include UTC source/receive/decision timestamps, signal/intent/order IDs, account alias, symbol and deployment revision; redact credentials and unnecessary identity data.

Metrics should distinguish last-tick age, ingestion lag, queue age/depth, entry-decision reason, order-confirmation age, unresolved intents, broker-versus-journal drift and unprotected exposure. Numeric service levels are not established; measure a baseline and obtain acceptance targets rather than fabricate them.

Trust boundaries:

- **Telegram -> execution:** configured channel selection limits source scope, but text/reply provenance still needs validation. Broad regex matching is not equivalent to an authorized unambiguous instruction. Ambiguous text must not become an arbitrary active-position command.
- **Broker response -> state:** validate IDs, types, status/fill consistency and account ownership. Current cumulative-fill checks are valuable; broker/account-wide reconciliation is absent.
- **Credentials -> local files/console:** [token writes](../../auth.py:90) use replacement, but restrictive permissions are applied afterward; [interactive auth](../../auth.py:133) echoes pasted credentials and main prints profile/error details. Concurrent writers use the same temp filename. Hardening is justified; no current credential theft is asserted.
- **HTTP -> API:** application code provides no access-control layer and returns raw exception text. The API does not directly submit orders, but it consumes authenticated upstream resources. Actual public exposure depends on deployment binding/security groups, which were not inspected here.
- **Repository -> deployment:** broad dependency ranges and no tracked lock/CI definitions reduce reproducibility. Existing ignore rules do not cover all common credential/session artifacts; no claim is made that such secrets are currently committed.

## 8. Quality-attribute evaluation

| Attribute | Current assessment | Evidence needed to meet the objective |
| --- | --- | --- |
| Predictable behavior | **Insufficient end to end.** Arithmetic and some state transitions deterministic; timing, lost ticks, implicit modes and ambiguous identity undermine repeatability | Replay same normalized ordered events to identical decisions; explicit behavior for stale/duplicate/reordered inputs; no silent event loss |
| Safe recovery | **Not supported automatically.** Journal snapshots exist, but no startup reconciliation/restoration or single-account ownership | Crash-point tests around every external side effect; idempotent reconciliation; unknown/manual exposure quarantine; duplicate-executor exclusion |
| Testability | **Moderate locally, weak at failure boundaries.** Useful 85-test baseline, mockable broker; mocks differ from live stream semantics | One domain reducer and adapter contract; virtual clock; fault injection; recorded schema fixtures; restart, burst and concurrency tests |
| Maintainability | **Degraded but repairable incrementally.** Small codebase, useful modules, but mixed responsibilities, cycles, parallel paths and stale documentation | One parser contract, one execution lifecycle, typed ports/settings, isolated adapters, versioned state schema and current architecture decisions |

The prior 85 passing tests are evidence of those exercised cases, not certification of these quality attributes. The previously reproduced lost-tick and command/cancellation defects demonstrate gaps in the test model rather than a need to discard all existing tests.

## 9. Proposed architecture and contracts

Retain one deployable execution application initially, with explicit boundaries:

```text
Transport adapters -> durable inbox -> correlated signal coordinator
                                         |
                               instrument/risk admission
                                         |
                             deterministic strategy reducer
                                         |
                              durable intent / outbox
                                         |
                        order manager -> broker adapter -> Fyers
                             ^                 |
                             +-- fills/status -+

Recovery + account ownership gate all new submissions.
Exposure supervisor consumes confirmed position/protection state.
Market adapter supplies ordered, timestamped events with explicit health.
Operational API reads readiness/projection state; it does not own strategy.
```

Contracts:

1. Every signal preserves chat/message/reply/revision identity; queueing never downgrades it to an anonymous string.
2. Pure strategy transitions return typed proposed actions; I/O failures do not partially mutate strategy state without a recorded outcome.
3. An order intent is durable before dispatch. Acknowledgement is not fill; unknown outcome is neither failure-to-submit nor permission-to-retry.
4. Confirmed position equals attributed cumulative buy fills minus sells. Replacement orders wait for reconciliation of conflicting pending orders.
5. Entry admission and exposure management have separate health states. Ingestion failure blocks new signals without automatically stopping protection.
6. Account ownership is exclusive and checked before submission. Cross-host failover requires fencing and broker-aware reconciliation, not just a local lock file.
7. Price consumption has explicit sequence/freshness/overflow semantics. A reconnect invalidates stale readiness/cache assumptions.
8. Persistence includes durable input/intent/event records and versioned projections. SQLite remains viable with disciplined single-writer transactions and tested backup/restore.

Do not assert exactly-once delivery or atomic broker/database transactions. Use durable intent, idempotent event processing, broker correlation and explicit reconciliation of uncertainty.

## 10. Incremental implementation order and validation gates

1. **Recovery foundation:** startup reconciliation and exclusive executor ownership, matching the already identified next development task. Gate: flat boot succeeds; uncertain/foreign exposure blocks new entry; pending exit never duplicated; repeated recovery is idempotent.
2. **Identity and events:** durable typed signals, reply graph, cancellation terminality, ordered market delivery. Gate: existing three defect probes become regression tests; interleaved/duplicate/delayed messages preserve attribution.
3. **Isolation and state:** extract pure reducer, typed errors and order manager; invalid commands/Telegram errors no longer automatically terminate exposure supervision. Gate: crash/fault matrix establishes position and protection outcomes without oversell.
4. **Protection and operations:** validate broker-side protection capabilities, approved outage behavior, structured observability, credential lifecycle and supervised deployment. Gate: deployment/recovery drills and shadow runs meet agreed criteria; no live failure experiments implied by this review.
5. **Maintenance consolidation:** canonical validation, injected settings/clock, unified entrypoints, resource-owned API sockets, dependency lock and supported-runtime CI. Preserve original tests and reconcile intentionally changed policies with owner decisions.

Business decisions from the [requirements audit](FUNCTIONAL_REQUIREMENTS_AUDIT.md) remain open: range semantics, expiry, signal validity, sizing allocation, command precedence, session rules and outage actions. Architectural correctness fixes should not silently decide these rules.

## 11. Limits of this conclusion

This review inspected source, current runbook and earlier audit evidence. It did not collect fresh EC2 logs, production database/account snapshots, network traces, IAM/security-group configuration, benchmarks or broker capability certification. No new tests, load tests or failure drills were performed in this section. SDK-internal timeouts/retries and external service behavior were not exhaustively audited. Risks based on static paths are labeled as such; no historical financial impact or production failure rate is inferred.

Overall: the architecture can be made maintainable and testable without a wholesale rewrite, but safe recovery and independent protection require substantive design work, not additional parser patches alone.
