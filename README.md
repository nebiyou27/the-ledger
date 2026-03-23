# The Ledger

Event-sourced loan decisioning system for the Week 5 challenge.

The repository models a multi-step credit workflow with:

- append-only event streams
- optimistic concurrency control
- transactional outbox delivery
- asynchronous projections with checkpoints
- narrative and MCP-based acceptance tests

If you want the rationale first, start with:

- `DESIGN.md`
- `ARCHITECTURE.md`
- `DOMAIN_NOTES.md`
- `EVENT_SCHEMA_REFERENCE.md`

## Quick Start

### Full stack with Docker

This brings up PostgreSQL, the seeding job, the backend API, and the frontend:

```bash
docker compose up --build
```

Useful endpoints:

- Backend API: `http://localhost:8000`
- Frontend: `http://localhost:3000`

### Local Python setup

```bash
pip install -r requirements.txt
```

On PowerShell, use:

```powershell
$env:DATABASE_URL = "postgresql://postgres:apex@localhost/apex_ledger"
```

Create or reset the schema:

```bash
psql postgresql://postgres:apex@localhost/apex_ledger -f src/schema.sql
psql postgresql://postgres:apex@localhost/apex_ledger -f sql/event_store.sql
```

## Demo Flows

Run the full pipeline for one application:

```bash
python scripts/run_pipeline.py --app APEX-0007 --llm mock
```

Run the NARR-05 human-override demo and write artifacts:

```bash
python scripts/demo_narr05.py --output-dir artifacts
```

The demo output includes a regulatory package JSON and a short narrative report.

## Testing

Run the main verification suite:

```bash
pytest tests/test_schema_and_generator.py -v
pytest tests/test_event_store.py -v
pytest tests/test_narratives.py -v
pytest tests/test_concurrency.py -v
```

If you want the most important acceptance checks first, start with:

```bash
pytest tests/test_narratives.py -v
pytest tests/test_mcp_server.py -v
```

## Repository Map

```text
ledger/     Core runtime, aggregates, projections, MCP, observability
src/        Compatibility layer for schema, handlers, and shared models
sql/        Database DDL for the event store
datagen/    Synthetic document and event generation
scripts/    Demo and pipeline entry points
tests/      Phase, narrative, and regression coverage
frontend/   React UI for the demo experience
```

## Core Concepts

- `events` is the source of truth.
- `event_streams` tracks versioning and lifecycle metadata.
- `outbox` is written in the same transaction as event appends.
- `projection_checkpoints` lets projections resume from the last processed position.
- Aggregates rebuild state from history rather than storing a mutable current-state row.

## Configuration

Common environment variables:

- `DATABASE_URL`
- `TEST_DB_URL`
- `ANTHROPIC_API_KEY`
- `APPLICANT_REGISTRY_URL`
- `DOCUMENTS_DIR`
- `REGULATION_VERSION`
- `LOG_LEVEL`
- `LEDGER_API_KEYS`

The sample values live in `.env.example`.

## Notes

- `src/schema.sql` is kept for schema/bootstrap compatibility.
- `sql/event_store.sql` contains the dedicated event-store DDL.
- `EVENT_SCHEMA_REFERENCE.md` documents the canonical event payloads and their versioned compatibility rules.
- `scripts/run_pipeline.py` can be run phase-by-phase with `--phase document|credit|fraud|compliance|decision|all`.
- `scripts/demo_narr05.py` is the quickest way to exercise the human-override path end to end.
