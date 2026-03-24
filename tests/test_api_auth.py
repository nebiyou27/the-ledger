from __future__ import annotations

from fastapi.testclient import TestClient

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
