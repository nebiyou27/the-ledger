# Architecture Diagram

## Overview

This diagram shows the command-write path and projection read path of the event-sourced loan processing system. The PostgreSQL `events` table is the central persistence layer. All aggregate state is reconstructed from event streams on demand - there is no separate "current state" table. The `outbox` table is written inside the **same database transaction** as the event append, guaranteeing at-least-once downstream delivery without dual-write risk.

Three aggregate boundaries are shown, each with its own stream and consistency domain:

| Aggregate | Stream Format | Example | Consistency Concern |
|---|---|---|---|
| **LoanApplication** | `loan-{application_id}` | `loan-APEX-0016` | Lifecycle state machine: NEW → SUBMITTED → … → APPROVED/DECLINED |
| **ComplianceRecord** | `compliance-{application_id}` | `compliance-APEX-0016` | Rule evaluation: each rule evaluated once, hard-block semantics, completion accounting |
| **AgentSession** | `agent-{agent_type}-{session_id}` | `agent-credit_analysis-sess-cre-9` | Session lifecycle: context validation, model-version locking, terminal state enforcement |

Additional domain streams: `credit-{id}`, `fraud-{id}`, `docpkg-{id}`, `audit-{id}`.

---

## Core Invariants

- Event streams are append-only; updates happen by writing new events, never by mutating prior history.
- `stream_position` is unique per stream, and global ordering comes from the identity-backed `global_position`.
- Command handlers must load state, validate domain rules, and append atomically with the tracked `expected_version`.
- The outbox is written in the same transaction as the event append so downstream delivery can be retried safely.
- Projections are asynchronous, checkpointed, and restart from the last saved global position.

---

## Command-Write Path

```mermaid
flowchart TD
    CMD["Command\ne.g. SubmitApplication,\nCompleteComplianceCheck"]
    Handler["CommandHandler\n(Agent or API endpoint)"]
    StreamLoad["store.load_stream(stream_id)\nReturns ordered event list"]
    EventsRead[("events table\nSELECT WHERE stream_id\nORDER BY stream_position")]
    Agg["Aggregate.apply(event)\nRebuilds current state\nfrom event history"]
    Validate{"Validate / Decide\nassert_valid_transition()\nassert_can_approve()"}

    CMD -->|"inbound command"| Handler
    Handler -->|"load stream e.g.\nloan-APEX-0016"| StreamLoad
    StreamLoad -->|"SQL query"| EventsRead
    EventsRead -->|"replay each event\nthrough apply()"| Agg
    Agg -->|"state machine check\ndomain invariant guard"| Validate

    Validate -->|"new events +\nexpected_version=N"| AppendFn

    AppendFn["EventStore.append()"]

    subgraph TX["PostgreSQL Transaction — single atomic unit"]
        direction TB
        VersionCheck["event_streams\nSELECT ... FOR UPDATE\ncurrent_version vs expected_version\nstream_id + archived_at metadata\n❌ Mismatch -> OptimisticConcurrencyError"]
        InsertEvent["events table\nINSERT (stream_id, stream_position=N+1,\nevent_type, payload, recorded_at)"]
        InsertOutbox["outbox table\nINSERT (event_id, destination,\npayload) — SAME transaction"]
        UpdateVersion["event_streams\nUPDATE current_version = N+1"]

        VersionCheck --> InsertEvent
        InsertEvent --> InsertOutbox
        InsertOutbox --> UpdateVersion
    end

    AppendFn --> TX

    style TX fill:#1a2332,stroke:#4a90d9,stroke-width:2px,color:#fff
    style VersionCheck fill:#3d1f1f,stroke:#d94a4a,color:#fff
    style InsertOutbox fill:#1f3d2b,stroke:#4ab07d,color:#fff
```

---

## Projection Read Path (Async)

```mermaid
flowchart LR
    EventsTable[("events table\nglobal_position ordering")]
    Daemon["ProjectionDaemon\npolls load_all(from_position)\nevery 100ms"]
    Checkpoint[("projection_checkpoints\nlast_position per projection")]

    Daemon -->|"load_checkpoint(name)"| Checkpoint
    Checkpoint -->|"from_position = N"| Daemon
    Daemon -->|"load_all(from_position=N)"| EventsTable
    EventsTable -->|"batch of events"| Daemon

    ProjApp["ApplicationSummaryProjection\nSLO: p95 lag ≤ 500ms"]
    ProjComp["ComplianceAuditProjection\nSLO: p95 lag ≤ 2s\ntemporal query support"]
    ProjPerf["AgentPerformanceProjection\nSLO: p95 lag ≤ 2s"]
    ProjReview["ManualReviewsProjection\nhuman-in-the-loop tracking"]

    Daemon -->|"process_event()"| ProjApp
    Daemon -->|"process_event()"| ProjComp
    Daemon -->|"process_event()"| ProjPerf
    Daemon -->|"process_event()"| ProjReview
    Daemon -->|"save_checkpoint(name, position)"| Checkpoint

    style ProjApp fill:#2d4a6e,color:#fff,stroke:#4a90d9
    style ProjComp fill:#2d4a6e,color:#fff,stroke:#4a90d9
    style ProjPerf fill:#2d4a6e,color:#fff,stroke:#4a90d9
    style ProjReview fill:#2d4a6e,color:#fff,stroke:#4a90d9
```

---

## Aggregate Boundaries

```mermaid
flowchart TB
    subgraph LoanAgg["LoanApplication Aggregate"]
        direction LR
        LS["Stream: loan-APEX-0016"]
        LE1["ApplicationSubmitted"]
        LE2["DecisionGenerated"]
        LE3["ApplicationApproved"]
        LS --- LE1 --- LE2 --- LE3
    end

    subgraph CompAgg["ComplianceRecord Aggregate"]
        direction LR
        CS["Stream: compliance-APEX-0016"]
        CE1["ComplianceCheckInitiated"]
        CE2["ComplianceRulePassed"]
        CE3["ComplianceCheckCompleted"]
        CS --- CE1 --- CE2 --- CE3
    end

    subgraph AgentAgg["AgentSession Aggregate"]
        direction LR
        AS["Stream: agent-credit_analysis-sess-cre-9"]
        AE1["AgentSessionStarted"]
        AE2["AgentNodeExecuted"]
        AE3["AgentSessionCompleted"]
        AS --- AE1 --- AE2 --- AE3
    end

    CompAgg -->|"ComplianceCheckCompleted\nevent signals orchestrator"| LoanAgg
    AgentAgg -->|"CreditAnalysisCompleted\nappended to credit-{id}"| LoanAgg

    style LoanAgg fill:#1f2d3d,stroke:#4a90d9,color:#fff
    style CompAgg fill:#1f3d2b,stroke:#4ab07d,color:#fff
    style AgentAgg fill:#3d2b1f,stroke:#b07d4a,color:#fff
```

---

## Key Implementation Files

| Component | File |
|---|---|
| Event store (PostgreSQL + InMemory) | `ledger/event_store.py` |
| SQL schema (events, event_streams, outbox, checkpoints) | `sql/event_store.sql` |
| LoanApplicationAggregate | `ledger/domain/aggregates/loan_application.py` |
| ComplianceRecordAggregate | `ledger/domain/aggregates/compliance_record.py` |
| AgentSessionAggregate | `ledger/domain/aggregates/agent_session.py` |
| AuditLedgerAggregate | `ledger/domain/aggregates/audit_ledger.py` |
| Upcasters (CreditAnalysisCompleted v1→v2, DecisionGenerated v1→v2) | `ledger/upcasting/upcasters.py` |
| Projection base + lag tracking | `ledger/projections/base.py` |
| ProjectionDaemon with checkpointing | `ledger/projections/daemon.py` |
| ApplicationSummaryProjection | `ledger/projections/application_summary.py` |
| ComplianceAuditProjection (with temporal query + rebuild) | `ledger/projections/compliance_audit.py` |
| AgentPerformanceProjection | `ledger/projections/agent_performance.py` |
| ManualReviewsProjection | `ledger/projections/manual_reviews.py` |
| OCC concurrency test | `tests/test_concurrency.py` |
