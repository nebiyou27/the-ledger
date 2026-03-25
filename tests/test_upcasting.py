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
    assert upcasted["payload"]["regulatory_basis"] is None


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
