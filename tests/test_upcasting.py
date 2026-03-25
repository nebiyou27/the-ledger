from __future__ import annotations

from uuid import uuid4

import pytest

from ledger.event_store import InMemoryEventStore
from ledger.upcasting import build_default_upcaster_registry


def test_registry_applies_version_chain():
    registry = build_default_upcaster_registry()
    raw = {
        "event_type": "CreditAnalysisCompleted",
        "event_version": 1,
        "payload": {
            "application_id": "APEX-1",
            "session_id": "sess-1",
            "decision": {"risk_tier": "MEDIUM"},
        },
        "recorded_at": "2025-12-20T10:00:00Z",
    }

    upcasted = registry.upcast(raw)
    assert upcasted["event_version"] == 2
    assert upcasted["payload"]["model_version"] == "legacy-pre-2026"
    assert upcasted["payload"]["confidence_score"] is None
    assert upcasted["payload"]["regulatory_basis"] == ["REG-001", "REG-002", "REG-003"]


@pytest.mark.asyncio
async def test_upcasting_read_does_not_mutate_persisted_event():
    store = InMemoryEventStore()
    await store.append(
        "loan-APEX-44",
        [
            {
                "event_type": "DecisionGenerated",
                "event_version": 1,
                "payload": {
                    "application_id": "APEX-44",
                    "recommendation": "APPROVE",
                    "confidence": 0.91,
                },
            }
        ],
        expected_version=-1,
    )

    raw_before = dict(store._streams["loan-APEX-44"][0])  # test-only raw storage check
    loaded = await store.load_stream("loan-APEX-44")
    raw_after = dict(store._streams["loan-APEX-44"][0])

    assert loaded[0]["event_version"] == 2
    assert loaded[0]["payload"]["model_versions"] == {}
    assert raw_before["event_version"] == 1
    assert raw_after["event_version"] == 1
    assert "model_versions" not in (raw_after.get("payload") or {})


@pytest.mark.asyncio
async def test_decision_generated_upcast_reconstructs_model_versions_from_history():
    store = InMemoryEventStore()
    application_id = "APEX-46"

    await store.append(
        f"agent-document_processing-sess-doc-{application_id}",
        [
            {
                "event_type": "AgentSessionStarted",
                "event_version": 1,
                "payload": {
                    "session_id": f"sess-doc-{application_id}",
                    "agent_type": "document_processing",
                    "agent_id": "doc-1",
                    "application_id": application_id,
                    "model_version": "doc-v9",
                    "context_source": "projection-backed",
                },
            }
        ],
        expected_version=-1,
    )
    await store.append(
        f"agent-decision_orchestrator-sess-orc-{application_id}",
        [
            {
                "event_type": "AgentSessionStarted",
                "event_version": 1,
                "payload": {
                    "session_id": f"sess-orc-{application_id}",
                    "agent_type": "decision_orchestrator",
                    "agent_id": "orch-1",
                    "application_id": application_id,
                    "model_version": "orchestrator-v9",
                    "context_source": "fresh",
                },
            }
        ],
        expected_version=-1,
    )
    await store.append(
        f"agent-credit_analysis-sess-cre-{application_id}",
        [
            {
                "event_type": "AgentSessionStarted",
                "event_version": 1,
                "payload": {
                    "session_id": f"sess-cre-{application_id}",
                    "agent_type": "credit_analysis",
                    "agent_id": "credit-1",
                    "application_id": application_id,
                    "model_version": "credit-v9",
                    "context_source": "projection-backed",
                },
            }
        ],
        expected_version=-1,
    )
    await store.append(
        f"credit-{application_id}",
        [
            {
                "event_type": "CreditAnalysisCompleted",
                "event_version": 1,
                "payload": {
                    "application_id": application_id,
                    "session_id": f"sess-cre-{application_id}",
                    "decision": {"risk_tier": "LOW"},
                    "model_version": "credit-v9",
                },
            }
        ],
        expected_version=-1,
    )
    await store.append(
        f"agent-fraud_detection-sess-fr-{application_id}",
        [
            {
                "event_type": "AgentSessionStarted",
                "event_version": 1,
                "payload": {
                    "session_id": f"sess-fr-{application_id}",
                    "agent_type": "fraud_detection",
                    "agent_id": "fraud-1",
                    "application_id": application_id,
                    "model_version": "fraud-v9",
                    "context_source": "projection-backed",
                },
            }
        ],
        expected_version=-1,
    )
    await store.append(
        f"fraud-{application_id}",
        [
            {
                "event_type": "FraudScreeningCompleted",
                "event_version": 1,
                "payload": {
                    "application_id": application_id,
                    "session_id": f"sess-fr-{application_id}",
                    "fraud_score": 0.02,
                    "risk_level": "LOW",
                    "anomalies_found": 0,
                    "recommendation": "PROCEED",
                    "screening_model_version": "fraud-v9",
                },
            }
        ],
        expected_version=-1,
    )
    await store.append(
        f"agent-compliance-sess-com-{application_id}",
        [
            {
                "event_type": "AgentSessionStarted",
                "event_version": 1,
                "payload": {
                    "session_id": f"sess-com-{application_id}",
                    "agent_type": "compliance",
                    "agent_id": "compliance-1",
                    "application_id": application_id,
                    "model_version": "compliance-v9",
                    "context_source": "projection-backed",
                },
            }
        ],
        expected_version=-1,
    )

    await store.append(
        f"loan-{application_id}",
        [
            {
                "event_type": "ApplicationSubmitted",
                "event_version": 1,
                "payload": {
                    "application_id": application_id,
                    "applicant_id": "COMP-46",
                    "requested_amount_usd": 50000,
                    "loan_purpose": "working_capital",
                },
            },
            {
                "event_type": "DecisionGenerated",
                "event_version": 1,
                "payload": {
                    "application_id": application_id,
                    "orchestrator_session_id": f"sess-orc-{application_id}",
                    "recommendation": "APPROVE",
                    "confidence": 0.9,
                    "contributing_sessions": [
                        f"agent-credit_analysis-sess-cre-{application_id}",
                        f"agent-document_processing-sess-doc-{application_id}",
                        f"agent-fraud_detection-sess-fr-{application_id}",
                        f"agent-compliance-sess-com-{application_id}",
                        f"agent-decision_orchestrator-sess-orc-{application_id}",
                    ],
                },
            },
        ],
        expected_version=-1,
    )

    loaded = await store.load_stream(f"loan-{application_id}")
    decision = next(event for event in loaded if event["event_type"] == "DecisionGenerated")
    assert decision["event_version"] == 2
    assert decision["payload"]["model_versions"]["credit"] == "credit-v9"
    assert decision["payload"]["model_versions"]["document_processing"] == "doc-v9"
    assert decision["payload"]["model_versions"]["fraud"] == "fraud-v9"
    assert decision["payload"]["model_versions"]["compliance"] == "compliance-v9"
    assert decision["payload"]["model_versions"]["orchestrator"] == "orchestrator-v9"


def _seed_legacy_event(store: InMemoryEventStore, stream_id: str, event: dict) -> dict:
    raw = {
        "event_id": str(uuid4()),
        "stream_id": stream_id,
        "stream_position": 0,
        "global_position": len(store._global),
        "event_type": event["event_type"],
        "event_version": event.get("event_version", 1),
        "payload": dict(event.get("payload") or {}),
        "metadata": dict(event.get("metadata") or {}),
        "recorded_at": event.get("recorded_at"),
    }
    store._streams[stream_id].append(raw)
    store._global.append(raw)
    store._versions[stream_id] = 0
    return raw


@pytest.mark.asyncio
async def test_legacy_v1_event_is_upcast_only_on_read():
    store = InMemoryEventStore()
    raw = _seed_legacy_event(
        store,
        "loan-APEX-45",
        {
            "event_type": "DecisionGenerated",
            "event_version": 1,
            "payload": {
                "application_id": "APEX-45",
                "recommendation": "APPROVE",
                "confidence": 0.88,
            },
        },
    )

    loaded_stream = await store.load_stream("loan-APEX-45")
    loaded_all = [event async for event in store.load_all(from_position=0)]

    assert loaded_stream[0]["event_version"] == 2
    assert loaded_stream[0]["payload"]["model_versions"] == {}
    assert loaded_all[0]["event_version"] == 2
    assert loaded_all[0]["payload"]["model_versions"] == {}
    assert raw["event_version"] == 1
    assert "model_versions" not in raw["payload"]
