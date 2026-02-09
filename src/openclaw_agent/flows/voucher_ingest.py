"""Flow – Voucher Ingest (Đọc & lập hóa đơn/chứng từ tự động).

Steps:
  1. Load documents from source (mock VN fixtures, ERP API, or payload)
  2. Parse/normalize each document into AcctVoucher mirror rows
  3. Store raw_payload for future OCR engine integration

Currently uses mock parser; designed to be plug-compatible with a real
OCR engine (Tesseract, Google Vision, etc.) in the future.

READ-ONLY principle: all writes go to local Acct* mirror tables only.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from openclaw_agent.common.models import AcctVoucher
from openclaw_agent.common.utils import new_uuid

log = logging.getLogger("openclaw.flows.voucher_ingest")


# ---------------------------------------------------------------------------
# Vietnamese mock document fixtures (used when source == "vn_fixtures")
# ---------------------------------------------------------------------------

VN_FIXTURES: list[dict[str, Any]] = [
    {
        "invoice_no": "0000123",
        "issue_date": "2025-01-15",
        "seller_name": "CÔNG TY TNHH ABC",
        "seller_tax_code": "0312345678",
        "buyer_name": "CÔNG TY CP XYZ",
        "buyer_tax_code": "0318765432",
        "subtotal": 10_000_000,
        "vat_rate": 10,
        "vat_amount": 1_000_000,
        "total_amount": 11_000_000,
        "currency": "VND",
        "doc_type": "invoice_vat",
        "description": "Bán hàng hóa theo hợp đồng 01/2025",
    },
    {
        "doc_no": "PC0001",
        "issue_date": "2025-01-20",
        "payer": "CÔNG TY TNHH ABC",
        "payee": "Nguyễn Văn A",
        "description": "Chi tiền tiếp khách",
        "amount": 2_500_000,
        "currency": "VND",
        "doc_type": "cash_disbursement",
    },
    {
        "doc_no": "PT0001",
        "issue_date": "2025-01-22",
        "payer": "Trần Thị B",
        "payee": "CÔNG TY TNHH ABC",
        "description": "Thu tiền thanh toán hóa đơn",
        "amount": 5_000_000,
        "currency": "VND",
        "doc_type": "cash_receipt",
    },
]


def _normalize_vn_fixture(doc: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw VN fixture document into AcctVoucher-compatible dict."""
    doc_type = doc.get("doc_type", "other")

    # Determine voucher_no
    voucher_no = doc.get("invoice_no") or doc.get("doc_no") or ""

    # Determine amount
    amount = float(doc.get("total_amount") or doc.get("amount") or 0)

    # Determine partner info
    if doc_type == "invoice_vat":
        # Invoice: partner is buyer for sales, seller for purchases
        partner_name = doc.get("buyer_name") or doc.get("seller_name") or ""
        partner_tax_code = doc.get("buyer_tax_code") or doc.get("seller_tax_code") or ""
        voucher_type = "sell_invoice"
        type_hint = "invoice_vat"
    elif doc_type == "cash_disbursement":
        partner_name = doc.get("payee", "")
        partner_tax_code = ""
        voucher_type = "payment"
        type_hint = "cash_disbursement"
    elif doc_type == "cash_receipt":
        partner_name = doc.get("payer", "")
        partner_tax_code = ""
        voucher_type = "receipt"
        type_hint = "cash_receipt"
    else:
        partner_name = doc.get("partner_name", "")
        partner_tax_code = doc.get("partner_tax_code", "")
        voucher_type = "other"
        type_hint = "other"

    return {
        "voucher_no": voucher_no,
        "voucher_type": voucher_type,
        "date": doc.get("issue_date", ""),
        "amount": amount,
        "currency": doc.get("currency", "VND"),
        "partner_name": partner_name,
        "partner_tax_code": partner_tax_code,
        "description": doc.get("description"),
        "has_attachment": False,
        "type_hint": type_hint,
        "raw_payload": doc,
        "source": "mock_vn_fixture",
    }


def _load_documents(
    source: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Load documents from the given source.

    - ``vn_fixtures``: built-in Vietnamese demo documents
    - ``payload``: use ``payload["documents"]`` directly
    - ``erpx_mock`` / ``erpx``: reserved for future ERP integration
    """
    if source == "payload" and "documents" in payload:
        return [_normalize_vn_fixture(d) for d in payload["documents"]]

    if source in ("vn_fixtures", "local_seed", ""):
        return [_normalize_vn_fixture(d) for d in VN_FIXTURES]

    # Future: fetch from ERP mock
    log.warning("Unknown source '%s', falling back to vn_fixtures", source)
    return [_normalize_vn_fixture(d) for d in VN_FIXTURES]


def flow_voucher_ingest(
    session: Session,
    run_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute the voucher ingest flow.

    Returns stats dict with count of newly created vouchers.
    """
    payload = payload or {}
    source = payload.get("source", "vn_fixtures")
    docs = _load_documents(source, payload)

    created = 0
    skipped = 0

    for doc in docs:
        voucher_no = doc["voucher_no"]

        # Idempotency: skip if voucher_no already exists with same source
        existing = (
            session.query(AcctVoucher)
            .filter_by(voucher_no=voucher_no, source=doc.get("source"))
            .first()
        )
        if existing:
            skipped += 1
            continue

        erp_voucher_id = f"ingest-{voucher_no}-{new_uuid()[:8]}"

        voucher_row = AcctVoucher(
            id=new_uuid(),
            erp_voucher_id=erp_voucher_id,
            voucher_no=voucher_no,
            voucher_type=doc.get("voucher_type", "other"),
            date=doc.get("date", ""),
            amount=doc["amount"],
            currency=doc.get("currency", "VND"),
            partner_name=doc.get("partner_name"),
            partner_tax_code=doc.get("partner_tax_code"),
            description=doc.get("description"),
            has_attachment=doc.get("has_attachment", False),
            type_hint=doc.get("type_hint"),
            raw_payload=doc.get("raw_payload"),
            source=doc.get("source", "mock_vn_fixture"),
            run_id=run_id,
        )
        session.add(voucher_row)
        created += 1

    session.flush()
    log.info(
        "voucher_ingest_done",
        extra={"created": created, "skipped": skipped, "run_id": run_id},
    )
    return {
        "count_new_vouchers": created,
        "skipped_existing": skipped,
        "total_documents": len(docs),
    }
