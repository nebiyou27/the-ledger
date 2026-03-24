from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ledger.agents.stub_agents import DecisionOrchestratorAgent
from ledger.event_store import InMemoryEventStore


def _event(event_type: str, **payload):
    return {"event_type": event_type, "event_version": 1, "payload": payload}


def _make_agent(store: InMemoryEventStore) -> DecisionOrchestratorAgent:
    agent = DecisionOrchestratorAgent(
        agent_id="orchestrator-agent-01",
        agent_type="decision_orchestrator",
        store=store,
        registry=None,
        llm=MagicMock(),
    )
    agent.session_id = "sess-orch-0001"
    agent._record_node_execution = AsyncMock(return_value=None)
    agent._record_output_written = AsyncMock(return_value=None)
    agent._record_tool_call = AsyncMock(return_value=None)
    return agent


async def _seed_upstream(
    store: InMemoryEventStore,
    application_id: str,
    *,
    credit_score: float = 82.0,
    policy_outcome: str = "APPROVE",
    hard_gate=None,
    factor_scores: dict | None = None,
    fraud_flags: list | None = None,
    fraud_ollama_score: int = 35,
    fraud_confidence: str = "high",
    compliance_event_type: str = "COMPLIANCE_PASSED",
    compliance_outcome: str = "CLEAR",
):
    factor_scores = factor_scores or {"dti": 0.1, "loan_to_income": 0.2, "prior_defaults": 0.0, "account_age": 0.9}
    fraud_flags = fraud_flags or []

    await store.append(
        f"docpkg-{application_id}",
        [
            _event(
                "DOCUMENT_PROCESSED",
                application_id=application_id,
                applicant_id="APPLICANT-1",
                loan_amount=250_000.0,
                annual_income=1_200_000.0,
                loan_term_months=36,
            )
        ],
        expected_version=-1,
    )
    await store.append(
        f"credit-{application_id}",
        [
            _event(
                "CREDIT_ANALYSIS_COMPLETED",
                application_id=application_id,
                credit_score=credit_score,
                policy_outcome=policy_outcome,
                hard_gate=hard_gate,
                factor_scores=factor_scores,
            )
        ],
        expected_version=-1,
    )
    await store.append(
        f"fraud-{application_id}",
        [
            _event(
                "FRAUD_CHECK_COMPLETED",
                application_id=application_id,
                flags=fraud_flags,
                ollama_score=fraud_ollama_score,
                confidence=fraud_confidence,
            )
        ],
        expected_version=-1,
    )
    await store.append(
        f"compliance-{application_id}",
        [
            _event(
                compliance_event_type,
                application_id=application_id,
                compliance_outcome=compliance_outcome,
            )
        ],
        expected_version=-1,
    )


async def _run_pipeline(agent: DecisionOrchestratorAgent, state: dict) -> dict:
    state = await agent._node_validate_inputs(state)
    if state.get("missing_upstream_events"):
        return await agent._node_write_output(state)
    state = await agent._node_load_credit(state)
    state = await agent._node_load_fraud(state)
    state = await agent._node_load_compliance(state)
    state = await agent._node_synthesize(state)
    state = await agent._node_constraints(state)
    state = await agent._node_write_output(state)
    return state


@pytest.mark.asyncio
async def test_happy_path_emits_loan_approved():
    store = InMemoryEventStore()
    await _seed_upstream(store, "ORCH-001")
    agent = _make_agent(store)

    with patch("ledger.agents.stub_agents._ollama") as mock_ollama:
        mock_ollama.ask.return_value = {
            "decision": "APPROVE",
            "confidence": "high",
            "reasoning": "The application is strong across credit, fraud, and compliance checks.",
        }
        out = await _run_pipeline(agent, agent._initial_state("ORCH-001"))

    loan_events = await store.load_stream("loan-ORCH-001")
    assert loan_events[-2]["event_type"] == "ORCHESTRATION_COMPLETED"
    assert loan_events[-1]["event_type"] == "LOAN_APPROVED"
    assert out["final_event_type"] == "LOAN_APPROVED"
    assert loan_events[-2]["payload"]["final_decision"] == "LOAN_APPROVED"
    assert loan_events[-2]["payload"]["upstream_summary"]["credit_score"] == 82.0


@pytest.mark.asyncio
async def test_low_confidence_forces_manual_review():
    store = InMemoryEventStore()
    await _seed_upstream(store, "ORCH-002")
    agent = _make_agent(store)

    with patch("ledger.agents.stub_agents._ollama") as mock_ollama:
        mock_ollama.ask.return_value = {
            "decision": "APPROVE",
            "confidence": "low",
            "reasoning": "The model is unsure but would otherwise approve.",
        }
        out = await _run_pipeline(agent, agent._initial_state("ORCH-002"))

    loan_events = await store.load_stream("loan-ORCH-002")
    assert loan_events[-1]["event_type"] == "MANUAL_REVIEW_REQUIRED"
    assert out["safety_rule_applied"] == "low_confidence"
    assert loan_events[-2]["payload"]["confidence"] == "low"


@pytest.mark.asyncio
async def test_hard_gate_overrides_fraud_and_confidence():
    store = InMemoryEventStore()
    await _seed_upstream(
        store,
        "ORCH-003",
        hard_gate="credit_policy",
        fraud_ollama_score=95,
        fraud_confidence="low",
    )
    agent = _make_agent(store)

    with patch("ledger.agents.stub_agents._ollama") as mock_ollama:
        mock_ollama.ask.return_value = {
            "decision": "APPROVE",
            "confidence": "high",
            "reasoning": "Would approve if not for the upstream hard gate.",
        }
        out = await _run_pipeline(agent, agent._initial_state("ORCH-003"))

    loan_events = await store.load_stream("loan-ORCH-003")
    assert loan_events[-1]["event_type"] == "LOAN_REJECTED"
    assert out["safety_rule_applied"] == "hard_gate"
    assert loan_events[-2]["payload"]["final_decision"] == "LOAN_REJECTED"


@pytest.mark.asyncio
async def test_fraud_risk_over_70_forces_manual_review():
    store = InMemoryEventStore()
    await _seed_upstream(store, "ORCH-004", fraud_ollama_score=88)
    agent = _make_agent(store)

    with patch("ledger.agents.stub_agents._ollama") as mock_ollama:
        mock_ollama.ask.return_value = {
            "decision": "APPROVE",
            "confidence": "high",
            "reasoning": "Strong enough to approve absent the fraud score.",
        }
        out = await _run_pipeline(agent, agent._initial_state("ORCH-004"))

    loan_events = await store.load_stream("loan-ORCH-004")
    assert loan_events[-1]["event_type"] == "MANUAL_REVIEW_REQUIRED"
    assert out["safety_rule_applied"] == "fraud_risk_score_gt_70"


@pytest.mark.asyncio
async def test_ollama_unavailable_uses_fallback_reasoning():
    store = InMemoryEventStore()
    await _seed_upstream(store, "ORCH-005")
    agent = _make_agent(store)

    with patch("ledger.agents.stub_agents._ollama", None):
        out = await _run_pipeline(agent, agent._initial_state("ORCH-005"))

    loan_events = await store.load_stream("loan-ORCH-005")
    assert loan_events[-1]["event_type"] == "MANUAL_REVIEW_REQUIRED"
    assert out["safety_rule_applied"] == "ollama_unavailable"
    assert out["reasoning"] == "Orchestration fallback — Ollama unavailable"
    assert loan_events[-2]["payload"]["reasoning"] == "Orchestration fallback — Ollama unavailable"


@pytest.mark.asyncio
async def test_missing_upstream_event_emits_failure():
    store = InMemoryEventStore()
    await _seed_upstream(store, "ORCH-006")
    store._streams["fraud-ORCH-006"].clear()  # type: ignore[attr-defined]
    agent = _make_agent(store)

    state = await agent._node_validate_inputs(agent._initial_state("ORCH-006"))
    state = await agent._node_write_output(state)

    loan_events = await store.load_stream("loan-ORCH-006")
    assert loan_events[-1]["event_type"] == "ORCHESTRATION_FAILED"
    assert loan_events[-1]["payload"]["reason"] == "missing_upstream_output"
    assert "FRAUD_CHECK_COMPLETED" in loan_events[-1]["payload"]["missing_events"]


@pytest.mark.asyncio
async def test_safety_rule_order_prefers_hard_gate_then_fraud_then_confidence():
    store = InMemoryEventStore()
    await _seed_upstream(
        store,
        "ORCH-007",
        hard_gate="credit_policy",
        fraud_ollama_score=96,
        fraud_confidence="low",
    )
    agent = _make_agent(store)

    with patch("ledger.agents.stub_agents._ollama") as mock_ollama:
        mock_ollama.ask.return_value = {
            "decision": "APPROVE",
            "confidence": "low",
            "reasoning": "This should never override the hard gate.",
        }
        out = await _run_pipeline(agent, agent._initial_state("ORCH-007"))

    loan_events = await store.load_stream("loan-ORCH-007")
    assert loan_events[-1]["event_type"] == "LOAN_REJECTED"
    assert out["safety_rule_applied"] == "hard_gate"
