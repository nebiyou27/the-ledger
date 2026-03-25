from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ledger.event_store import InMemoryEventStore
from ledger.api import _backend_get_application_detail, _build_documents, create_app


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


def test_document_download_endpoint_serves_saved_file(monkeypatch, tmp_path):
    documents_root = tmp_path / "documents"
    file_path = documents_root / "COMP-001" / "APL-2026-0001" / "application_proposal_pitch.pdf"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"pdf-bytes")

    class _Backend:
        async def sync(self):
            return {"ok": True}

        async def close(self):
            return None

    async def _build_backend():
        return _Backend()

    monkeypatch.setattr("ledger.api._build_backend", _build_backend)
    monkeypatch.setenv("DOCUMENTS_DIR", str(documents_root))

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/documents/COMP-001/APL-2026-0001/application_proposal_pitch.pdf")

    assert response.status_code == 200
    assert response.content == b"pdf-bytes"


def test_build_documents_includes_download_url(monkeypatch, tmp_path):
    documents_root = tmp_path / "documents"
    file_path = documents_root / "COMP-001" / "APL-2026-0001" / "application_proposal_pitch.pdf"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"pdf-bytes")

    monkeypatch.setenv("DOCUMENTS_DIR", str(documents_root))

    docs = _build_documents(
        [
            {
                "event_type": "DocumentUploaded",
                "payload": {
                    "filename": "application_proposal_pitch.pdf",
                    "document_id": "doc-123",
                    "document_type": "application_proposal",
                    "file_size_bytes": 9,
                    "file_path": str(file_path),
                },
            }
        ]
    )

    assert docs[0]["downloadUrl"] == "/documents/COMP-001/APL-2026-0001/application_proposal_pitch.pdf"


def test_application_detail_falls_back_to_loan_events_when_summary_is_missing(monkeypatch):
    monkeypatch.setenv("LEDGER_API_KEYS", "viewer=test-viewer,admin=test-admin")

    application_id = "APL-2026-0100"
    loan_events = [
        {
            "stream_id": f"loan-{application_id}",
            "event_type": "ApplicationSubmitted",
            "event_version": 1,
            "payload": {
                "application_id": application_id,
                "applicant_id": "COMP-001",
                "requested_amount_usd": 250000,
                "loan_purpose": "working_capital",
            },
            "recorded_at": datetime(2026, 3, 24, 10, 0, tzinfo=timezone.utc),
            "stream_position": 0,
        },
        {
            "stream_id": f"loan-{application_id}",
            "event_type": "DocumentUploaded",
            "event_version": 1,
            "payload": {
                "application_id": application_id,
                "document_id": "doc-abc",
                "document_type": "application_proposal",
                "document_format": "pdf",
                "filename": "proposal.pdf",
                "file_path": "documents/COMP-001/APL-2026-0100/proposal.pdf",
                "file_size_bytes": 1234,
                "file_hash": "hash",
                "fiscal_year": None,
                "uploaded_at": datetime(2026, 3, 24, 10, 1, tzinfo=timezone.utc),
                "uploaded_by": "browser-upload",
            },
            "recorded_at": datetime(2026, 3, 24, 10, 1, tzinfo=timezone.utc),
            "stream_position": 1,
        },
    ]

    class _Projection:
        def get_application(self, application_id: str):
            return None

        def all_rows(self):
            return []

    class _ComplianceAudit:
        def get_current_compliance(self, application_id: str):
            return None

        def get_compliance_at(self, application_id: str, as_of: str):
            return None

    class _Runtime:
        application_summary = _Projection()
        compliance_audit = _ComplianceAudit()
        manual_reviews = _Projection()

    class _Backend:
        def __init__(self):
            self.store = self
            self.runtime = _Runtime()
            self.registry = None

        async def sync(self):
            return {"ok": True}

        async def close(self):
            return None

        async def get_application_detail(self, application_id: str):
            return await _backend_get_application_detail(self, application_id)

        async def load_stream(self, stream_id: str):
            if stream_id == f"loan-{application_id}":
                return loan_events
            return []

        async def load_all(self, from_position: int = 0, application_id: str | None = None):
            for event in loan_events:
                if application_id is not None and event["payload"].get("application_id") != application_id:
                    continue
                yield event

    async def _build_backend():
        return _Backend()

    monkeypatch.setattr("ledger.api._build_backend", _build_backend)

    app = create_app()
    with TestClient(app) as client:
        response = client.get(f"/applications/{application_id}", headers={"Authorization": "Bearer test-viewer"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == application_id
    assert payload["documents"][0]["name"] == "proposal.pdf"


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
