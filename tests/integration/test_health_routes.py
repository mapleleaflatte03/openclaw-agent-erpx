"""Regression test: /healthz, /readyz, /agent/v1/healthz, /agent/v1/readyz must exist."""
from __future__ import annotations

from fastapi.testclient import TestClient

from openclaw_agent.agent_service.main import app

# /readyz may return 500 in test env (no DB) — that's OK; 404 means route missing.
_READYZ_OK = (200, 503, 500)


def test_healthz_exists():
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/healthz")
    assert r.status_code == 200, f"/healthz returned {r.status_code}"


def test_readyz_exists():
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/readyz")
    assert r.status_code in _READYZ_OK, f"/readyz returned {r.status_code}"


def test_agent_v1_healthz_exists():
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/agent/v1/healthz")
    assert r.status_code == 200, f"/agent/v1/healthz returned {r.status_code}"


def test_agent_v1_readyz_exists():
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/agent/v1/readyz")
    assert r.status_code in _READYZ_OK, f"/agent/v1/readyz returned {r.status_code}"
