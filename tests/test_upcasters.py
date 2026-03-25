import pytest

from ledger.upcasters import UpcasterRegistry, build_default_upcaster_registry
from ledger.event_store import InMemoryEventStore


def test_credit_analysis_upcast_v1_to_v2_adds_regulatory_basis():
    registry = UpcasterRegistry()
    raw = {
        "event_type": "CreditAnalysisCompleted",
        "event_version": 1,
        "payload": {
            "application_id": "APEX-1",
            "session_id": "sess-cre-1",
            "decision": {"risk_tier": "MEDIUM"},
        },
    }

    upcasted = registry.upcast(raw)

    assert upcasted["event_version"] == 2
    assert upcasted["payload"]["regulatory_basis"] == ["REG-001", "REG-002", "REG-003"]
    assert "regulatory_basis" not in raw["payload"]


def test_decision_generated_upcast_v1_to_v2_adds_model_versions():
    registry = build_default_upcaster_registry()
    raw = {
        "event_type": "DecisionGenerated",
        "event_version": 1,
        "payload": {
            "application_id": "APEX-1",
            "recommendation": "APPROVE",
            "confidence": 0.82,
        },
    }

    upcasted = registry.upcast(raw)

    assert upcasted["event_version"] == 2
    assert upcasted["payload"]["model_versions"] == {}
    assert "model_versions" not in raw["payload"]


def test_non_target_event_is_returned_unchanged():
    registry = UpcasterRegistry()
    raw = {
        "event_type": "ApplicationSubmitted",
        "event_version": 1,
        "payload": {"application_id": "APEX-1"},
    }

    upcasted = registry.upcast(raw)

    assert upcasted["event_type"] == "ApplicationSubmitted"
    assert upcasted["event_version"] == 1
    assert upcasted["payload"] == {"application_id": "APEX-1"}


@pytest.mark.asyncio
async def test_inmemory_store_applies_default_upcasters_on_read():
    store = InMemoryEventStore()
    await store.append(
        "loan-APEX-1",
        [
            {
                "event_type": "DecisionGenerated",
                "event_version": 1,
                "payload": {
                    "application_id": "APEX-1",
                    "recommendation": "APPROVE",
                    "confidence": 0.9,
                },
            }
        ],
        expected_version=-1,
    )

    events = await store.load_stream("loan-APEX-1")
    assert events[0]["event_version"] == 2
    assert events[0]["payload"]["model_versions"] == {}
