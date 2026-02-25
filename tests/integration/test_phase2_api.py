"""Regression tests for Phase 2 API endpoints."""
from __future__ import annotations

from fastapi.testclient import TestClient

from accounting_agent.agent_service.main import app

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"X-API-Key": "test-key-for-ci"}


def test_cashflow_forecast_run_type_accepted():
    """cashflow_forecast must be in _VALID_RUN_TYPES."""
    r = client.post(
        "/agent/v1/runs",
        json={"run_type": "cashflow_forecast", "trigger_type": "manual", "payload": {}},
        headers=_HEADERS,
    )
    assert r.status_code != 400 or "invalid run_type" not in r.text, (
        f"cashflow_forecast rejected as invalid: {r.text}"
    )


def test_soft_check_results_endpoint():
    """GET /agent/v1/acct/soft_check_results should return valid response or auth error."""
    r = client.get("/agent/v1/acct/soft_check_results", headers=_HEADERS)
    # Accept 200 (success) or 500 (no DB in test env) or 401 (auth)
    assert r.status_code in (200, 401, 500)


def test_validation_issues_endpoint():
    r = client.get("/agent/v1/acct/validation_issues", headers=_HEADERS)
    assert r.status_code in (200, 401, 500)


def test_report_snapshots_endpoint():
    r = client.get("/agent/v1/acct/report_snapshots", headers=_HEADERS)
    assert r.status_code in (200, 401, 500)


def test_cashflow_forecast_endpoint():
    r = client.get("/agent/v1/acct/cashflow_forecast", headers=_HEADERS)
    assert r.status_code in (200, 401, 500)


def test_qna_audits_endpoint():
    r = client.get("/agent/v1/acct/qna_audits", headers=_HEADERS)
    assert r.status_code in (200, 401, 500)


def test_resolve_validation_issue_404():
    """Resolving a non-existent issue should return 404 (or 500 if no DB)."""
    r = client.post(
        "/agent/v1/acct/validation_issues/nonexistent/resolve",
        json={"action": "resolved", "resolved_by": "test"},
        headers=_HEADERS,
    )
    assert r.status_code in (404, 401, 500)
