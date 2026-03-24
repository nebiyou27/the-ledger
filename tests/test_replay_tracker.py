from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ledger.api import create_app
from ledger.event_store import InMemoryEventStore
from ledger.metrics import get_replay_progress, set_replay_progress
from ledger.mcp_server import create_runtime, create_server


def _resource_json(resource_result) -> object:
    return json.loads(resource_result.contents[0].content)


@pytest.mark.asyncio
async def test_get_replay_progress_returns_idle_state_when_no_replay_events_exist():
    store = InMemoryEventStore()

    snapshot = await get_replay_progress(store)

    assert snapshot == {
        "status": "IDLE",
        "is_replaying": False,
        "projection_name": None,
        "events_processed": 0,
        "total_events": 0,
        "percent_complete": 0.0,
        "started_at": None,
        "estimated_completion": None,
        "last_updated": None,
    }


@pytest.mark.asyncio
async def test_get_replay_progress_returns_correct_progress_during_an_active_replay():
    store = InMemoryEventStore()

    await set_replay_progress(
        store,
        {
            "event_type": "REPLAY_STARTED",
            "projection_name": "application_summary",
            "events_processed": 0,
            "total_events": 500,
            "percent_complete": 0.0,
            "started_at": "2026-03-24T10:00:00+00:00",
        },
    )
    await set_replay_progress(
        store,
        {
            "event_type": "REPLAY_PROGRESS",
            "projection_name": "application_summary",
            "events_processed": 200,
            "total_events": 500,
            "percent_complete": 40.0,
            "started_at": "2026-03-24T10:00:00+00:00",
            "estimated_completion": "2026-03-24T10:04:00+00:00",
        },
    )

    snapshot = await get_replay_progress(store)

    assert snapshot["status"] == "REPLAYING"
    assert snapshot["is_replaying"] is True
    assert snapshot["projection_name"] == "application_summary"
    assert snapshot["events_processed"] == 200
    assert snapshot["total_events"] == 500
    assert snapshot["percent_complete"] == 40.0
    assert snapshot["started_at"] == "2026-03-24T10:00:00+00:00"
    assert snapshot["estimated_completion"] == "2026-03-24T10:04:00+00:00"
    assert snapshot["last_updated"] is not None


@pytest.mark.asyncio
async def test_get_replay_progress_returns_completed_state_after_replay_completed_event():
    store = InMemoryEventStore()

    await set_replay_progress(
        store,
        {
            "event_type": "REPLAY_STARTED",
            "projection_name": "compliance_audit",
            "events_processed": 0,
            "total_events": 120,
            "percent_complete": 0.0,
            "started_at": "2026-03-24T11:00:00+00:00",
        },
    )
    await set_replay_progress(
        store,
        {
            "event_type": "REPLAY_COMPLETED",
            "projection_name": "compliance_audit",
            "events_processed": 120,
            "total_events": 120,
            "percent_complete": 100.0,
            "started_at": "2026-03-24T11:00:00+00:00",
            "estimated_completion": "2026-03-24T11:03:00+00:00",
        },
    )

    snapshot = await get_replay_progress(store)

    assert snapshot["status"] == "COMPLETED"
    assert snapshot["is_replaying"] is False
    assert snapshot["events_processed"] == 120
    assert snapshot["total_events"] == 120
    assert snapshot["percent_complete"] == 100.0


@pytest.mark.asyncio
async def test_get_replay_progress_returns_failed_state_after_replay_failed_event():
    store = InMemoryEventStore()

    await set_replay_progress(
        store,
        {
            "event_type": "REPLAY_STARTED",
            "projection_name": "manual_reviews",
            "events_processed": 0,
            "total_events": 75,
            "percent_complete": 0.0,
            "started_at": "2026-03-24T12:00:00+00:00",
        },
    )
    await set_replay_progress(
        store,
        {
            "event_type": "REPLAY_FAILED",
            "projection_name": "manual_reviews",
            "events_processed_before_failure": 25,
            "total_events": 75,
            "percent_complete": 33.33,
            "started_at": "2026-03-24T12:00:00+00:00",
            "reason": "Transient database error",
        },
    )

    snapshot = await get_replay_progress(store)

    assert snapshot["status"] == "FAILED"
    assert snapshot["is_replaying"] is False
    assert snapshot["events_processed"] == 25
    assert snapshot["total_events"] == 75
    assert snapshot["percent_complete"] == 33.33


def test_api_replay_progress_endpoint_returns_correct_snapshot(monkeypatch):
    monkeypatch.setenv("LEDGER_API_KEYS", "viewer=test-viewer,admin=test-admin")

    store = InMemoryEventStore()

    async def _seed() -> None:
        await set_replay_progress(
            store,
            {
                "event_type": "REPLAY_COMPLETED",
                "projection_name": "agent_performance",
                "events_processed": 900,
                "total_events": 900,
                "percent_complete": 100.0,
                "started_at": "2026-03-24T13:00:00+00:00",
                "estimated_completion": "2026-03-24T13:06:00+00:00",
            },
        )

    class _Backend:
        def __init__(self):
            self.store = store
            self.runtime = SimpleNamespace(store=store)
            self._seeded = False

        async def sync(self):
            if not self._seeded:
                await _seed()
                self._seeded = True
            return {"ok": True}

        async def close(self):
            return None

    async def _build_backend():
        return _Backend()

    monkeypatch.setattr("ledger.api._build_backend", _build_backend)

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/replay/progress", headers={"Authorization": "Bearer test-viewer"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "COMPLETED"
    assert payload["projection_name"] == "agent_performance"
    assert payload["events_processed"] == 900
    assert payload["total_events"] == 900
    assert payload["percent_complete"] == 100.0


@pytest.mark.asyncio
async def test_mcp_replay_progress_resource_returns_correct_snapshot():
    store = InMemoryEventStore()
    await set_replay_progress(
        store,
        {
            "event_type": "REPLAY_PROGRESS",
            "projection_name": "application_summary",
            "events_processed": 50,
            "total_events": 200,
            "percent_complete": 25.0,
            "started_at": "2026-03-24T14:00:00+00:00",
            "estimated_completion": "2026-03-24T14:06:00+00:00",
        },
    )

    server = create_server(create_runtime(store))
    resource = await server.read_resource("ledger://replay/progress")
    payload = _resource_json(resource)

    assert payload["status"] == "REPLAYING"
    assert payload["projection_name"] == "application_summary"
    assert payload["events_processed"] == 50
    assert payload["total_events"] == 200
    assert payload["percent_complete"] == 25.0
