# Independent technical review and approval matrix

Reviewed 2026-11-13 (Asia/Calcutta). Baseline: `62f190bed1cda49872a3e569e6fe2bfeaac77709`, branch `main`, initially clean working tree. Application baseline remains the implementation reviewed at `a4aac3a`; the subsequent commit added review documents and audit tests.

## Decision and authority

**Overall verdict: APPROVED WITH CONDITIONS for staged offline engineering. Implementation is not authorized by this review.** The interrupted user request explicitly requires a review deliverable and stopping before corrections. This document supplies technical judgments, not user approval, production readiness certification, or authorization to deploy, restart, replay messages, or change broker state.

The requested filename is retained. No claim is made about the authorship of earlier reports, a separately authenticated reviewer identity, or verification of the VS Code model selector. Findings were checked against current source, fresh offline tests/probes, and official FYERS documentation rather than accepted on attribution.

The central diagnosis is valid: confirmed-fill and cancellation safeguards are useful, but orchestration lacks durable execution identity, restart reconciliation, reliable market-event delivery and isolation of input rejection from exposure monitoring. Retain those working safeguards. A modular monolith with SQLite and one execution owner is proportionate; a wholesale rewrite or microservices migration is not justified.

## Evidence and limits

Reviewed the [architecture audit](ARCHITECTURE_RELIABILITY_AUDIT.md), [requirements audit](FUNCTIONAL_REQUIREMENTS_AUDIT.md), [solution architecture](SOLUTION_ARCHITECTURE_REVIEW.md), [technical register](TECHNICAL_DESIGN_CODE_REVIEW.md), [FMEA](FAILURE_MODE_EFFECTS_ANALYSIS.md), [output validation](DATA_OUTPUT_VALIDATION.md), [improvement plan](RELIABILITY_IMPROVEMENT_PLAN.md), and [FYERS integration audit](FYERS_INTEGRATION_AUDIT.md), with relevant source and tests. These documents overlap; their IDs do not count as separate incidents. No repository AGENTS.md was found. The Python fact-grounded coding skill guided source and execution checks.

Evidence labels below: **S** = independently inspected source/control path; **T** = fresh existing-suite result; **P** = fresh targeted synthetic probe; **D** = official documentation checked this turn. Static consequence is not proof of a production incident or its frequency.

### Fresh baseline

| Item | Result |
| --- | --- |
| Runtime | Local virtualenv Python 3.12.0 |
| Installed FYERS distribution | 3.2.1, checked from installed package metadata |
| Discovery | `unittest.defaultTestLoader.discover(<repository>/tests)` |
| Execution | 93 tests, 0.631 seconds reported by unittest |
| Passing | 90 |
| Expected failures | 3: unread tick delivery, reconnect recovery, fractional-fill rejection |
| Unexpected failures / errors / unexpected successes / skipped | 0 / 0 / 0 / 0 |
| Process exit | 0; expected failures remain unresolved defects |
| Network / protected-file audit violations | 0 |

The suite was run through an in-memory Python harness, with bytecode writes disabled (`-B`). Before discovery it replaced the environment with minimal Windows runtime variables and temporary HOME/USERPROFILE, token and journal paths; patched dotenv loading off; rejected HTTP requests and real FYERS client construction; and installed audit guards against external socket connections/DNS and production `.env`, `.fyers` and `.telegram` file access. Windows asyncio's internal socket-pair creation was permitted narrowly during socketpair construction. Existing tests used their own mocks. Temporary state was removed by TemporaryDirectory. These are process-level test guards, not an OS-level network-isolation certification. No real broker client or production credentials were used.

Additional fresh probes, using synthetic objects and disabled dotenv loading:

| Finding | Observed result |
| --- | --- |
| TD03 | EXIT replying to 999 applies to active source IDs (1,2,3): `True` |
| TD04 | Instrument -> BUY ABOVE -> CANCEL -> targets/SL emits `CANCEL`, then `TradeCallMessage` |
| TD06 | Changing entry to range 110–111 retains targets (102,103,104) and stores `buy_above=None` |
| TD08 | Range 100–100/SL90: Telegram accepts, execution rejects; range 100–105/SL102: Telegram rejects, execution accepts |

No fresh EC2, production account, database, financial statement, or historical tick evidence was obtained. Python 3.9/EC2 compatibility, full crash recovery, cross-process exclusion, transport latency and financial accuracy remain unverified. Existing expected failures do not substitute for recovery acceptance tests.

## Primary finding decisions

APPROVED means the narrowly stated correction is technically justified; normal regression validation still applies. APPROVED WITH CONDITIONS means the diagnosis is valid but the listed design/compatibility conditions must be met. BLOCKED means the final behavior cannot be selected from available policy evidence; it does not reject the diagnosis.

| ID / severity | Independent evidence and diagnosis | Verdict and correction | Dependencies / compatibility risk | Required regression evidence / open policy |
| --- | --- | --- | --- | --- |
| TD01 High | S/T: [wait_for_update](../../market_data_ws.py:122) snapshots producer sequence on every call; [callback](../../market_data_ws.py:49) overwrites latest value. Existing desired-invariant test still fails. | **APPROVED WITH CONDITIONS:** separate display cache from ordered strategy consumption; track consumer progress and explicit overflow. | TD02; high risk from replaying old backlog as actionable prices. A cursor alone cannot recover overwritten ticks. | Before-read ticks, bursts during broker I/O, multiple symbols/consumer lifetime, overflow and generation changes. Q07 must specify freshness/overflow admission; do not blindly execute stale crossings. |
| TD02 High | S/T: [on_connect](../../market_data_ws.py:41) does not clear `_error`; waiter raises the old error after reconnect. | **APPROVED WITH CONDITIONS:** generation-aware recovery after demonstrated subscription/readiness and fresh data. | TD01 lifecycle; avoid clearing terminal auth errors or trusting old cache. | Transient error -> reconnect -> fresh tick; failed subscription/auth remains unready; old generation invalidated. Readiness contract needed, not a trading-rule change. |
| TD03 High | S/P: [_command_applies](../../live_trader.py:139) accepts all commands without explicit trade ID, including unrelated replies. | **APPROVED WITH CONDITIONS:** unique signal/reply-descendant mapping; ambiguity must not mutate arbitrary exposure. | TD05 identity; rejecting all non-original replies would break legitimate replies to amendments. | Two same-symbol setups, old/queued reply, explicit mismatched ID, valid descendant and ambiguity. Q15 governs unthreaded fallback. |
| TD04 High | S/P: [router](../../telegram_trade_listener.py:483) emits cancellation without invalidating assembly; [idle handling](../../live_trader.py:421) ignores controls. | **APPROVED WITH CONDITIONS:** terminal cancellation for the identified incomplete/queued signal. | TD03/05; never clear an unrelated setup. | Cancel before risk levels -> zero call/BUY from late chunks; duplicate cancel; independent new setup works. Q15 needed for ambiguous or post-entry CANCEL. |
| TD05 High | S: [queue drain](../../live_trader.py:283) turns calls into strings; [dispatch](../../live_trader.py:412) labels them `reentry`; [registry exclusion](../../live_trader.py:445) loses journaling. | **APPROVED WITH CONDITIONS:** preserve typed envelopes and stable identity on every order-producing path; durable queue follows schema design. | TD03/12; high scheduling/reentry risk; current queued call is not necessarily a reentry. | A/B/C retain distinct origin/revision/timestamps; B cancellation; queued fills journaled; no new entry before confirmed old closure. Q16 governs TTL/priority, Q17 retention. |
| TD06 High | S/P: [modify_entry](../../trading.py:665) performs partial replace without full invariants or range normalization. | **BLOCKED for final amendment semantics; diagnosis approved.** Use an atomic validated update or explicitly nontradable draft after policy selection. | TD08; [existing sequential-update test](../../tests/test_trading_safety.py:35) intentionally updates fields separately. | Invalid revised entry cannot remain armed; completed draft commits consistently; no partial mutation/nonfinite prices. Q02/Q14: staging, pending-order amendment and milestone rules. |
| TD07 Critical | S: [command dispatch](../../live_trader.py:291) calls throwing modifiers; [outer handler](../../live_trader.py:336) stops exposed trade; finally closes stream. | **APPROVED WITH CONDITIONS:** isolate typed command rejection and preserve last valid state/protection. | TD06 transactional validation; do not catch all ValueError/RuntimeError around broker work. | Confirmed exposure -> invalid amendment -> subsequent stop still exits exactly once. Broker/storage uncertainty must still escalate distinctly. Policy-changing valid amendments remain blocked. |
| TD08 Medium | S/P: [Telegram validator](../../telegram_trade_listener.py:288) and [execution validator](../../trading.py:70) disagree on equal ranges and SL boundary. | **BLOCKED for canonical boundary selection; diagnosis approved.** Share domain validation after policy decision. | TD06; blindly selecting either parser changes accepted signals. | Same boundary corpus through Telegram/manual/file adapters; same normalized model/rejection. Q02 determines equality, positivity and SL relationship. |
| TD09 High | S/T: [fill decoder](../../trading.py:259) calls `int` before validation; 1.9 truncates and bool can become an integer. | **APPROVED:** strict finite, integral, nonboolean quantity validation before mutation. | Small adapter change; preserve explicitly supported integral representations and cumulative semantics. No evidence FYERS actually emits fractional option fills. | Fraction/bool/null/NaN/infinity/negative/overfill; FILLED mismatch; repeated/regressing observations. Define accepted schema forms from contract fixtures, not permissive coercion. |
| TD10 High | S: [mode choice](../../live_trader.py:193) occurs once; [standalone CLI](../../trading.py:988) has the same sticky authority. | **BLOCKED for final session policy.** Separate permanent execution mode from submission-time entry permission. | Injected clock/calendar; never turn a paper executor live when time changes. | Pre-open/open/cutoff/holiday cases; exits independently permitted under Q19. Current 09:00–15:40 behavior is implementation evidence, not approved exchange policy. |
| TD11 Critical | S: [startup](../../live_trader.py:353), [standalone startup](../../trading.py:988), [registry](../../trade_lifecycle.py:125) lack ownership acquisition and broker recovery traversal. | **APPROVED WITH CONDITIONS:** first implement refusal/reconciliation gate plus single-host account execution ownership across both entrypoints. | Validated orders/trades/positions reads; do not mistake failed reads for flat. Local lock cannot exclude another host/manual trader. | Flat boot, foreign exposure, partial/pending order, unresolved legacy snapshot, missing/malformed snapshot, repeated recovery, second process. Gate holds ownership for executor lifetime. Automatic restoration/adoption remains outside first unit and subject to Q09. |
| TD12 Critical | S: [submit](../../trading.py:208) precedes [snapshot](../../live_trader.py:240); [update](../../trade_lifecycle.py:164) overwrites payload. | **APPROVED WITH CONDITIONS:** durable intent/attempt before dispatch, explicit unknown outcome, idempotent observation history. | TD11; versioned schema/migration and all execution paths. Approval is design direction, not approval of an unspecified migration. | Crash before/after commit/dispatch/ACK/fill; independent durable fake broker; no retry-as-new on ambiguity. Preserve legacy records, do not fabricate missing histories. Q17 retention; correlation capability needs verification. |
| TD13 Critical | S: [timeout loop](../../live_trader.py:313) retries indefinitely; [local exit](../../trading.py:536) needs working process/feed; no broker-held protective order created here. | **APPROVED WITH CONDITIONS** for separate health/admission/protection state; **BLOCKED** for automatic outage actions. | TD01/02/11/12 and bounded broker I/O. A startup gate does not protect an existing position during host death. | Feed silence, listener failure, auth/storage loss while exposed; truthful degraded state. Q07/Q18 specify age, deadlines, alerts and permitted actions; validate broker-held capability before proposing it as protection. |
| TD14 Medium | S: [API construction/fetch](../../app.py:121) and [endpoint](../../app.py:206) have no stream cleanup. | **APPROVED:** deterministic resource cleanup on success/failure. | Low scope; request-owned cleanup is sufficient initially. Shared lifespan adapter is optional, not mandatory redesign. | Repeated success/error, partial initialization, close failure preserving primary error; unchanged response contract. No production leak magnitude claimed. |
| TD15 High | S: [listener](../../telegram_trade_listener.py:559) starts at newest ID, uses memory dedup/NewMessage and newest-20 polling. | **APPROVED WITH CONDITIONS:** durable collection/disposition and paginated gap capture; **BLOCKED** for historical entry execution. | TD03/05/12; bounded ingestion, edit revisions and recovery semantics. New-only startup is intentional current behavior, not automatically a defect. | Gap >20, event/poll duplicate, restart, edits, partial fetch failure. Q01/Q16/Q20: stale entries, revisions, expiry; collecting history does not authorize trading it. |
| TD16 Medium | S: [manual input](../../auth.py:133) echoes credential input; [main](../../auth.py:237) prints raw profile/errors. | **APPROVED:** non-echo entry and allowlisted/redacted diagnostics. | Preserve usable interactive provisioning; SDK/error paths need coverage too. | Sentinel secrets absent in stdout/stderr/errors for success/failure; token values never enter audit lineage. No claim existing secrets were stolen or committed. |

## Architecture and FYERS crosswalk

These rows cover additional findings and qualify overlapping diagnoses. Severity differences across reports reflect plausible consequence, not measured probabilities. TD07/TD12 are retained at Critical because valid exposure or durable effect identity can be lost.

| Architecture finding | Severity / verdict | Source evidence, scope and required proof |
| --- | --- | --- |
| F01 | Critical / APPROVED WITH CONDITIONS | TD11; both CLIs must share the gate. Full restoration is a later gate. |
| F02 | Critical / APPROVED WITH CONDITIONS | TD13/07; [worker exception forwarding](../../live_trader.py:115) can also stop protection. Test ingestion isolation; outage action stays Q18-blocked. |
| F03 | High / APPROVED WITH CONDITIONS | TD01; explicit loss/freshness contract required. |
| F04 | High / APPROVED WITH CONDITIONS | TD03; preserve legitimate descendant replies. |
| F05 | High / APPROVED WITH CONDITIONS | TD04; cancellation belongs to identified signal lifecycle. |
| F06 | High / APPROVED WITH CONDITIONS | TD05; durable identity accepted, TTL value blocked on Q16. |
| F07 | Critical consequence / APPROVED WITH CONDITIONS | TD07; narrow typed rejection, no blanket exception suppression. |
| F08 | Critical consequence / APPROVED WITH CONDITIONS | TD12; durable attempt is not exactly-once remote execution. |
| F09 | High / BLOCKED | TD10/Q19; don't infer permission from clock alone. |
| F10 | High / APPROVED WITH CONDITIONS | TD15; collection and execution replay require separate decisions. |
| F11 | High / APPROVED WITH CONDITIONS | [ContractSpec](../../position_sizing.py:33) contains symbol/lot only; [metadata lookup](../../position_sizing.py:39) defaults missing activity to active; [resolver](../../trading.py:950) takes first strike/type match. Approve stricter versioned metadata and ambiguity reporting. Expiry/risk-budget/slicing rules blocked on Q03/Q05/Q09; test multiple expiries, tick/freeze constraints and missing metadata without live orders. |
| F12 | Medium / APPROVED WITH CONDITIONS | TD14 plus [health](../../app.py:186)/[raw HTTP errors](../../app.py:208). Cleanup/redaction approved. `/health` is valid as liveness; reject the interpretation that a constant response alone is a faulty liveness endpoint. Readiness/freshness contract needs Q21. |
| F13 | Medium / APPROVED WITH CONDITIONS | TD16 plus [token write](../../auth.py:90), [dependency ranges](../../requirements.txt), [ignore rules](../../.gitignore), contradictory historical runbooks. Approve scoped reproducibility/redaction/documentation work; test permissions/concurrent writes separately. Missing tracked service config does not prove missing external supervision. No deployment authorization. |

| FYERS finding | Severity / verdict | Independent assessment, dependencies and required validation |
| --- | --- | --- |
| FY01 | High / APPROVED WITH CONDITIONS | TD01, fresh expected failure. |
| FY02 | High / APPROVED WITH CONDITIONS | TD02, fresh expected failure. |
| FY03 | High / APPROVED | TD09, fresh expected failure. |
| FY04 | Critical consequence / APPROVED WITH CONDITIONS | Installed synchronous SDK POST/GET/DELETE and token exchange omit explicit timeout (source lines 105/164/214/700). Approve adapter-level bounded transport and unknown-write classification. A thread timeout does not stop an in-flight request and must not justify retry. Test accepted-but-response-lost separately from read timeout; deadlines depend on Q18. |
| FY05 | Critical / APPROVED WITH CONDITIONS | TD11/12. Ownership plus durable workflow, no inferred flat account. |
| FY06 | High / APPROVED WITH CONDITIONS | TD03–05, source and fresh reply/cancellation probes. |
| FY07 | High / APPROVED WITH CONDITIONS | [fill application](../../trading.py:486) enforces cumulative deltas, not full account reconciliation. Approve invariant/observation model; reject inventing a broker status-transition graph or rejecting every delayed status solely by arrival order. Test contradictions and delayed observations against supported contracts. |
| FY08 | High / APPROVED WITH CONDITIONS | [timestamp use](../../live_trader.py:320) reads generic `timestamp`; official SymbolUpdate now accessible and documents different fields. Add explicit schema/time normalization and redacted fixtures; don't infer time-unit/order guarantees from field names. Full refresh schema still unverified here. |
| FY09 | High policy dependency / BLOCKED | Q07/Q09/Q13/Q18 and accounting rules determine deadlines, ownership and outage actions. Specifications must precede behavior-changing acceptance tests. |
| FY10 | Critical / APPROVED WITH CONDITIONS | TD11 plus [exit quantity](../../trading.py:536) uses tracked allocation, not account snapshots. Second-process exclusion and manual-square-off tests required. Local lock does not solve manual/shared-account attribution. |
| FY11 | Medium / APPROVED | TD14/16 narrow cleanup/redaction. |
| FY12 | High / APPROVED WITH CONDITIONS | [journal](../../live_trader.py:240)/[registry update](../../trade_lifecycle.py:164) lack full event lineage. Add correlated events with TD12, not a speculative logging rewrite. Test orphan records, paper/live separation and unknown execution price. |
| FY13 | Medium reproducibility; High if incompatible execution schema / APPROVED WITH CONDITIONS | Local SDK 3.2.1 verified; EC2 parity not verified. Record tested versions/lock compatibility after runtime testing; no unsupported claim that all allowed 3.x releases behave identically. |

## Hypotheses and rejected overclaims

| ID | Verdict / consequence | Evidence and next required test |
| --- | --- | --- |
| H01 | APPROVED WITH CONDITIONS for investigation / High | [Registry](../../trade_lifecycle.py:148) locks per instance. Two-process barrier test must establish collision/lost-update behavior before claiming observed corruption. Primary key uniqueness does not fence broker execution. |
| H02 | APPROVED WITH CONDITIONS for investigation / High | [Unbounded command drain](../../live_trader.py:275) can delay ticks; no latency measurement. Use deterministic backlog and delayed broker fixtures before selecting batch limits. |
| H03 | APPROVED WITH CONDITIONS / Critical consequence | Static lack of explicit sync SDK HTTP deadlines now independently confirmed (FY04). Actual hang duration remains unmeasured; test transport deadlines rather than report a production hang. |
| H04 | APPROVED WITH CONDITIONS for investigation / Medium | [Fixed temporary token path/chmod after replace](../../auth.py:90) is visible. Supported-OS concurrent-writer and ACL tests still needed; no exposure incident asserted. |
| H05 | BLOCKED / High | [Target modifiers](../../trading.py:689) preserve milestone indices. Q14 must define intended rebooking; test approved semantics instead of resetting flags speculatively. |
| H06 | BLOCKED / High | [Entry buffer](../../trading.py:614) caps before rounding. Q08 must define pre/post-round cap before calling arithmetic a policy violation. |
| H07 | APPROVED WITH CONDITIONS for investigation / High | F11 metadata incompleteness confirmed; a particular broker rejection remains unproven. Use official contract fixtures and approved expiry rules. |
| H08 | REJECTED as a blanket causal conclusion / High | No aligned historical message/tick/fill trace establishes that all misses share one cause. Existing incident notes include fills, sizing skips and auth failures; these were not freshly verified. |

Also reject: describing 93 tests as 93 passing safety tests; treating ACK as fill or cancel ACK as terminal cancellation; asserting a tag guarantees broker idempotency; requiring order-update WebSockets to fix current REST polling; inferring nonexistent P&L arithmetic defects when no P&L engine exists; claiming a host lock excludes manual trades or other hosts; and claiming the 2% stop trigger caps realized loss.

## Official FYERS contract checks

Public documentation was checked on 2026-11-13; no authenticated account calls were made.

| Reference | Confirmed fact and limit |
| --- | --- |
| [Order by ID](https://fyers.in/api-docs/v3/api/trading/order-book/get-order-by-order-id/) | Query uses FYERS `id`, distinct from `exchOrdId`; current `orderbook(data={id})` matches this. No idempotent submission guarantee inferred. |
| [Webhook status/fill reference](https://fyers.in/api-docs/v3/api/trading/webhooks/webhook-response/) | Numeric statuses 1/2/4/5/6/7 align with local mapping. Filled and remaining quantities describe execution progress. This does not prove webhook and REST envelopes identical or all textual aliases valid. |
| [Funds](https://fyers.in/api-docs/v3/api/account/funds/) | `fund_limit` has titled ledger rows with equity/commodity amounts; title lookup is documented. Available Balance arithmetic is not order-specific margin approval. |
| [Symbol master JSON](https://fyers.in/api-docs/v3/api/market-information/symbol-master/symbol-master-json/) | Active/inactive trade status and contract metadata are documented. Missing activity cannot be treated as documented proof of activity. |
| [General socket](https://fyers.in/api-docs/v3/api/websockets/general-socket/overview/) | Separate account order/trade/position push events exist; adding them requires reconciliation/merge/dedup semantics. They are not an automatic recovery mechanism. |
| [SymbolUpdate](https://fyers.in/api-docs/v3/api/websockets/data-socket/data-socket-symbol-update/) | Schema includes `last_traded_time`; sample includes `exch_feed_time`, not generic `timestamp`. Distinguish data from connection/subscription message types. This closes the prior page-access gap, not full runtime schema certification. |
| [Auth-code exchange](https://fyers.in/api-docs/v3/api/authentication/individual-app/validate-auth-code/) | Auth-code exchange, hashed app credentials and access-token role confirmed. Full refresh contract, transport guarantees and active-session recovery were not independently certified. |

SDK source evidence refers to the installed `fyers_apiv3/fyersModel.py` (3.2.1), not a claim about all releases or remote EC2. No SDK source was modified. Do not retrofit hardcoded quotas, token lifetimes, visibility delays or retry guarantees from assumptions.

## Requirements, FMEA and data-contract disposition

The 33 requirements are a useful traceability inventory, not a signed acceptance specification. Preserve current floor sizing, no-chase crossing, target allocations, T3 ratchet and emergency threshold while fixing unrelated correctness issues. Historical approval provenance is incomplete; this review neither revokes earlier actual user authorization nor invents missing policy approval.

| Requirement group | Technical disposition |
| --- | --- |
| R01/R02/R08/R16–19/R21–24/R27 | Approve corrections subject to TD dependencies and named Q blockers; no blanket acceptance of current behavior. |
| R04/R06/R10/R11/R13/R15/R25/R29/R30 | Retain tested mechanisms; approval applies to exercised in-process cases, not complete recovery/protection or deployment compatibility. |
| R03/R05/R07/R09/R12/R20/R26/R28/R31/R33 | Policy/capability conditions remain; no expiry, risk, entry/session, outage, phrase-precedence or freshness rule selected here. |
| R14/R32 | Strategy optimality and complete historical attribution remain unverified; code tests cannot establish either. |

All FM01–FM22 are approved as relevant **test scenarios**, with the following limits. They are not 22 newly reproduced defects:

| FMEA IDs | Verdict and acceptance dependency |
| --- | --- |
| FM01–03 | APPROVED WITH CONDITIONS: typed read/write uncertainty and dependency-specific health; Q18 deadlines/actions. |
| FM04–06 | APPROVED WITH CONDITIONS: duplicate/reordered/stale observations; retain cumulative-fill safeguards, Q07 freshness. |
| FM07 | BLOCKED for final contract selection until Q03; metadata validation experiments approved. |
| FM08–10 | APPROVED WITH CONDITIONS: rejection/partial/delayed visibility; no false flat state or premature replacement; Q13/Q18 timing. |
| FM11–14 | APPROVED WITH CONDITIONS: crash/termination/storage/restart matrix; refusal gate before restoration. |
| FM15–16 | APPROVED WITH CONDITIONS: drift/duplicate processing; Q09 ownership, durable signal identity. |
| FM17–18 | APPROVED WITH CONDITIONS: clock/auth failure; Q19 sessions and Q18 active-exposure response. |
| FM19–20 | APPROVED WITH CONDITIONS: malformed/conflicting operations; strict validation, serialization and two-process tests. |
| FM21 | BLOCKED for accounting acceptance; missing ledger/cost-basis/fee/mark rules, not a confirmed P&L formula bug. |
| FM22 | APPROVED WITH CONDITIONS: complete origin/intent/observation chain or explicit missing evidence; Q17 retention. |

DI01–DI05, DI10–DI14, DI16–DI17 and DI19 are approved design targets conditional on schema and policy decisions, not existing guarantees. DI06–DI09 and DI15 retain useful tested arithmetic/fill/ratchet behavior while adding strict validation and recovery proof. DI18 is blocked for financial correctness until accounting rules and executions exist. DI20 redaction is approved. Proposed SourceMessage/SignalRevision/Intent/Execution/Projection contracts need concrete schema versioning, indexes, migration and atomicity review before implementation approval; accepting their direction does not approve every proposed field as mandatory forever.

## Approved implementation sequence, conditional on later user authorization

1. **Baseline and contracts:** preserve this commit, all 93 tests and existing artifacts; retain guarded test execution. Define versioned broker read/error contracts and ownership scope. No new dependencies are justified yet.
2. **Recovery containment (TD11):** shared gate for integrated and standalone entrypoints; acquire account-scoped single-host lock, inspect broker orders/trades/positions and unresolved local state, persist a truthful decision. Initial scope permits clean reconciled startup and refuses unsupported legacy/manual/unknown exposure. It must not attempt automatic restoration. A failed broker read never becomes empty exposure. Hold the lock throughout execution and test OS process death/release.
3. **Independent narrow corrections:** TD09 strict fill decode, TD14 deterministic cleanup and TD16 redaction need not wait for unrelated business policy. TD07 may proceed with narrowly typed rejection and atomic preservation of current valid state. Avoid bundling a reducer rewrite into these fixes.
4. **Durable identity and effects (TD12/05/03/04):** review schema/migration first; retain input/intent/attempt/observation identity across direct, queued, manual and reentry paths. Persist before dispatch; crash/reconcile without duplicate effects. Build reply descendants and terminal cancellation on this identity. Explicit ambiguity blocks mutation; unthreaded behavior remains Q15-dependent.
5. **Market delivery (TD01/02/FY08):** define generation, consumer ordering, explicit overflow and timestamp basis; fix desired-invariant tests. Freshness and live admission remain blocked until Q07 is approved. Do not replay historical backlog as current entry opportunity.
6. **Policy-dependent features:** TD06/08 domain amendments, TD10 sessions, TD13 outage responses, TD15 execution catch-up, instrument/risk selection and financial reporting follow explicit policy decisions with acceptance examples. Durable collection can precede permission to execute recovered entries.
7. **Consolidation and operational evidence:** extract only boundaries required by completed fixes; unify entrypoint guards, current runbook, dependency reproducibility, supported-runtime CI and redacted health/alerts. Automatic restoration, independent protection and deployment require their own completed validation gates and user authorization.

This refines the proposed order: no need to delay cheap independent fixes behind a full recovery rewrite; no need to wait for automatic restoration to add a refusal gate; durable identity is a dependency of robust reply/cancellation recovery; and both CLIs must be covered from the first execution-gating unit.

## File-level impact assessment

No changes below were implemented in this review.

| File/boundary | Intended scope and validation risk |
| --- | --- |
| [live_trader.py](../../live_trader.py) | Startup gate, typed queue, rejection isolation, lifecycle correlation. Integration tests must cover active exposure and queue/reentry sequencing. |
| [trading.py](../../trading.py) | Strict broker decode, shared startup protection, durable effect hooks; later approved validation/session rules. Preserve partial-fill/cancel/runner mechanics. |
| [market_data_ws.py](../../market_data_ws.py) | Ordered delivery, cache separation, generations and schema/time handling; consumer/API compatibility tests. |
| [telegram_trade_listener.py](../../telegram_trade_listener.py) | Identified assemblies, reply graph, cancellation, durable collection/revisions; preserve current replay fixtures. |
| [trade_lifecycle.py](../../trade_lifecycle.py) | Versioned transactional state/history and durable identities; legacy schema preserved or explicitly refused, never silently reinterpreted. |
| [position_sizing.py](../../position_sizing.py) | Later metadata port/strict contract fields; retain existing sizing arithmetic until policy changes approved. |
| [auth.py](../../auth.py) | Non-echo provisioning/redaction, separately reviewed token-storage/transport hardening; no credential migration in this task. |
| [app.py](../../app.py) | Resource ownership/cleanup and sanitized errors; readiness redesign only under an agreed contract. |
| [tests/](../../tests) | Reproducing regressions, broker/schema fixtures, process-lock and crash tests; preserve all baseline cases and transparent expected-failure history. |
| New recovery/ownership and durable-store modules | Justified small boundaries; filenames/schema to follow concrete implementation review, not an architectural rewrite mandate. |
| [requirements.txt](../../requirements.txt), [RUNNING.md](../OPERATIONS.md#strategy-and-order-behavior), [startTradeRunbook.md](../OPERATIONS.md#daily-operation), [HANDOFF.md](../history/HANDOFF.md) | Later tested dependency/runtime matrix and one current operating contract; archive superseded instructions without losing incident evidence. |

## Required test and rollback strategy

Each authorized unit must start with a meaningful failing regression, apply the smallest correction, run focused and related tests, then the full guarded suite. Remove an existing expectedFailure only when its desired invariant is actually met; no new markers or weakened expectations to disguise regressions.

Required acceptance cases: ACK/pending0 creates zero exposure; duplicate cumulative30 remains30; fill100 becomes100; cancel race with sold70 allows replacement30 only after terminal confirmation; sold100 permits no replacement; rejected exit never reports flat; cancelled incomplete setup cannot buy; wrong-thread command causes zero mutation; invalid amendment does not terminate healthy monitoring; unknown dispatch is not retried as new; broker read failure cannot enable READY; second executor cannot dispatch; every accepted effect has durable origin or explicit unresolved legacy evidence.

Use separate durable fake-broker state so simulated engine crashes cannot erase accepted orders. Inject failures around intent commit, attempt record, dispatch, acknowledgement persistence, fill persistence and projection updates. Test both CLIs, repeated recovery, stale lock/process death, manual activity and storage failure. Use virtual clocks and deterministic schedules for freshness/session/backlog tests; use approved values only. Test redaction with sentinel secrets and resource cleanup on success/failure. Preserve meaningful original tests; mocks must use the same confirmation contract as production adapters.

Release gates require no unexpected failures, no forbidden external/secret/state access, no duplicate dispatch/oversell/false flat, and every relevant policy blocker resolved. Record commit, runtime/SDK, fixture/seed, case counts, isolation result and unexecuted cases. Current test result is a baseline only; the proposed recovery/crash/compatibility suite has not been executed.

Rollback must be scoped by independent changesets. Preserve original working files and journal originals; use synthetic copied databases for migration/backup/restore tests. Reverting code must not discard or misread newer intent/event records: old binaries must refuse unsupported schema, or use a tested forward-repair/compatible migration. Never erase uncertain broker effects to make local state appear clean. No destructive reset, production database rollback or live restart is part of this review.

## Completion

Only this report was added. Application code, tests, configuration and broker/server state were not modified. No fixes, deployment or historical replay were performed. Technical review is complete; stop here as requested. Later implementation requires user authorization, with the policy-dependent items above remaining blocked until their rules are supplied.
