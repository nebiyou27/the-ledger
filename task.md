# The Ledger — Master Task Checklist

## Phase 0 — Lock the Baseline
- [x] Explore full directory tree and map what exists
- [x] Read EventStore (real Postgres + InMemory)
- [x] Read all 4 aggregates: LoanApplication, AgentSession, ComplianceRecord, AuditLedger
- [x] Read command handlers (534-line real implementation)
- [x] Read event schema (717-line, 7 aggregates, 37 event types)
- [x] Read all projections: ApplicationSummary, AgentPerformance, ComplianceAudit, ManualReviews, Daemon
- [x] Read integrity: AuditChain, GasTown
- [x] Read upcasters (real: CreditAnalysisCompleted v1→v2, DecisionGenerated v1→v2)
- [x] Read ComplianceAgent (mostly implemented), confirm other 3 agents are stubs
- [x] Check existing test output (pytest_output.txt — 1 import error found)
- [x] Fix ManualReviewsProjection import bug (already was [Projection](file:///c:/Users/hp/Documents/the-ledger/ledger/projections/base.py#23-64), no bug)
- [x] Fix BaseApexAgent missing `client=None` kwarg (caused 4 test failures)
- [x] Run full baseline test suite → **57 passed, 14 skipped (Postgres), 0 failed**
- [x] Save [artifacts/test_results_baseline.txt](file:///c:/Users/hp/Documents/the-ledger/artifacts/test_results_baseline.txt)
- [x] Verify PostgreSQL connection to `apex_ledger`

## Phase 1 — Verify and Finish Postgres EventStore
- [x] Inspect schema.sql — all 4 required tables confirmed in [sql/event_store.sql](file:///c:/Users/hp/Documents/the-ledger/sql/event_store.sql)
- [x] Verify [append()](file:///c:/Users/hp/Documents/the-ledger/ledger/event_store.py#113-207) writes events + outbox rows in same transaction
- [x] Verify OCC using `expected_version`
- [x] Verify [stream_version()](file:///c:/Users/hp/Documents/the-ledger/ledger/event_store.py#384-386), [load_stream()](file:///c:/Users/hp/Documents/the-ledger/ledger/event_store.py#443-458), [load_all()](file:///c:/Users/hp/Documents/the-ledger/ledger/event_store.py#459-478)
- [x] Run `pytest tests/test_event_store.py -v`
- [x] Run `pytest tests/test_concurrency.py -v`
- [x] Save `artifacts/test_results_phase1.txt`
- [x] Save `artifacts/occ_collision_report.txt`

## Phase 2 — Complete Aggregates, Command Handlers & Registry
- [x] Verify LoanApplicationAggregate [load()](file:///c:/Users/hp/Documents/the-ledger/ledger/domain/aggregates/loan_application.py#72-80) and [apply()](file:///c:/Users/hp/Documents/the-ledger/ledger/domain/aggregates/audit_ledger.py#26-58) completeness
- [x] Verify AgentSessionAggregate Gas Town enforcement
- [x] Verify ComplianceRecordAggregate
- [x] Verify AuditLedgerAggregate
- [x] Verify/implement ApplicantRegistryClient 4 query methods
- [x] Verify command handlers enforce all 6 business rules
- [x] Run `pytest tests/phase2/ -v`
- [x] Save `artifacts/test_results_phase2.txt`

## Phase 2.5 — Document Pipeline Bridge
- [x] Implement `DocumentProcessingAgent._node_validate_inputs`
- [x] Implement `DocumentProcessingAgent._node_validate_formats`
- [x] Implement `DocumentProcessingAgent._node_extract_is`
- [x] Implement `DocumentProcessingAgent._node_extract_bs`
- [x] Implement `DocumentProcessingAgent._node_assess_quality`
- [x] Implement `DocumentProcessingAgent._node_write_output`
- [x] Wire to datagen/ extractor adapter
- [x] Verify end-to-end document processing for one application
- [x] `artifacts/document_pipeline_report.txt`

## Phase 3A — Credit and Fraud Agents
- [x] Verify CreditAnalysisAgent (already provided as reference)
- [x] Implement [FraudDetectionAgent](file:///c:/Users/hp/Documents/the-ledger/ledger/agents/stub_agents.py#190-262) all 5 nodes
- [x] Wire fraud scoring logic (revenue_discrepancy, submission_pattern, balance_consistency)
- [x] Run selected apps through credit/fraud pipeline

## Phase 3B — Compliance and Decision Orchestrator
- [x] Verify ComplianceAgent [_node_write_output](file:///c:/Users/hp/Documents/the-ledger/ledger/agents/base_agent.py#331-333) (partially done)
- [x] Implement [DecisionOrchestratorAgent](file:///c:/Users/hp/Documents/the-ledger/ledger/agents/stub_agents.py#552-631) all 7 nodes
- [x] Enforce confidence floor + human-review behaviour
- [x] Validate contributing session references
- [x] Run `NARR-01` and `NARR-04` narrative tests (repo markers skip them; manual smoke confirms behavior)
- [x] Save `artifacts/test_results_phase3.txt`
- [x] Save `artifacts/narrative_test_results_partial.txt`

## Phase 4 — Projections and Async Daemon
## Phase 4 — Projections and Async Daemon
- [x] Verify/finish [ApplicationSummary](file:///c:/Users/hp/Documents/the-ledger/ledger/projections/application_summary.py#9-105) projection
- [x] Verify/finish `AgentPerformanceLedger` projection
- [x] Verify/finish `ComplianceAuditView` projection
- [x] Implement `get_compliance_at(application_id, timestamp)`
- [x] Implement `rebuild_from_scratch()`
- [x] Finish [ProjectionDaemon](file:///c:/Users/hp/Documents/the-ledger/ledger/projections/daemon.py#9-90) fault tolerance and retry
- [x] Expose lag reporting
- [x] Run `NARR-02`
- [x] Save `artifacts/test_results_phase4_projections.txt`
- [x] Save `artifacts/projection_lag_report.txt`
## Phase 5 — Upcasting, Integrity, Gas Town Recovery
- [ ] Verify upcasters automatically applied on [load_stream()](file:///c:/Users/hp/Documents/the-ledger/ledger/event_store.py#443-458) / [load_all()](file:///c:/Users/hp/Documents/the-ledger/ledger/event_store.py#459-478)
- [x] Verify upcasters automatically applied on [load_stream()](file:///c:/Users/hp/Documents/the-ledger/ledger/event_store.py#443-458) / [load_all()](file:///c:/Users/hp/Documents/the-ledger/ledger/event_store.py#459-478)
- [x] Confirm upcasting is read-time only (immutability)
- [x] Verify SHA-256 audit chain in [audit_chain.py](file:///c:/Users/hp/Documents/the-ledger/ledger/integrity/audit_chain.py)
- [x] Implement/verify tamper detection
- [x] Implement `reconstruct_agent_context()`
- [x] Emit `NEEDS_RECONCILIATION` when recovery cannot safely proceed
- [x] Run `pytest tests/phase4/ -v` (repo has no tests/phase4 folder; verified equivalent suites instead)
- [x] Run `NARR-03` and `NARR-05`
- [x] Save `artifacts/test_results_phase5_integrity.txt`
- [x] Save `artifacts/upcasting_report.txt`
- [x] Save `artifacts/gas_town_report.txt`
## Phase 6 — MCP Server
- [x] Create MCP server entry point
- [x] Implement 8 MCP command tools
- [x] Implement 6 MCP resource queries (projection-backed)
- [x] Ensure structured error returns for tools
- [x] Run full lifecycle through MCP only
- [x] Save `artifacts/test_results_phase6_mcp.txt`

## Phase 7 — Bonus and Demo Polish
- [x] Implement `WhatIfProjector`
- [x] Implement `generate_regulatory_package()`
- [x] Create `scripts/demo_narr05.py`
- [x] Add bank-loan polish (richer fields, underwriting output)
- [x] Save `artifacts/regulatory_package_NARR05.json`
- [x] Save `artifacts/narrative_test_results.txt`

## Continuous Evidence Artifacts
- [x] [artifacts/test_results_baseline.txt](file:///c:/Users/hp/Documents/the-ledger/artifacts/test_results_baseline.txt)
- [x] `artifacts/test_results_phase1.txt`
- [x] `artifacts/occ_collision_report.txt`
- [x] `artifacts/test_results_phase2.txt`
- [x] `artifacts/document_pipeline_report.txt`
- [x] `artifacts/test_results_phase3.txt`
- [x] `artifacts/narrative_test_results_partial.txt`
- [x] `artifacts/test_results_phase4_projections.txt`
- [x] `artifacts/projection_lag_report.txt`
- [x] `artifacts/test_results_phase5_integrity.txt`
- [x] `artifacts/upcasting_report.txt`
- [x] `artifacts/gas_town_report.txt`
- [x] `artifacts/test_results_phase6_mcp.txt`
- [x] `DATA_GENERATION.md`
- [x] `artifacts/regulatory_package_NARR05.json`
- [x] `artifacts/narrative_test_results.txt`
- [ ] `artifacts/api_cost_report.txt`



