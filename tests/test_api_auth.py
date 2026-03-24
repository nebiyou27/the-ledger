from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ledger.event_store import InMemoryEventStore
from ledger.api import create_app


def test_missing_credentials_return_401_when_auth_is_enabled(monkeypatch):
    monkeypatch.setenv("LEDGER_API_KEYS", "viewer=test-viewer,admin=test-admin")

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/applications")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing or invalid API credentials"


def test_forbidden_role_returns_403(monkeypatch):
    monkeypatch.setenv("LEDGER_API_KEYS", "viewer=test-viewer,admin=test-admin")

    app = create_app()
    with TestClient(app) as client:
        response = client.post("/refresh", headers={"Authorization": "Bearer test-viewer"})

    assert response.status_code == 403
    assert "not allowed" in response.json()["detail"]


def test_event_throughput_endpoint_allows_viewer_access(monkeypatch):
    monkeypatch.setenv("LEDGER_API_KEYS", "viewer=test-viewer,admin=test-admin")

    async def _snapshot(*args, **kwargs):
        return {
            "windowMinutes": 60,
            "bucketMinutes": 5,
            "windowStartAt": "2026-03-24T09:30:00+00:00",
            "windowEndAt": "2026-03-24T10:30:00+00:00",
            "latestEventAt": "2026-03-24T10:30:00+00:00",
            "totalEvents": 9,
            "eventsPerMinute": 0.15,
            "eventsPerHour": 9.0,
            "peakBucketEvents": 3,
            "peakBucketLabel": "10:00",
            "buckets": [],
        }

    monkeypatch.setattr("ledger.api.build_event_throughput_snapshot", _snapshot)

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/metrics/events", headers={"Authorization": "Bearer test-viewer"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["totalEvents"] == 9
    assert payload["peakBucketLabel"] == "10:00"


def test_stream_sizes_endpoint_allows_viewer_access(monkeypatch):
    monkeypatch.setenv("LEDGER_API_KEYS", "viewer=test-viewer,admin=test-admin")

    store = InMemoryEventStore()
    seeded_events = [
        ("loan-API-1", [{"event_type": "ApplicationSubmitted", "event_version": 1, "payload": {}, "recorded_at": datetime(2026, 3, 24, 9, 0, tzinfo=timezone.utc)}], -1),
        ("compliance-API-1", [{"event_type": "ComplianceCheckInitiated", "event_version": 1, "payload": {}, "recorded_at": datetime(2026, 3, 24, 9, 10, tzinfo=timezone.utc)}], -1),
        ("agent-credit_analysis-sess-1", [{"event_type": "AgentSessionStarted", "event_version": 1, "payload": {}, "recorded_at": datetime(2026, 3, 24, 9, 15, tzinfo=timezone.utc)}], -1),
        ("audit-API-1", [{"event_type": "LedgerEntryRecorded", "event_version": 1, "payload": {}, "recorded_at": datetime(2026, 3, 24, 9, 20, tzinfo=timezone.utc)}], -1),
    ]

    class _Backend:
        def __init__(self):
            self.store = store
            self._seeded = False

        async def sync(self):
            if not self._seeded:
                for stream_id, events, expected_version in seeded_events:
                    await self.store.append(stream_id, events, expected_version=expected_version)
                self._seeded = True
            return {"ok": True}

        async def close(self):
            return None

    async def _build_backend():
        return _Backend()

    monkeypatch.setattr("ledger.api._build_backend", _build_backend)

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/metrics/streams", headers={"Authorization": "Bearer test-viewer"})

    assert response.status_code == 200
    payload = response.json()
    by_name = {row["streamName"]: row for row in payload}
    assert by_name["LoanApplication"]["eventCount"] == 1
    assert by_name["ComplianceRecord"]["eventCount"] == 1
    assert by_name["AgentSession"]["eventCount"] == 1
    assert by_name["AuditLedger"]["eventCount"] == 1


def test_review_queue_metrics_endpoint_allows_reviewer_access(monkeypatch):
    monkeypatch.setenv("LEDGER_API_KEYS", "reviewer=test-reviewer,admin=test-admin")

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/review-queue/metrics", headers={"Authorization": "Bearer test-reviewer"})

    assert response.status_code == 200
    payload = response.json()
    assert "backlogCount" in payload
    assert "oldestPendingAgeMillis" in payload


def test_readiness_endpoint_reports_backend_state():
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["ready"] is True
    assert "store" in payload


def test_stuck_sessions_endpoint_uses_requested_timeout(monkeypatch):
    calls: list[int] = []

    class _Backend:
        async def sync(self):
            return {"ok": True}

        async def close(self):
            return None

        async def list_stuck_agent_sessions(self, timeout_ms: int = 600000):
            calls.append(timeout_ms)
            return [
                {
                    "sessionId": "sess-open",
                    "status": "STARTED",
                    "ageMs": 901000,
                    "timeoutMs": timeout_ms,
                }
            ]

    async def _build_backend():
        return _Backend()

    monkeypatch.setattr("ledger.api._build_backend", _build_backend)

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/agents/stuck-sessions?timeout_ms=901000")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["sessionId"] == "sess-open"
    assert payload[0]["timeoutMs"] == 901000
    assert calls == [901000]
