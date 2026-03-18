from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ledger.event_store import InMemoryEventStore
from ledger.projections import (
    AgentPerformanceProjection,
    ApplicationSummaryProjection,
    ComplianceAuditProjection,
    ProjectionDaemon,
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
