from __future__ import annotations

import json

import pytest

from ledger.event_store import InMemoryEventStore
from ledger.mcp_server import create_runtime, create_server


def _resource_json(resource_result) -> object:
    return json.loads(resource_result.contents[0].content)


@pytest.mark.asyncio
async def test_complete_loan_lifecycle_through_mcp_only():
    server = create_server(create_runtime(InMemoryEventStore()))
    application_id = "mcp-lifecycle-1"

    # Bootstrap the application stream so the lifecycle commands have a loan to operate on.
    await server.call_tool(
        "submit_application",
        {
            "application_id": application_id,
            "applicant_id": "co-lifecycle-1",
            "requested_amount_usd": 150000,
            "submitted_at": "2026-03-20T09:00:00Z",
            "required_document_types": [
                "application_proposal",
                "income_statement",
                "balance_sheet",
            ],
        },
    )

    credit_session_id = "credit-session-lifecycle-1"
    fraud_session_id = "fraud-session-lifecycle-1"
    compliance_session_id = "compliance-session-lifecycle-1"
    decision_session_id = "decision-session-lifecycle-1"

    await server.call_tool(
        "start_agent_session",
        {
            "application_id": application_id,
            "session_id": credit_session_id,
            "agent_id": "credit-agent-lifecycle",
            "agent_type": "credit_analysis",
            "model_version": "lifecycle-model-1",
            "context_source": "projection-backed",
        },
    )
    await server.call_tool(
        "record_credit_analysis",
        {
            "application_id": application_id,
            "session_id": credit_session_id,
            "agent_type": "credit_analysis",
            "model_version": "lifecycle-model-1",
            "risk_tier": "LOW",
            "recommended_limit_usd": 140000,
            "confidence": 0.91,
            "rationale": "Strong coverage and consistent historicals.",
            "completed_at": "2026-03-20T09:05:00Z",
        },
    )

    await server.call_tool(
        "start_agent_session",
        {
            "application_id": application_id,
            "session_id": fraud_session_id,
            "agent_id": "fraud-agent-lifecycle",
            "agent_type": "fraud_detection",
            "model_version": "lifecycle-model-1",
            "context_source": "projection-backed",
        },
    )
    await server.call_tool(
        "record_fraud_screening",
        {
            "application_id": application_id,
            "session_id": fraud_session_id,
            "fraud_score": 0.08,
            "risk_level": "LOW",
            "anomalies_found": 0,
            "recommendation": "PROCEED",
            "screening_model_version": "fraud-model-1",
            "completed_at": "2026-03-20T09:06:00Z",
        },
    )

    await server.call_tool(
        "record_compliance_check",
        {
            "application_id": application_id,
            "session_id": compliance_session_id,
            "overall_verdict": "CLEAR",
            "rules_evaluated": 1,
            "rules_passed_count": 1,
            "rules_failed_count": 0,
            "rules_noted_count": 0,
            "has_hard_block": False,
            "regulation_set_version": "2026-Q1",
            "rules_to_evaluate": ["kyc"],
            "rules_passed": [
                {
                    "rule_id": "kyc",
                    "rule_name": "KYC",
                    "rule_version": "1.0",
                    "evidence_hash": "abc123",
                }
            ],
            "rules_failed": [],
            "rules_noted": [],
            "completed_at": "2026-03-20T09:07:00Z",
        },
    )

    await server.call_tool(
        "start_agent_session",
        {
            "application_id": application_id,
            "session_id": decision_session_id,
            "agent_id": "decision-agent-lifecycle",
            "agent_type": "decision_orchestrator",
            "model_version": "lifecycle-model-1",
            "context_source": "projection-backed",
        },
    )
    await server.call_tool(
        "generate_decision",
        {
            "application_id": application_id,
            "orchestrator_session_id": decision_session_id,
            "recommendation": "APPROVE",
            "confidence": 0.55,
            "approved_amount_usd": None,
            "conditions": ["Maintain liquidity ratio"],
            "executive_summary": "Proceeding to human review.",
            "key_risks": [],
            "contributing_sessions": [
                f"credit-{application_id}",
                f"fraud-{application_id}",
                f"compliance-{application_id}",
            ],
            "model_versions": {"credit": "lifecycle-model-1", "orchestrator": "lifecycle-model-1"},
            "generated_at": "2026-03-20T09:08:00Z",
            "review_reason": "Confidence below approval threshold",
            "approved_by": "auto",
        },
    )

    await server.call_tool(
        "record_human_review",
        {
            "application_id": application_id,
            "reviewer_id": "human-reviewer-1",
            "final_decision": "APPROVE",
            "override": False,
            "original_recommendation": "REFER",
            "approved_amount_usd": 140000,
            "conditions": ["Maintain liquidity ratio"],
            "interest_rate_pct": 11.75,
            "term_months": 36,
            "effective_date": "2026-03-20",
            "decline_reasons": [],
            "adverse_action_notice_required": False,
            "adverse_action_codes": [],
            "reviewed_at": "2026-03-20T09:09:00Z",
        },
    )

    await server.call_tool(
        "run_integrity_check",
        {
            "entity_type": "loan",
            "entity_id": application_id,
        },
    )

    compliance = _resource_json(await server.read_resource(f"ledger://applications/{application_id}/compliance"))
    assert compliance["application_id"] == application_id
    assert compliance["overall_verdict"] == "CLEAR"
    assert len(compliance["passed_rules"]) == 1
    assert compliance["passed_rules"][0]["rule_id"] == "kyc"

    audit_trail = _resource_json(await server.read_resource(f"ledger://applications/{application_id}/audit-trail"))
    assert isinstance(audit_trail, list)
    assert audit_trail
    assert any(event["event_type"] == "AuditIntegrityCheckRun" for event in audit_trail)

    health = _resource_json(await server.read_resource("ledger://ledger/health"))
    assert health["ok"] is True
    assert health["p99_target_ms"] == 10
    assert "application_summary" in health["projections"]
