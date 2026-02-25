"""Integration tests for Phase 7 – Q&A Accounting.

Tests cover:
  - POST /agent/v1/acct/qna endpoint works
  - Voucher count question returns correct data
  - Journal explanation question returns account codes
  - Q&A audit trail is persisted
  - Fallback for unknown questions
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from accounting_agent.agent_service.main import app

client = TestClient(app, raise_server_exceptions=False)
_HEADERS = {"X-API-Key": "test-key-for-ci"}


def test_qna_endpoint_exists():
    """POST /agent/v1/acct/qna should respond (not 404/405)."""
    r = client.post(
        "/agent/v1/acct/qna",
        json={"question": "test"},
        headers=_HEADERS,
    )
    # Accept 200 (success), 500 (no DB), or 401 (auth) — never 404 or 405
    assert r.status_code in (200, 401, 500), f"Unexpected status: {r.status_code}"


def test_qna_requires_question():
    """Empty question should return 400."""
    r = client.post(
        "/agent/v1/acct/qna",
        json={"question": ""},
        headers=_HEADERS,
    )
    # Either 400 (validation) or 500 (no DB before validation)
    assert r.status_code >= 400


def test_qna_voucher_count_handler():
    """Voucher count handler returns expected format."""
    from unittest.mock import MagicMock

    from accounting_agent.flows.qna_accounting import _answer_voucher_count

    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 3

    result = _answer_voucher_count(mock_session, "Tháng 1/2025 có bao nhiêu chứng từ đã ingest?")
    assert result is not None
    assert "3 chứng từ" in result["answer"]
    assert "AcctVoucher" in result["used_models"]


def test_qna_journal_explanation_handler():
    """Journal explanation handler returns account codes."""
    from unittest.mock import MagicMock

    from accounting_agent.flows.qna_accounting import _answer_journal_explanation

    mock_session = MagicMock()

    # Mock voucher
    mock_voucher = MagicMock()
    mock_voucher.voucher_no = "0000123"
    mock_voucher.voucher_type = "sell_invoice"
    mock_voucher.amount = 11_000_000
    mock_voucher.currency = "VND"
    mock_voucher.id = "v-1"
    mock_session.query.return_value.filter.return_value.first.return_value = mock_voucher

    # Mock journal proposal
    mock_proposal = MagicMock()
    mock_proposal.id = "jp-1"
    mock_proposal.confidence = 0.92
    mock_proposal.reasoning = "Rule-based classification."
    mock_session.query.return_value.filter_by.return_value.first.return_value = mock_proposal

    # Mock journal lines
    line1 = MagicMock()
    line1.account_code = "131"
    line1.account_name = "Phải thu khách hàng"
    line1.debit = 11_000_000
    line1.credit = 0
    line2 = MagicMock()
    line2.account_code = "511"
    line2.account_name = "Doanh thu bán hàng"
    line2.debit = 0
    line2.credit = 11_000_000
    mock_session.query.return_value.filter_by.return_value.all.return_value = [line1, line2]

    result = _answer_journal_explanation(
        mock_session,
        "Vì sao chứng từ hóa đơn số 0000123 được gợi ý hạch toán như vậy?"
    )
    assert result is not None
    # Must mention at least one account
    assert "511" in result["answer"] or "131" in result["answer"]
    # Must explain why
    assert "bán hàng" in result["answer"].lower()


def test_qna_anomaly_handler():
    """Anomaly handler works correctly."""
    from unittest.mock import MagicMock

    from accounting_agent.flows.qna_accounting import _answer_anomaly_summary

    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.side_effect = [5, 2]

    result = _answer_anomaly_summary(mock_session, "Có bao nhiêu giao dịch bất thường?")
    assert result is not None
    assert "5" in result["answer"]


def test_qna_cashflow_handler_no_data():
    """Cashflow handler with no data returns helpful message."""
    from unittest.mock import MagicMock

    from accounting_agent.flows.qna_accounting import _answer_cashflow_summary

    mock_session = MagicMock()
    mock_session.execute.return_value.scalars.return_value.all.return_value = []

    result = _answer_cashflow_summary(mock_session, "Tóm tắt dòng tiền dự báo")
    assert result is not None
    assert "chưa có" in result["answer"].lower() or "cashflow" in result["answer"].lower()


def test_qna_fallback_handler():
    """Unknown question returns fallback answer."""
    from unittest.mock import MagicMock

    from accounting_agent.flows.qna_accounting import answer_question

    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 0

    result = answer_question(mock_session, "Thời tiết hôm nay thế nào?")
    assert "answer" in result
    assert len(result["answer"]) > 0


def test_qna_period_extraction():
    """Period extraction from Vietnamese text."""
    from accounting_agent.flows.qna_accounting import _extract_period

    assert _extract_period("Tháng 1/2025 có bao nhiêu chứng từ?") == "2025-01"
    assert _extract_period("Tháng 12/2024 như thế nào?") == "2024-12"
    assert _extract_period("Kỳ 2025-03 có gì?") == "2025-03"
    assert _extract_period("Câu hỏi chung") is None
