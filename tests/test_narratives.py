"""Narrative scenario tests for the Week 5 challenge."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ledger.demo_scenarios import build_narr05_demo_store
from ledger.event_store import InMemoryEventStore
from ledger.exceptions import OptimisticConcurrencyError
from ledger.integrity.gas_town import reconstruct_agent_context
from ledger.regulatory import generate_regulatory_package
from ledger.schema.events import AgentType
from src.commands.handlers import (
    handle_compliance_check,
    handle_credit_analysis_completed,
    handle_start_agent_session,
)


def _event(event_type: str, **payload):
    return {"event_type": event_type, "event_version": 1, "payload": payload}


def _agent_session_stream(agent_type: str, session_id: str) -> str:
    return f"agent-{agent_type}-{session_id}"


@pytest.mark.asyncio
async def test_narr01_concurrent_occ_collision():
    """
    NARR-01: Two CreditAnalysisAgent instances run simultaneously.
    Expected: one task succeeds, the other hits OCC, and the credit stream ends
    with a single CreditAnalysisCompleted event.
    """
    store = InMemoryEventStore()
    application_id = "NARR-01"

    await store.append(
        f"loan-{application_id}",
        [
            _event("ApplicationSubmitted", application_id=application_id, applicant_id="COMP-001", requested_amount_usd=250000, loan_purpose="expansion"),
            _event("DocumentUploadRequested", application_id=application_id),
            _event("DocumentUploaded", application_id=application_id),
            _event("CreditAnalysisRequested", application_id=application_id),
        ],
        expected_version=-1,
    )

    await handle_start_agent_session(
        store,
        {
            "application_id": application_id,
            "session_id": "sess-cre-a",
            "agent_id": "credit-agent-a",
            "agent_type": AgentType.CREDIT_ANALYSIS.value,
            "model_version": "credit-v1",
            "context_source": "fresh",
        },
    )
    await handle_start_agent_session(
        store,
        {
            "application_id": application_id,
            "session_id": "sess-cre-b",
            "agent_id": "credit-agent-b",
            "agent_type": AgentType.CREDIT_ANALYSIS.value,
            "model_version": "credit-v1",
            "context_source": "fresh",
        },
    )

    credit_stream = f"credit-{application_id}"
    await store.append(
        credit_stream,
        [
            _event("CreditRecordOpened", application_id=application_id, applicant_id="COMP-001", opened_at="2026-03-20T10:04:00+00:00")
        ],
        expected_version=-1,
    )

    barrier = asyncio.Event()
    ready_count = 0

    async def _attempt(session_id: str):
        nonlocal ready_count
        version = await store.stream_version(credit_stream)
        ready_count += 1
        if ready_count == 2:
            barrier.set()
        await barrier.wait()
        return await store.append(
            credit_stream,
            [
                _event(
                    "CreditAnalysisCompleted",
                    application_id=application_id,
                    session_id=session_id,
                    decision={
                        "risk_tier": "MEDIUM",
                        "recommended_limit_usd": 180000,
                        "confidence": 0.81,
                        "rationale": "Healthy revenue trend.",
                        "key_concerns": [],
                        "data_quality_caveats": [],
                        "policy_overrides_applied": [],
                    },
                    model_version="credit-v1",
                    model_deployment_id="deploy-1",
                    input_data_hash="hash-1",
                    analysis_duration_ms=1250,
                    regulatory_basis=["ability-to-repay"],
                    completed_at="2026-03-20T10:05:00+00:00",
                )
            ],
            expected_version=version,
        )

    results = await asyncio.gather(_attempt("sess-cre-a"), _attempt("sess-cre-b"), return_exceptions=True)

    assert sum(isinstance(result, OptimisticConcurrencyError) for result in results) == 1
    credit_events = await store.load_stream(credit_stream)
    assert [event["event_type"] for event in credit_events].count("CreditAnalysisCompleted") == 1


@pytest.mark.asyncio
async def test_narr02_document_extraction_failure():
    """
    NARR-02: Income statement extraction is incomplete.
    Expected: the docpkg stream records missing EBITDA, and credit analysis
    records a caveat with capped confidence.
    """
    store = InMemoryEventStore()
    application_id = "NARR-02"

    await store.append(
        f"loan-{application_id}",
        [
            _event("ApplicationSubmitted", application_id=application_id, applicant_id="COMP-002", requested_amount_usd=175000, loan_purpose="working_capital"),
            _event("DocumentUploadRequested", application_id=application_id),
            _event("DocumentUploaded", application_id=application_id),
            _event("CreditAnalysisRequested", application_id=application_id),
        ],
        expected_version=-1,
    )

    await handle_start_agent_session(
        store,
        {
            "application_id": application_id,
            "session_id": "sess-cre-narr02",
            "agent_id": "credit-agent-narr02",
            "agent_type": AgentType.CREDIT_ANALYSIS.value,
            "model_version": "credit-v1",
            "context_source": "fresh",
        },
    )

    await store.append(
        f"docpkg-{application_id}",
        [
            _event(
                "ExtractionCompleted",
                package_id=f"docpkg-{application_id}",
                document_id="doc-income-1",
                document_type="income_statement",
                facts={
                    "total_revenue": 550000,
                    "gross_profit": 220000,
                    "ebitda": None,
                    "net_income": 76000,
                    "field_confidence": {"ebitda": 0.0},
                    "extraction_notes": ["ebitda"],
                },
                raw_text_length=2048,
                tables_extracted=1,
                processing_ms=1180,
                completed_at="2026-03-20T09:15:00+00:00",
            ),
            _event(
                "QualityAssessmentCompleted",
                package_id=f"docpkg-{application_id}",
                document_id="doc-income-1",
                overall_confidence=0.63,
                is_coherent=False,
                anomalies=[],
                critical_missing_fields=["ebitda"],
                reextraction_recommended=True,
                auditor_notes="Income statement is missing EBITDA.",
                assessed_at="2026-03-20T09:15:30+00:00",
            ),
        ],
        expected_version=-1,
    )

    await handle_credit_analysis_completed(
        store,
        {
            "application_id": application_id,
            "session_id": "sess-cre-narr02",
            "model_version": "credit-v1",
            "risk_tier": "MEDIUM",
            "recommended_limit_usd": 125000,
            "confidence": 0.75,
            "rationale": "Proceeding with explicit caveat for missing EBITDA.",
            "key_concerns": ["Missing EBITDA"],
            "data_quality_caveats": ["ebitda"],
            "completed_at": "2026-03-20T10:05:00+00:00",
        },
    )

    docpkg_events = await store.load_stream(f"docpkg-{application_id}")
    extraction = next(event for event in docpkg_events if event["event_type"] == "ExtractionCompleted")
    qa = next(event for event in docpkg_events if event["event_type"] == "QualityAssessmentCompleted")
    credit_events = await store.load_stream(f"credit-{application_id}")
    completed = next(event for event in credit_events if event["event_type"] == "CreditAnalysisCompleted")

    assert extraction["payload"]["facts"]["ebitda"] is None
    assert extraction["payload"]["facts"]["field_confidence"]["ebitda"] == 0.0
    assert qa["payload"]["critical_missing_fields"] == ["ebitda"]
    assert completed["payload"]["decision"]["data_quality_caveats"]
    assert completed["payload"]["decision"]["confidence"] <= 0.75


@pytest.mark.asyncio
async def test_narr03_agent_crash_recovery():
    """
    NARR-03: FraudDetectionAgent crashes mid-session and resumes from replay.
    """
    store = InMemoryEventStore()
    application_id = "NARR-03"
    crashed_session = "sess-fra-1"
    recovered_session = "sess-fra-2"

    await store.append(
        _agent_session_stream(AgentType.FRAUD_DETECTION.value, crashed_session),
        [
            _event(
                "AgentSessionStarted",
                session_id=crashed_session,
                agent_type=AgentType.FRAUD_DETECTION.value,
                agent_id="fraud-agent-01",
                application_id=application_id,
                model_version="fraud-v1",
                langgraph_graph_version="demo-v1",
                context_source="fresh",
                context_token_count=512,
            ),
            _event(
                "AgentInputValidated",
                session_id=crashed_session,
                agent_type=AgentType.FRAUD_DETECTION.value,
                application_id=application_id,
                inputs_validated=["application_id"],
                validation_duration_ms=42,
            ),
            _event(
                "AgentNodeExecuted",
                session_id=crashed_session,
                agent_type=AgentType.FRAUD_DETECTION.value,
                node_name="load_facts",
                node_sequence=1,
                input_keys=["application_id"],
                output_keys=["extracted_facts"],
                llm_called=False,
                llm_tokens_input=None,
                llm_tokens_output=None,
                llm_cost_usd=None,
                duration_ms=105,
            ),
            _event(
                "AgentToolCalled",
                session_id=crashed_session,
                agent_type=AgentType.FRAUD_DETECTION.value,
                tool_name="load_event_store_stream",
                tool_input_summary="stream_id=docpkg-NARR-03",
                tool_output_summary="Loaded extraction facts",
                tool_duration_ms=55,
            ),
            _event(
                "AgentSessionFailed",
                session_id=crashed_session,
                agent_type=AgentType.FRAUD_DETECTION.value,
                application_id=application_id,
                error_type="RuntimeError",
                error_message="simulated crash after load_facts",
                last_successful_node="load_facts",
                recoverable=True,
            ),
        ],
        expected_version=-1,
    )

    context = await reconstruct_agent_context(store, agent_id="fraud-agent-01", session_id=crashed_session)
    assert context.session_health_status == "NEEDS_RECONCILIATION"
    assert "recover:RuntimeError" in context.pending_work

    await handle_start_agent_session(
        store,
        {
            "application_id": application_id,
            "session_id": recovered_session,
            "agent_id": "fraud-agent-01",
            "agent_type": AgentType.FRAUD_DETECTION.value,
            "model_version": "fraud-v1",
            "context_source": f"prior_session_replay:{crashed_session}",
        },
    )

    await store.append(
        _agent_session_stream(AgentType.FRAUD_DETECTION.value, recovered_session),
        [
            _event(
                "AgentSessionRecovered",
                session_id=recovered_session,
                agent_type=AgentType.FRAUD_DETECTION.value,
                recovered_from_session_id=crashed_session,
                recovery_point="cross_reference_registry",
            ),
            _event(
                "AgentNodeExecuted",
                session_id=recovered_session,
                agent_type=AgentType.FRAUD_DETECTION.value,
                node_name="cross_reference_registry",
                node_sequence=2,
                input_keys=["application_id"],
                output_keys=["registry_profile"],
                llm_called=False,
                llm_tokens_input=None,
                llm_tokens_output=None,
                llm_cost_usd=None,
                duration_ms=88,
            ),
            _event(
                "AgentOutputWritten",
                session_id=recovered_session,
                agent_type=AgentType.FRAUD_DETECTION.value,
                application_id=application_id,
                events_written=[
                    {"stream_id": f"fraud-{application_id}", "event_type": "FraudScreeningCompleted", "stream_position": 0}
                ],
                output_summary="Fraud screening recovered and completed.",
            ),
        ],
        expected_version=0,
    )
    await store.append(
        f"fraud-{application_id}",
        [
            _event(
                "FraudScreeningCompleted",
                application_id=application_id,
                session_id=recovered_session,
                fraud_score=0.18,
                risk_level="LOW",
                anomalies_found=0,
                recommendation="PROCEED",
                screening_model_version="fraud-v1",
                input_data_hash="fraud-hash",
                completed_at="2026-03-20T10:12:00+00:00",
            )
        ],
        expected_version=-1,
    )

    resumed = await reconstruct_agent_context(store, agent_id="fraud-agent-01", session_id=recovered_session)
    assert resumed.context_text.startswith("Session replay summary:")
    assert resumed.stream_id == _agent_session_stream(AgentType.FRAUD_DETECTION.value, recovered_session)
    assert resumed.pending_work == []

    crashed_events = await store.load_stream(_agent_session_stream(AgentType.FRAUD_DETECTION.value, crashed_session))
    recovered_events = await store.load_stream(_agent_session_stream(AgentType.FRAUD_DETECTION.value, recovered_session))
    fraud_events = await store.load_stream(f"fraud-{application_id}")

    assert sum(1 for event in crashed_events + recovered_events if event["event_type"] == "AgentNodeExecuted" and event["payload"].get("node_name") == "load_facts") == 1
    assert any(event["event_type"] == "AgentSessionRecovered" for event in recovered_events)
    assert sum(1 for event in fraud_events if event["event_type"] == "FraudScreeningCompleted") == 1


@pytest.mark.asyncio
async def test_narr04_compliance_hard_block():
    """
    NARR-04: Montana applicant triggers REG-003 hard block and declines.
    """
    store = InMemoryEventStore()
    application_id = "NARR-04"

    await store.append(
        f"loan-{application_id}",
        [
            _event("ApplicationSubmitted", application_id=application_id, applicant_id="COMP-MT", requested_amount_usd=300000, loan_purpose="expansion"),
            _event("DocumentUploadRequested", application_id=application_id),
            _event("DocumentUploaded", application_id=application_id),
            _event("CreditAnalysisRequested", application_id=application_id),
            _event("CreditAnalysisCompleted", application_id=application_id),
            _event("FraudScreeningRequested", application_id=application_id),
            _event("FraudScreeningCompleted", application_id=application_id),
            _event("ComplianceCheckRequested", application_id=application_id),
        ],
        expected_version=-1,
    )

    written = await handle_compliance_check(
        store,
        {
            "application_id": application_id,
            "session_id": "sess-com-narr04",
            "overall_verdict": "BLOCKED",
            "rules_evaluated": 3,
            "rules_passed_count": 2,
            "rules_failed_count": 1,
            "rules_noted_count": 0,
            "has_hard_block": True,
            "regulation_set_version": "2026-Q1-v1",
            "rules_to_evaluate": ["REG-001", "REG-002", "REG-003"],
            "rules_passed": [
                {"rule_id": "REG-001", "rule_name": "BSA Check", "rule_version": "2026-Q1-v1", "evidence_hash": "ok-1"},
                {"rule_id": "REG-002", "rule_name": "OFAC", "rule_version": "2026-Q1-v1", "evidence_hash": "ok-2"},
            ],
            "rules_failed": [
                {
                    "rule_id": "REG-003",
                    "rule_name": "Jurisdiction Lending Eligibility",
                    "rule_version": "2026-Q1-v1",
                    "failure_reason": "Jurisdiction MT not approved for commercial lending at this time.",
                    "is_hard_block": True,
                }
            ],
            "rules_noted": [],
            "completed_at": "2026-03-20T10:20:00+00:00",
            "decline_reasons": ["REG-003: Jurisdiction MT not approved for commercial lending at this time."],
            "adverse_action_notice_required": True,
            "adverse_action_codes": ["COMPLIANCE_BLOCK"],
        },
    )

    compliance_events = await store.load_stream(f"compliance-{application_id}")
    loan_events = await store.load_stream(f"loan-{application_id}")

    assert any(event["event_type"] == "ComplianceRuleFailed" and event["payload"]["rule_id"] == "REG-003" and event["payload"]["is_hard_block"] for event in compliance_events)
    assert not any(event["event_type"] == "DecisionGenerated" for event in loan_events)
    declined = next(event for event in loan_events if event["event_type"] == "ApplicationDeclined")
    assert declined["payload"]["adverse_action_notice_required"] is True
    assert "REG-003" in declined["payload"]["decline_reasons"][0]
    assert written == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_narr05_human_override():
    """
    NARR-05: Orchestrator recommends DECLINE; human loan officer overrides to APPROVE.
    """
    store, application_id = await build_narr05_demo_store()

    loan_events = await store.load_stream(f"loan-{application_id}")
    event_types = [event["event_type"] for event in loan_events]

    assert application_id == "NARR-05"
    assert "DecisionGenerated" in event_types
    assert "HumanReviewCompleted" in event_types
    assert "ApplicationApproved" in event_types

    decision = next(event for event in loan_events if event["event_type"] == "DecisionGenerated")
    review = next(event for event in loan_events if event["event_type"] == "HumanReviewCompleted")
    approval = next(event for event in loan_events if event["event_type"] == "ApplicationApproved")

    assert decision["payload"]["recommendation"] == "DECLINE"
    assert review["payload"]["override"] is True
    assert review["payload"]["reviewer_id"] == "LO-Sarah-Chen"
    assert approval["payload"]["approved_amount_usd"] == 750000
    assert len(approval["payload"]["conditions"]) == 2

    package = await generate_regulatory_package(store, application_id)
    assert package["what_if"]["actual"]["final_decision"] == "APPROVED"
    assert package["underwriting"]["override_used"] is True
    assert package["what_if"]["underwriting"]["conditions_count"] == 2
