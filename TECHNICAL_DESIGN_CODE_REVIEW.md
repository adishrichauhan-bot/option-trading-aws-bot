# 4. Technical design and code review

Date: 2026-11-12. Baseline: `a4aac3a`. Documentation-only deliverable.

## Scope, evidence and severity

Compared [runbook](./startTradeRunbook.md), [running design notes](./RUNNING.md), [handoff](./HANDOFF.md), [requirements matrix](./FUNCTIONAL_REQUIREMENTS_AUDIT.md) and actual source/test paths. No formal approved detailed design is available; documented implementation choices are not automatically approved business rules.

Evidence labels: **R** = reproduced offline in this review; **P** = reproduced in the preceding audit; **S** = confirmed by the identified source/control path, not experimentally reproduced here. A confirmed code defect does not establish occurrence or financial impact in production. **H** hypotheses are segregated at the end.

Critical: possible duplicate/unmanaged exposure or inability to recover safely. High: wrong/missed execution or material lifecycle corruption. Medium: validation, operability or resource-management defects with conditional consequences. Severity reflects potential impact, not measured probability.

The Python fact-grounded coding skill informed this review. New probes used the existing local virtualenv and mocks, with no broker calls. Existing application/tests/configuration were not edited. Prior baseline: 85 tests passed in the initial audit; this section did not rerun that suite. Regression tests below are specifications, **not newly implemented or passing tests**.

## Documented design versus actual code

| Documented/intended design | Actual implementation | Finding |
| --- | --- | --- |
| Live WebSocket observations drive crossings/protection | Waiter discards unread updates received between reads | TD01 |
| Socket reconnect configured | Prior error remains latched after on_connect | TD02 |
| Controls update/cancel their trade | Unrelated reply accepted; incomplete cancelled setup survives | TD03/TD04 |
| Persistent IDs and order journal | Queued calls lose provenance and bypass registry | TD05 |
| Validated amendments | Domain invariants not revalidated; rejection can stop open-position monitoring | TD06/TD07 |
| Validate full calls before execution | Two parsers disagree on legal ranges and SL | TD08 |
| Confirm cumulative broker fills | Correct basic lifecycle; malformed fractional/bool quantities can be coerced | TD09 |
| Market session gates live orders | Mode chosen once when trade starts, not at order admission | TD10 |
| Recover without duplicated entries/exits (requested next task) | No startup reconciliation or exclusive executor ownership | TD11 |
| Lifecycle journal supports audit/recovery | Submission occurs before durable intent; snapshots omit complete history | TD12 |
| Stops protect active position | Protection requires live local process/ticks; timeouts have no stale-feed deadline | TD13 |
| Market-data API serves requests | Per-request sockets lack cleanup | TD14 |
| New messages consumed reliably | Poll fallback truncates to newest20; no durable cursor/revisions | TD15 |
| Credentials remain private | Interactive input/profile/raw error output are not consistently redacted | TD16 |

Some differences are policy ambiguities, not confirmed bugs: floor versus original ceiling requirement; inside-range versus upper-bound breakout; target equality; channel EXIT precedence; optimum trailing parameters. See open questions in the requirements matrix. Do not silently resolve them through a code fix.

## Confirmed defect register

### TD01 — Unread market events are silently discarded

1. **Finding ID:** TD01; evidence P/S.
2. **Affected requirement:** R07/R08/R13/R15: observation-dependent entry/stop/trailing.
3. **Module/function:** [wait_for_update](./market_data_ws.py:122), [_on_message](./market_data_ws.py:49).
4. **Evidence:** wait snapshots `_sequences[symbol]` at each invocation and requires a later sequence. Producer replaces one latest-value slot. Prior probe injected79 and84 before read; next read timed out.
5. **Root cause:** producer-time baseline is mistaken for last-consumed position; cache is used as an event stream.
6. **Trigger:** ticks arrive during broker requests, printing, journal writes or sleep; bursts arrive before consumer waits.
7. **Consequence:** observed crossing/high/stop event can disappear; downstream strategy cannot reconstruct it from later prices.
8. **Severity:** High.
9. **Correction:** bounded ordered event delivery with consumer cursor, explicit overflow/freshness semantics; separate display cache from strategy input.
10. **Regression test:** inject79->84 before read and during blocked consumer work; assert delivery under the approved event contract or explicit loss state, never silent absence. Verify trigger/stop events are not skipped.

### TD02 — Successful reconnect cannot clear a previous socket error

1. **Finding ID:** TD02; evidence R/S.
2. **Affected requirement:** R08/R28: feed recovery and continued monitoring.
3. **Module/function:** [_on_error](./market_data_ws.py:59), [_on_connect](./market_data_ws.py:41), [wait_for_update](./market_data_ws.py:122).
4. **Evidence:** new probe called `_on_error('temporary')`, then mocked `_on_connect()`; subsequent wait still raised RuntimeError, with `_error == 'temporary'`.
5. **Root cause:** error state is latched independently of connection generation and is never cleared by reconnect.
6. **Trigger:** a recoverable SDK error precedes a reconnect callback.
7. **Consequence:** reconnect=True does not restore wrapper usability; active engine may stop despite successful transport reconnection.
8. **Severity:** High.
9. **Correction:** explicit reconnect state machine; clear transient faults only on validated reconnect/subscription readiness, invalidate stale cache, retain terminal auth faults separately.
10. **Regression test:** error -> reconnect -> subscribed fresh tick becomes consumable; old cached tick is not treated as fresh; unsuccessful/auth-rejected reconnect remains not ready.

### TD03 — Reply correlation permits commands for another setup

1. **Finding ID:** TD03; evidence P/S.
2. **Affected requirement:** R18/R20/R17.
3. **Module/function:** [_command_applies](./live_trader.py:139).
4. **Evidence:** every branch without explicit internal trade ID returns True. Prior probe: source IDs1/2/3, EXIT replying999 -> True.
5. **Root cause:** single-active execution is incorrectly treated as proof that all conversation commands refer to that trade.
6. **Trigger:** delayed reply to old setup, command for queued setup, multiple setups in channel.
7. **Consequence:** wrong position can be exited or have entry/SL/targets changed.
8. **Severity:** High.
9. **Correction:** resolve durable reply descendants to signal identity; quarantine ambiguity; define unthreaded fallback separately with owner.
10. **Regression test:** two setups with same symbol but distinct reply roots; command for B must cause zero mutations/orders on active A. Known modification descendants of A must still resolve correctly.

### TD04 — Cancelled incomplete setup can later become tradable

1. **Finding ID:** TD04; evidence P/S.
2. **Affected requirement:** R19/R01.
3. **Module/function:** [_process_message](./telegram_trade_listener.py:482), [idle command handling](./live_trader.py:418).
4. **Evidence:** CANCEL is emitted and returned without invalidating assembler state; idle main ignores it. Prior probe instrument -> range -> CANCEL -> targets/SL emitted TradeCommand followed by TradeCallMessage.
5. **Root cause:** cancellation only operates on active execution, not the setup lifecycle.
6. **Trigger:** provider cancels before final risk chunk or while signal is waiting.
7. **Consequence:** late chunks resurrect cancelled intent and can cause entry.
8. **Severity:** High.
9. **Correction:** terminal cancellation keyed to signal/root before entry, including queue state; do not clear unrelated assemblies.
10. **Regression test:** cancelled pending setup produces zero BUY intents after late chunks; new independent setup still assembles; repeated cancel is idempotent.

### TD05 — Queued calls lose identity and bypass persistence

1. **Finding ID:** TD05; evidence S.
2. **Affected requirement:** R22/R23/R18.
3. **Module/function:** [queue drain](./live_trader.py:283), [pending dispatch](./live_trader.py:412).
4. **Evidence:** TradeCallMessage becomes `telegram_call_to_signal(event)` string; later dispatch sets trade_id='reentry', source IDs empty and registry=None.
5. **Root cause:** operational queue reuses a shorthand reentry mechanism instead of preserving typed signal envelopes.
6. **Trigger:** any complete new call arrives while another trade is running.
7. **Consequence:** normal queued calls have no unique journal, source timestamp/reply mapping or durable queue state; restart loses them.
8. **Severity:** High.
9. **Correction:** persist all accepted signals before scheduling and retain typed identity/revision/timestamps for every dispatch path.
10. **Regression test:** receive A then B while A active; B receives distinct persistent ID, retains source fields, journals fills, and can be cancelled by its own reply root before execution.

### TD06 — Entry amendment bypasses domain invariants

1. **Finding ID:** TD06; evidence R/S.
2. **Affected requirement:** R02/R16/R17.
3. **Module/function:** [modify_entry](./trading.py:665), [initial validation](./trading.py:70).
4. **Evidence:** probe amended a call with targets102/103/104 to range110–111; accepted state retained those targets and stored buy_above=None. Initial parser would reject targets below entry and normally normalizes buy_above to upper bound. Method validates only low>high for ranges, not complete levels/finite positive values.
5. **Root cause:** partial dataclass replacement bypasses canonical validation; multi-command update protocol has no explicit draft/commit boundary.
6. **Trigger:** entry amendment alone, invalid numeric values through direct API, or sequential amendments interrupted between fields.
7. **Consequence:** executable state can have targets below entry; normalized field assumptions break. Actual later crash depends on consumer, so no particular downstream TypeError is claimed as reproduced.
8. **Severity:** High.
9. **Correction:** atomic validated amendment, or explicit nontradable draft while required fields are incomplete; preserve normalized field invariants. Decide sequential amendment semantics explicitly.
10. **Regression test:** range110–111 with old targets102/103/104 cannot remain armed; valid completed levels112/113/114 commit together. Reject NaN/negative inputs; assert buy_above normalization and no order during draft.

### TD07 — Invalid command terminates management of valid open exposure

1. **Finding ID:** TD07; evidence S.
2. **Affected requirement:** R16/R17/R28.
3. **Module/function:** [command dispatch](./live_trader.py:291), [modifiers](./trading.py:665), [outer handler](./live_trader.py:336).
4. **Evidence:** MODIFY ENTRY after entry raises RuntimeError; invalid SL raises ValueError. Dispatch has no local rejection handler; outer handler converts exceptions with exposure to BrokerStateError, finally closes stream, and main returns.
5. **Root cause:** input rejection and exposure-integrity failure share the same exception boundary.
6. **Trigger:** stale/invalid amendment while position exists, including unrelated command accepted by TD03.
7. **Consequence:** otherwise valid open position loses local monitoring due to invalid input.
8. **Severity:** Critical.
9. **Correction:** validate commands transactionally and emit CommandRejected without changing valid state; preserve protection loop. Broker uncertainty remains a distinct class requiring reconciliation.
10. **Regression test:** open confirmed position, inject invalid amendment, then a stop tick; assert rejection logged, stream still active and exactly one correct exit lifecycle executed.

### TD08 — Ingress parsers accept incompatible level constraints

1. **Finding ID:** TD08; evidence R/S.
2. **Affected requirement:** R02.
3. **Module/function:** [Telegram parse](./telegram_trade_listener.py:288), [execution parse](./trading.py:70).
4. **Evidence:** new probes: range100–100/SL90 accepted by Telegram, rejected by execution; range100–105/SL102 rejected by Telegram, accepted by execution (targets110/120/130).
5. **Root cause:** duplicated validators encode different low/high and SL conditions.
6. **Trigger:** equal endpoints or SL between range boundaries; different file/manual/Telegram ingress.
7. **Consequence:** acceptance depends on interface; upstream completed signal can fail downstream. Which policy is correct remains Q02, but inconsistency is confirmed.
8. **Severity:** Medium.
9. **Correction:** one normalized domain validation contract after owner resolves legal boundaries; adapters only normalize syntax.
10. **Regression test:** table-driven equal/unequal/inverted ranges and boundary SL cases through every ingress; assert identical domain acceptance and normalized levels.

### TD09 — Broker fill validation silently coerces malformed quantities

1. **Finding ID:** TD09; evidence R/S; malformed-input robustness defect, not evidence Fyers actually sends fractional fills.
2. **Affected requirement:** R10/R11.
3. **Module/function:** [order_state](./trading.py:238).
4. **Evidence:** mocked pending response filledQty=1.9 returned filled_quantity=1; code uses int(row['filledQty']). Boolean values are also accepted by Python int conversion.
5. **Root cause:** coercion precedes validation of numeric type/integrality; bounds check cannot detect truncation.
6. **Trigger:** malformed SDK/broker fixture or incompatible schema with noninteger numeric fill.
7. **Consequence:** engine can accept a corrupted exposure count instead of flagging uncertainty.
8. **Severity:** High due to accounting role; production occurrence unverified.
9. **Correction:** validate finite, nonnegative, integral quantities and explicitly permitted representations; reject booleans/fractions before converting. Keep monotonic and submitted-quantity checks.
10. **Regression test:** reject1.9, True, NaN, infinity, negative and over-request values; allow valid integer and only documented integral string representations. Failure must not mutate applied fill or submit an exit.

### TD10 — Session permission is stale at eventual submission

1. **Finding ID:** TD10; evidence S.
2. **Affected requirement:** R26.
3. **Module/function:** [run_trade](./live_trader.py:193), [session window](./trading.py:340), [executor](./trading.py:198).
4. **Evidence:** live=is_live_trading_window() executes once; executor retains boolean while waiting indefinitely for trigger.
5. **Root cause:** setup-start eligibility is used as order-time authority.
6. **Trigger:** setup begins before open and triggers after open, or starts inside window and triggers after cutoff.
7. **Consequence:** first case remains paper, second can submit live outside documented window. Exchange rejection is possible, not guaranteed.
8. **Severity:** High.
9. **Correction:** explicit execution mode plus order-time admission using approved session/calendar policy; separate opening from closing exposure.
10. **Regression test:** injected clock crosses both boundaries between arming and submission; assert new entry follows approved current session, while mock stays paper and exits obey separate policy.

### TD11 — Requested restart reconciliation is absent

1. **Finding ID:** TD11; evidence S; confirmed missing capability.
2. **Affected requirement:** R24.
3. **Module/function:** [main](./live_trader.py:353), [registry API](./trade_lifecycle.py:125).
4. **Evidence:** startup creates registry and listener but performs no broker orders/trades/positions reconciliation or account executor lock. Registry exposes create/update/get only.
5. **Root cause:** lifetime of memory state is treated as lifetime of workflow; recovery and ownership were deferred.
6. **Trigger:** crash/reboot/restart or two independent instances with same broker account.
7. **Consequence:** no automatic restoration of protection or prevention of conflicting new orders. Specific duplicate fills not reproduced here.
8. **Severity:** Critical.
9. **Correction:** startup RECOVERING gate, account ownership and broker/durable-state reconciliation; quarantine unknown/manual exposure instead of auto-adoption/liquidation.
10. **Regression test:** flat boot, pending/partial entry, pending exit, unknown submit, foreign exposure, unavailable broker and second owner; only resolved authorized state allows new entry; repeated recovery is idempotent.

### TD12 — Persistence follows side effects and cannot reconstruct all executions

1. **Finding ID:** TD12; evidence S.
2. **Affected requirement:** R23/R24.
3. **Module/function:** [_place_order](./trading.py:208), [journal](./live_trader.py:240), [registry update](./trade_lifecycle.py:164).
4. **Evidence:** remote place_order occurs before tracked order append and subsequent journal; database overwrites a JSON snapshot. No durable pre-submit intent exists. Snapshot does not serialize full strategy state/history.
5. **Root cause:** observability snapshot is used where a durable workflow protocol is required.
6. **Trigger:** process/storage failure between broker acceptance and journal; command/fill transitions overwritten by later updates.
7. **Consequence:** accepted external effect can be missing locally; history and recovery are incomplete. Filesystem corruption is not asserted.
8. **Severity:** Critical.
9. **Correction:** pre-submit durable intent and correlation, append-only transitions/versioned projections; uncertain dispatch reconciled rather than retried. Remote API and SQLite cannot be one atomic transaction.
10. **Regression test:** inject crash after intent commit, after broker accept, before ACK save and after fill before snapshot. Recovery must preserve uncertainty/attribution and never resubmit as a fresh order blindly.

### TD13 — Feed silence can leave exposed position unmanaged indefinitely

1. **Finding ID:** TD13; evidence S.
2. **Affected requirement:** R08/R15/R28.
3. **Module/function:** [timeout branch](./live_trader.py:317), [local stop](./trading.py:536), [socket close](./market_data_ws.py:68).
4. **Evidence:** TimeoutError simply continues; local stop decision requires on_price; close callback only updates connected flag/prints. Order polling continues but does not replace absent price-based stop decisions.
5. **Root cause:** transport silence has no freshness deadline or protection-degraded state; local protection depends on fresh feed.
6. **Trigger:** feed silent/disconnected without fatal callback while exposure remains.
7. **Consequence:** no new stop decision, no defined escalation for silence; 2% protection cannot evaluate absent prices.
8. **Severity:** Critical.
9. **Correction:** feed-health state and approved outage/protection response; evaluate broker-held protection capabilities. Do not invent an automatic flatten rule without approval.
10. **Regression test:** confirmed exposure then repeated timeouts; at approved freshness boundary assert explicit degraded state, entry block and exact approved protection/alert response. No invented price and no duplicate exit.

### TD14 — HTTP market-data requests do not release sockets

1. **Finding ID:** TD14; evidence S.
2. **Affected requirement:** R31.
3. **Module/function:** [market_data](./app.py:192), [FyersMarketData.fetch](./app.py:132).
4. **Evidence:** each request constructs FyersMarketData/socket, subscribes during fetch, and returns or raises without stream.close/finally or application lifecycle ownership.
5. **Root cause:** long-lived resource is created in request scope without a release contract.
6. **Trigger:** repeated requests or exceptions after subscription starts.
7. **Consequence:** abandoned socket/thread resources may accumulate; actual growth and SDK garbage-collection behavior not measured.
8. **Severity:** Medium.
9. **Correction:** shared application-lifetime client/stream with bounded subscriptions and shutdown, or deterministic per-request cleanup including error paths.
10. **Regression test:** mock socket lifecycle over repeated success/error requests; assert bounded instances and expected close calls; then run a separate approved load/resource test.

### TD15 — Poll fallback cannot recover a message gap beyond its last20 window

1. **Finding ID:** TD15; evidence S.
2. **Affected requirement:** R01/R27.
3. **Module/function:** [_listen](./telegram_trade_listener.py:559).
4. **Evidence:** get_messages(limit=20) then latest_id advanced while iterating newest batch; no pagination from prior cursor. Only NewMessage handler; seen IDs and baseline are memory-only.
5. **Root cause:** bounded recent sample is treated as complete gap recovery; source revision/checkpoint protocol absent.
6. **Trigger:** event delivery unavailable/delayed for more than20 new messages and polling fallback used.
7. **Consequence:** older unseen messages are skipped permanently by that cursor. This does not imply routine loss when live events delivered them successfully. Edits likewise have no defined handling.
8. **Severity:** High.
9. **Correction:** durable cursor/inbox, paginated recovery with explicit stale-entry policy, revision handling and idempotency. Do not replay historical calls as live orders merely to fill a gap.
10. **Regression test:** seed cursorN, disable events, supply25 messages; assert all25 get a recorded disposition, no duplicate event/poll processing, and stale entries never execute without approved policy.

### TD16 — Interactive authentication bypasses consistent secret redaction

1. **Finding ID:** TD16; evidence S.
2. **Affected requirement:** R25/R28 and documented credential privacy constraints.
3. **Module/function:** [manual_auth_code](./auth.py:133), [main](./auth.py:237), [refresh_access_token](./auth.py:210).
4. **Evidence:** input() echoes full pasted redirect/code; main prints raw profile and refresh exception; refresh exception can embed full response. Noninteractive trading refresh has explicit redaction, showing inconsistent paths.
5. **Root cause:** interactive convenience output is outside the redaction boundary.
6. **Trigger:** manual login in retained/shared terminal scrollback, or error response containing sensitive fields.
7. **Consequence:** credential/account data may appear in captured console history/logs. No new credential exposure or theft was performed/proven in this audit.
8. **Severity:** Medium.
9. **Correction:** non-echo credential entry, structured allowlisted errors/profile summary and uniform redaction across interactive/noninteractive paths.
10. **Regression test:** inject sentinel secret values in auth inputs/responses/exceptions and capture stdout/stderr; assert no sentinel appears, while operator receives usable sanitized failure reason.

## New offline probe results

These used existing test helpers, mocked socket/client and paper executor, imported through Python stdin; no persistent tests were added.

| Probe | Input/action | Observed result |
| --- | --- | --- |
| TD02 | Error='temporary', then on_connect, then wait | RuntimeError; error still 'temporary' |
| TD06 | Amend entry to110–111 with targets102/103/104 | Accepted; buy_above=None; old targets unchanged |
| TD09 | Mock pending order filledQty=1.9 | Returned integer fill1 rather than rejection |
| TD08a | Range100–100, SL90, targets110/120/130 | Telegram accepts; trading parser rejects |
| TD08b | Range100–105, SL102, targets110/120/130 | Telegram rejects; trading parser accepts |

TD01/TD03/TD04 offline reproductions are recorded in the [earlier audit](./ARCHITECTURE_RELIABILITY_AUDIT.md). They were not repeated in this section. No captured production logs are claimed as fresh evidence here.

## Hypotheses and unresolved risks — not confirmed production defects

| ID | Hypothesis / source concern | What is known versus unproven | Verification needed |
| --- | --- | --- | --- |
| H01 | Cross-instance registry races / logical lost updates | [create/update](./trade_lifecycle.py:148) use instance-local locks and select/merge/write; primary key prevents duplicate stored IDs, not broker activity. Exact concurrent outcomes not executed. | Two-connection/process barrier test; verify transaction semantics, conflict/error behavior and retry policy. |
| H02 | Backlog starves market evaluation | Unbounded queue drained before ticks; sustained arrival can delay consumer. No load/latency measurement. | Deterministic producer flood with injected clock and broker latency; measure stop-decision delay under bounded workload. |
| H03 | SDK transport blocks beyond acceptable protection latency | Synchronous calls share trade loop; SDK internal timeouts/retries not comprehensively inspected. | Inspect pinned SDK transport, timeout injection and measured deadlines; do not assume there are no SDK timeouts. |
| H04 | Token-cache writer race or permission exposure | Fixed .tmp path and chmod after replace visible in [write_tokens](./auth.py:90); actual concurrent writes and OS permissions not tested. | Concurrent writers and restrictive-permission tests on supported OS; inspect umask/ACL without disclosing secrets. |
| H05 | Duplicate target milestone behavior after target amendments | targets_hit/target_exits_sent indices remain while levels change; intended re-book/reset behavior unspecified. | Resolve Q14, then model-based transition tests. Do not invent target-reset policy. |
| H06 | Tick rounding violates intended entry buffer cap | Code caps raw buffer then rounds up; whether cap applies post-round is unspecified. | Resolve Q08 and add boundary fixtures; arithmetic is visible, policy violation not yet established. |
| H07 | Exact symbol/expiry/freeze constraints produce rejected orders | Resolver metadata/constraints incomplete, but current instrument rejection not proven by a new broker trace. | Redacted production request/response fixture and approved expiry/limit rules; no live diagnostic orders. |
| H08 | Current incidents all caused by tick loss or parsing | Defects can explain some misses, but prior evidence includes actual fills and sizing skips. | Timestamp-aligned source/reply/tick/order/process traces for each call. No blanket causal attribution. |

## Coverage of requested investigation areas

| Area | Findings / limits |
| --- | --- |
| Incorrect algorithms and logic | TD01/TD03/TD04/TD06/TD08/TD10/TD15 |
| Race conditions and async execution | TD01/TD02/TD15; H01/H02/H04 require targeted interleaving tests. Cancellation-fill races already have useful baseline tests. |
| Inconsistent types / API assumptions | TD06 None normalization, TD09 int coercion, TD08 parser contract mismatch. No unsupported claim that broker actually emits fractional fills. |
| Exception gaps | TD07/TD13/TD16; journal error handler can itself fail writing error status, identified statically in prior architecture review. |
| Resource leaks | TD14; production growth unmeasured |
| Invalid transitions / duplicate processing | TD04/TD06/TD11/TD12; revision/restart dedup incomplete in TD15 |
| Data corruption risks | TD09 exposure accounting, TD12 lost durable intent; H01/H04 logical write conflicts. No evidence of physical database corruption. |
| Missing validation / configuration | TD06/TD08/TD09/TD10; import-time environment coupling noted in [architecture review](./SOLUTION_ARCHITECTURE_REVIEW.md). Actual secret configuration deliberately not printed. |
| Restart/recovery | TD11/TD12/TD13 |

## Correction order and verification gate

1. Establish startup reconciliation and execution ownership (TD11), with durable submission intent foundation (TD12).
2. Correct event loss/reconnect (TD01/TD02) and identity/cancellation/queue defects (TD03–TD05).
3. Make amendments atomic/validated and command rejection nonfatal (TD06–TD08); harden fill decoding (TD09).
4. Correct session admission and implement approved outage behavior (TD10/TD13); improve ingestion recovery (TD15).
5. Close API resources and sanitize authentication (TD14/TD16); execute hypothesis-specific tests before upgrading H findings to confirmed defects.

For each implementation: preserve original tests; first add a meaningful failing regression, apply correction, run focused and full suites with isolated journal and mocked Fyers. Review emergency cancellation/partial-fill behavior after execution-path changes. Do not automatically deploy, restart or replay calls as part of this documentation-only review.
