# DOMAIN NOTES

## 1. Aggregate Boundary Choice

I kept `LoanApplication`, `ComplianceRecord`, `AgentSession`, and `AuditLedger` as separate aggregates rather than collapsing them into one large stream.

Why:
- `LoanApplication` needs lifecycle coordination, but it should not be forced to serialize every agent action.
- `ComplianceRecord` has a rule-evaluation boundary that is naturally independent.
- `AgentSession` is a recovery and traceability boundary for agent execution.
- `AuditLedger` is an integrity boundary, not a business workflow boundary.

Rejected alternative:
- A single `loan-{id}` stream for everything.

What that would break:
- It would create unnecessary OCC contention across credit, fraud, compliance, and orchestration work.
- Two agents would collide even when they are operating on different concerns.
- Recovery and audit replay would become harder to reason about because the stream would mix lifecycle, agent trace, and integrity concerns.

## 2. OCC Collision Sequence

When two writers target the same stream at the same `expected_version`:

1. Both load the stream and observe version `N`.
2. Both decide on new events.
3. The first append acquires the row lock, verifies `current_version = N`, writes the events, and advances the version to `N+1`.
4. The second append reaches the same check, sees `current_version = N+1`, and raises `OptimisticConcurrencyError`.
5. The loser reloads the stream, replays the new history, and decides again.

What the losing agent must do:
- Reload.
- Re-evaluate the domain rules.
- Either retry with a new command or stop if the decision is no longer relevant.

## 3. Async Daemon Coordination

I would coordinate async projections with a single checkpoint per projection and a lowest-available starting position across projections.

The daemon:
- reads the lowest checkpoint across projections,
- loads events from `events`,
- applies each projection,
- writes checkpoints after successful processing.

Why this pattern:
- It keeps the read side restartable.
- It makes projection rebuilds deterministic.
- It prevents a crash from losing progress.

Failure mode guarded against:
- A daemon restart reprocessing the whole log from the beginning.
- A projection handler crash silently skipping ahead and losing an event.

## 4. Event Sourcing vs Event-Driven Architecture

These are not the same thing.

Event-driven architecture:
- Events are messages.
- Messages can be transient.
- The system of record still lives somewhere else.

Event sourcing:
- Events are the system of record.
- State is reconstructed from history.
- The immutable log is the source of truth.

Why this matters here:
- The Week 5 challenge requires temporal replay, auditability, and reproducibility.
- A message bus cannot guarantee that.

## 5. Missing Schema Elements

The schema is strong, but I would still call out a few future improvements:

- A formal snapshot table for aggregates with explicit schema versioning.
- Outbox delivery status columns for retry/ack tracking.
- Projection health metadata for lag and retry history.
- A stronger archival policy for cold streams and retention.

## 6. Recovery and Audit Tradeoffs

The system prefers correctness and replayability over cleverness.

That means:
- no hidden in-memory state as the source of truth,
- no mutation of old events,
- no manual “fix in place” of historical records,
- no projection advancement unless the batch really succeeded.

This is slower than a CRUD system, but it is the right tradeoff for regulated loan decisions.
