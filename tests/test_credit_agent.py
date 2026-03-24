from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ledger.agents.credit_analysis_agent import CreditAnalysisAgent, CreditAnalysisError
from ledger.event_store import InMemoryEventStore


def _base_state(
    application_id: str = "APP-1000",
    applicant_id: str = "APPLICANT-1000",
    loan_amount: float = 50000.0,
    annual_income: float = 120000.0,
    loan_term_months: int = 36,
    monthly_debt: float = 1000.0,
    annual_rate: float = 0.06,
) -> dict:
    return {
        "application_id": application_id,
        "session_id": "sess-test-0001",
        "applicant_id": applicant_id,
        "loan_amount": loan_amount,
        "annual_income": annual_income,
        "loan_term_months": loan_term_months,
        "monthly_debt": monthly_debt,
        "annual_rate": annual_rate,
        "dti_ratio": None,
        "loan_to_income_ratio": None,
        "monthly_payment_estimate": None,
        "prior_defaults": None,
        "prior_loans": None,
        "account_age_months": None,
        "factor_scores": None,
        "credit_score": None,
        "policy_outcome": None,
        "hard_gate": None,
        "explanation": None,
        "key_factors": None,
        "confidence": None,
        "errors": [],
        "output_events": [],
        "next_agent": None,
    }


def _make_agent(store: InMemoryEventStore) -> CreditAnalysisAgent:
    agent = CreditAnalysisAgent(
        agent_id="agent-credit",
        agent_type="credit_analysis",
        store=store,
        registry=None,
        llm=MagicMock(),
    )
    agent.session_id = "sess-credit-0001"
    agent._record_node_execution = AsyncMock(return_value=None)
    agent._record_output_written = AsyncMock(return_value=None)
    agent._record_tool_call = AsyncMock(return_value=None)
    return agent


async def _run_pipeline(agent: CreditAnalysisAgent, state: dict) -> dict:
    state = await agent._node_validate_inputs(state)
    state = await agent._node_load_registry(state)
    state = await agent._node_load_facts(state)
    state = await agent._node_analyze(state)
    state = await agent._node_policy(state)
    state = await agent._node_explain_decision(state)
    state = await agent._node_write_output(state)
    return state


async def _append_history_event(
    store: InMemoryEventStore,
    stream_id: str,
    event_type: str,
    payload: dict,
) -> None:
    version = await store.stream_version(stream_id)
    await store.append(stream_id, [{"event_type": event_type, "payload": payload}], expected_version=version)


@pytest.mark.asyncio
async def test_perfect_applicant_approves_and_emits_loan_approved():
    store = InMemoryEventStore()
    agent = _make_agent(store)
    state = _base_state()

    with patch("ledger.agents.credit_analysis_agent._ollama") as mock_ollama:
        mock_ollama.ask.return_value = {
            "explanation": "Strong affordability and clean history support approval.",
            "key_factors": ["low dti", "low loan to income", "no defaults", "seasoned account"],
            "confidence": "high",
        }
        out = await _run_pipeline(agent, state)

    assert out["policy_outcome"] == "APPROVE"
    assert out["hard_gate"] is None

    credit_events = await store.load_stream("credit-APP-1000")
    loan_events = await store.load_stream("loan-APP-1000")
    assert credit_events[-1]["event_type"] == "CREDIT_ANALYSIS_COMPLETED"
    assert loan_events[-1]["event_type"] == "LOAN_APPROVED"
    assert credit_events[-1]["payload"]["policy_outcome"] == "APPROVE"
    assert credit_events[-1]["payload"]["explanation"] == "Strong affordability and clean history support approval."


@pytest.mark.asyncio
async def test_dti_hard_gate_rejects_with_reason():
    store = InMemoryEventStore()
    agent = _make_agent(store)
    state = _base_state(monthly_debt=6000.0)

    with patch("ledger.agents.credit_analysis_agent._ollama") as mock_ollama:
        mock_ollama.ask.return_value = {
            "explanation": "High debt burden triggers the DTI hard limit.",
            "key_factors": ["dti"],
            "confidence": "high",
        }
        out = await _run_pipeline(agent, state)

    assert out["policy_outcome"] == "REJECT"
    assert out["hard_gate"] == "dti_hard_limit"
    loan_events = await store.load_stream("loan-APP-1000")
    assert loan_events[-1]["event_type"] == "LOAN_REJECTED"


@pytest.mark.asyncio
async def test_concentration_risk_hard_gate_rejects():
    store = InMemoryEventStore()
    agent = _make_agent(store)
    state = _base_state(loan_amount=1200000.0, annual_income=100000.0, monthly_debt=500.0)

    with patch("ledger.agents.credit_analysis_agent._ollama") as mock_ollama:
        mock_ollama.ask.return_value = {
            "explanation": "Loan size is too concentrated relative to income.",
            "key_factors": ["loan concentration"],
            "confidence": "high",
        }
        out = await _run_pipeline(agent, state)

    assert out["policy_outcome"] == "REJECT"
    assert out["hard_gate"] == "concentration_risk"
    loan_events = await store.load_stream("loan-APP-1000")
    assert loan_events[-1]["event_type"] == "LOAN_REJECTED"


@pytest.mark.asyncio
async def test_prior_defaults_hard_gate_rejects():
    store = InMemoryEventStore()
    await _append_history_event(
        store,
        "history-APPLICANT-1000-1",
        "LoanDefaultRecorded",
        {
            "applicant_id": "APPLICANT-1000",
            "default_occurred": True,
            "recorded_at": datetime(2023, 1, 1, tzinfo=timezone.utc).isoformat(),
        },
    )
    await _append_history_event(
        store,
        "history-APPLICANT-1000-2",
        "LoanDefaultRecorded",
        {
            "applicant_id": "APPLICANT-1000",
            "default_occurred": True,
            "recorded_at": datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(),
        },
    )

    agent = _make_agent(store)
    state = _base_state()

    with patch("ledger.agents.credit_analysis_agent._ollama") as mock_ollama:
        mock_ollama.ask.return_value = {
            "explanation": "Two prior defaults trigger the policy block.",
            "key_factors": ["prior defaults"],
            "confidence": "high",
        }
        out = await _run_pipeline(agent, state)

    assert out["policy_outcome"] == "REJECT"
    assert out["hard_gate"] == "credit_policy"


@pytest.mark.asyncio
async def test_manual_review_scorecard_path():
    store = InMemoryEventStore()
    agent = _make_agent(store)
    state = _base_state(monthly_debt=3600.0, loan_amount=96000.0, annual_income=120000.0)

    with patch("ledger.agents.credit_analysis_agent._ollama") as mock_ollama:
        mock_ollama.ask.return_value = {
            "explanation": "Moderate risk profile warrants manual review.",
            "key_factors": ["mid-range score"],
            "confidence": "medium",
        }
        out = await _run_pipeline(agent, state)

    assert out["policy_outcome"] == "MANUAL_REVIEW"
    assert out["hard_gate"] is None
    loan_events = await store.load_stream("loan-APP-1000")
    assert loan_events[-1]["event_type"] == "MANUAL_REVIEW_REQUIRED"
    assert 45 <= out["credit_score"] < 70


@pytest.mark.asyncio
async def test_reject_scorecard_path_without_hard_gate():
    store = InMemoryEventStore()
    agent = _make_agent(store)
    state = _base_state(monthly_debt=5000.0, loan_amount=240000.0, annual_income=120000.0)

    with patch("ledger.agents.credit_analysis_agent._ollama") as mock_ollama:
        mock_ollama.ask.return_value = {
            "explanation": "Scorecard risk is too weak for approval.",
            "key_factors": ["low score"],
            "confidence": "medium",
        }
        out = await _run_pipeline(agent, state)

    assert out["policy_outcome"] == "REJECT"
    assert out["hard_gate"] is None
    loan_events = await store.load_stream("loan-APP-1000")
    assert loan_events[-1]["event_type"] == "LOAN_REJECTED"
    assert out["credit_score"] < 45


@pytest.mark.asyncio
async def test_ollama_unavailable_uses_fallback_explanation():
    store = InMemoryEventStore()
    agent = _make_agent(store)
    state = _base_state()

    with patch("ledger.agents.credit_analysis_agent._ollama", None):
        out = await _run_pipeline(agent, state)

    assert out["policy_outcome"] == "APPROVE"
    assert out["explanation"] == "Automated scorecard decision — manual explanation unavailable"
    assert out["confidence"] == "low"
    loan_events = await store.load_stream("loan-APP-1000")
    assert loan_events[-1]["event_type"] == "LOAN_APPROVED"


@pytest.mark.asyncio
async def test_first_time_borrower_returns_zero_history():
    store = InMemoryEventStore()
    agent = _make_agent(store)
    state = _base_state(applicant_id="NEW-APPLICANT")

    with patch("ledger.agents.credit_analysis_agent._ollama") as mock_ollama:
        mock_ollama.ask.return_value = {
            "explanation": "Clean first-time borrower profile with strong affordability.",
            "key_factors": ["no prior history"],
            "confidence": "high",
        }
        out = await _run_pipeline(agent, state)

    assert out["prior_defaults"] == 0
    assert out["prior_loans"] == 0
    assert out["account_age_months"] == 0
    assert out["policy_outcome"] == "APPROVE"


@pytest.mark.asyncio
async def test_validate_inputs_raises_typed_error():
    store = InMemoryEventStore()
    agent = _make_agent(store)
    state = _base_state(loan_amount=0)

    with pytest.raises(CreditAnalysisError) as excinfo:
        await agent._node_validate_inputs(state)

    assert excinfo.value.field == "loan_amount"
    assert excinfo.value.reason == "must be > 0"
