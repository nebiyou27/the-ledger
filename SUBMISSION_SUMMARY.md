# Submission Summary

Date: 2026-03-25

## What Was Delivered

Phases 1 through 5 are complete, plus the bonus/demo work:

- Phase 1: event store baseline verified, including append-only storage, stream versioning, and optimistic concurrency control.
- Phase 2: domain aggregates, command handlers, and registry wiring verified.
- Phase 2.5: document pipeline bridge implemented and wired into the extraction flow.
- Phase 3A: credit and fraud agent implementations completed and validated.
- Phase 3B: compliance and decision orchestration completed, including hard-constraint enforcement.
- Phase 4: projections and async daemon hardened and verified.
- Phase 5: upcasting, integrity, and Gas Town recovery verified.
- Bonus: narrative polish and regulatory package generation completed.

Key design choices:

- The system remains event-sourced, with append-only streams as the source of truth.
- Upcasting happens at the read boundary, not at write time.
- Compliance remains deterministic Python logic rather than LLM-driven.
- The pipeline keeps a local fallback path for dev and test workflows.

## What Was Added Beyond The Challenge

- OpenRouter-backed OpenAI SDK adapter in `ledger/agents/llm_adapter.py`.
- Model and environment updates for `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, and fallback `OLLAMA_MODEL`.
- CLI naming cleanup so the pipeline now says `--llm openrouter` instead of `--llm ollama`.
- Regression coverage proving read-time upcasting is transparent and non-mutating.
- A realistic operational cost report at `artifacts/api_cost_report.txt`.
- A clean submission checklist entry in `task.md`.

## Test Suite State

Final test result:

- `146 passed`
- `8 skipped`

The skipped tests are all PostgreSQL-only event-store tests in `tests/test_event_store.py`:

- line 27: requires PostgreSQL
- line 32: requires PostgreSQL
- line 38: requires PostgreSQL
- line 45: requires PostgreSQL
- line 59: requires PostgreSQL
- line 67: requires PostgreSQL
- line 72: requires PostgreSQL
- line 76: requires PostgreSQL

## Known Trade-Offs

- Polling vs WAL-driven CDC: the current architecture uses the existing event-store and projection flow rather than a WAL-native CDC pipeline. That keeps the system simpler and more portable, but it is not the lowest-latency option.
- Narrative coverage: the narrative and demo flows are present, but some evidence is still based on manual smoke verification rather than a separate dedicated test folder.
- Cost estimates: the API cost report uses representative token estimates from the current prompts, so real spend will vary with prompt growth and response length.
- Legacy fallback: the local Ollama path remains in place for offline and zero-cost dev/test runs.

## How To Run It

The repository README already covers the evaluator path clearly:

- `docker compose up --build` to start the full stack
- `pip install -r requirements.txt` for local Python setup
- `psql ... -f src/schema.sql` and `psql ... -f sql/event_store.sql` for schema bootstrap
- `python scripts/run_pipeline.py --app APEX-0007 --llm mock` for a single-app demo run

Required environment variables are documented in `README.md` and `.env.example`:

- `DATABASE_URL`
- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL`
- `OLLAMA_MODEL` for local fallback
- `TEST_DB_URL` for PostgreSQL-only tests

## Notes

- The OpenRouter model choice is `google/gemini-2.5-flash`.
- The cost report and README both note the live pricing date so the estimate is auditable.
- The repo is ready for a fresh clone, `.env` fill-in, and a one-command Docker launch.
