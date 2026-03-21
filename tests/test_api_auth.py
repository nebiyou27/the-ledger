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
