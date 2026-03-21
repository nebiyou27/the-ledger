# The Ledger

Event-sourced multi-agent credit decision system for the Week 5 challenge.  
This repository is implemented as a phased delivery track with test gates for each milestone.

## What This Repository Includes

- Event schema and validator for ledger domain events
- Synthetic data generation pipeline (documents + seed events)
- Phase-based tests to guide implementation
- Scaffolding for:
  - Event store and registry client
  - Domain aggregates
  - Agent pipeline
  - Projections and upcasters
  - MCP integration

Reference design notes are in `DESIGN.md`.

## Architecture Snapshot

The system follows event sourcing patterns:

- `events` is the immutable source of truth
- `event_streams` tracks per-stream version and lifecycle metadata
- `projection_checkpoints` supports resumable projections
- `outbox` enables reliable publication in the same transaction as event writes

Stream position convention in this repository is **0-based**:

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

## Environment Variables

See `.env.example` for full values.

- `ANTHROPIC_API_KEY`: model access key
- `DATABASE_URL`: primary ledger database
- `APPLICANT_REGISTRY_URL`: registry source
- `DOCUMENTS_DIR`: local generated documents directory
- `REGULATION_VERSION`: rule set version
- `LOG_LEVEL`: runtime logging level
- `LEDGER_API_KEYS`: optional comma-separated `role=token` pairs for API access control

## Demo Access

When `LEDGER_API_KEYS` is set, use the matching Bearer token for each screen:

- `viewer`: applications list, application detail, and timeline
- `reviewer`: review queue
- `compliance`: compliance view
- `analyst`: agent performance view
- `admin`: all screens, including refresh

The sample `.env.example` pairs `NEXT_PUBLIC_LEDGER_API_KEY=dev-viewer-key` with the viewer role so the default frontend can read the application screens without extra setup.

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
- To inspect a single application end-to-end, run `python scripts/audit_application.py --application-id APEX-0007`.
- The script also writes a JSON artifact to `artifacts/audit-APEX-0007.json` by default.

## Known Limitations

- Security hardening is intentionally lightweight in this challenge build. The API supports API-key authentication and role-based authorization when `LEDGER_API_KEYS` is configured, but CORS remains permissive for demo convenience and the model is still not production-hardened.
- Compliance state is projection-backed first and read-side reconstructed as a fallback when needed. That makes the system resilient during demos, but it still depends on projection freshness for the best experience.
- Integrity verification is read-only now, which is the safer behavior for audit checks. If you want long-lived tamper evidence, the next step would be a dedicated immutable audit store rather than relying on the application event streams alone.
