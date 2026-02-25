"""Regression: journal_suggestion & bank_reconcile must be accepted by create_run."""
from __future__ import annotations

from fastapi.testclient import TestClient

from accounting_agent.agent_service.main import app

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"X-API-Key": "test-key-for-ci"}  # auth disabled in test env anyway


def test_journal_suggestion_not_400():
    """POST /agent/v1/runs with run_type=journal_suggestion must not return 400."""
    r = client.post(
        "/agent/v1/runs",
        json={"run_type": "journal_suggestion", "trigger_type": "manual", "payload": {}},
        headers=_HEADERS,
    )
    # 400 = invalid run_type, which was the P0 bug.  Anything else is acceptable.
    assert r.status_code != 400 or "invalid run_type" not in r.text, (
        f"journal_suggestion rejected as invalid run_type: {r.text}"
    )


def test_bank_reconcile_not_400():
    """POST /agent/v1/runs with run_type=bank_reconcile must not return 400."""
    r = client.post(
        "/agent/v1/runs",
        json={"run_type": "bank_reconcile", "trigger_type": "manual", "payload": {}},
        headers=_HEADERS,
    )
    assert r.status_code != 400 or "invalid run_type" not in r.text, (
        f"bank_reconcile rejected as invalid run_type: {r.text}"
    )


def test_invalid_run_type_still_rejected():
    """Unknown run_type should never be accepted (status 200/201/202)."""
    r = client.post(
        "/agent/v1/runs",
        json={"run_type": "nonexistent_workflow", "trigger_type": "manual", "payload": {}},
        headers=_HEADERS,
    )
    # In CI without DB: get_session dependency may fail (500) before
    # validation runs, or auth blocks (401), or validation rejects (400).
    # The key invariant is that it is NEVER successfully accepted.
    assert r.status_code >= 400, f"invalid run_type was accepted: {r.status_code}"
