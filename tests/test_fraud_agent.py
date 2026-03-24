from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from ledger.agents.base_agent import BaseApexAgent
from ledger.agents.stub_agents import FraudDetectionAgent
from ledger.event_store import InMemoryEventStore


class _NoopLLM:
    async def generate(self, **kwargs):
        return ("noop", 0, 0, 0.0)


class _FakeRegistry:
    def __init__(self, company, history, loans):
        self.company = company
        self.history = history
        self.loans = loans

    async def get_company(self, company_id: str):
        return self.company

    async def get_financial_history(self, company_id: str, years=None):
        return self.history

    async def get_loan_relationships(self, company_id: str):
        return self.loans


def _event(event_type: str, **payload):
    return {"event_type": event_type, "event_version": 1, "payload": payload}


async def _seed_inputs(
    store: InMemoryEventStore,
    application_id: str,
    applicant_id: str,
    requested_amount_usd: float,
    total_revenue: float,
    dti_ratio: float | None = None,
    prior_defaults: int = 0,
):
    now = datetime.now(timezone.utc).isoformat()
    await store.append(
        f"loan-{application_id}",
        [
            _event(
                "ApplicationSubmitted",
                application_id=application_id,
                applicant_id=applicant_id,
                requested_amount_usd=requested_amount_usd,
                submitted_at=now,
            ),
            _event(
                "FraudScreeningRequested",
                application_id=application_id,
                requested_at=now,
                triggered_by_event_id="seed-trigger",
            ),
        ],
        expected_version=-1,
    )
    facts = {
        "total_revenue": total_revenue,
        "total_assets": 1_500_000,
        "total_liabilities": 900_000,
        "total_equity": 600_000,
    }
    if dti_ratio is not None:
        facts["dti_ratio"] = dti_ratio
    await store.append(
        f"docpkg-{application_id}",
        [
            _event(
                "ExtractionCompleted",
                application_id=application_id,
                document_id="doc-1",
                facts=facts,
            )
        ],
        expected_version=-1,
    )
    loans = [{"default_occurred": True} for _ in range(prior_defaults)]
    registry = _FakeRegistry(
        company={
            "company_id": applicant_id,
            "trajectory": "STABLE",
            "submission_channel": "web",
            "ip_region": "US-East",
        },
        history=[
            {
                "fiscal_year": 2024,
                "total_revenue": total_revenue,
            }
        ],
        loans=loans,
    )
    return registry


@pytest.mark.asyncio
async def test_happy_path_emits_passed_when_ollama_scores_low():
    store = InMemoryEventStore()
    registry = await _seed_inputs(
        store,
        application_id="FRAUD-001",
        applicant_id="COMP-001",
        requested_amount_usd=100_000,
        total_revenue=500_000,
        dti_ratio=0.2,
        prior_defaults=0,
    )

    agent = FraudDetectionAgent(
        agent_id="fraud-agent-01",
        agent_type="fraud_detection",
        store=store,
        registry=registry,
        llm=_NoopLLM(),
    )

    with patch(
        "ledger.agents.stub_agents.OllamaClient",
        side_effect=lambda: type(
            "FakeOllamaClient",
            (),
            {"ask": lambda self, system_prompt, user_message: {"score": 12, "confidence": "high", "reasoning": "Low anomaly risk."}},
        )(),
    ):
        await agent.process_application("FRAUD-001")

    fraud_events = await store.load_stream("fraud-FRAUD-001")
    event_types = [event["event_type"] for event in fraud_events]

    assert "FRAUD_CHECK_PASSED" in event_types
    assert "FRAUD_CHECK_COMPLETED" in event_types
    assert "FraudScreeningCompleted" in event_types
    completed = next(event for event in fraud_events if event["event_type"] == "FRAUD_CHECK_COMPLETED")
    assert completed["payload"]["ollama_score"] == 12
    assert completed["payload"]["flag_count"] == 0
    assert completed["payload"]["confidence"] == "high"


@pytest.mark.asyncio
async def test_high_ollama_score_emits_flag_raised():
    store = InMemoryEventStore()
    registry = await _seed_inputs(
        store,
        application_id="FRAUD-002",
        applicant_id="COMP-002",
        requested_amount_usd=100_000,
        total_revenue=500_000,
        dti_ratio=0.2,
        prior_defaults=0,
    )

    agent = FraudDetectionAgent(
        agent_id="fraud-agent-02",
        agent_type="fraud_detection",
        store=store,
        registry=registry,
        llm=_NoopLLM(),
    )

    with patch(
        "ledger.agents.stub_agents.OllamaClient",
        side_effect=lambda: type(
            "FakeOllamaClient",
            (),
            {"ask": lambda self, system_prompt, user_message: {"score": 88, "confidence": "medium", "reasoning": "High fraud risk."}},
        )(),
    ):
        await agent.process_application("FRAUD-002")

    fraud_events = await store.load_stream("fraud-FRAUD-002")
    assert any(event["event_type"] == "FRAUD_FLAG_RAISED" for event in fraud_events)
    completed = next(event for event in fraud_events if event["event_type"] == "FRAUD_CHECK_COMPLETED")
    assert completed["payload"]["ollama_score"] == 88


@pytest.mark.asyncio
async def test_two_rule_flags_emit_flag_raised_even_with_low_ollama_score():
    store = InMemoryEventStore()
    registry = await _seed_inputs(
        store,
        application_id="FRAUD-003",
        applicant_id="COMP-003",
        requested_amount_usd=3_000_000,
        total_revenue=400_000,
        dti_ratio=0.72,
        prior_defaults=2,
    )

    agent = FraudDetectionAgent(
        agent_id="fraud-agent-03",
        agent_type="fraud_detection",
        store=store,
        registry=registry,
        llm=_NoopLLM(),
    )

    with patch(
        "ledger.agents.stub_agents.OllamaClient",
        side_effect=lambda: type(
            "FakeOllamaClient",
            (),
            {"ask": lambda self, system_prompt, user_message: {"score": 5, "confidence": "high", "reasoning": "Mostly clear."}},
        )(),
    ):
        await agent.process_application("FRAUD-003")

    fraud_events = await store.load_stream("fraud-FRAUD-003")
    assert any(event["event_type"] == "FRAUD_FLAG_RAISED" for event in fraud_events)
    completed = next(event for event in fraud_events if event["event_type"] == "FRAUD_CHECK_COMPLETED")
    assert completed["payload"]["flag_count"] >= 2
    assert "prior_default_history" in completed["payload"]["flags"]


@pytest.mark.asyncio
async def test_ollama_unavailable_uses_fallback_and_still_emits_events():
    store = InMemoryEventStore()
    registry = await _seed_inputs(
        store,
        application_id="FRAUD-004",
        applicant_id="COMP-004",
        requested_amount_usd=100_000,
        total_revenue=500_000,
        dti_ratio=0.2,
        prior_defaults=0,
    )

    agent = FraudDetectionAgent(
        agent_id="fraud-agent-04",
        agent_type="fraud_detection",
        store=store,
        registry=registry,
        llm=_NoopLLM(),
    )

    with patch(
        "ledger.agents.stub_agents.OllamaClient",
        side_effect=lambda: type(
            "FakeOllamaClient",
            (),
            {"ask": lambda self, system_prompt, user_message: {"error": "ollama_unavailable", "fallback": True}},
        )(),
    ):
        await agent.process_application("FRAUD-004")

    fraud_events = await store.load_stream("fraud-FRAUD-004")
    completed = next(event for event in fraud_events if event["event_type"] == "FRAUD_CHECK_COMPLETED")
    assert completed["payload"]["ollama_score"] == 0
    assert completed["payload"]["confidence"] == "low"
    assert "Ollama unavailable" in completed["payload"]["reasoning"]
    assert any(event["event_type"] == "FRAUD_CHECK_PASSED" for event in fraud_events)

