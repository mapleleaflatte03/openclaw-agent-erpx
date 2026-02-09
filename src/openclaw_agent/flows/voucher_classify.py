"""Flow – Voucher Classification (Phân loại chứng từ/giao dịch).

Steps:
  1. Fetch unclassified AcctVouchers from mirror DB
  2. Apply rule-based classification logic
  3. Update classification_tag on each voucher

Supports Ray batch via kernel.batch.batch_classify_vouchers when USE_RAY=1.

Classification tags:
  - PURCHASE_INVOICE   – Hóa đơn đầu vào
  - SALES_INVOICE      – Hóa đơn đầu ra
  - CASH_DISBURSEMENT  – Phiếu chi
  - CASH_RECEIPT       – Phiếu thu
  - PAYROLL            – Lương
  - FIXED_ASSET        – Tài sản cố định
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


def _classify_tag(voucher: AcctVoucher) -> str:
    """Determine classification_tag from voucher fields (rule-based).

    This is deterministic and does not require an LLM.
    Future: integrate langchain or LLM-based classifier.
    """
    vtype = (voucher.voucher_type or "").lower()
    type_hint = (voucher.type_hint or "").lower()
    desc = (voucher.description or "").lower()

    # Priority 1: type_hint (set by ingest)
    if type_hint == "cash_disbursement" or vtype == "payment":
        return "CASH_DISBURSEMENT"
    if type_hint == "cash_receipt" or vtype == "receipt":
        return "CASH_RECEIPT"

    # Priority 2: invoice classification via description / type
    if vtype == "sell_invoice":
        return "SALES_INVOICE"
    if vtype == "buy_invoice":
        return "PURCHASE_INVOICE"

    # Priority 3: keyword-based heuristics on description
    if any(kw in desc for kw in ("bán hàng", "hóa đơn đầu ra", "doanh thu")):
        return "SALES_INVOICE"
    if any(kw in desc for kw in ("mua hàng", "hóa đơn đầu vào", "nhập kho")):
        return "PURCHASE_INVOICE"
    if any(kw in desc for kw in ("lương", "payroll", "tiền lương")):
        return "PAYROLL"
    if any(kw in desc for kw in ("tài sản cố định", "fixed asset", "tscđ")):
        return "FIXED_ASSET"
    if any(kw in desc for kw in ("phiếu chi", "chi tiền")):
        return "CASH_DISBURSEMENT"
    if any(kw in desc for kw in ("phiếu thu", "thu tiền")):
        return "CASH_RECEIPT"

    # Priority 4: invoice_vat type_hint with seller/buyer tax code
    if type_hint == "invoice_vat":
        # Default: treat as sales invoice
        return "SALES_INVOICE"

    return "OTHER"


def _classify_single_dict(v_dict: dict[str, Any]) -> dict[str, Any]:
    """Classify from a plain dict representation (for Ray batch_map)."""
    vtype = (v_dict.get("voucher_type") or "").lower()
    type_hint = (v_dict.get("type_hint") or "").lower()
    desc = (v_dict.get("description") or "").lower()

    if type_hint == "cash_disbursement" or vtype == "payment":
        tag = "CASH_DISBURSEMENT"
    elif type_hint == "cash_receipt" or vtype == "receipt":
        tag = "CASH_RECEIPT"
    elif vtype == "sell_invoice":
        tag = "SALES_INVOICE"
    elif vtype == "buy_invoice":
        tag = "PURCHASE_INVOICE"
    elif any(kw in desc for kw in ("bán hàng", "hóa đơn đầu ra", "doanh thu")):
        tag = "SALES_INVOICE"
    elif any(kw in desc for kw in ("mua hàng", "hóa đơn đầu vào", "nhập kho")):
        tag = "PURCHASE_INVOICE"
    elif any(kw in desc for kw in ("lương", "payroll", "tiền lương")):
        tag = "PAYROLL"
    elif any(kw in desc for kw in ("tài sản cố định", "fixed asset", "tscđ")):
        tag = "FIXED_ASSET"
    elif any(kw in desc for kw in ("phiếu chi", "chi tiền")):
        tag = "CASH_DISBURSEMENT"
    elif any(kw in desc for kw in ("phiếu thu", "thu tiền")):
        tag = "CASH_RECEIPT"
    elif type_hint == "invoice_vat":
        tag = "SALES_INVOICE"
    else:
        tag = "OTHER"

    return {"id": v_dict.get("id"), "classification_tag": tag}


def flow_voucher_classify(
    session: Session,
    run_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify untagged AcctVouchers.

    Returns stats dict with count of classified vouchers.
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
            id_to_tag = {r["id"]: r["classification_tag"] for r in results}
            for v in vouchers:
                tag = id_to_tag.get(v.id)
                if tag:
                    v.classification_tag = tag
                    classified += 1
            session.flush()
            log.info("voucher_classify via Ray: %d classified", classified)
            return {"classified": classified, "already_tagged": 0}
        except Exception as e:
            log.warning("Ray classify failed, falling back: %s", e)

    # Sequential classification
    for v in vouchers:
        v.classification_tag = _classify_tag(v)
        classified += 1

    session.flush()
    log.info("voucher_classify_done: %d classified", classified)
    return {"classified": classified, "already_tagged": 0}
