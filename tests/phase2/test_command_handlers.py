from __future__ import annotations

import pytest

from ledger.event_store import InMemoryEventStore
from src.commands.handlers import (
    handle_credit_analysis_completed,
    handle_fraud_screening_completed,
    handle_generate_decision,
    handle_start_agent_session,
)


@pytest.mark.asyncio
async def test_handle_start_agent_session_appends_first_event():
    store = InMemoryEventStore()

    positions = await handle_start_agent_session(
        store,
        {
            "application_id": "APEX-1",
            "session_id": "sess-1",
            "agent_id": "agent-1",
            "agent_type": "credit_analysis",
            "model_version": "model-v1",
            "context_source": "fresh",
            "context_token_count": 1200,
        },
    )

    assert positions == [0]
    events = await store.load_stream("agent-credit_analysis-sess-1")
    assert [e["event_type"] for e in events] == ["AgentSessionStarted"]


@pytest.mark.asyncio
async def test_credit_analysis_rejects_duplicate_without_human_supersede():
    store = InMemoryEventStore()

    await store.append(
        "loan-APEX-1",
        [
            {"event_type": "ApplicationSubmitted", "payload": {"applicant_id": "COMP-1", "requested_amount_usd": 1000, "loan_purpose": "working_capital"}},
            {"event_type": "DocumentUploadRequested", "payload": {}},
            {"event_type": "DocumentUploaded", "payload": {}},
            {"event_type": "CreditAnalysisRequested", "payload": {}},
        ],
        expected_version=-1,
    )
    await store.append(
        "agent-credit_analysis-sess-cre-1",
        [
            {
                "event_type": "AgentSessionStarted",
                "payload": {
                    "session_id": "sess-cre-1",
                    "agent_type": "credit_analysis",
                    "agent_id": "agent-1",
                    "application_id": "APEX-1",
                    "model_version": "model-v1",
                    "context_source": "fresh",
                },
            }
        ],
        expected_version=-1,
    )

    cmd = {
        "application_id": "APEX-1",
        "session_id": "sess-cre-1",
        "agent_type": "credit_analysis",
        "model_version": "model-v1",
        "risk_tier": "MEDIUM",
        "recommended_limit_usd": 800,
        "confidence": 0.77,
    }
    await handle_credit_analysis_completed(store, cmd)

    with pytest.raises(ValueError):
        await handle_credit_analysis_completed(store, cmd)


@pytest.mark.asyncio
async def test_generate_decision_low_confidence_forces_refer():
    store = InMemoryEventStore()

    await store.append(
        "loan-APEX-2",
        [
            {"event_type": "ApplicationSubmitted", "payload": {"applicant_id": "COMP-2", "requested_amount_usd": 5000, "loan_purpose": "working_capital"}},
            {"event_type": "DocumentUploadRequested", "payload": {}},
            {"event_type": "DocumentUploaded", "payload": {}},
            {"event_type": "CreditAnalysisRequested", "payload": {}},
            {"event_type": "CreditAnalysisCompleted", "payload": {}},
            {"event_type": "FraudScreeningRequested", "payload": {}},
            {"event_type": "FraudScreeningCompleted", "payload": {}},
            {"event_type": "ComplianceCheckRequested", "payload": {}},
            {"event_type": "ComplianceCheckCompleted", "payload": {"overall_verdict": "CLEAR"}},
            {"event_type": "DecisionRequested", "payload": {}},
        ],
        expected_version=-1,
    )
    await store.append(
        "agent-decision_orchestrator-sess-orc-1",
        [
            {
                "event_type": "AgentSessionStarted",
                "payload": {
                    "session_id": "sess-orc-1",
                    "agent_type": "decision_orchestrator",
                    "agent_id": "agent-orc-1",
                    "application_id": "APEX-2",
                    "model_version": "model-v2",
                    "context_source": "fresh",
                },
            },
            {
                "event_type": "DecisionGenerated",
                "payload": {
                    "application_id": "APEX-2",
                    "recommendation": "REFER",
                    "confidence": 0.65,
                },
            },
        ],
        expected_version=-1,
    )

    await handle_generate_decision(
        store,
        {
            "application_id": "APEX-2",
            "orchestrator_session_id": "sess-orc-1",
            "recommendation": "APPROVE",
            "confidence": 0.41,
            "approved_amount_usd": 3200,
            "contributing_sessions": ["agent-decision_orchestrator-sess-orc-1"],
        },
    )

    loan_events = await store.load_stream("loan-APEX-2")
    assert loan_events[-2]["event_type"] == "DecisionGenerated"
    assert loan_events[-2]["payload"]["recommendation"] == "REFER"
    assert loan_events[-1]["event_type"] == "HumanReviewRequested"


@pytest.mark.asyncio
async def test_generate_decision_rejects_contributing_session_without_decision_event():
    store = InMemoryEventStore()
    await store.append(
        "loan-APEX-3",
        [
            {"event_type": "ApplicationSubmitted", "payload": {"applicant_id": "COMP-3", "requested_amount_usd": 9000, "loan_purpose": "working_capital"}},
            {"event_type": "DocumentUploadRequested", "payload": {}},
            {"event_type": "DocumentUploaded", "payload": {}},
            {"event_type": "CreditAnalysisRequested", "payload": {}},
            {"event_type": "CreditAnalysisCompleted", "payload": {}},
            {"event_type": "FraudScreeningRequested", "payload": {}},
            {"event_type": "FraudScreeningCompleted", "payload": {}},
            {"event_type": "ComplianceCheckRequested", "payload": {}},
            {"event_type": "ComplianceCheckCompleted", "payload": {"overall_verdict": "CLEAR"}},
            {"event_type": "DecisionRequested", "payload": {}},
        ],
        expected_version=-1,
    )
    await store.append(
        "agent-decision_orchestrator-sess-orc-2",
        [
            {
                "event_type": "AgentSessionStarted",
                "payload": {
                    "session_id": "sess-orc-2",
                    "agent_type": "decision_orchestrator",
                    "agent_id": "agent-orc-2",
                    "application_id": "APEX-3",
                    "model_version": "model-v2",
                    "context_source": "fresh",
                },
            }
        ],
        expected_version=-1,
    )

    with pytest.raises(ValueError):
        await handle_generate_decision(
            store,
            {
                "application_id": "APEX-3",
                "orchestrator_session_id": "sess-orc-2",
                "recommendation": "APPROVE",
                "confidence": 0.8,
                "approved_amount_usd": 7000,
                "contributing_sessions": ["agent-decision_orchestrator-sess-orc-2"],
            },
        )


@pytest.mark.asyncio
async def test_fraud_screening_completed_appends_fraud_and_loan_events():
    store = InMemoryEventStore()
    await store.append(
        "loan-APEX-4",
        [
            {"event_type": "ApplicationSubmitted", "payload": {"applicant_id": "COMP-4", "requested_amount_usd": 8000, "loan_purpose": "working_capital"}},
            {"event_type": "DocumentUploadRequested", "payload": {}},
            {"event_type": "DocumentUploaded", "payload": {}},
            {"event_type": "CreditAnalysisRequested", "payload": {}},
            {"event_type": "CreditAnalysisCompleted", "payload": {}},
            {"event_type": "FraudScreeningRequested", "payload": {}},
        ],
        expected_version=-1,
    )

    written = await handle_fraud_screening_completed(
        store,
        {
            "application_id": "APEX-4",
            "session_id": "sess-fraud-1",
            "fraud_score": 0.21,
            "risk_level": "LOW",
            "anomalies_found": 0,
            "recommendation": "PROCEED",
            "screening_model_version": "fraud-v1",
            "input_data_hash": "h1",
            "rules_to_evaluate": ["REG-001", "REG-002"],
        },
    )

    assert written == [0]
    fraud_events = await store.load_stream("fraud-APEX-4")
    assert [e["event_type"] for e in fraud_events] == ["FraudScreeningCompleted"]

    loan_events = await store.load_stream("loan-APEX-4")
    assert loan_events[-2]["event_type"] == "FraudScreeningCompleted"
    assert loan_events[-1]["event_type"] == "ComplianceCheckRequested"
