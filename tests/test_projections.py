from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from ledger.event_store import InMemoryEventStore
from ledger.projections import (
    AgentPerformanceProjection,
    AgentSessionFailureProjection,
    ApplicationSummaryProjection,
    ComplianceAuditProjection,
    ProjectionDaemon,
)
from src.commands.handlers import (
    handle_credit_analysis_completed,
    handle_start_agent_session,
    handle_submit_application,
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.mark.asyncio
async def test_projection_daemon_updates_checkpoints_and_summary():
    store = InMemoryEventStore()
    now = datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc)

    await store.append(
        "loan-APEX-100",
        [
            {
                "event_type": "ApplicationSubmitted",
                "payload": {
                    "application_id": "APEX-100",
                    "applicant_id": "COMP-100",
                    "requested_amount_usd": 15000,
                },
                "recorded_at": _iso(now),
            },
            {
                "event_type": "DecisionGenerated",
                "payload": {
                    "application_id": "APEX-100",
                    "orchestrator_session_id": "sess-orc-100",
                    "recommendation": "REFER",
                    "confidence": 0.58,
                },
                "recorded_at": _iso(now + timedelta(seconds=1)),
            },
        ],
        expected_version=-1,
    )

    app = ApplicationSummaryProjection()
    daemon = ProjectionDaemon(store, [app])
    processed = await daemon._process_batch()
    assert processed > 0

    checkpoint = await store.load_checkpoint("application_summary")
    assert checkpoint >= 2
    row = app.get_application("APEX-100")
    assert row is not None
    assert row["applicant_id"] == "COMP-100"
    assert row["decision"] == "REFER"


@pytest.mark.asyncio
async def test_compliance_audit_temporal_query_and_rebuild():
    store = InMemoryEventStore()
    t0 = datetime(2026, 3, 19, 11, 0, tzinfo=timezone.utc)

    await store.append(
        "compliance-APEX-200",
        [
            {
                "event_type": "ComplianceCheckInitiated",
                "payload": {
                    "application_id": "APEX-200",
                    "session_id": "sess-com-1",
                    "regulation_set_version": "2026-Q1",
                    "rules_to_evaluate": ["REG-001", "REG-002"],
                },
                "recorded_at": _iso(t0),
            },
            {
                "event_type": "ComplianceRulePassed",
                "payload": {
                    "application_id": "APEX-200",
                    "session_id": "sess-com-1",
                    "rule_id": "REG-001",
                    "rule_name": "AML",
                    "rule_version": "1.0",
                    "evidence_hash": "h1",
                },
                "recorded_at": _iso(t0 + timedelta(seconds=1)),
            },
            {
                "event_type": "ComplianceCheckCompleted",
                "payload": {
                    "application_id": "APEX-200",
                    "session_id": "sess-com-1",
                    "rules_evaluated": 2,
                    "rules_passed": 1,
                    "rules_failed": 0,
                    "rules_noted": 0,
                    "has_hard_block": False,
                    "overall_verdict": "CLEAR",
                },
                "recorded_at": _iso(t0 + timedelta(seconds=2)),
            },
        ],
        expected_version=-1,
    )

    comp = ComplianceAuditProjection()
    daemon = ProjectionDaemon(store, [comp])
    await daemon._process_batch()

    current = comp.get_current_compliance("APEX-200")
    assert current is not None
    assert current["overall_verdict"] == "CLEAR"
    assert len(current["passed_rules"]) == 1

    compliance_events = await store.load_stream("compliance-APEX-200")
    at_t1 = comp.get_compliance_at("APEX-200", compliance_events[1]["recorded_at"])
    assert at_t1 is not None
    assert at_t1["overall_verdict"] is None
    assert len(at_t1["passed_rules"]) == 1

    await comp.rebuild_from_scratch(store)
    rebuilt = comp.get_current_compliance("APEX-200")
    assert rebuilt is not None
    assert rebuilt["overall_verdict"] == "CLEAR"


@pytest.mark.asyncio
async def test_agent_performance_projection_and_lag():
    store = InMemoryEventStore()
    t0 = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)

    await store.append(
        "agent-credit_analysis-sess-cre-9",
        [
            {
                "event_type": "AgentSessionStarted",
                "payload": {
                    "session_id": "sess-cre-9",
                    "agent_type": "credit_analysis",
                    "agent_id": "agent-cre-9",
                    "application_id": "APEX-900",
                    "model_version": "model-v9",
                    "context_source": "fresh",
                },
                "recorded_at": _iso(t0),
            }
        ],
        expected_version=-1,
    )
    await store.append(
        "credit-APEX-900",
        [
            {
                "event_type": "CreditAnalysisCompleted",
                "payload": {
                    "application_id": "APEX-900",
                    "session_id": "sess-cre-9",
                    "decision": {"risk_tier": "LOW", "confidence": 0.88},
                    "analysis_duration_ms": 1234,
                },
                "recorded_at": _iso(t0 + timedelta(seconds=1)),
            }
        ],
        expected_version=-1,
    )

    perf = AgentPerformanceProjection()
    daemon = ProjectionDaemon(store, [perf])
    await daemon._process_batch()

    rows = perf.all_rows()
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "agent-cre-9"
    assert rows[0]["analyses_completed"] == 1
    assert rows[0]["avg_duration_ms"] == 1234

    lag = daemon.get_lag("agent_performance")
    assert lag.positions_behind == 0


@pytest.mark.asyncio
async def test_agent_session_failure_projection_finds_overdue_open_sessions():
    store = InMemoryEventStore()
    t0 = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)

    await store.append(
        "agent-credit_analysis-sess-open",
        [
            {
                "event_type": "AgentSessionStarted",
                "payload": {
                    "session_id": "sess-open",
                    "agent_type": "credit_analysis",
                    "agent_id": "agent-open",
                    "application_id": "APEX-901",
                    "model_version": "model-v1",
                    "context_source": "fresh",
                    "started_at": _iso(t0),
                },
                "recorded_at": _iso(t0),
            }
        ],
        expected_version=-1,
    )
    await store.append(
        "agent-credit_analysis-sess-complete",
        [
            {
                "event_type": "AgentSessionStarted",
                "payload": {
                    "session_id": "sess-complete",
                    "agent_type": "credit_analysis",
                    "agent_id": "agent-complete",
                    "application_id": "APEX-902",
                    "model_version": "model-v1",
                    "context_source": "fresh",
                    "started_at": _iso(t0 + timedelta(minutes=1)),
                },
                "recorded_at": _iso(t0 + timedelta(minutes=1)),
            },
            {
                "event_type": "AgentSessionCompleted",
                "payload": {
                    "session_id": "sess-complete",
                    "agent_type": "credit_analysis",
                    "application_id": "APEX-902",
                    "total_nodes_executed": 3,
                    "total_llm_calls": 1,
                    "total_tokens_used": 250,
                    "total_cost_usd": 0.12,
                    "total_duration_ms": 5000,
                    "completed_at": _iso(t0 + timedelta(minutes=2)),
                },
                "recorded_at": _iso(t0 + timedelta(minutes=2)),
            },
        ],
        expected_version=-1,
    )
    await store.append(
        "agent-credit_analysis-sess-failed",
        [
            {
                "event_type": "AgentSessionStarted",
                "payload": {
                    "session_id": "sess-failed",
                    "agent_type": "credit_analysis",
                    "agent_id": "agent-failed",
                    "application_id": "APEX-903",
                    "model_version": "model-v1",
                    "context_source": "fresh",
                    "started_at": _iso(t0 + timedelta(minutes=3)),
                },
                "recorded_at": _iso(t0 + timedelta(minutes=3)),
            },
            {
                "event_type": "AgentSessionFailed",
                "payload": {
                    "session_id": "sess-failed",
                    "agent_type": "credit_analysis",
                    "application_id": "APEX-903",
                    "error_type": "TimeoutError",
                    "error_message": "upstream timeout",
                    "recoverable": False,
                    "failed_at": _iso(t0 + timedelta(minutes=4)),
                },
                "recorded_at": _iso(t0 + timedelta(minutes=4)),
            },
        ],
        expected_version=-1,
    )

    projection = AgentSessionFailureProjection()
    daemon = ProjectionDaemon(store, [projection])
    while await daemon._process_batch():
        pass

    overdue = projection.get_stuck_sessions(timeout_ms=15 * 60 * 1000, now=t0 + timedelta(minutes=20))

    assert [row["session_id"] for row in overdue] == ["sess-open"]
    assert overdue[0]["is_overdue"] is True
    assert overdue[0]["status"] == "STARTED"


@pytest.mark.asyncio
async def test_projection_lag_under_concurrent_command_handlers():
    store = InMemoryEventStore()

    async def _handler(index: int) -> None:
        application_id = f"APEX-CONC-{index:03d}"
        session_id = f"sess-conc-{index:03d}"

        await handle_submit_application(
            store,
            {
                "application_id": application_id,
                "applicant_id": f"COMP-CONC-{index:03d}",
                "requested_amount_usd": 25000 + index,
                "submitted_at": "2026-03-19T13:00:00Z",
                "required_document_types": [
                    "application_proposal",
                    "income_statement",
                    "balance_sheet",
                ],
            },
        )
        await handle_start_agent_session(
            store,
            {
                "application_id": application_id,
                "session_id": session_id,
                "agent_id": f"credit-agent-{index:03d}",
                "agent_type": "credit_analysis",
                "model_version": "conc-model-1",
                "context_source": "projection-backed",
            },
        )
        await handle_credit_analysis_completed(
            store,
            {
                "application_id": application_id,
                "session_id": session_id,
                "agent_type": "credit_analysis",
                "model_version": "conc-model-1",
                "risk_tier": "LOW",
                "recommended_limit_usd": 20000 + index,
                "confidence": 0.9,
                "rationale": "Concurrent handler stress test.",
                "completed_at": "2026-03-19T13:01:00Z",
            },
        )

    await asyncio.gather(*(_handler(index) for index in range(50)))

    app = ApplicationSummaryProjection()
    comp = ComplianceAuditProjection()
    daemon = ProjectionDaemon(store, [app, comp])

    while await daemon._process_batch():
        pass

    app_lag = daemon.get_lag("application_summary")
    comp_lag = daemon.get_lag("compliance_audit")

    assert app_lag.positions_behind == 0
    assert app_lag.millis < 500
    assert comp_lag.millis < 2000
