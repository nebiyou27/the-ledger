from __future__ import annotations

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
    assert upcasted["payload"]["regulatory_basis"] == []


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
