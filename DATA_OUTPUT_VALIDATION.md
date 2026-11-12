# 6. Data and output validation

Date: 2026-11-12. Source baseline: `a4aac3a`. Documentation-only review.

## Assessment and evidence boundary

**End-to-end output traceability is not currently established.** Useful parsed-call, sizing, order and position outputs exist, but identities disappear on queued/manual paths, journal snapshots overwrite history, and prices printed for decisions are not execution prices. Outputs cannot reliably reconstruct every trade or substantiate financial results after a failure.

This assessment uses inspected source and previous audit evidence. No fresh production records, statements or API output captures were obtained and no tests were rerun in this section. Prior evidence includes 85 passing tests and reproduced tick-loss, reply/cancellation, parser inconsistency and malformed-fill defects. Those results do not certify every output.

The Python fact-grounded coding skill informed source-based conclusions. Proposed contracts/invariants below are design and acceptance targets, not implemented features. Unresolved business rules remain in the [requirements matrix](./FUNCTIONAL_REQUIREMENTS_AUDIT.md); related defects are in the [technical review](./TECHNICAL_DESIGN_CODE_REVIEW.md).

## 1. Output inventory and lineage assessment

| Output | Source and transformation | Correctness/completeness assessment | Traceability gap |
| --- | --- | --- | --- |
| Parsed Telegram call JSON | [assembler/parser](./telegram_trade_listener.py:87): normalized text -> instrument, entry, targets, SL; message metadata attached | Supplied formats tested; two validators disagree; one assembly context can mix conversation intent | Source IDs are available initially, but individual source timestamps/revisions/reply graph and parser version are not durably retained |
| Control-command JSON | [parse_trade_command](./trade_lifecycle.py:42): regex recognition and numeric extraction | Explicit formats supported; broad wording and unrelated-reply acceptance can make valid-looking output semantically wrong | Printed command is not proof it targeted the correct signal or was applied; no durable accepted/rejected event chain |
| Preflight/mode/resolved symbol | [run_trade](./live_trader.py:168), [resolver](./trading.py:950) | Preflight quantity is intentionally pending calculation; selected mode can become stale; expiry policy incomplete | No uniform configuration/metadata snapshot or version attached to each subsequent output |
| ENTRY WAIT and rejected/skipped messages | First tick/validation/funds outcome -> console reason | Helps explain some non-entry behavior; not a complete per-signal disposition ledger | Missing durable queued identity and structured reason taxonomy; absence of a print cannot prove no decision occurred |
| Sizing result JSON | [FundsSizer](./position_sizing.py:103): funds, reserve, selected entry price, lot and cap -> quantity/estimated premium | Decimal arithmetic and affordable whole-lot tests exist. Estimate is not fill cost/P&L; initial funds errors can occur before last_result exists | Funds observation time/response reference and full effective policy are not recorded; no uniform signal/intent ID in sizing output |
| ORDER SUBMIT/LIVE ORDER PAYLOAD | [executor](./trading.py:208): proposed action -> broker request | Printed before broker acknowledgement; does not prove acceptance or fill | Missing universal intent/signal correlation and durable pre-dispatch record; console loss loses evidence |
| Broker order states/fills | [order_state](./trading.py:238), [fill application](./trading.py:486) | Cumulative delta accounting is sound for tested normal cases; fractional fill coercion is a confirmed defect | Execution prices, fees, broker execution IDs and event chronology are not a complete persisted ledger |
| ACTION reason/quantity/price | [strategy action](./trading.py:358), [action print](./live_trader.py:328) | Represents decision/submission semantics, not an exchange execution. Price can be entry limit, observed LTP, or zero/sentinel context for some exit paths | No explicit price_kind/confirmation field; readers can mistake request/action for filled trade |
| Tick/active-SL/remaining output | [trade loop](./live_trader.py:323) | Remaining reflects last observed confirmed fills, not continuously reconciled account position; ticks can be lost | No uniform exchange/receipt/decision timestamps, source sequence or broker-as-of time |
| SQLite trades row | [TradeRegistry](./trade_lifecycle.py:125), [snapshot](./live_trader.py:240) | Last-state record, not full audit/event history. ACTIVE covers multiple states; CLOSED may include rejected/no-fill entry | Queued/manual/reentry bypasses; provenance reduced; no exact reconstruction after submission/persistence crash window |
| Completion/error console output | [main](./live_trader.py:448) | Mock-incomplete distinguished, but generic trade completed/closed does not itself mean executed profitably or even filled | Needs explicit outcome and confirmed quantities; error-status persistence can fail too |
| HTTP market-data JSON | [fetch](./app.py:132): chain -> ATM strike -> streamed quotes | fetched_at is created before fetch finishes; returned values need not share one market instant; cache age not enforced; absent fields may be null | No per-field source/as-of/age or metadata version; cannot infer freshness from response timestamp alone |
| HTTP /health and error responses | [health](./app.py:186), [endpoint](./app.py:192) | health means API process response only; raw exceptions forwarded as502 detail | No trading-engine readiness/protection evidence; possible information disclosure in errors |
| Authentication/login diagnostics | [auth](./auth.py:237) | Readiness is valid at check time, not indefinitely; raw profile/errors and echoed input weaken privacy | No durable redacted readiness lifecycle; credentials must never be lineage payloads |
| Token cache and symbol-master cache | [token writes](./auth.py:90), [master loader](./trading.py:882) | Operational artifacts, not trade reports; token replace used, master cache date/source freshness limited | No uniform schema/version/integrity manifest; secret cache must stay outside general audit export |
| P&L/statements/performance report | No implemented accounting/report-generation engine identified | **Missing/not verifiable**, not a proven arithmetic P&L bug. Estimated premium and channel progress are not financial reports | Actual execution prices/charges/cost basis and complete attribution absent |
| Human-written audit/handoff reports | Repository/chat/source evidence -> Markdown assessments | Useful secondary evidence; historical notes contain superseded defaults and test counts | Must identify reviewed commit, evidence date and tested-versus-proposed claims; not a substitute for raw execution evidence |

## 2. Validation across requested dimensions

| Dimension | Current finding | Required validation/control |
| --- | --- | --- |
| Input validation | Entry parsers disagree on equal endpoints and SL within range; amendment bypasses full domain validation; fractional filledQty truncated | One domain validator; strict finite/integer/schema checks; immutable raw input plus explicit rejection reason |
| Transformations | Normalization removes decorative content; concatenation and reentry assembly infer context; queued conversion loses fields | Version every transform; preserve raw source references and field-level provenance; ambiguity must not become inferred executable intent |
| Calculation accuracy | Whole-lot Decimal sizing covered; price/ATR/strategy use floats; pending fill counts tracked; no financial ledger | Exact monetary/tick policy, deterministic calculation fixtures, distinguish requested/estimated/executed values |
| Timestamp consistency | Telegram ISO dates, UTC API timestamp, naive local DB dates, wall-clock ATR fallback and monotonic poll clock coexist | Store explicit UTC instants with source/receipt/processing meanings; monotonic elapsed time separately; explicit units/timezone and clock-health policy |
| Identifier uniqueness | trades PK unique in DB; date/sequence generated per instance; broker IDs tracked; manual/reentry labels reused | Durable signal/intent/event identities scoped by account/source; primary key alone is not executor fencing or business idempotency |
| State consistency | Fill deltas guard negative/regressing counts; many flags and snapshots lack formal transitions; external positions not reconciled | State transition schema and invariant checks; separate order/position/signal outcomes and protection status |
| Duplicate records | Same-process seen_ids and cumulative-fill delta reduce duplicates | Durable source-revision and execution-event dedup; explicit intentional-new-signal policy; no dedup by identical text alone |
| Missing records | No durable inbox; queued/manual journal gaps; side effects before persistence; mutable snapshots | Durable accepted/rejected dispositions and pre-submit intents; completeness checks and explicit UNKNOWN gaps |
| Reconciliation accuracy | Tracked order polling only, not account-wide/startup reconciliation | Independently compare intent/order/execution/position ledgers and broker snapshots with as-of times and unresolved discrepancies |
| Report generation | No realized/unrealized P&L report; operational console and API outputs not certified financial reports | Define reporting scope/cost basis/fees/marks; produce provisional/incomplete status when inputs missing |
| Logging completeness | Mixed prints and JSON; some outputs omit correlation/timestamp; raw auth/error data | Structured redacted events with required identity/time fields; retention, delivery-failure detection and access control |

## 3. Calculation contracts and limitations

### Sizing and entry pricing

Current floor sizing: `B = available_funds - reserve`; `q = min(floor(B/(P*L))*L, floor(max_quantity/L)*L)`. Entry rejected when budget cannot cover a lot. P is rounded buffered limit price for range entries, current LTP for market entries. The latter cannot guarantee realized affordability after slippage/fees.

Worked oracles, not new test results:

- B=10000, P=94, L=20, loose cap -> q=100 and estimated premium9400.
- Funds10000, reserve1000, P=100, L=20 -> q=80 and premium8000; cap70 reduces q to60.
- Explicit ceil for10000/94/20 selects120 before affordability rejection; 11280 exceeds10000. Whether floor remains approved business policy is Q04, not inferred from arithmetic.

Range buffer is `min(max(1, 0.5*ATR), 0.02*LTP)`, then limit rounded upward to configured tick. Post-round cost may exceed the pre-round 2% buffer cap; cap interpretation remains Q08. Contract tick metadata should replace an unqualified global tick assumption. Persist price basis and rounding decisions so a reviewer can recompute the exact request.

### Targets, trailing and ATR

Target allocations: first `floor(Q*0.5/L)*L`, second `floor(Q*0.3/L)*L`, runner remainder. For Q100/L20 ->40/20/40; for Q20/L20 ->0/0/20. These are mechanical outputs, not a claim the percentages are achieved exactly.

Runner stop: `max(previous_stop, T3, high - max(high*trailing_fraction, ATR*ATR_multiple))` on new highs. Default fraction0.03, multiple2. No guarantee of a safe/optimal realized outcome follows from formula correctness.

[OneMinuteATR](./trading.py:365) computes a simple mean of retained completed true ranges, including fewer than period bars during warm-up. First true range is high-low; subsequent values use prior completed close. It is **not automatically equivalent to a charting platform's Wilder-smoothed ATR**. No supplied requirement selects that alternative. Bars derive only from ticks actually processed; loss/coalescing changes bar highs/lows. Any different minute bucket, including an older one, finalizes a bar; out-of-order timestamp policy is absent. No completed empty bars are manufactured for time gaps.

Required ATR lineage: price-event IDs or immutable bar inputs, timestamp basis, window definition, period, warm-up state, gap/order policy and algorithm version. Without these, historical trailing/limit calculations are not exactly reproducible.

### Fills and monetary reporting

For a tracked order, delta = latest cumulative filled quantity - already applied quantity; buy delta increases and sell delta decreases remaining. Duplicate cumulative values contribute zero. Fractional/bool coercion must be rejected before arithmetic. Broker cumulative quantity is not the same thing as an execution-price ledger.

No implemented realized P&L formula is being validated. A future long-only ledger could calculate realized proceeds minus attributed entry cost minus applicable charges **only after** approving allocation/cost-basis/fee rules. Unrealized P&L additionally needs a mark source/time and remaining cost basis. These are specification dependencies, not invented policies. Reports must not substitute submitted limit, LTP, channel progression or estimated premium for actual fills.

## 4. Proposed versioned data contracts

Fields below are proposed required data, not claims about today's schemas. Use explicit nullability: null means unknown/not applicable with a reason, never silently zero. Reject nonfinite numbers. Serialize monetary values as canonical decimal strings or defined integer ticks/minor units; quantities are strict integers with documented units. JSON should reject NaN/Infinity rather than emit nonstandard numeric values. Required metadata omissions yield validation failure, not implicit active status.

| Contract | Required identity, content and lineage | Validation and semantics |
| --- | --- | --- |
| SourceMessage v1 | source_system, chat_id, message_id, revision, reply_parent, source_at, received_at, raw_text reference/hash, ingestion_event_id | Unique(source,chat,message,revision); retain raw content securely; edits are distinct revisions; missing source time explicitly flagged |
| SignalRevision v1 | signal_id, revision, source_message_refs, instrument fields, entry_kind/levels, targets, SL, parser_version, policy_version, status/reason | Complete approved domain validation; field provenance identifies which message supplied/replaced each value; cancelled revision cannot reactivate implicitly |
| CommandDecision v1 | command_id, source_ref, target_signal_id/revision or ambiguity, action, parsed values, accepted/rejected reason, state_version | No command application without unique target; validation rejection has no trading-state mutation; application event links prior/new revision |
| InstrumentSnapshot v1 | snapshot_id, symbol, exchange, underlying, strike/type/expiry, lot_size, tick_size, activity, applicable quantity constraints, source/as_of | Exact identity; positive integral lot; valid tick; approved expiry; unknown required constraints block dependent admission |
| MarketEvent v1 | event_id, symbol, exchange_at if supplied, received_at, source_sequence if supplied, connection_generation, price/quote fields, validity | Finite positive valid LTP; preserve time unit; freshness/order/loss policy explicit; lack of upstream sequence not fabricated |
| SizingDecision v1 | decision_id, signal_revision, instrument_snapshot, funds_observation/ref/as_of, reserve/cap/rounding, price_event/ref/price_kind, resulting quantity, estimated_premium, reason | Recomputable exact arithmetic; valid lot multiple; requested cash estimate within policy; skip/error persisted even when funds cannot be read |
| OrderIntent v1 | intent_id, account_alias, signal_revision or manual origin, action/reason enum, symbol/product, side/type, qty/limit/tick, risk/config versions, created_at, causation_refs | Durable before dispatch; unique action identity; explicit LIVE/PAPER mode; ACK/fill distinct; unknown dispatch recorded |
| OrderObservation/Execution v1 | broker_order_id scoped by account, intent_id, broker status, cumulative_fill, execution ID/price/qty if available, broker_at/received_at, response reference | Strict schema/integrality/bounds; cumulative monotonic unless explicit correction; execution IDs deduped; absent execution price remains unknown |
| PositionProjection v1 | account/product/symbol, allocation_id, attributed buys/sells, open_qty, cost_basis if established, broker_as_of, local_version, reconciliation/protection status | Derived from ledger, not editable authority; no negative bot allocation unless explicitly supported strategy; external/manual allocation distinct |
| ReconciliationResult v1 | run_id, input snapshots/as_of, intent/order/execution/position references, comparisons, discrepancies, resolution evidence, admission decision | Failed broker request != zero position; unresolved differences stay explicit; no forced balancing by invented trades |
| AuditEvent v1 | event_id, aggregate_id/version, event_type, causation/correlation IDs, occurred_at/recorded_at, producer/build/config version, payload schema version | Append-only authoritative history with idempotency; corrections append; payload redacted; delivery/persistence failures detectable |
| ReportManifest v1 | report_id, account scope, period/as_of, input ledger versions, method/config version, completeness/reconciliation state, calculation/output hash | Every figure drillable to inputs; incomplete report labelled and missing fields enumerated; paper/live never combined silently |
| APIQuoteResponse v1 | response_id, requested_at/completed_at, symbol and per-value source/as_of/age, missing-field reasons, metadata version | Response time not quote time; no assumption of atomic cross-symbol snapshot; explicit stale/unavailable result |

Protected token-cache contract is separate: no token values, redirect codes, PINs or secrets in AuditEvent/ReportManifest. Record only redacted credential-version/reference and readiness outcome where operationally necessary. Audit lineage does not require public exposure of raw personal/channel data.

## 5. Mandatory invariants and current support

| ID | Invariant | Current support / gap | Verification oracle |
| --- | --- | --- | --- |
| DI01 | Every output has stable identity, schema version, source/causation and rule/config version | Missing broadly | Reject orphan generated records; traverse each output to source or explicitly documented system/manual origin |
| DI02 | A source revision is accepted once; intentional revisions/new calls remain distinguishable | Memory-only dedup | Duplicate event+poll+restart changes accepted count by zero |
| DI03 | Normalized signal is invariant across ingress adapters | Confirmed parser disagreement | Same domain input yields same accepted model/rejection via Telegram/file/manual |
| DI04 | Transformations do not discard execution provenance | Queued string conversion violates | Queue/dequeue preserves all IDs, revisions and source fields |
| DI05 | Executable amendments leave complete valid state, or explicitly nontradable draft | Amendment defect | No order when revised entry/targets/SL inconsistent |
| DI06 | q>0, q integral and q%lot=0; estimated spend obeys approved sizing policy | Normal sizing tested | Boundary/decimal/reserve/cap fixtures recompute exactly; failures produce zero intents |
| DI07 | Acknowledgement does not change confirmed exposure | Implemented/tested within run | ACK/pending0 keeps remaining0; only confirmed fill changes it |
| DI08 | Duplicate cumulative fill has zero delta; malformed/regressing fill does not silently mutate | Duplicate/regression safeguards; malformed coercion gap | Replay30,30,100 ->100; reject1.9/bool/NaN before state update |
| DI09 | Attributed open quantity = buy fills - sell fills; outstanding exit allocation cannot oversell | Partial in-process safeguards; account reconciliation absent | Cancel/fill races, external changes and restart cannot create unsupported sell |
| DI10 | Cancelled/expired signal cannot become active through late chunks alone | Cancellation defect | Cancel before final risk levels -> zero activation from late chunks |
| DI11 | Order external effects have durable intent and uncertain outcomes remain unknown | Missing pre-submit durability | Crash at every boundary leaves resolvable intent or explicit unknown, no fabricated failure/success |
| DI12 | Every trade path is journaled; projections can be rebuilt from history | Queued/manual gaps and snapshots | Rebuild same final state for direct/queued/reentry/manual/PAPER and LIVE separately |
| DI13 | Timestamps have explicit meanings/units; elapsed deadlines use monotonic clock | Mixed bases | Clock-step/unit/timezone tests; no use of response timestamp as quote timestamp |
| DI14 | ATR/trailing output reproducible from versioned accepted event/bar sequence | Event loss and absent historical inputs | Replay identical accepted inputs gives identical indicators/decisions; missing inputs mark incompleteness |
| DI15 | Trailing stop never decreases after activation under current policy | Tested strategy behavior | New highs/retracements and invalid downward amendment preserve ratchet |
| DI16 | CLOSED/REJECTED/CANCELLED/NO_FILL are distinguishable; zero allocation is not account-flat proof | General CLOSED conflates outcomes | Rejected entry records rejected/no-fill; completed exit has terminal fills; account reconciliation separately reported |
| DI17 | Missing data is not zero; failed broker query is not empty account | Some strict sizing checks, no recovery gate | Malformed/missing inputs cannot produce invented funds/price/fills or READY |
| DI18 | Reported actual cost/P&L derives only from attributed executions and approved charges/method | No P&L ledger/report implemented | Independent multi-fill accounting fixture and broker statement comparison under approved method |
| DI19 | Audit corrections preserve original evidence and append rationale | Mutable snapshots only | Revision history survives corrections and duplicate replay |
| DI20 | Credentials never appear in logs/reports/source references | Interactive auth redaction gaps | Sentinel secret scan of all output/error paths, with private storage/access controls separately tested |

## 6. Required end-to-end lineage

```text
Source message revision(s) / explicit manual origin
  -> parser/validator version -> SignalRevision
  -> InstrumentSnapshot + accepted MarketEvent + FundsObservation + policy
  -> SizingDecision + strategy state/version -> OrderIntent
  -> broker acknowledgement -> OrderObservation / execution ledger
  -> position/protection projection -> ReconciliationResult
  -> operational outcome or financial report with ReportManifest
```

Every command amendment introduces a linked revision; every stop/target exit links the triggering price/state event and active policy; every skipped/rejected signal has a terminal or waiting decision reason. Non-trading outputs also need appropriate lineage: health -> component observations, quotes -> actual quote events, auth readiness -> redacted validation event.

Example acceptance case (synthetic, not a real transaction): message rootM1 plus risk chunkM3 produce signalS1 revision1; metadataI1 supplies lot20; tickT8 and fundsF1 produce sizingZ1; intentO1 references S1/I1/T8/F1/Z1; brokerB1 confirms cumulative20 then40; positionV2 has40. Replaying the20 update does not change40. A report lacking execution prices may report confirmed quantity40, but must not claim realized cost or profit. If a source or response was never captured, lineage says unknown rather than reconstructing an invented value.

## 7. Reconciliation and record completeness

Reconcile separate layers, not one generic status:

1. **Input completeness:** source cursor coverage, revisions, assembled/cancelled/rejected/queued decisions; gaps explicitly flagged.
2. **Intent completeness:** every dispatched request has one durable origin and desired action; unknown submit outcome tracked.
3. **Order completeness:** broker IDs/statuses mapped to intents; duplicate observations not duplicate actions; foreign orders separately identified.
4. **Execution completeness:** cumulative quantity agrees with attributed execution rows or is marked quantity-only pending execution details.
5. **Position completeness:** aggregate execution ledger compared with broker position snapshot by account/product/symbol and as-of time. Manual allocations are not silently absorbed.
6. **Financial completeness:** entry/exit costs, fees, marks and adjustments follow approved method and statement comparisons; unresolved discrepancies prevent a final/complete label.

Asynchronous broker snapshots may legitimately differ temporarily. Do not force equality by editing local quantities or inventing fills. Record observation times, repeat under an approved visibility policy and retain unresolved discrepancies. No numeric reconciliation tolerance is invented in this report.

## 8. Proposed report and logging outputs

Minimum operational report: signal ID/source refs; instrument/expiry; parsed and amended levels; policy/mode; received/armed/decision times; queue age; waiting/rejected/skipped reason; intent/broker IDs; requested versus filled quantity; execution prices when known; remaining allocation; active stop/protection state; reconciliation result; completeness flag.

Minimum financial report, **only after accounting specification is approved**: account/period scope, attributed executions, cost-basis method, proceeds/cost/charges, realized result, remaining quantity/cost basis, mark source/as-of, unrealized result, adjustments and statement reconciliation status. No invented zeros for unknown fees/prices.

Log structured events rather than unqualified text such as “Trade completed.” Use typed event names (e.g. INTENT_DISPATCHED, ORDER_ACKNOWLEDGED, FILL_CONFIRMED, ENTRY_REJECTED, POSITION_CLOSED_CONFIRMED) and price_kind (OBSERVED_LTP, REQUEST_LIMIT, EXECUTION_AVERAGE) where applicable. Keep health dimensions separate: process_alive, ingestion_ready, entry_admission_ready, reconciliation_state and exposure_protection_state.

Retain raw external evidence securely with access-controlled references, redaction policy and an approved retention period. Hashes help detect changes but do not by themselves prove authenticity, completeness or correct ownership. Export manifest/checksums must cover a defined immutable input set.

## 9. Verification plan and next deliverables

- Add schema fixtures for every contract and all null/malformed/finite/integer boundaries; test actual JSON serialization, not only Python objects.
- Preserve existing arithmetic/fill tests; add canonical-parser, amended-state, timestamp ordering/unit, tick-loss and duplicate-restart tests.
- Build a deterministic lineage test from split messages through partial fills and exit, asserting every record joins to source/rule/events, including skipped and queued paths.
- Crash-test before/after durable intent, broker acknowledgement and fill application using separate fake broker/store; compare recovered projection with expected ledger.
- Compare normalized API outputs against fixtures with differing quote ages and missing fields; no false freshness claim.
- Build independent financial calculation oracles only after cost-basis/fee/mark policy is approved; reconcile to redacted broker execution/statement fixtures.
- Verify logs and export completeness, redaction and retained correction history; report irrecoverable gaps explicitly.

No new test results are claimed here. Highest priorities are durable universal identity/intent, strict fill validation, canonical domain validation, timestamped event capture and reconciliation. A polished report generator cannot repair missing underlying evidence.

Only this document was added; application functionality, existing files/tests and server state were preserved.
