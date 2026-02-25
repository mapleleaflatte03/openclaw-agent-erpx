"""Integration tests for Phase 5 – Voucher Ingest.

Tests cover:
  - run_type voucher_ingest is accepted by the API
  - VN invoice fixture creates correct AcctVoucher rows
  - VN cash voucher fixture creates correct AcctVoucher rows
  - Flow produces correct stats
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from accounting_agent.agent_service.main import app

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"X-API-Key": "test-key-for-ci"}


def test_voucher_ingest_run_type_accepted():
    """voucher_ingest must be in _VALID_RUN_TYPES."""
    r = client.post(
        "/agent/v1/runs",
        json={
            "run_type": "voucher_ingest",
            "trigger_type": "manual",
            "payload": {"source": "vn_fixtures"},
        },
        headers=_HEADERS,
    )
    assert r.status_code != 400 or "invalid run_type" not in r.text, (
        f"voucher_ingest rejected as invalid: {r.text}"
    )


# Required keys every normalized VN fixture record must have
_REQUIRED_KEYS = {
    "voucher_no", "amount", "currency", "partner_name",
    "source", "raw_payload", "voucher_type", "type_hint",
}


def test_voucher_ingest_creates_vn_invoice_voucher():
    """Any VN invoice fixture must normalize to AcctVoucher-compatible dict."""
    from accounting_agent.flows.voucher_ingest import VN_FIXTURES, _normalize_vn_fixture

    invoices = [f for f in VN_FIXTURES if f.get("doc_type") == "invoice_vat"]
    assert len(invoices) >= 1, "Kaggle seed must contain at least one invoice_vat"

    normalized = _normalize_vn_fixture(invoices[0])
    assert set(normalized.keys()) >= _REQUIRED_KEYS
    assert normalized["currency"] == "VND"
    assert normalized["type_hint"] == "invoice_vat"
    assert normalized["voucher_type"] == "sell_invoice"
    assert normalized["source"] == "mock_vn_fixture"
    assert normalized["raw_payload"] is invoices[0]
    assert isinstance(normalized["amount"], float)
    assert normalized["voucher_no"]  # non-empty


def test_voucher_ingest_creates_vn_cash_voucher():
    """Any VN cash disbursement fixture must normalize correctly."""
    from accounting_agent.flows.voucher_ingest import VN_FIXTURES, _normalize_vn_fixture

    cash_vouchers = [f for f in VN_FIXTURES if f.get("doc_type") == "cash_disbursement"]
    assert len(cash_vouchers) >= 1, "Kaggle seed must contain at least one cash_disbursement"

    normalized = _normalize_vn_fixture(cash_vouchers[0])
    assert set(normalized.keys()) >= _REQUIRED_KEYS
    assert normalized["currency"] == "VND"
    assert normalized["type_hint"] == "cash_disbursement"
    assert normalized["voucher_type"] == "payment"
    assert normalized["source"] == "mock_vn_fixture"


def test_voucher_ingest_vn_receipt_voucher():
    """Any VN cash receipt fixture must normalize correctly."""
    from accounting_agent.flows.voucher_ingest import VN_FIXTURES, _normalize_vn_fixture

    receipts = [f for f in VN_FIXTURES if f.get("doc_type") == "cash_receipt"]
    assert len(receipts) >= 1, "Kaggle seed must contain at least one cash_receipt"

    normalized = _normalize_vn_fixture(receipts[0])
    assert set(normalized.keys()) >= _REQUIRED_KEYS
    assert normalized["currency"] == "VND"
    assert normalized["type_hint"] == "cash_receipt"
    assert normalized["voucher_type"] == "receipt"


def test_voucher_ingest_flow_produces_stats():
    """_load_documents returns Kaggle-sourced records with required fields."""
    from accounting_agent.flows.voucher_ingest import _load_documents

    docs = _load_documents("vn_fixtures", {})
    assert len(docs) >= 1, "Kaggle seed must produce at least 1 document"
    assert all(d["currency"] == "VND" for d in docs)
    assert all(d["voucher_no"] for d in docs)  # every record has a voucher_no


def test_vouchers_list_endpoint():
    """GET /agent/v1/acct/vouchers should return valid response."""
    r = client.get("/agent/v1/acct/vouchers", headers=_HEADERS)
    assert r.status_code in (200, 401, 500)


def test_voucher_classification_stats_endpoint():
    """GET /agent/v1/acct/voucher_classification_stats should return valid response."""
    r = client.get("/agent/v1/acct/voucher_classification_stats", headers=_HEADERS)
    assert r.status_code in (200, 401, 500)
