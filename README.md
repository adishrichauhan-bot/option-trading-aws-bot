# Option Trading AWS Bot

Python application with Telegram message assembly, a FYERS execution engine,
and a separate FastAPI market-data service for NIFTY/SENSEX and nearby options.

This document contains the project overview, current implementation boundaries,
consolidated technical reviews, historical evidence, and remaining work.
[runbook.md](./runbook.md) contains offline validation, read-only diagnostics,
and maintenance procedures. These are the only maintained project documents.

Documentation consolidated on 2026-11-25 against local commit `7afebb9`.
This is an editorial change, not a deployment or a new reliability certification.
Previously stashed development work is not part of this baseline. Remote code,
services, credentials, and running processes may differ from the local checkout.

## Architecture

| Component | Responsibility |
| --- | --- |
| [live_trader.py](./live_trader.py) | Integrated input loop, trade orchestration, queue, and journal snapshots |
| [trading.py](./trading.py) | Parsing, contract resolution, strategy, broker adapter, and standalone CLI |
| [telegram_trade_listener.py](./telegram_trade_listener.py) | Telegram account session, message chunks, and command recognition |
| [trade_lifecycle.py](./trade_lifecycle.py) | Command models, trade snapshots, and audit storage |
| [startup_recovery.py](./startup_recovery.py) | Broker-read validation, startup refusal, and local account ownership |
| [trading_policy.py](./trading_policy.py) | Friday restriction evaluated in IST |
| [position_sizing.py](./position_sizing.py) | Exact-contract lot metadata and funds-based quantity calculation |
| [market_data_ws.py](./market_data_ws.py) | FYERS market-data socket and deterministic mock feed |
| [auth.py](./auth.py) | Credential validation, optional refresh, and interactive authorization |
| [app.py](./app.py) | Independent HTTP market-data API |
| [journal_report.py](./journal_report.py) | Read-only journal inspection |
| [reconcile_journal.py](./reconcile_journal.py) | Explicit legacy-record acknowledgement with broker-flat validation |
| [tests/](./tests/) | Unit, integration, incident fixtures, and guarded offline runner |

The current flow is message chunks -> complete call -> contract resolution ->
market observations -> strategy -> broker requests -> cumulative-fill polling ->
journal snapshot. One trade is active; later calls wait in memory. The API is a
separate service and does not supervise the execution engine.

Dependencies are declared in [requirements.txt](./requirements.txt). Configuration
names are documented by [.env.example](./.env.example). Secrets, account sessions,
token caches, and production databases belong outside versioned documentation.

## Current implementation and limits

These are descriptions of code, not investment recommendations or approval of
business rules. Offline simulations must be distinguished from broker execution.

- Split calls include an instrument, entry description, three targets, and a stop.
  While assembly is pending, ordinary target/stop fields remain call chunks.
  Explicit modifications are controls. Reentry assembly requires fresh levels.
- BUY ABOVE uses an observed crossing; the first observation above a threshold is
  insufficient. Buying ranges use an eligible executable ask and a calculated
  ceiling, rather than an upper-bound breakout. The default minimum reward/risk
  parameter is 1.5. The current range opportunity timeout is 60 seconds; quote-age
  protection is separate. Removed timeout changes remain outside this baseline.
- Lot size comes from the exact contract. Current sizing budget is the lower of
  half the available balance and available balance minus reserve. Floor whole-lot
  rounding is the default; an unaffordable explicit ceiling result is skipped.
  Old examples using the entire available balance are superseded. Estimated
  affordability is not an executed cost, margin approval, or financial result.
- Only confirmed cumulative fill deltas change tracked exposure. An acknowledgement
  is not a fill. Duplicate observations must not count twice. Cancellation
  acknowledgement alone is insufficient evidence of terminal cancellation.
- Target allocations are whole-lot portions of confirmed entry, with a remaining
  runner. Automatic stop adjustments preserve a higher active stop. T3 starts a
  runner rather than automatically liquidating everything. These are existing
  implementation choices, not claims of optimal strategy or guaranteed loss limits.
- Protection depends on the local process, feed, credentials, and broker access.
  Stopping a process does not close positions. Startup containment does not restore
  management of an old position or provide independent protection.
- Normal broker-backed execution can select real-order mode automatically from
  time. It is not a safe substitute for an offline test. Friday startup/new-entry
  refusal is enforced separately; protective exits are treated separately.
- Both execution entrypoints use account-scoped local ownership and fresh broker
  reads. Pending orders, nonzero positions, invalid reads, unavailable ownership,
  or a failed startup-audit write refuse readiness. The OS lock lasts for the
  execution lifetime and releases on process death; a remaining filename does not
  prove ownership. Different hosts, OS homes, and manual actors are not fenced.
- Current startup records unresolved legacy snapshot counts but does not refuse a
  broker-flat account solely for those counts. Older review text claiming that
  every unresolved legacy row blocks startup is historical. Explicit legacy
  acknowledgement preserves original snapshots and records a fingerprint/note.
- Registered trades have mutable journal snapshots, not a complete event ledger.
  Queued/manual/reentry provenance and durable pre-dispatch intent remain incomplete.
- `/health` reports API liveness only. It does not establish credential readiness,
  quote freshness, execution readiness, or protection health.

## Consolidated review register

The November 12 reviews assessed `a4aac3a`; the November 13 review assessed
`62f190b`. Their findings overlap and are not independent production incidents.
The table preserves the technical finding IDs and updates their disposition using
later implementation notes and current source. A source-supported failure path
is not proof of historical occurrence, loss, or frequency.

| ID | Finding | Current disposition / required evidence |
| --- | --- | --- |
| TD01 | Unread market updates can be discarded | Open; ordered consumption, explicit overflow, and freshness contract required; expected-failure test remains |
| TD02 | Reconnect can retain an earlier socket error | Open; connection generations and demonstrated fresh readiness required; expected-failure test remains |
| TD03 | Unrelated replies can affect the active trade | Open; durable signal/reply-descendant mapping and ambiguity handling required |
| TD04 | Cancellation can leave incomplete assembly alive | Open; terminal cancellation must apply to the identified signal and reject late resurrection |
| TD05 | Queued calls lose identity/journal coverage | Open; preserve typed origin, revisions, timestamps, and durable identity on every path |
| TD06 | Amendments do not enforce a complete canonical domain contract | Partial narrow rejection handling; final staged/atomic amendment semantics require policy decisions |
| TD07 | Invalid amendments can terminate monitoring | Narrow typed rejection isolation implemented; broader ingestion/storage failure isolation remains open |
| TD08 | Telegram and execution validators disagree | Open; equal ranges, positivity, and stop boundaries need one approved contract |
| TD09 | Fill quantities were permissively coerced | Strict integral/nonboolean validation implemented; preserve cumulative-fill and bounds regressions |
| TD10 | Session permission is selected once per trade | Open beyond Friday guard; final submission-time session/calendar policy unresolved |
| TD11 | No startup reconciliation or executor ownership | Initial refusal gate and single-host lock implemented; automatic restoration/cross-host fencing remain absent |
| TD12 | Persistence follows external side effects | Open; durable intents/attempts and crash-safe reconciliation required before recovery claims |
| TD13 | Feed/process failure removes local protection | Open; dependency health and outage response need a specified policy and verified capabilities |
| TD14 | Request-owned API sockets lacked cleanup | Deterministic cleanup and sanitized HTTP errors implemented; no load benchmark claimed |
| TD15 | Message recovery, revisions, and durable cursor are incomplete | Open; newest-message baseline and limited polling do not establish durable gap capture |
| TD16 | Authentication output/input exposed sensitive details | Scoped hidden input and redaction implemented; full SDK/storage/concurrent-writer hardening unverified |

Crosswalk from the architecture audit: F01=TD11; F02=TD13/07;
F03=TD01; F04=TD03; F05=TD04; F06=TD05; F07=TD07; F08=TD12;
F09=TD10; F10=TD15; F11=contract metadata/risk policy;
F12=TD14/API readiness; F13=TD16/dependencies/operations.

FYERS integration crosswalk: FY01=TD01; FY02=TD02; FY03=TD09;
FY04=bounded synchronous transport and unknown-write outcomes;
FY05=TD11/12; FY06=TD03-05; FY07=observation merging;
FY08=timestamp/schema assumptions; FY09=missing policies;
FY10=ownership/external activity; FY11=TD14/16;
FY12=audit lineage; FY13=runtime/SDK parity.

The historical technical review also identified eight hypotheses. They remain
investigation leads, not eight additional confirmed incidents. Examples include
writer races, queue starvation, blocking transport, and token-writer races.

## Requirements and unresolved decisions

Historical R01-R33 cover assembly/validation (R01-03), sizing (R04-06),
entry/market observation (R07-09), fills/exits/runner (R10-15),
amendments/routing/cancellation/reentry (R16-21), queue/journal/recovery
(R22-24), authentication/sessions/ingestion/failures (R25-28),
tests/mock/API (R29-31), and incident attribution/risk scope (R32-33).
Tests of an implementation choice do not resolve an unspecified business rule.

| Question | Decision still requiring explicit specification |
| --- | --- |
| Q01 | Interleaved setups, edits, incomplete-call expiry, and media instructions |
| Q02 | Canonical positive-price, equal-range, and stop-boundary validation |
| Q03 | Supported instruments/exchanges, exact identity, and expiry selection |
| Q04 | Approved rounding and reserve/cap ordering |
| Q05 | Allocation, cash reserve, fees/slippage allowance, and account risk limits |
| Q06 | Range/equality/first-observation behavior and already-passed targets |
| Q07 | Market-event age, ordering, overflow, loss, and latency guarantees |
| Q08 | Entry rounding interpretation and pending-opportunity lifetime |
| Q09 | Manual/external exposure attribution and permitted ownership scope |
| Q10 | Target allocations for small lot counts, equality, and multiple milestones |
| Q11 | Explicit exit/booking instruction precedence over runner behavior |
| Q12 | Trailing evaluation objective, dataset, and acceptance metrics |
| Q13 | Exact emergency boundary and indefinitely uncertain cancellation |
| Q14 | Pending-entry amendments, stop revisions, and booked milestones |
| Q15 | Ambiguous/unthreaded commands and post-entry CANCEL meaning |
| Q16 | Concurrency, queue capacity, age, priority, and supersession |
| Q17 | Required persistent history, retention, and coverage of every input path |
| Q18 | Dependency outages, deadlines, retries, alerts, and deliberate shutdown |
| Q19 | Session calendar, entry/exit windows, overnight policy, and execution authority |
| Q20 | Historical collection versus permission to re-arm recovered instructions |
| Q21 | API liveness/readiness distinction and market-data age contract |
| Q22 | Meaning of received, armed, submitted, partially filled, filled, and skipped |

Later range, budget, timeout, and Friday implementations supersede specific old
code descriptions; they do not settle every question above. Do not silently
select new business rules while fixing unrelated engineering defects.

## Failure analysis and verification targets

FM01-FM22 are proposed scenarios, not 22 reproduced production failures.

| IDs | Failure scenarios | Required offline verification |
| --- | --- | --- |
| FM01-03 | API timeout, throttling, network disconnection | Separate failed reads from unknown writes; bounded dependency handling; no blind retry |
| FM04-06 | Duplicate/reordered events and stale/missing prices | Idempotent fills, chronology/generation rules, explicit data loss and degraded health |
| FM07 | Wrong/ambiguous instrument | Exact metadata fixtures, multiple expiries, missing/inactive records |
| FM08-10 | Rejection, partial fill, delayed order visibility | Preserve actual remaining quantity; reconcile terminal cancellation before replacement |
| FM11-14 | Crash, termination, storage failure, restart | Separate durable fake-broker state; inject crashes around commit/dispatch/acknowledgement/fill |
| FM15-16 | External account drift and duplicate signals/workers | Attribute ownership; preserve source identity; prevent duplicate/conflicting effects |
| FM17-18 | Clock changes and authentication expiry | Explicit UTC source/receipt times, monotonic durations, truthful readiness |
| FM19-20 | Malformed data and concurrent conflicting actions | Strict schema validation, serialization, no partial mutation or unsupported exposure |
| FM21 | Incorrect financial reporting | Approved accounting method and actual execution/fee ledger; otherwise mark incomplete |
| FM22 | Missing audit evidence | Trace origin -> intent -> observation -> outcome, or explicitly record missing evidence |

Compound scenarios include accepted-but-lost response plus disk failure;
partial exit plus cancellation uncertainty and late fill; feed loss plus expired
credentials; old replies against a new active setup; manual account changes;
and two workers sharing storage. Passing isolated happy-path tests is insufficient.

## Data contracts and accounting boundaries

Proposed contracts, not implemented schemas: SourceMessage, SignalRevision,
CommandDecision, InstrumentSnapshot, MarketEvent, SizingDecision, OrderIntent,
OrderObservation/Execution, PositionProjection, ReconciliationResult, AuditEvent,
ReportManifest, and APIQuoteResponse. Each needs explicit versioning, identity,
causation, timestamp meanings, nullability, and compatibility before adoption.

The DI01-DI20 data invariants consolidate to:

- DI01-05: stable lineage, deduplicated revisions, consistent parsing, preserved
  queue provenance, and valid atomic amendments or explicitly inactive drafts.
- DI06-09: strict whole-lot quantities, acknowledgement separate from exposure,
  idempotent cumulative fills, and attributed buys minus sells without oversell.
- DI10-12: terminal cancellation, durable intent before effects, and reconstructible
  evidence for direct, queued, manual, and reentry paths.
- DI13-15: explicit timestamp bases, reproducible indicator inputs, and a
  nondecreasing trailing stop under the current policy.
- DI16-20: distinct outcomes, missing data never converted to zero, accounting
  based on executions, append-only corrections, and credential-free diagnostics.

A journal CLOSED label does not establish that an entry filled or that a result
was profitable. Submitted limit prices, observed LTP, target messages, estimated
premium, and account balance changes are not substitutes for execution prices,
charges, attribution, and an approved accounting method. No complete realized/
unrealized P&L reporting engine is established by this baseline.

ATR uses completed one-minute true ranges, with available warm-up bars. It is
not automatically equivalent to Wilder-smoothed chart ATR. Lost or out-of-order
processed ticks can change indicator inputs; exact historical reproduction needs
the accepted event/bar sequence and algorithm/configuration version.

## Historical evidence

These records describe their stated dates and environments; they are not a test
run performed during this documentation consolidation.

| Date / baseline | Evidence and subsequent interpretation |
| --- | --- |
| Nov 10 | 39 local tests recorded after fill/cancel/runner work; older T3-liquidation descriptions superseded |
| Nov 11 | Telegram targets/SL were misrouted during incomplete assembly; routing regression recorded 47 total passing tests |
| Nov 11-12 | Parsed calls failed on expired credentials; later listener baselines intentionally excluded earlier calls; later profile success did not prove earlier readiness |
| Nov 12 | Split reentry/pending-entry fixtures recorded 64 passing tests; historical replay was synthetic, not production catch-up |
| Nov 12 | Two afternoon calls were sizing skips under ceiling; one reportedly missed call had filled; another first quote was already above trigger |
| Nov 12 / a4aac3a | Initial review recorded 85 passing tests; FYERS audit later recorded 93 tests, 90 passing and 3 expected failures |
| Nov 13 / 62f190b | Independent guarded review repeated 93/90/3, with zero guard violations; review verdict was conditional staged offline engineering, not production certification |
| Nov 13 / Phase 1 | 119 tests: 117 passing, 2 expected failures, recorded locally on Python 3.12.0 and EC2 Python 3.9.25 / FYERS SDK 3.2.1 |
| Nov 13 / Friday guard | Local record: 131 tests, 129 passing, 2 expected failures; no new broker orders claimed |
| Nov 25 / local rollback | Uncommitted morning-startup, diagnostic, and related development was stashed; it is not included in current source |

The historical synchronization record identifies `f2196c6` as a pushed/pulled
Phase 1 change. Updating files on EC2 did not replace the older running Python
process. Historical PID, test counts, and observed SDK versions are not current
host status. Runtime parity must be measured per environment.

## Remaining engineering roadmap

Retain a modular monolith, SQLite, tested fill/cancel safeguards, and existing
regressions. A wholesale rewrite or microservices migration was not justified.

1. Maintain the shared startup refusal/ownership boundary and strict broker-read
   contracts; do not describe it as automatic restoration.
2. Design versioned durable origin/intent/attempt/observation records across every
   input path, with compatibility and crash-point tests before migration.
3. Build reliable reply mapping and terminal cancellation on preserved identity.
4. Define and test ordered market consumption, overflow, connection generations,
   and explicit timestamp schemas before removing expected-failure markers.
5. Resolve domain/session/outage/accounting policies before behavior changes.
6. Add only justified adapter boundaries, bounded I/O, injected clocks/settings,
   runtime/dependency reproducibility, and redacted health reporting.

Acceptance evidence must cover both CLI boundaries, duplicate/partial fills,
late fills during cancellation, unknown dispatch, invalid amendments, failed
reads/writes, process death, and repeated recovery. No passing mock suite proves
external availability, liquidity, profitability, or complete protection.

## Consolidation provenance

The former operating guide and journal-recovery notes are consolidated into
[runbook.md](./runbook.md); implementation progress and handoff evidence are
summarized above. The architecture, requirements, technical, FYERS, output,
FMEA, reliability-plan, and independent-review documents are merged into the
registers and roadmap above. Duplicate proposals and superseded procedures are
removed rather than presented as current instructions.

The complete original 15 documents remain recoverable from Git commit `7afebb9`,
including original R/TD/F/FY/FM/DI/Q tables, eight hypotheses, historical source
line citations, external references, and proposed diagrams. Those references
were historical evidence; external API documentation was not revalidated here.
Use Git history for exact original wording, not as a current operating guide.
