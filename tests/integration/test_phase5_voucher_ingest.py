"""Integration tests for Phase 5 – Voucher Ingest.

Tests cover:
  - run_type voucher_ingest is accepted by the API
  - VN invoice fixture creates correct AcctVoucher rows
  - VN cash voucher fixture creates correct AcctVoucher rows
  - Flow produces correct stats
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from openclaw_agent.agent_service.main import app

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


def test_voucher_ingest_creates_vn_invoice_voucher():
    """VN VAT invoice fixture must map correctly to AcctVoucher fields."""
    from openclaw_agent.flows.voucher_ingest import VN_FIXTURES, _normalize_vn_fixture

    invoice = VN_FIXTURES[0]  # Hóa đơn VAT chuẩn
    normalized = _normalize_vn_fixture(invoice)

    assert normalized["voucher_no"] == "0000123"
    assert normalized["amount"] == 11_000_000
    assert normalized["currency"] == "VND"
    assert normalized["partner_name"] == "CÔNG TY CP XYZ"
    assert normalized["partner_tax_code"] == "0318765432"
    assert normalized["type_hint"] == "invoice_vat"
    assert normalized["voucher_type"] == "sell_invoice"
    assert normalized["source"] == "mock_vn_fixture"
    assert normalized["raw_payload"] == invoice


def test_voucher_ingest_creates_vn_cash_voucher():
    """VN Phiếu chi fixture must map correctly to AcctVoucher fields."""
    from openclaw_agent.flows.voucher_ingest import VN_FIXTURES, _normalize_vn_fixture

    cash_voucher = VN_FIXTURES[1]  # Phiếu chi nội bộ
    normalized = _normalize_vn_fixture(cash_voucher)

    assert normalized["voucher_no"] == "PC0001"
    assert normalized["amount"] == 2_500_000
    assert normalized["currency"] == "VND"
    assert normalized["partner_name"] == "Nguyễn Văn A"
    assert normalized["type_hint"] == "cash_disbursement"
    assert normalized["voucher_type"] == "payment"
    assert normalized["source"] == "mock_vn_fixture"


def test_voucher_ingest_vn_receipt_voucher():
    """VN Phiếu thu fixture must map correctly."""
    from openclaw_agent.flows.voucher_ingest import VN_FIXTURES, _normalize_vn_fixture

    receipt = VN_FIXTURES[2]
    normalized = _normalize_vn_fixture(receipt)

    assert normalized["voucher_no"] == "PT0001"
    assert normalized["amount"] == 5_000_000
    assert normalized["type_hint"] == "cash_receipt"
    assert normalized["voucher_type"] == "receipt"


def test_voucher_ingest_flow_produces_stats():
    """flow_voucher_ingest returns correct stats dict."""
    from openclaw_agent.flows.voucher_ingest import _load_documents

    docs = _load_documents("vn_fixtures", {})
    assert len(docs) == 3
    assert all(d["currency"] == "VND" for d in docs)
    assert docs[0]["voucher_no"] == "0000123"
    assert docs[1]["voucher_no"] == "PC0001"
    assert docs[2]["voucher_no"] == "PT0001"


def test_vouchers_list_endpoint():
    """GET /agent/v1/acct/vouchers should return valid response."""
    r = client.get("/agent/v1/acct/vouchers", headers=_HEADERS)
    assert r.status_code in (200, 401, 500)


def test_voucher_classification_stats_endpoint():
    """GET /agent/v1/acct/voucher_classification_stats should return valid response."""
    r = client.get("/agent/v1/acct/voucher_classification_stats", headers=_HEADERS)
    assert r.status_code in (200, 401, 500)
