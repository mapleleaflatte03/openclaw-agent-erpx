"""Integration tests for Phase 6 – Voucher Classification.

Tests cover:
  - run_type voucher_classify is accepted by the API
  - VN invoice → SALES_INVOICE tag
  - VN cash voucher → CASH_DISBURSEMENT tag
  - Classification logic consistency
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from accounting_agent.agent_service.main import app

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"X-API-Key": "test-key-for-ci"}


def test_voucher_classify_run_type_accepted():
    """voucher_classify must be in _VALID_RUN_TYPES."""
    r = client.post(
        "/agent/v1/runs",
        json={
            "run_type": "voucher_classify",
            "trigger_type": "manual",
            "payload": {},
        },
        headers=_HEADERS,
    )
    assert r.status_code != 400 or "invalid run_type" not in r.text, (
        f"voucher_classify rejected as invalid: {r.text}"
    )


def test_acct_classify_tags_vn_invoice():
    """Hóa đơn bán hàng VAT → SALES_INVOICE tag."""
    from unittest.mock import MagicMock

    from accounting_agent.flows.voucher_classify import _classify_tag

    voucher = MagicMock()
    voucher.voucher_type = "sell_invoice"
    voucher.type_hint = "invoice_vat"
    voucher.description = "Bán hàng hóa theo hợp đồng"

    tag = _classify_tag(voucher)
    assert tag == "SALES_INVOICE"


def test_acct_classify_tags_vn_cash_voucher():
    """Phiếu chi → CASH_DISBURSEMENT tag."""
    from unittest.mock import MagicMock

    from accounting_agent.flows.voucher_classify import _classify_tag

    voucher = MagicMock()
    voucher.voucher_type = "payment"
    voucher.type_hint = "cash_disbursement"
    voucher.description = "Chi tiền tiếp khách"

    tag = _classify_tag(voucher)
    assert tag == "CASH_DISBURSEMENT"


def test_acct_classify_tags_vn_receipt():
    """Phiếu thu → CASH_RECEIPT tag."""
    from unittest.mock import MagicMock

    from accounting_agent.flows.voucher_classify import _classify_tag

    voucher = MagicMock()
    voucher.voucher_type = "receipt"
    voucher.type_hint = "cash_receipt"
    voucher.description = "Thu tiền thanh toán hóa đơn"

    tag = _classify_tag(voucher)
    assert tag == "CASH_RECEIPT"


def test_acct_classify_tags_buy_invoice():
    """Hóa đơn mua hàng → PURCHASE_INVOICE tag."""
    from unittest.mock import MagicMock

    from accounting_agent.flows.voucher_classify import _classify_tag

    voucher = MagicMock()
    voucher.voucher_type = "buy_invoice"
    voucher.type_hint = ""
    voucher.description = "Mua hàng nguyên vật liệu"

    tag = _classify_tag(voucher)
    assert tag == "PURCHASE_INVOICE"


def test_acct_classify_tags_payroll():
    """Description chứa 'lương' → PAYROLL tag."""
    from unittest.mock import MagicMock

    from accounting_agent.flows.voucher_classify import _classify_tag

    voucher = MagicMock()
    voucher.voucher_type = "other"
    voucher.type_hint = ""
    voucher.description = "Chi trả tiền lương tháng 01/2025"

    tag = _classify_tag(voucher)
    assert tag == "PAYROLL"


def test_acct_classify_tags_unknown():
    """Unknown voucher → OTHER tag."""
    from unittest.mock import MagicMock

    from accounting_agent.flows.voucher_classify import _classify_tag

    voucher = MagicMock()
    voucher.voucher_type = "other"
    voucher.type_hint = ""
    voucher.description = "Giao dịch không xác định"

    tag = _classify_tag(voucher)
    assert tag == "OTHER"


def test_classify_single_dict():
    """Test dict-based classification for Ray batch."""
    from accounting_agent.flows.voucher_classify import _classify_single_dict

    result = _classify_single_dict({
        "id": "test-1",
        "voucher_type": "sell_invoice",
        "type_hint": "invoice_vat",
        "description": "Bán hàng",
    })
    assert result["classification_tag"] == "SALES_INVOICE"
    assert result["id"] == "test-1"

    result2 = _classify_single_dict({
        "id": "test-2",
        "voucher_type": "payment",
        "type_hint": "cash_disbursement",
        "description": "Chi tiền",
    })
    assert result2["classification_tag"] == "CASH_DISBURSEMENT"
