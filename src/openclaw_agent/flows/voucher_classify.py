"""Flow – Voucher Classification (Phân loại chứng từ/giao dịch).

Steps:
  1. Fetch unclassified AcctVouchers from mirror DB
  2. Apply rule-based classification with confidence scoring
  3. Update classification_tag on each voucher

Fine-tune hooks:
  - ``_classify_with_confidence()`` → returns (tag, confidence, reason)
  - VN tax regulation awareness (Thông tư 133/200, account chart mapping)
  - ``_suggest_account_entry()`` → heuristic debit/credit suggestion
  - Extensible rule registry for adding new classification patterns

Supports Ray batch via kernel.batch.batch_classify_vouchers when USE_RAY=1.

Classification tags:
  - PURCHASE_INVOICE   – Hóa đơn đầu vào
  - SALES_INVOICE      – Hóa đơn đầu ra
  - CASH_DISBURSEMENT  – Phiếu chi
  - CASH_RECEIPT       – Phiếu thu
  - PAYROLL            – Lương
  - FIXED_ASSET        – Tài sản cố định
  - TAX_DECLARATION    – Kê khai thuế
  - BANK_TRANSACTION   – Giao dịch ngân hàng
  - OTHER              – Khác / chưa xác định
"""
from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy.orm import Session

from openclaw_agent.common.models import AcctVoucher

log = logging.getLogger("openclaw.flows.voucher_classify")

_USE_RAY = os.getenv("USE_RAY", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# VN Account Chart mapping (Thông tư 133/200 – fine-tune hook)
# ---------------------------------------------------------------------------

# Heuristic debit/credit account suggestions per classification tag.
# Based on Hệ thống tài khoản kế toán Việt Nam (Thông tư 200/2014/TT-BTC).
# Fine-tune hook: expand with more granular account mappings.
_VN_ACCOUNT_SUGGESTIONS: dict[str, dict[str, str]] = {
    "PURCHASE_INVOICE": {"debit": "156", "credit": "331", "label": "Hàng hóa / Phải trả NCC"},
    "SALES_INVOICE": {"debit": "131", "credit": "511", "label": "Phải thu KH / Doanh thu"},
    "CASH_DISBURSEMENT": {"debit": "331", "credit": "111", "label": "Trả NCC / Tiền mặt"},
    "CASH_RECEIPT": {"debit": "111", "credit": "131", "label": "Tiền mặt / Phải thu"},
    "PAYROLL": {"debit": "642", "credit": "334", "label": "CP QLDN / Phải trả NLĐ"},
    "FIXED_ASSET": {"debit": "211", "credit": "331", "label": "TSCĐ / Phải trả NCC"},
    "TAX_DECLARATION": {"debit": "3331", "credit": "111", "label": "Thuế GTGT / Tiền mặt"},
    "BANK_TRANSACTION": {"debit": "112", "credit": "131", "label": "TGNH / Phải thu"},
    "OTHER": {"debit": "642", "credit": "111", "label": "CP khác / Tiền mặt"},
}

# Extended keyword banks per tag (Vietnamese + English)
_TAG_KEYWORDS: dict[str, list[str]] = {
    "TAX_DECLARATION": ["kê khai thuế", "tờ khai", "thuế gtgt", "thuế tndn",
                         "tax return", "thuế tncn", "quyết toán thuế"],
    "BANK_TRANSACTION": ["chuyển khoản", "ngân hàng", "bank transfer",
                          "internet banking", "ủy nhiệm chi"],
}


def _classify_with_confidence(
    vtype: str, type_hint: str, desc: str,
) -> tuple[str, float, str]:
    """Classify and return (tag, confidence, reason_vi).

    Fine-tune hook: confidence scoring allows downstream filtering of
    low-confidence classifications for human review.
    """
    vtype = vtype.lower()
    type_hint = type_hint.lower()
    desc = desc.lower()

    # Priority 1: type_hint (set by ingest) — high confidence
    if type_hint == "cash_disbursement" or vtype == "payment":
        return ("CASH_DISBURSEMENT", 0.95, "type_hint hoặc voucher_type = payment")
    if type_hint == "cash_receipt" or vtype == "receipt":
        return ("CASH_RECEIPT", 0.95, "type_hint hoặc voucher_type = receipt")

    # Priority 2: invoice classification via type — high confidence
    if vtype == "sell_invoice":
        return ("SALES_INVOICE", 0.95, "voucher_type = sell_invoice")
    if vtype == "buy_invoice":
        return ("PURCHASE_INVOICE", 0.95, "voucher_type = buy_invoice")

    # Priority 2.5: new tag keywords — medium-high confidence
    for tag, keywords in _TAG_KEYWORDS.items():
        if any(kw in desc for kw in keywords):
            return (tag, 0.80, f"keyword match trong mô tả: {tag}")

    # Priority 3: keyword-based heuristics on description — medium confidence
    if any(kw in desc for kw in ("bán hàng", "hóa đơn đầu ra", "doanh thu")):
        return ("SALES_INVOICE", 0.80, "keyword: bán hàng/đầu ra/doanh thu")
    if any(kw in desc for kw in ("mua hàng", "hóa đơn đầu vào", "nhập kho")):
        return ("PURCHASE_INVOICE", 0.80, "keyword: mua hàng/đầu vào/nhập kho")
    if any(kw in desc for kw in ("lương", "payroll", "tiền lương")):
        return ("PAYROLL", 0.80, "keyword: lương/payroll")
    if any(kw in desc for kw in ("tài sản cố định", "fixed asset", "tscđ")):
        return ("FIXED_ASSET", 0.80, "keyword: TSCĐ/fixed asset")
    if any(kw in desc for kw in ("phiếu chi", "chi tiền")):
        return ("CASH_DISBURSEMENT", 0.75, "keyword: phiếu chi/chi tiền")
    if any(kw in desc for kw in ("phiếu thu", "thu tiền")):
        return ("CASH_RECEIPT", 0.75, "keyword: phiếu thu/thu tiền")

    # Priority 4: invoice_vat type_hint — medium confidence
    if type_hint == "invoice_vat":
        return ("SALES_INVOICE", 0.60, "type_hint = invoice_vat (default)")

    return ("OTHER", 0.30, "không khớp rule nào — cần review thủ công")


def _suggest_account_entry(tag: str) -> dict[str, str]:
    """Suggest debit/credit accounts based on classification tag.

    Fine-tune hook: Returns heuristic account mapping per Thông tư 200.
    """
    return _VN_ACCOUNT_SUGGESTIONS.get(tag, _VN_ACCOUNT_SUGGESTIONS["OTHER"])


def _classify_tag(voucher: AcctVoucher) -> str:
    """Determine classification_tag from voucher fields (rule-based).

    Wrapper that returns only the tag string for backward compatibility.
    """
    tag, _conf, _reason = _classify_with_confidence(
        voucher.voucher_type or "",
        voucher.type_hint or "",
        voucher.description or "",
    )
    return tag


def _classify_single_dict(v_dict: dict[str, Any]) -> dict[str, Any]:
    """Classify from a plain dict representation (for Ray batch_map).

    Returns id, tag, confidence, reason, and suggested accounts.
    """
    tag, confidence, reason = _classify_with_confidence(
        v_dict.get("voucher_type", ""),
        v_dict.get("type_hint", ""),
        v_dict.get("description", ""),
    )
    acct = _suggest_account_entry(tag)
    return {
        "id": v_dict.get("id"),
        "classification_tag": tag,
        "confidence": confidence,
        "reason": reason,
        "suggested_debit": acct["debit"],
        "suggested_credit": acct["credit"],
    }


def flow_voucher_classify(
    session: Session,
    run_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify untagged AcctVouchers.

    Returns stats dict with classifications, confidence distribution,
    and VN account suggestions.
    """
    payload = payload or {}

    # Fetch vouchers to classify
    q = session.query(AcctVoucher).filter(AcctVoucher.classification_tag.is_(None))
    period = payload.get("period")
    if period:
        q = q.filter(AcctVoucher.date.like(f"{period}%"))
    vouchers = q.all()

    if not vouchers:
        log.info("No unclassified vouchers found")
        return {"classified": 0, "already_tagged": 0}

    classified = 0
    low_confidence = 0
    tag_distribution: dict[str, int] = {}

    # Try Ray batch if enabled
    if _USE_RAY and len(vouchers) > 5:
        try:
            from openclaw_agent.kernel.batch import parallel_map
            v_dicts = [
                {
                    "id": v.id,
                    "voucher_type": v.voucher_type,
                    "type_hint": v.type_hint,
                    "description": v.description,
                }
                for v in vouchers
            ]
            results = parallel_map(_classify_single_dict, v_dicts, use_ray=True)
            id_to_result = {r["id"]: r for r in results}
            for v in vouchers:
                r = id_to_result.get(v.id)
                if r:
                    v.classification_tag = r["classification_tag"]
                    classified += 1
                    tag_distribution[r["classification_tag"]] = (
                        tag_distribution.get(r["classification_tag"], 0) + 1
                    )
                    if r.get("confidence", 1.0) < 0.7:
                        low_confidence += 1
            session.flush()
            log.info("voucher_classify via Ray: %d classified", classified)
            return {
                "classified": classified,
                "already_tagged": 0,
                "low_confidence_count": low_confidence,
                "tag_distribution": tag_distribution,
            }
        except Exception as e:
            log.warning("Ray classify failed, falling back: %s", e)

    # Sequential classification with confidence
    for v in vouchers:
        tag, confidence, reason = _classify_with_confidence(
            v.voucher_type or "", v.type_hint or "", v.description or "",
        )
        v.classification_tag = tag
        classified += 1
        tag_distribution[tag] = tag_distribution.get(tag, 0) + 1
        if confidence < 0.7:
            low_confidence += 1

    session.flush()
    log.info("voucher_classify_done: %d classified", classified)
    return {
        "classified": classified,
        "already_tagged": 0,
        "low_confidence_count": low_confidence,
        "tag_distribution": tag_distribution,
    }
