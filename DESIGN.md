# Phase 1 Design Notes

## Scope

Phase 1 establishes the append-only event store foundation for The Ledger:

- `events` is the immutable source of truth.
- `event_streams` holds per-stream version and lifecycle metadata.
- `projection_checkpoints` supports resumable projection workers.
- `outbox` keeps event publication in the same transaction as event persistence.

## Positioning Convention

This starter uses **0-based stream positions**:

- new stream version: `-1`
- first appended event position: `0`
- current stream version: last written event position

This matches the active `tests/phase1/` gate tests and the in-memory store used by the starter agents. It is slightly different from the older skipped PostgreSQL test file, which appears to assume 1-based positions.

## Table Design

### `events`

- `event_id`: immutable identifier for causation/audit lookups and outbox linkage.
- `stream_id`: groups events by aggregate instance for replay.
- `stream_position`: optimistic-concurrency ordering within one stream.
- `global_position`: total ordering across all streams for projections and replays.
- `event_type`: dispatch key for deserialization and handlers.
- `event_version`: supports schema evolution and read-time upcasting.
- `payload`: domain data needed to rebuild state.
- `metadata`: correlation, causation, and transport-oriented context that should not pollute domain payloads.
- `recorded_at`: authoritative write timestamp assigned by the store, not callers.

### `event_streams`

- `stream_id`: primary key for one aggregate stream.
- `aggregate_type`: lightweight classification derived from stream naming.
- `current_version`: last written stream position; `-1` before first event in this starter's convention.
- `created_at`: stream creation audit timestamp.
- `archived_at`: prevents future appends while preserving history.
- `metadata`: reserved for stream-level metadata without widening the schema prematurely.

### `projection_checkpoints`

- `projection_name`: unique identity for one projection worker or projection family.
- `last_position`: last fully processed global position.
- `updated_at`: operational visibility for lag/stall detection.

### `outbox`

- `id`: stable primary key for the publication record.
- `event_id`: ties the outgoing message to the stored event.
- `destination`: lets one store feed multiple downstream consumers.
- `payload`: serialized publication envelope.
- `created_at`: operational timestamp for monitoring and retry windows.
- `published_at`: marks successful dispatch.
- `attempts`: retry counter for resilient delivery.

## Implementation Notes

- OCC is enforced by locking the `event_streams` row with `FOR UPDATE` inside the append transaction.
- Outbox rows are written in the same transaction as event rows.
- Upcasters run only on reads (`load_stream`, `load_all`, `get_event`) and never mutate stored rows.
- The module keeps `asyncpg` optional at import time so the in-memory test double still works in lean environments.

## Missing Elements Worth Considering Later

- Add an explicit idempotency key to metadata and index it for safe retry deduplication.
- Add targeted JSONB indexes once real query patterns are known.
- Add archival reason / actor fields if archival becomes an audited workflow.
- Add a dead-letter strategy for poisoned projection events in later phases.
