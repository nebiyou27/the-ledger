# Submission Guide

Welcome to the event-sourced ledger submission. This repository implements a full event-sourced architecture for loan processing, designed around strict aggregate boundaries, optimistic concurrency control, and distributed projection mechanics.

This guide maps the core event-sourcing rubric requirements directly to the repository evidence.

## 1. Core Documentation
Start here to understand the architectural reasoning:

- **[domain_notes_final.md](file:///c:/Users/hp/Documents/the-ledger/domain_notes_final.md)**  
  The primary intellectual defense of the system. Contains deep-dives into EDA vs Event Sourcing (§1), Aggregate boundary selection (§2), OCC tracing (§3), Projection Lag SLOs (§4), Upcasting immutability (§5), and Distributed Coordinate design (§6).
  
- **[DESIGN.md](file:///c:/Users/hp/Documents/the-ledger/DESIGN.md)**  
  High-level architectural constraints and decisions, including comparisons to EventStoreDB.

- **[ARCHITECTURE.md](file:///c:/Users/hp/Documents/the-ledger/ARCHITECTURE.md)**  
  Visual Mermaid diagrams showing the command-write path (including the transactional outbox), the asynchronous projection read path, and aggregate boundary isolation.

## 2. Test Evidence
The repository contains 69 tests. **64 pass, 5 are skipped (future agent-integration narratives), 0 fail.**
- **[test_evidence.txt](file:///c:/Users/hp/Documents/the-ledger/test_evidence.txt)**: Full `pytest` output proving the infrastructure is green.

## 3. Key Implementation Files by Rubric Area

| Feature | Implementation | Key Test Proof |
|---|---|---|
| **Event Store & OCC** | `ledger/event_store.py` | `tests/test_concurrency.py` (Proves OCC prevents lost updates and surfaces expected/actual versions) |
| **Upcasting** | `ledger/upcasting/upcasters.py` | `tests/test_upcasters.py` (Proves read-time transformation without mutation) |
| **Projection Lag Tracking** | `ledger/projections/base.py` | `tests/test_projections.py` (Validates `positions_behind` and `millis` lag metrics) |
| **Temporal Queries** | `ledger/projections/compliance_audit.py` | `tests/test_projections.py` (Validates point-in-time reconstruction) |
| **Aggregate Boundaries** | `ledger/domain/aggregates/` | `tests/phase2/test_domain_aggregates.py` (Proves constraints within boundaries) |

## Implementation Notes & Known Limitations
- **Distributed Projections:** `domain_notes_final.md` §6 outlines the production-readiness design using PostgreSQL advisory locks. The current `ProjectionDaemon` (`ledger/projections/daemon.py`) is intentionally a **single-instance worker**, but implements the checkpoint-inside-transaction pattern required by the distributed design.
- **OCC Retry:** The OCC error mechanics are proven at the data store level. The `_append_stream` retry-reload loop described in the design docs is the handler-level strategy, awaiting integration testing with the agent pipelines.
