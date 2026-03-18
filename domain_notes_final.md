# DOMAIN_NOTES.md

## Pre-Implementation Architectural Reasoning

This document captures my architectural reasoning and design decisions before writing any implementation code. It addresses six questions that test whether I understand Event Sourcing at a level sufficient to build a production system — not just define the terms. Every answer is grounded in the concrete system I am about to build: a PostgreSQL-backed event store driving a multi-agent loan-processing pipeline in Python.

The system under design has these characteristics:
- Append-only `events` table in PostgreSQL, one row per event
- Event streams scoped per aggregate (e.g., `loan-APEX-0001`)
- Five AI agents that currently process applications in a staged pipeline, each writing domain events
- Async projection workers that build read models for dashboards and query APIs
- No direct mutation of historical events, ever

---

## 1. EDA vs. ES: Callbacks Are Not a Ledger

### The Question (Restated)

A component captures event-like data through callbacks — for example, LangChain trace hooks that fire when an LLM node starts or finishes. Does this constitute Event-Driven Architecture (EDA) or Event Sourcing (ES)? If I redesigned this component to use The Ledger's event store, what concretely changes?

### Direct Answer

It is Event-Driven Architecture, not Event Sourcing. The distinction is not about the shape of the data — both produce timestamped records of things that happened. The distinction is about **what role the events play in the system**.

### Reasoning

There are two fundamentally different ways to use events:

**Events as notifications (EDA):** The callback fires, a subscriber does something with it (logs it, updates a counter, sends a Slack message), and then the notification can be thrown away. The system's authoritative state lives somewhere else — in a database row, in the LangChain state dict, in memory. The events are **derived from** the real state. If I lost every callback trace, the system would still function. I would lose observability, but not correctness.

**Events as the system of record (ES):** The event is the durable business record from which state is derived. There is no separate "current state" table. If I want to know the status of loan APEX-0001, I load every event in the `loan-APEX-0001` stream and replay them to compute the current state. If I lost the events, I would lose the data — there is nothing else. The persisted event stream is the regulatory record; auditability is not a side effect, it is a property of the storage model.

The callback approach fails to be ES in three specific ways:

1. **No append-only guarantee.** Callbacks are fire-and-forget. If the subscriber crashes mid-processing, the event is gone. In ES, the event is durably written to the store before anything else happens.

2. **No stream identity.** Callbacks don't organize events into per-aggregate streams with ordered positions. I cannot replay "everything that happened to loan APEX-0001 in order."

3. **No concurrency control.** Two callbacks can fire for the same entity at the same time with no coordination. ES uses `expected_version` to enforce a strict ordering — you cannot append event #4 to a stream unless event #3 already exists.

### Concrete Example

In the current callback-based design, the credit analysis agent might fire:

```python
# LangChain trace callback — EDA pattern
on_chain_end(output={"risk_tier": "MEDIUM", "confidence": 0.81})
# This is logged, maybe sent to a dashboard, but the "real" state
# is in the agent's in-memory state dict or a status column elsewhere.
```

Redesigned with The Ledger:

```python
# Event Sourcing pattern — event IS the record
await event_store.append(
    stream_id="credit-APEX-0016",
    events=[CreditAnalysisCompleted(
        application_id="APEX-0016",
        session_id="sess-cre-64885ba4",
        decision={"risk_tier": "MEDIUM", "confidence": 0.81, ...},
        model_version="claude-sonnet-4-20250514",
        completed_at=datetime.now()
    ).to_store_dict()],
    expected_version=2  # OCC: I know exactly what came before
)
```

**What changes:**
- The credit decision is no longer a row in a `loan_status` table that gets overwritten. It is an immutable event in a stream.
- I can replay the stream to see every credit decision ever made for this loan, including ones that were superseded.
- The `expected_version=2` ensures that if another agent already wrote to this stream, I get a conflict error instead of silently overwriting.
- Projections downstream (dashboards, reports) derive their state from these events. I never have to build a separate audit log — the event stream **is** the audit log.

### What Is Gained

| Capability | Callback/EDA | Event Sourcing |
|-----------|-------------|---------------|
| Full audit trail | Manual, bolt-on | Automatic — events are the trail |
| Rebuild state after bug | Impossible — state was overwritten | Replay events with fixed logic |
| Temporal queries ("what was the state on March 5th?") | Requires separate time-travel table | Replay events up to that timestamp |
| Concurrent write safety | None — last write wins | OCC with `expected_version` |
| Regulatory proof of decision chain | Fragile — depends on logging not failing | Durable — events are the regulatory record |

### Why Not Keep EDA and Add Logging?

Because logging is best-effort. If the log write fails, the system does not roll back the decision. In ES, the durable event write is the authoritative record that the decision occurred — if it fails, the decision did not happen. This is not a philosophical distinction; it is the difference between "we have a log that says we checked compliance" and "the compliance check is proven to have occurred because the event exists."

---

## 2. The Aggregate Question: Why Four Boundaries, Not One

### The Question (Restated)

The loan-processing system uses four aggregates. What are they, why are they separate consistency boundaries, and what alternative boundary was considered and rejected?

### Direct Answer

The four aggregates are:

1. **LoanApplication** (stream: `loan-{application_id}`)
2. **DocumentPackage** (stream: `docpkg-{application_id}`)
3. **CreditRecord** (stream: `credit-{application_id}`)
4. **ComplianceRecord** (stream: `compliance-{application_id}`)

I am choosing aggregate boundaries based on **what must be atomically consistent at write time**, not based on what data is related or shown together in the UI. My boundary rule is: if two decisions do not need to succeed or fail atomically to preserve a business invariant, they should not share an aggregate.

(The system also has AgentSession and AuditLedger aggregates, but those are infrastructure concerns, not domain aggregates in the loan-processing sense.)

### Why They Are Separate

Each aggregate is a **consistency boundary** — a cluster of data and rules that must be internally consistent at all times. The key question is: "What data needs to be atomically consistent when I write?"

**LoanApplication** owns the lifecycle state machine: `SUBMITTED → DOCUMENTS_PENDING → ... → APPROVED/DECLINED`. It is the orchestration spine. Its invariant: the state machine transitions must be valid. You cannot go from `NEW` directly to `APPROVED`.

**DocumentPackage** owns document processing: upload tracking, format validation, fact extraction, quality assessment. Its invariant: all required documents must be processed before marking `PackageReadyForAnalysis`. This is independent of the loan state machine — documents can fail extraction and be re-extracted without affecting the loan state.

**CreditRecord** owns the financial analysis: historical profile, extracted facts consumption, credit decision. Its invariant: a `CreditAnalysisCompleted` event must not exist without prior `HistoricalProfileConsumed` and `ExtractedFactsConsumed` events in the same stream. The credit analysis is a self-contained judgment.

**ComplianceRecord** owns regulatory rule evaluation: 6 rules checked sequentially, overall verdict. Its invariant: `ComplianceCheckCompleted` must reflect the actual pass/fail results of all evaluated rules. A hard-block failure must produce a `BLOCKED` verdict regardless of how many rules passed.

### Why Fraud Is Not a Separate Aggregate

Fraud screening is a major operational concern, but I am not treating it as a separate aggregate because it does not currently own a long-lived independent consistency boundary in my design. The fraud detection agent runs, produces a `FraudScreeningCompleted` event in the loan stream, and is done — there is no ongoing fraud lifecycle with retries, escalations, or analyst review state that requires its own consistency enforcement. If fraud logic later grows into a richer lifecycle with multi-step investigation workflows and independent state transitions, I would split it into a `fraud-{application_id}` aggregate at that point.

### The Rejected Alternative: Merging CreditRecord and FraudScreening

I considered having a single `RiskAssessment` aggregate that covers both credit analysis and fraud detection. The reasoning was: "they both assess risk for the same application, so they belong together."

I rejected a combined `RiskAssessment` aggregate because it would force credit and fraud decisions into the same consistency boundary. In my current design, fraud events still land in the loan stream because fraud does not yet have a separate long-lived lifecycle. If fraud later becomes independently concurrent with its own retries, escalations, and analyst review state, I would promote it to its own `fraud-{application_id}` aggregate to remove that contention.

The coupling problem this prevents is **concurrency contention**. If credit and fraud shared a single stream and I later wanted to run them in parallel (reasonable — they read different data sources), both agents would contend for the same `expected_version`. Agent A writes at version 3, agent B also writes at version 3, one of them gets an OCC conflict and must retry. With separate streams per concern, that contention is reduced or eliminated where the concerns truly have independent lifecycles.

### Why Not One Giant Aggregate?

The naïve approach — one `LoanApplication` aggregate containing documents, credit, fraud, compliance, and the state machine — creates a single stream that every agent writes to. This means:

- **High contention:** 5 agents all fighting over the same `expected_version`, causing frequent OCC retries.
- **Large replay cost:** To enforce a business rule in the compliance agent, I have to replay hundreds of document extraction events that compliance does not care about.
- **Coupling of unrelated invariants:** A bug in document extraction code could break the compliance rule checker because they share the same aggregate.

---

## 3. Concurrency in Practice: Tracing an OCC Conflict

### The Question (Restated)

Two AI agents simultaneously process the same loan application. Both read the stream version as 3 and call `append_events` with `expected_version=3`. Walk through exactly what happens in the event store. What does the losing agent receive, and what does it do next?

### Direct Answer

Agent A commits first, so its event becomes stream position 4 and the stream's `current_version` becomes 4. Agent B gets an `OptimisticConcurrencyError` telling it the stream is now at version 4, not 3. Agent B must re-read the stream version, verify its event is still valid given the new state, and retry the append with `expected_version=4`.

The correctness comes from the fact that the version check and append happen inside the same transaction while the stream metadata row is locked.

### Step-by-Step Sequence

```
TIME    AGENT A                         AGENT B                         EVENT STORE (stream: loan-APEX-0016)
─────   ──────────────────              ──────────────────              ────────────────────────────────────
                                                                        current_version = 3
                                                                        events: [pos 0, 1, 2, 3]

t=0     Reads stream_version            Reads stream_version
        → gets 3                        → gets 3

t=1     Calls append(                   Calls append(
          stream_id="loan-APEX-0016",     stream_id="loan-APEX-0016",
          events=[FraudScreening         events=[ComplianceCheck
                  Requested(...)],                Requested(...)],
          expected_version=3)             expected_version=3)

t=2     PostgreSQL acquires             (blocked — waiting for
        FOR UPDATE row lock             row lock to be released)
        on event_streams row

t=3     OCC check: current=3,
        expected=3 → MATCH ✅
        Inserts event at pos 4.
        Updates current_version = 4.
        Commits transaction.
        Row lock released.
                                                                        current_version = 4
                                                                        events: [pos 0, 1, 2, 3, 4]

t=4                                     Acquires row lock.
                                        OCC check: current=4,
                                        expected=3 → MISMATCH ❌
                                        Raises OptimisticConcurrency
                                        Error("expected v3, actual v4")
                                        Transaction rolled back.
                                        Row lock released.
```

### What the Losing Agent Receives

```python
OptimisticConcurrencyError(
    stream_id="loan-APEX-0016",
    expected=3,
    actual=4
)
```

This tells Agent B: "You assumed you were writing after event #3, but event #4 already exists. Someone else wrote while you were working."

### What the Losing Agent Does Next (Retry Path)

```python
async def _append_stream(self, stream_id, event_dict, causation_id=None):
    for attempt in range(MAX_OCC_RETRIES):  # up to 5 attempts
        try:
            # Step 1: Re-read the current version
            ver = await self.store.stream_version(stream_id)
            
            # Step 2: Append with the FRESH expected_version
            await self.store.append(
                stream_id=stream_id,
                events=[event_dict],
                expected_version=ver,
                causation_id=causation_id
            )
            return  # Success — exit the retry loop
            
        except OptimisticConcurrencyError:
            if attempt < MAX_OCC_RETRIES - 1:
                # Exponential backoff: 100ms, 200ms, 400ms, 800ms
                await asyncio.sleep(0.1 * (2 ** attempt))
                continue
            raise  # All retries exhausted — surface the error
```

On retry, Agent B:
1. Reads the stream version again → now sees 4
2. Calls `append(expected_version=4)`
3. If no one else wrote in the meantime, this succeeds and the event lands at position 5
4. If another conflict happens, the loop tries again with backoff

### Why This Is Correct

The OCC check is inside a PostgreSQL transaction that uses `SELECT ... FOR UPDATE` to lock the stream metadata row. This serializes concurrent writers at the database level: only one transaction can hold the lock at a time, and the second one blocks until the first commits or rolls back. The `expected_version` comparison then catches any version mismatch. The version check and the insert are atomic — there is no window between "I checked the version" and "I wrote the event" where another writer could slip in.

Without OCC, two agents could both believe they are writing event #4, resulting in duplicate position numbers or lost events. OCC makes this impossible — exactly one writer wins each round, and losers are notified cleanly.

### Risk: Retry Storm

If many agents contend on the same stream, retries compound. This is why I split aggregates into separate streams (see Section 2) — the credit agent writes to `credit-{id}`, not `loan-{id}`, so it does not contend with the fraud agent.

---

## 4. Projection Lag and Consequences: The Stale-Read Problem

### The Question (Restated)

A `LoanApplication` projection is eventually consistent with approximately 200ms of lag. A loan officer queries "what is the available credit limit?" immediately after an agent commits a `DisbursementCommitted` event. The officer sees the old (higher) limit because the projection has not caught up yet. What should the system do?

### Direct Answer

The correct response is not to pretend eventual consistency does not exist, but to design explicit freshness behavior around it. I treat "show this on a dashboard" and "use this value to authorize a financial action" as different consistency problems. The first can tolerate projection lag; the second must read from authoritative state.

### Why the Stale Read Happens

The sequence is:

```
t=0ms    Agent writes DisbursementCommitted to loan-APEX-0016 stream (position 12)
         Event store: ✅ event is durable
         Projection: still at position 11 — hasn't seen position 12 yet

t=50ms   Loan officer opens dashboard, queries projection
         Projection returns: available_limit = $957,000 (stale — based on events 0-11)

t=200ms  Projection worker picks up event at position 12
         Projection updates: available_limit = $457,000 (correct — includes disbursement)

t=250ms  If officer refreshes now, they see $457,000
```

The 200ms gap between write and projection update is caused by the async projection worker's polling interval. The projection worker reads events in batches, processes them, saves a checkpoint, then polls again. During any poll interval, the projection is behind the event store.

Projection lag is a monitored operational metric, not a guess; the system should expose current lag per projection so stale-read behavior is observable and alertable.

### What the System Should Do

**Informational reads (dashboards, reports) — use the projection, with freshness indicator**

The API should expose freshness metadata derived from projection checkpoint vs authoritative stream state, so the UI can indicate "updating" without needing to understand event-store internals:

```json
{
  "available_credit_limit": 957000,
  "data_freshness": "stale",
  "last_updated": "2026-03-17T12:05:31Z",
  "pending_updates": true,
  "message": "A recent update is being processed. Refresh in a moment for the latest."
}
```

The UI can then decide:
- If `pending_updates: false`, display the number normally
- If `pending_updates: true`, show a visual indicator (spinner, warning badge)

This is honest, cheap, and does not require architectural changes. The loan officer knows the number might be stale and can wait or refresh.

**Decision-critical reads (approvals, disbursements) — bypass the projection**

For specific high-stakes queries (like "am I about to approve a loan that exceeds the available limit?"), the system bypasses the projection and reads directly from the authoritative event stream:

```python
async def get_fresh_credit_limit(application_id: str) -> Decimal:
    """Bypass projection — replay events for authoritative answer."""
    events = await event_store.load_stream(f"loan-{application_id}")
    aggregate = LoanApplicationAggregate(application_id)
    for event in events:
        aggregate.apply(event)
    return aggregate.available_credit_limit
```

This is slower (replay cost) but always correct. Use it surgically — for approval workflows, not for dashboards.

**Inline (synchronous) projection — considered and rejected**

An inline projection updates the read model inside the same transaction as the event write. This eliminates lag entirely.

I rejected this because:
- It couples the write path to the read path. If the projection logic has a bug, event writes fail.
- It slows down the append operation. The credit analysis agent should not wait for a dashboard table update before it can finish.
- It defeats the scalability benefit of CQRS. The whole point is that writes and reads operate independently.

Inline projection is appropriate only for projections that the aggregate itself needs for its own invariants — not for UI dashboards.

---

## 5. The Upcasting Scenario: Schema Evolution Without Mutation

### The Question (Restated)

`CreditDecisionMade` was originally defined in 2024 as `{application_id, decision, reason}`. In 2026, the schema expands to `{application_id, decision, reason, model_version, confidence_score, regulatory_basis}`. Write the upcaster. How do I handle historical events that predate `model_version`?

### Direct Answer

The upcaster is a pure function that transforms v1 events to v2 on read. Historical events in storage are never mutated. For fields like `model_version` where the true value is unknowable for old events, I use `null` rather than fabricating a plausible value. Upcasters must be deterministic, side-effect free, and read-time only.

### Why Historical Events Are Never Mutated

Three reasons:

1. **Audit integrity.** If a regulator asks "what data existed on March 5th, 2024?" the answer must be the exact bytes that were written on that date. Mutating old events to add `model_version` would make them say something they never said.

2. **Hash chain validity.** If we implement integrity hashing (which the AuditLedger aggregate does with `AuditIntegrityCheckRun`), mutating any event breaks every downstream hash.

3. **Operational risk.** A migration that touches millions of historical event rows is a high-risk database operation. If it fails partway through, some events have the new schema and some don't. Upcasting avoids this entirely — the storage is unchanged.

### The Upcaster

```python
class UpcasterRegistry:
    def __init__(self):
        self._upcasters = {}  # {event_type: {from_version: transform_fn}}

    def register(self, event_type: str, from_version: int, to_version: int):
        def decorator(fn):
            self._upcasters.setdefault(event_type, {})[from_version] = fn
            return fn
        return decorator

    def upcast(self, event: dict) -> dict:
        """Apply chain of upcasters until event reaches current version."""
        event_type = event["event_type"]
        version = event.get("event_version", 1)
        chain = self._upcasters.get(event_type, {})
        while version in chain:
            event = dict(event)  # shallow copy — never mutate the input
            event["payload"] = chain[version](dict(event["payload"]))
            version += 1
            event["event_version"] = version
        return event


registry = UpcasterRegistry()

@registry.register("CreditDecisionMade", from_version=1, to_version=2)
def upcast_credit_decision_v1_to_v2(payload: dict) -> dict:
    """
    v1: {application_id, decision, reason}
    v2: {application_id, decision, reason, model_version, confidence_score, regulatory_basis}
    """
    payload.setdefault("model_version", None)
    payload.setdefault("confidence_score", None)
    payload.setdefault("regulatory_basis", None)
    return payload
```

### Inference Strategy for Historical Events

Each new field requires an explicit decision about what value to assign to events that predate it:

- **`model_version`:** Set to `None`, not a guess. We did not track this in 2024. `None` is truthful and queryable: `WHERE model_version IS NULL` cleanly selects "pre-tracking era" events.

- **`confidence_score`:** Set to `None`. Old decisions did not produce a numeric score. Backfilling `0.5` would look like a low-confidence decision, which is misleading. A regulator could ask "why was this 50% application approved?" and there would be no honest answer.

- **`regulatory_basis`:** Set to `None` rather than `[]`. An empty list can be interpreted as "we checked and no regulations applied," which is too strong a claim for historical events that simply predate this tracking. `None` means "not recorded in this schema version," which is the truthful assertion. If consumers interpret `[]` as "we checked and none applied," that creates a false regulatory comfort for legacy decisions. `None` avoids this ambiguity.

The general principle: **`None` means "this field did not exist when this event was created."** Every consumer can handle that case explicitly. Fabricating a plausible-but-false value is always worse than admitting the data was not collected.

### How event_version Is Handled

Events are stored with the version they were written at. The upcaster is applied only on read — in `load_stream()` and `load_all()`:

```python
async def load_stream(self, stream_id, from_position=0, to_position=None):
    rows = await conn.fetch(query, *params)
    events = []
    for row in rows:
        e = {**dict(row), "payload": dict(row["payload"]),
                         "metadata": dict(row["metadata"])}
        if self.upcasters:
            e = self.upcasters.upcast(e)  # v1 → v2 transformation happens here
        events.append(e)
    return events
```

Consumers of `load_stream()` always see v2 events. They never need to handle v1. The upcaster is the single point of version translation.

### Chaining Upcasters

If a third version is added later (v2 → v3), I register another upcaster:

```python
@registry.register("CreditDecisionMade", from_version=2, to_version=3)
def upcast_credit_decision_v2_to_v3(payload: dict) -> dict:
    payload.setdefault("explainability_report_id", None)
    return payload
```

A v1 event hitting `load_stream()` automatically chains: v1 → v2 → v3. The upcaster registry handles this by looping `while version in chain`.

---

## 6. Distributed Projection Execution: The Marten Async Daemon Parallel

### The Question (Restated)

Marten 7.0 supports distributed projection execution across multiple nodes. How would I achieve the same pattern in Python with PostgreSQL? What coordination primitive prevents duplicate processing, and how does the system recover from crashes?

### Direct Answer

I use **PostgreSQL advisory locks** to assign exclusive ownership of each projection to one worker at a time. Each projection maintains its own checkpoint (last processed `global_position`). If a worker crashes, the advisory lock is automatically released and another worker picks up from the last checkpoint. Distributed async projections are only safe under crash recovery if handlers are idempotent.

### Architecture Overview

```
                    ┌────────────────────────────────┐
                    │         Event Store             │
                    │   (events table, ordered by     │
                    │    global_position)             │
                    └─────────┬──────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
        ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐
        │ Worker A   │  │ Worker B   │  │ Worker C   │
        │ Owns:      │  │ Owns:      │  │ Owns:      │
        │ - Dashboard│  │ - RiskView │  │ - CostView │
        │ projection │  │ projection │  │ projection │
        └────────────┘  └────────────┘  └────────────┘
```

### Coordination Mechanism: PostgreSQL Advisory Locks

Each projection has a unique integer ID. When a worker starts processing a projection, it tries to acquire a session-level advisory lock on that ID:

```python
class ProjectionWorker:
    def __init__(self, event_store, db_pool, projection_name: str, handler):
        self.store = event_store
        self.pool = db_pool
        self.projection_name = projection_name
        # Deterministic lock ID from projection name
        self.lock_id = int(hashlib.md5(projection_name.encode()).hexdigest()[:8], 16)
        self.handler = handler

    async def try_acquire_ownership(self, conn) -> bool:
        """Try to acquire exclusive ownership. Returns immediately (non-blocking)."""
        row = await conn.fetchrow(
            "SELECT pg_try_advisory_lock($1) AS acquired",
            self.lock_id
        )
        return row["acquired"]

    async def run(self):
        """Main worker loop."""
        async with self.pool.acquire() as conn:
            if not await self.try_acquire_ownership(conn):
                return  # Another worker already owns this projection
            
            try:
                while True:
                    await self._process_batch(conn)
                    await asyncio.sleep(0.2)  # poll interval
            finally:
                # Release on clean shutdown
                await conn.execute("SELECT pg_advisory_unlock($1)", self.lock_id)

    async def _process_batch(self, conn):
        # Load checkpoint
        checkpoint = await self._load_checkpoint(conn)
        
        # Fetch next batch of events after checkpoint
        events = await conn.fetch(
            "SELECT global_position, stream_id, event_type, event_version, "
            "payload, metadata, recorded_at FROM events "
            "WHERE global_position > $1 ORDER BY global_position ASC LIMIT $2",
            checkpoint, 500
        )
        
        if not events:
            return
        
        # Process each event and save checkpoint in the SAME transaction
        async with conn.transaction():
            for event in events:
                await self.handler(dict(event))
            
            # Checkpoint update is inside the same transaction as the projection
            # writes — if the transaction fails, neither the projection state
            # nor the checkpoint advances, preventing checkpoint drift.
            new_checkpoint = events[-1]["global_position"]
            await self._save_checkpoint(conn, new_checkpoint)

    async def _load_checkpoint(self, conn) -> int:
        row = await conn.fetchrow(
            "SELECT last_position FROM projection_checkpoints "
            "WHERE projection_name = $1", self.projection_name
        )
        return row["last_position"] if row else 0

    async def _save_checkpoint(self, conn, position: int):
        await conn.execute(
            "INSERT INTO projection_checkpoints (projection_name, last_position, updated_at) "
            "VALUES ($1, $2, NOW()) "
            "ON CONFLICT (projection_name) DO UPDATE SET last_position = $2, updated_at = NOW()",
            self.projection_name, position
        )
```

### Checkpoint Transactionality

The projection state update and checkpoint update occur in the same database transaction. This is a critical correctness requirement: if the transaction fails after processing events but before the checkpoint saves, neither the projection state nor the checkpoint advances. Without this, a crash can produce checkpoint drift — the checkpoint says "processed up to position 510" but the projection table only reflects events up to 505.

### Why Advisory Locks, Not Row-Level Locks

- **Automatic release on disconnect.** If a worker crashes or loses its database connection, PostgreSQL releases the session-level advisory lock immediately. No manual cleanup, no stuck locks. This is the key resilience property.
- **Non-blocking try.** `pg_try_advisory_lock` returns `false` immediately if the lock is held, rather than blocking. Workers can quickly discover "this projection is already being handled" and move on.
- **No deadlocks.** Each worker acquires one lock. There is no multi-lock ordering problem.

The alternative — using `SELECT ... FOR UPDATE` on a projection registration row — would work but requires explicit timeout handling and manual lock release on crash. Advisory locks give crash recovery for free.

### The Duplicate Processing Risk and Why Idempotency Is Non-Negotiable

Even with checkpoint transactionality, there is a window where a crash causes the same events to be reprocessed — the transaction was being processed but had not yet committed:

```
Worker processes events at positions 101, 102, 103 inside a transaction
Worker crashes BEFORE the transaction commits
Worker restarts, loads checkpoint → still at 100
Worker reprocesses events 101, 102, 103 again  ← duplicate processing
```

This is why projection handlers **must** be idempotent. This is not a nice-to-have — it is a hard requirement for correctness under crash recovery:

```python
async def dashboard_projection_handler(event: dict):
    """Idempotent: UPSERT, not INSERT."""
    if event["event_type"] == "CreditAnalysisCompleted":
        payload = event["payload"]
        await conn.execute(
            """INSERT INTO loan_dashboard (application_id, risk_tier, confidence, updated_at)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (application_id) DO UPDATE
               SET risk_tier = EXCLUDED.risk_tier,
                   confidence = EXCLUDED.confidence,
                   updated_at = EXCLUDED.updated_at""",
            payload["application_id"],
            payload["decision"]["risk_tier"],
            payload["decision"]["confidence"],
            event["recorded_at"]
        )
```

Using `ON CONFLICT ... DO UPDATE` (upsert) means that processing the same event twice produces the same result. The projection converges to the correct state regardless of replays.

### Crash Recovery Walkthrough

```
t=0     Worker A holds advisory lock #7834 for "dashboard" projection
        Checkpoint: global_position = 500

t=1     Worker A processes events 501-510
        Worker A crashes (process killed, network drops, OOM)

t=2     PostgreSQL detects connection loss
        Advisory lock #7834 is automatically released
        Checkpoint is still at 500 (transaction never committed)

t=3     Worker B starts, calls pg_try_advisory_lock(7834)
        Lock acquired ✅
        Worker B loads checkpoint → 500
        Worker B reprocesses events 501-510 (idempotent — no harm)
        Worker B saves checkpoint at 510
        Worker B continues processing new events from 511+
```

No data loss. No duplicate side-effects (because idempotent handlers). No manual intervention.

### Scaling: Multiple Projections Across Workers

A single worker process can own multiple projections. Or projections can be spread across processes:

```python
async def main():
    pool = await asyncpg.create_pool(db_url)
    
    # Each worker attempts to own one projection
    workers = [
        ProjectionWorker(store, pool, "dashboard",    dashboard_handler),
        ProjectionWorker(store, pool, "risk_report",   risk_handler),
        ProjectionWorker(store, pool, "cost_tracker",  cost_handler),
    ]
    
    # Run all concurrently — if another process already owns one, it skips
    await asyncio.gather(*(w.run() for w in workers))
```

If I run this on 3 separate machines, each projection is owned by exactly one machine. Workers that fail to acquire a lock simply skip that projection. If a machine dies, its locks release and the surviving machines pick up the work.

---

## Aggregate Boundary Summary

| Aggregate | Stream | Owns | Why Separate |
|-----------|--------|------|-------------|
| LoanApplication | `loan-{id}` | Lifecycle state machine | Authoritative application state; orchestration spine |
| DocumentPackage | `docpkg-{id}` | Document ingestion/extraction lifecycle | Avoids coupling document churn to loan decision stream |
| CreditRecord | `credit-{id}` | Financial analysis history | Isolates credit decisioning from other workflows |
| ComplianceRecord | `compliance-{id}` | Rule evaluation and compliance verdict | Deterministic policy boundary, no AI involvement |

Fraud screening events currently land in the loan stream because fraud does not yet own a separate long-lived lifecycle. If fraud later develops independent concurrency with retries, escalation, and analyst review, it gets promoted to its own `fraud-{id}` aggregate.

---

## Key Tradeoffs I Am Intentionally Accepting

| Tradeoff | What I Accept | What I Gain |
|----------|-------------|------------|
| **Replay cost** | Rebuilding an aggregate requires loading its full event history. For a loan with 50+ events, this is milliseconds. For millions of events, it would need snapshotting. | Complete audit trail and temporal queries without a separate audit system. |
| **Eventual consistency on projections** | Dashboard data may be up to 200ms stale. A loan officer could see outdated credit limits. | Write path (agents) is never blocked by read path (dashboards). Agents run at full speed. |
| **Schema coupling via events** | Event schemas are contracts between writers and readers. Changing `CreditAnalysisCompleted` requires updating upcasters AND all consumers. | Strong type safety. Every consumer knows exactly what fields exist. No "surprise" schema drift. |
| **Operational complexity** | Running advisory-lock-based projection workers is more complex than a single-process cron job. | Horizontal scaling and automatic failover. No single point of failure for projections. |
| **Storage growth** | Events are never deleted. The events table grows monotonically. For 100 applications with ~50 events each, this is ~5,000 rows — trivial. For 1M applications, archival strategy needed. | Every decision ever made is recoverable. Regulatory compliance is not a bolt-on. |
| **OCC retry overhead** | Concurrent writes to the same stream cause retries with exponential backoff. Under heavy contention, an agent might retry 3-4 times. | Zero risk of lost writes, duplicate positions, or silent data corruption. The retry cost is the price of correctness. |

---

## Common Failure Modes I Will Design Against

### 1. The "Lost Event" Problem

**What happens:** An agent computes a credit decision and attempts to append it. The append call fails (network timeout, DB connection dropped). The agent assumes the event was not written, but PostgreSQL actually committed it before the connection dropped.

**Defense:** Before retrying, the agent reloads the stream and checks for an event with the same idempotency key or command correlation identifier, rather than matching only on event type. If the event is already present, the retry is skipped. This requires each command to carry a unique correlation ID that propagates into the event, making deduplication reliable even when the same agent can emit the same event type more than once in a session.

### 2. The "Projection Poison Pill"

**What happens:** A single malformed event causes the projection handler to throw an exception. The projection worker crashes, restarts, loads the checkpoint, hits the same event, crashes again — infinite loop.

**Defense:** After N consecutive failures on the same `global_position`, the worker quarantines the event in a dead-letter workflow and only advances past it under an explicit operational policy, because skipping silently is not acceptable for regulated processing. A human or automated review process must inspect the dead-letter queue before the quarantined event is considered resolved.

### 3. The "Upcaster Side-Effect"

**What happens:** Someone writes an upcaster that queries the database or calls an external API — "let me look up the model_version from the deployment log." This makes the upcaster slow, non-deterministic, and potentially failing.

**Defense:** Upcasters must be deterministic, side-effect free, and read-time only. They take a payload dict and return a payload dict. No I/O, no database access, no network calls. This is enforced by code review, and testable: call the upcaster in a test with no database connection and verify it succeeds.

### 4. The "OCC Starvation" Problem

**What happens:** Many agents write to the same stream simultaneously. One unlucky agent keeps losing the OCC race and exhausts its retry limit.

**Defense:** First line — design aggregates so that contention is rare (separate streams per concern, as discussed in Section 2). Second line — exponential backoff with jitter so that retrying agents don't all retry at the same instant. Third line — alert if any agent exhausts `MAX_OCC_RETRIES`, as this indicates a design problem.

### 5. The "Checkpoint Ahead of Processing" Bug

**What happens:** A projection worker saves the checkpoint BEFORE fully processing the events. If it crashes after saving but before completing processing, those events are permanently skipped.

**Defense:** Always save the checkpoint inside the same transaction as the projection state update. The risk of reprocessing on crash (covered by idempotent handlers) is strictly preferable to the risk of skipping events permanently. This ordering is non-negotiable.

### 6. The "Time-Travel Lie"

**What happens:** Someone writes a "correcting" event and backdates its `recorded_at` to make it look like it happened in the past. This corrupts temporal queries.

**Defense:** The `recorded_at` timestamp is set by the event store at write time, never accepted from the caller. The event store uses `datetime.utcnow()` (or PostgreSQL's `NOW()`). Callers can include a `business_timestamp` in the payload if they need to record when something happened in the real world, but `recorded_at` is always the storage timestamp and is not caller-controlled.
