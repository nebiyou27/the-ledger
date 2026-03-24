from __future__ import annotations

import json

import pytest

from ledger.event_store import InMemoryEventStore
from ledger.mcp_server import create_runtime, create_server


def _resource_json(resource_result) -> object:
    return json.loads(resource_result.contents[0].content)


@pytest.mark.asyncio
async def test_mcp_server_exposes_the_phase_6_surface():
    server = create_server(create_runtime(InMemoryEventStore()))

    tools = await server.list_tools()
    tool_names = {tool.name for tool in tools}
    assert {
        "submit_application",
        "start_agent_session",
        "record_credit_analysis",
        "record_fraud_screening",
        "record_compliance_check",
        "generate_decision",
        "record_human_review",
        "run_integrity_check",
    }.issubset(tool_names)

    error_result = await server.call_tool(
        "submit_application",
        {
            "application_id": "mcp-app-1",
            "applicant_id": "co-1",
            "requested_amount_usd": -1,
        },
    )
    assert error_result.structured_content["ok"] is False
    assert "must be > 0" in error_result.structured_content["error"]["message"]
    assert error_result.structured_content["error"]["type"] == "DomainError"
    assert error_result.structured_content["error"]["error_type"] == "DomainError"
    assert error_result.structured_content["error"]["suggested_action"] == "fix_command_and_retry"

    submitted = await server.call_tool(
        "submit_application",
        {
            "application_id": "mcp-app-1",
            "applicant_id": "co-1",
            "requested_amount_usd": 125000,
            "submitted_at": "2026-03-20T10:00:00+00:00",
            "required_document_types": [
                "application_proposal",
                "income_statement",
                "balance_sheet",
            ],
        },
    )
    assert submitted.structured_content["ok"] is True

    await server.call_tool(
        "start_agent_session",
        {
            "application_id": "mcp-app-1",
            "session_id": "credit-session-1",
            "agent_id": "credit-agent",
            "agent_type": "credit_analysis",
            "model_version": "mcp-model-1",
            "context_source": "projection-backed",
        },
    )
    await server.call_tool(
        "start_agent_session",
        {
            "application_id": "mcp-app-1",
            "session_id": "decision-session-1",
            "agent_id": "orchestrator-agent",
            "agent_type": "decision_orchestrator",
            "model_version": "mcp-model-1",
            "context_source": "projection-backed",
            "started_at": "2026-03-20T09:45:00+00:00",
        },
    )

    await server.call_tool(
        "record_credit_analysis",
        {
            "application_id": "mcp-app-1",
            "session_id": "credit-session-1",
            "model_version": "mcp-model-1",
            "risk_tier": "LOW",
            "recommended_limit_usd": 100000,
            "confidence": 0.91,
            "rationale": "Strong balance sheet and low leverage.",
            "completed_at": "2026-03-20T10:05:00+00:00",
        },
    )

    await server.call_tool(
        "record_fraud_screening",
        {
            "application_id": "mcp-app-1",
            "session_id": "fraud-session-1",
            "fraud_score": 0.12,
            "risk_level": "LOW",
            "anomalies_found": 0,
            "recommendation": "PROCEED",
            "screening_model_version": "fraud-1",
            "completed_at": "2026-03-20T10:06:00+00:00",
        },
    )

    await server.call_tool(
        "record_compliance_check",
        {
            "application_id": "mcp-app-1",
            "session_id": "compliance-session-1",
            "overall_verdict": "CLEAR",
            "rules_evaluated": 2,
            "rules_passed_count": 2,
            "rules_failed_count": 0,
            "rules_noted_count": 0,
            "has_hard_block": False,
            "regulation_set_version": "2026-Q1",
            "rules_to_evaluate": ["kyc", "sanctions"],
            "rules_passed": [
                {"rule_id": "kyc", "rule_name": "KYC", "rule_version": "1.0", "evidence_hash": "abc"},
                {"rule_id": "sanctions", "rule_name": "Sanctions", "rule_version": "1.0", "evidence_hash": "def"},
            ],
            "rules_failed": [],
            "rules_noted": [],
            "completed_at": "2026-03-20T10:07:00+00:00",
        },
    )

    decision = await server.call_tool(
        "generate_decision",
        {
            "application_id": "mcp-app-1",
            "orchestrator_session_id": "decision-session-1",
            "recommendation": "APPROVE",
            "confidence": 0.92,
            "approved_amount_usd": 100000,
            "conditions": ["Maintain minimum cash balance"],
            "executive_summary": "Approved after clean credit, fraud, and compliance review.",
            "key_risks": [],
            "contributing_sessions": [
                "credit-mcp-app-1",
                "fraud-mcp-app-1",
                "compliance-mcp-app-1",
            ],
            "model_versions": {"credit": "mcp-model-1", "orchestrator": "mcp-model-1"},
            "generated_at": "2026-03-20T10:08:00+00:00",
            "approved_by": "mcp",
            "effective_date": "2026-03-20",
        },
    )
    assert decision.structured_content["ok"] is True

    refresh = await server.call_tool("refresh_projections", {"max_rounds": 4})
    assert refresh.structured_content["ok"] is True

    integrity = await server.call_tool(
        "run_integrity_check",
        {
            "entity_type": "loan",
            "entity_id": "mcp-app-1",
        },
    )
    assert integrity.structured_content["ok"] is True
    assert integrity.structured_content["result"]["entity_type"] == "loan"

    summary = _resource_json(
        await server.read_resource("ledger://applications/mcp-app-1")
    )
    assert summary["application_id"] == "mcp-app-1"
    assert summary["state"] == "FINAL_APPROVED"
    assert summary["decision"] == "APPROVE"

    compliance = _resource_json(
        await server.read_resource("ledger://applications/mcp-app-1/compliance")
    )
    assert compliance["overall_verdict"] == "CLEAR"
    assert compliance["has_hard_block"] is False

    compliance_at = _resource_json(
        await server.read_resource("ledger://applications/mcp-app-1/compliance?as_of=2026-03-20T10:07:00Z")
    )
    assert compliance_at["overall_verdict"] == "CLEAR"

    agent_rows = _resource_json(await server.read_resource("ledger://agents/credit-agent/performance"))
    assert any(row["agent_id"] == "credit-agent" for row in agent_rows)

    stuck_sessions = _resource_json(
        await server.read_resource("ledger://agents/stuck-sessions/600000")
    )
    assert any(row["session_id"] == "decision-session-1" for row in stuck_sessions)
    stuck = next(row for row in stuck_sessions if row["session_id"] == "decision-session-1")
    assert stuck["status"] == "STARTED"
    assert stuck["is_overdue"] is True

    audit_trail = _resource_json(await server.read_resource("ledger://applications/mcp-app-1/audit-trail"))
    assert isinstance(audit_trail, list)
    assert audit_trail

    health = _resource_json(await server.read_resource("ledger://ledger/health"))
    assert health["ok"] is True
    assert health["p99_target_ms"] == 10
    assert "application_summary" in health["projections"]

    backlog = _resource_json(await server.read_resource("ledger://metrics/manual-review-backlog"))
    assert backlog["backlogCount"] == 0
    assert backlog["resolvedCount"] == 0

    stream_sizes = _resource_json(await server.read_resource("ledger://metrics/streams"))
    by_name = {row["streamName"]: row for row in stream_sizes}
    assert by_name["LoanApplication"]["eventCount"] > 0
    assert by_name["ComplianceRecord"]["eventCount"] > 0
    assert by_name["AgentSession"]["eventCount"] > 0
    assert by_name["AuditLedger"]["eventCount"] > 0
