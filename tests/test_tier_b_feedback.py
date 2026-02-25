"""Tests for Tier B feedback API endpoint."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from accounting_agent.common.db import Base, make_engine
from accounting_agent.common.settings import get_settings


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    """TestClient backed by in-memory SQLite."""
    agent_db = tmp_path / "agent.sqlite"
    monkeypatch.setenv("AGENT_DB_DSN", f"sqlite+pysqlite:///{agent_db}")
    monkeypatch.setenv("ERPX_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("ERPX_TOKEN", "testtoken")
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("MINIO_SECRET_KEY", "minioadmin")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
    monkeypatch.setenv("AGENT_API_KEY", "test-key")

    engine = make_engine()
    Base.metadata.create_all(engine)

    from accounting_agent.agent_service import main as svc_main

    get_settings.cache_clear()
    monkeypatch.setattr(svc_main, "ensure_buckets", lambda _settings: None)
    svc_main.ENGINE = None
    with TestClient(svc_main.app, raise_server_exceptions=False) as c:
        yield c


_HEADERS = {"X-API-Key": "test-key"}


def test_post_feedback_explicit_yes(client):
    resp = client.post(
        "/agent/v1/tier-b/feedback",
        json={
            "obligation_id": "obl-001",
            "feedback_type": "explicit_yes",
            "user_id": "user-A",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["obligation_id"] == "obl-001"
    assert data["feedback_type"] == "explicit_yes"
    assert "id" in data


def test_post_feedback_explicit_no(client):
    resp = client.post(
        "/agent/v1/tier-b/feedback",
        json={
            "obligation_id": "obl-002",
            "feedback_type": "explicit_no",
            "user_id": "user-B",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["feedback_type"] == "explicit_no"


def test_post_feedback_implicit_accept(client):
    resp = client.post(
        "/agent/v1/tier-b/feedback",
        json={
            "obligation_id": "obl-003",
            "feedback_type": "implicit_accept",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["feedback_type"] == "implicit_accept"


def test_list_feedback(client):
    # Insert some feedback first
    for obl_id in ("obl-a", "obl-b", "obl-c"):
        client.post(
            "/agent/v1/tier-b/feedback",
            json={"obligation_id": obl_id, "feedback_type": "explicit_yes"},
            headers=_HEADERS,
        )
    resp = client.get("/agent/v1/tier-b/feedback", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert len(data["items"]) >= 3


def test_list_feedback_filtered(client):
    client.post(
        "/agent/v1/tier-b/feedback",
        json={"obligation_id": "obl-filter", "feedback_type": "explicit_no"},
        headers=_HEADERS,
    )
    resp = client.get(
        "/agent/v1/tier-b/feedback",
        params={"obligation_id": "obl-filter"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) >= 1
    assert all(item["obligation_id"] == "obl-filter" for item in data["items"])
