"""Flow – Q&A Accounting (Trợ lý hỏi đáp & diễn giải nghiệp vụ kế toán).

Template-based + DB query answering. No LLM required (yet).
Answers are built from real Acct* data — NEVER fabricates numbers.

Supported question patterns:
  1. Voucher count queries ("bao nhiêu chứng từ", "tháng X có bao nhiêu")
  2. Journal explanation queries ("vì sao", "hạch toán", "tài khoản")
  3. Anomaly summary queries ("bất thường", "anomaly")
  4. Cashflow queries ("dòng tiền", "cashflow")
  5. Classification summary ("phân loại", "classification")
  6. Default: generic helpful answer

Future: integrate LangGraph chain for multi-step reasoning.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclaw_agent.common.models import (
    AcctAnomalyFlag,
    AcctCashflowForecast,
    AcctJournalLine,
    AcctJournalProposal,
    AcctVoucher,
)

log = logging.getLogger("openclaw.flows.qna_accounting")


def _extract_period(question: str) -> str | None:
    """Try to extract YYYY-MM from question text."""
    # "tháng 1/2025" or "tháng 01/2025"
    m = re.search(r"tháng\s*(\d{1,2})\s*/\s*(\d{4})", question, re.I)
    if m:
        month = int(m.group(1))
        year = int(m.group(2))
        return f"{year}-{month:02d}"

    # "2025-01" direct
    m = re.search(r"(\d{4})-(\d{2})", question)
    if m:
        return m.group(0)

    return None


def _extract_voucher_no(question: str) -> str | None:
    """Try to extract a voucher/invoice number."""
    m = re.search(r"(?:chứng từ|hóa đơn|số)\s*(?:số\s*)?([A-Z0-9]+)", question, re.I)
    if m:
        return m.group(1)
    return None


def _answer_voucher_count(session: Session, question: str) -> dict[str, Any] | None:
    """Answer: how many vouchers in a period?"""
    keywords = ["bao nhiêu chứng từ", "số lượng chứng từ", "có bao nhiêu", "tổng chứng từ"]
    if not any(kw in question.lower() for kw in keywords):
        return None

    period = _extract_period(question)
    q = select(func.count(AcctVoucher.id))
    if period:
        q = q.where(AcctVoucher.date.like(f"{period}%"))
    count = session.execute(q).scalar() or 0

    if period:
        # Format for display
        parts = period.split("-")
        display_period = f"tháng {int(parts[1])}/{parts[0]}"
        answer = f"Trong {display_period}, hệ thống ghi nhận {count} chứng từ đã ingest."
    else:
        answer = f"Tổng cộng hệ thống đã ingest {count} chứng từ."

    return {
        "answer": answer,
        "used_models": ["AcctVoucher"],
    }


def _answer_journal_explanation(session: Session, question: str) -> dict[str, Any] | None:
    """Answer: why was a voucher classified / journaled a certain way?"""
    keywords = ["vì sao", "tại sao", "hạch toán", "gợi ý", "tài khoản"]
    if not any(kw in question.lower() for kw in keywords):
        return None

    voucher_no = _extract_voucher_no(question)
    if not voucher_no:
        return {
            "answer": (
                "Để giải thích bút toán, vui lòng cung cấp số chứng từ/hóa đơn cụ thể. "
                "Ví dụ: 'Vì sao chứng từ hóa đơn số 0000123 được gợi ý hạch toán như vậy?'"
            ),
            "used_models": [],
        }

    # Find voucher
    voucher = session.query(AcctVoucher).filter(
        AcctVoucher.voucher_no.like(f"%{voucher_no}%")
    ).first()

    if not voucher:
        return {
            "answer": f"Không tìm thấy chứng từ số {voucher_no} trong hệ thống.",
            "used_models": ["AcctVoucher"],
        }

    # Find journal proposal for this voucher
    proposal = session.query(AcctJournalProposal).filter_by(voucher_id=voucher.id).first()
    if not proposal:
        return {
            "answer": (
                f"Chứng từ số {voucher.voucher_no} (loại: {voucher.voucher_type}, "
                f"số tiền: {voucher.amount:,.0f} {voucher.currency}) đã được ghi nhận "
                f"nhưng chưa có đề xuất bút toán."
            ),
            "used_models": ["AcctVoucher"],
        }

    # Build explanation from journal lines
    lines = session.query(AcctJournalLine).filter_by(proposal_id=proposal.id).all()
    lines_desc = []
    accounts_mentioned = []
    for ln in lines:
        if ln.debit > 0:
            lines_desc.append(f"Nợ TK {ln.account_code} ({ln.account_name}): {ln.debit:,.0f}")
        if ln.credit > 0:
            lines_desc.append(f"Có TK {ln.account_code} ({ln.account_name}): {ln.credit:,.0f}")
        accounts_mentioned.append(ln.account_code)

    lines_str = "; ".join(lines_desc)
    reasoning = proposal.reasoning or ""

    # Build VN explanation
    vtype_map = {
        "sell_invoice": "hóa đơn bán hàng",
        "buy_invoice": "hóa đơn mua hàng",
        "receipt": "phiếu thu",
        "payment": "phiếu chi",
    }
    vtype_label = vtype_map.get(voucher.voucher_type, voucher.voucher_type)

    answer_parts = [
        f"Chứng từ số {voucher.voucher_no} là {vtype_label}, "
        f"số tiền {voucher.amount:,.0f} {voucher.currency}.",
    ]

    if voucher.voucher_type == "sell_invoice":
        answer_parts.append(
            "Do đây là hóa đơn bán hàng nên ghi nhận doanh thu TK 511 "
            "và phải thu khách hàng TK 131."
        )
    elif voucher.voucher_type == "buy_invoice":
        answer_parts.append(
            "Do đây là hóa đơn mua hàng nên ghi nhận chi phí NVL TK 621 "
            "và phải trả người bán TK 331."
        )
    elif voucher.voucher_type == "receipt":
        answer_parts.append(
            "Do đây là phiếu thu nên ghi nhận tiền mặt TK 111 "
            "và giảm phải thu TK 131."
        )
    elif voucher.voucher_type == "payment":
        answer_parts.append(
            "Do đây là phiếu chi nên ghi nhận phải trả TK 331 "
            "và giảm tiền gửi ngân hàng TK 112."
        )

    answer_parts.append(f"Bút toán đề xuất: {lines_str}.")
    answer_parts.append(f"Độ tin cậy: {proposal.confidence:.0%}.")

    if reasoning:
        answer_parts.append(f"Lý do: {reasoning}")

    return {
        "answer": " ".join(answer_parts),
        "used_models": ["AcctVoucher", "AcctJournalProposal", "AcctJournalLine"],
    }


def _answer_anomaly_summary(session: Session, question: str) -> dict[str, Any] | None:
    """Answer: anomaly/risk summary."""
    keywords = ["bất thường", "anomaly", "rủi ro", "risk"]
    if not any(kw in question.lower() for kw in keywords):
        return None

    total = session.execute(select(func.count(AcctAnomalyFlag.id))).scalar() or 0
    open_count = session.execute(
        select(func.count(AcctAnomalyFlag.id)).where(AcctAnomalyFlag.resolution == "open")
    ).scalar() or 0

    if total == 0:
        answer = "Hiện tại chưa phát hiện giao dịch bất thường nào trong hệ thống."
    else:
        answer = (
            f"Hệ thống đã phát hiện tổng cộng {total} cảnh báo bất thường, "
            f"trong đó {open_count} cảnh báo chưa được xử lý."
        )

    return {
        "answer": answer,
        "used_models": ["AcctAnomalyFlag"],
    }


def _answer_cashflow_summary(session: Session, question: str) -> dict[str, Any] | None:
    """Answer: cashflow summary."""
    keywords = ["dòng tiền", "cashflow", "cash flow", "thu chi"]
    if not any(kw in question.lower() for kw in keywords):
        return None

    rows = session.execute(select(AcctCashflowForecast)).scalars().all()
    if not rows:
        return {
            "answer": "Chưa có dữ liệu dự báo dòng tiền. Hãy chạy 'cashflow_forecast' trước.",
            "used_models": ["AcctCashflowForecast"],
        }

    total_in = sum(r.amount for r in rows if r.direction == "inflow")
    total_out = sum(r.amount for r in rows if r.direction == "outflow")
    net = total_in - total_out

    answer = (
        f"Dòng tiền dự báo: Thu {total_in:,.0f} VND, Chi {total_out:,.0f} VND, "
        f"Ròng {net:,.0f} VND ({len(rows)} dòng dự báo)."
    )

    return {
        "answer": answer,
        "used_models": ["AcctCashflowForecast"],
    }


def _answer_classification_summary(session: Session, question: str) -> dict[str, Any] | None:
    """Answer: classification breakdown."""
    keywords = ["phân loại", "classification", "loại chứng từ"]
    if not any(kw in question.lower() for kw in keywords):
        return None

    q = (
        select(AcctVoucher.classification_tag, func.count(AcctVoucher.id))
        .group_by(AcctVoucher.classification_tag)
    )
    rows = session.execute(q).all()

    if not rows:
        return {
            "answer": "Chưa có dữ liệu phân loại chứng từ.",
            "used_models": ["AcctVoucher"],
        }

    parts = [f"{tag or 'Chưa phân loại'}: {cnt}" for tag, cnt in rows]
    answer = "Phân loại chứng từ hiện tại: " + ", ".join(parts) + "."

    return {
        "answer": answer,
        "used_models": ["AcctVoucher"],
    }


def answer_question(session: Session, question: str) -> dict[str, Any]:
    """Main Q&A dispatcher — tries each handler in priority order.

    Returns: {"answer": str, "used_models": list[str]}
    """
    handlers = [
        _answer_voucher_count,
        _answer_journal_explanation,
        _answer_anomaly_summary,
        _answer_cashflow_summary,
        _answer_classification_summary,
    ]

    for handler in handlers:
        result = handler(session, question)
        if result is not None:
            return result

    # Default fallback
    return {
        "answer": (
            "Xin lỗi, tôi chưa hiểu câu hỏi này. Bạn có thể hỏi về:\n"
            "- Số lượng chứng từ (ví dụ: 'Tháng 1/2025 có bao nhiêu chứng từ?')\n"
            "- Giải thích bút toán (ví dụ: 'Vì sao chứng từ số 0000123 được hạch toán vậy?')\n"
            "- Giao dịch bất thường (ví dụ: 'Có bao nhiêu anomaly chưa xử lý?')\n"
            "- Dòng tiền (ví dụ: 'Tóm tắt dòng tiền dự báo')\n"
            "- Phân loại chứng từ (ví dụ: 'Thống kê phân loại chứng từ')"
        ),
        "used_models": [],
    }
