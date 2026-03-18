# The Ledger Starter

Professional starter for a multi-agent, event-sourced credit decision system.  
This repository is designed as a phased implementation track with test gates for each milestone.

## What This Starter Includes

- Event schema and validator for ledger domain events
- Synthetic data generation pipeline (documents + seed events)
- Phase-based tests to guide implementation
- Scaffolding for:
  - Event store and registry client
  - Domain aggregates
  - Agent pipeline
  - Projections and upcasters
  - MCP integration

Reference design notes for Phase 1 are in `DESIGN.md`.

## Architecture Snapshot

The system follows event sourcing patterns:

- `events` is the immutable source of truth
- `event_streams` tracks per-stream version and lifecycle metadata
- `projection_checkpoints` supports resumable projections
- `outbox` enables reliable publication in the same transaction as event writes

Stream position convention in this starter is **0-based**:

- new stream version: `-1`
- first event position: `0`
- current stream version: last event position written

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

Or with `uv` (locked):

```bash
uv sync --all-groups
```

### 2. Start PostgreSQL

```bash
docker run -d --name ledger-postgres -e POSTGRES_PASSWORD=apex -e POSTGRES_DB=apex_ledger -p 5432:5432 postgres:16
```

### 2b. Run database schema migration

```bash
psql postgresql://postgres:apex@localhost/apex_ledger -f src/schema.sql
```

### 3. Configure environment

```bash
cp .env.example .env
```

Set at minimum:

- `ANTHROPIC_API_KEY`
- `DATABASE_URL`

### 4. Generate dataset and seed events

```bash
python datagen/generate_all.py --db-url postgresql://postgres:apex@localhost/apex_ledger
```

### 5. Validate schema only (no DB write)

```bash
python datagen/generate_all.py --skip-db --skip-docs --validate-only
```

### 6. Run test gates

```bash
pytest tests/test_schema_and_generator.py -v
pytest tests/test_event_store.py -v
pytest tests/test_narratives.py -v
pytest tests/test_concurrency.py -v
```

## Implementation Roadmap

| Component | Primary Path | Phase |
|---|---|---|
| EventStore | `ledger/event_store.py` | 1 |
| ApplicantRegistryClient | `ledger/registry/client.py` | 1 |
| Domain aggregates | `ledger/domain/` | 2 |
| Agent behavior | `ledger/agents/` | 2-3 |
| Projections | `ledger/projections/` | 4 |
| Upcasters | `ledger/upcasters.py` | 4 |
| MCP server | `ledger/mcp_server.py` | 5 |

## Project Layout

```text
.
  src/              # Deliverable compatibility paths (schema, handlers, models)
  datagen/          # Synthetic dataset and event generation
  data/             # Generated artifacts
  ledger/           # Core runtime and domain code
  scripts/          # Utility scripts
  sql/              # Database schema and setup
  tests/            # Phase-based gates
```

## Sunday Interim Deliverables

- SQL schema: `src/schema.sql`
- Event store class: `src/event_store.py`
- Event models + exceptions: `src/models/events.py`
- Loan aggregate: `src/aggregates/loan_application.py`
- Agent session aggregate: `src/aggregates/agent_session.py`
- Command handlers: `src/commands/handlers.py`
- Concurrency test: `tests/test_concurrency.py`
- Locked dependencies: `pyproject.toml` (use `uv sync`)

## Environment Variables

See `.env.example` for full values.

- `ANTHROPIC_API_KEY`: model access key
- `DATABASE_URL`: primary ledger database
- `APPLICANT_REGISTRY_URL`: registry source
- `DOCUMENTS_DIR`: local generated documents directory
- `REGULATION_VERSION`: rule set version
- `LOG_LEVEL`: runtime logging level

## Testing Strategy

Tests are organized to match phased delivery.  
Recommended order:

```bash
pytest tests/test_schema_and_generator.py -v
pytest tests/test_event_store.py -v
pytest tests/test_registry_client.py -v
pytest tests/test_credit_agent_registry_wiring.py -v
pytest tests/test_narratives.py -v
```

## Notes

- `asyncpg` is optional at import time to keep in-memory testing lightweight.
- Upcasters are expected to run on reads, not mutate persisted event records.
- Keep append operations transaction-safe with optimistic concurrency checks.
