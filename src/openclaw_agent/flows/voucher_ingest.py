"""Flow – Voucher Ingest (Đọc & lập hóa đơn/chứng từ tự động).

Steps:
  1. Load documents from source (mock VN fixtures, ERP API, or payload)
  2. OCR placeholder pipeline: PDF/image → text → parse structured fields
  3. Parse/normalize each document into AcctVoucher mirror rows
  4. Store raw_payload for future OCR engine integration

Fine-tune hooks:
  - ``_ocr_extract(path)`` → text extraction with confidence scoring
  - ``_normalize_vn_diacritics(text)`` → fix common Vietnamese diacritic OCR errors
  - ``_validate_nd123(fields)`` → validate against Nghị định 123 e-invoice format

Currently uses mock parser; designed to be plug-compatible with a real
OCR engine (Tesseract, Google Vision, etc.) in the future.

VN OCR training data (surveyed 2026-02):
  - MC_OCR 2021: kaggle.com/datasets/domixi1989/vietnamese-receipts-mc-ocr-2021
    2.3 GB, 61k receipt images with annotations
  - Receipt OCR VN: kaggle.com/datasets/blyatfk/receipt-ocr
    76 MB, 6k line-level text annotations
  - Appen VN docs: kaggle.com/datasets/appenlimited/ocr-image-data-of-vietnamese-language-documents
    17 MB, 2 080 images across 11 doc categories (CC BY-SA 4.0)

READ-ONLY principle: all writes go to local Acct* mirror tables only.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from sqlalchemy.orm import Session

from openclaw_agent.common.models import AcctVoucher
from openclaw_agent.common.utils import new_uuid

log = logging.getLogger("openclaw.flows.voucher_ingest")

# Fine-tune flag for enabling OCR pipeline instead of mock
_USE_OCR = os.getenv("OPENCLAW_USE_OCR", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# OCR Placeholder Pipeline (Phase 5 fine-tune hooks)
# ---------------------------------------------------------------------------

def _ocr_extract(file_path: str) -> dict[str, Any]:
    """OCR placeholder: extract text from PDF/image file.

    Returns dict with 'text', 'confidence', and 'engine' fields.

    Fine-tune hook: Replace with Tesseract, Google Vision, or similar OCR.
    When fine-tuning for VN invoices (Nghị định 123), this function should:
      - Handle Vietnamese diacritics (ă, â, ê, ô, ơ, ư, đ)
      - Extract structured e-invoice fields (mã hóa đơn, MST, số tiền)
      - Score confidence vs Thông tư 133 account chart
    """
    log.info("ocr_extract_placeholder", extra={"path": file_path})
    return {
        "text": f"[OCR placeholder — file: {os.path.basename(file_path)}]",
        "confidence": 0.0,
        "engine": "placeholder",
        "needs_fine_tune": True,
    }


def _normalize_vn_diacritics(text: str) -> str:
    """Fix common Vietnamese diacritics OCR errors.

    Fine-tune hook: Expand with real error patterns from production OCR.
    Common issues: ắ→ă, ầ→â, ố→ô, ờ→ơ, ừ→ư, etc.
    """
    corrections: dict[str, str] = {
        "Cong ty": "Công ty",
        "hoa don": "hóa đơn",
        "chung tu": "chứng từ",
        "tien mat": "tiền mặt",
        "ngan hang": "ngân hàng",
        "thue GTGT": "thuế GTGT",
        "mua hang": "mua hàng",
        "ban hang": "bán hàng",
    }
    result = text
    for wrong, correct in corrections.items():
        result = result.replace(wrong, correct)
    return result


def _validate_nd123(fields: dict[str, Any]) -> dict[str, Any]:
    """Validate extracted fields against Nghị định 123 e-invoice format.

    Fine-tune hook: Validate MST format (10 or 13 digits), invoice number
    format, required fields per Thông tư 78/2021/TT-BTC.

    Returns dict with 'valid', 'errors', and 'warnings'.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # MST validation (10 or 13 digits)
    tax_code = fields.get("partner_tax_code", "")
    if tax_code and not re.match(r"^\d{10}(\d{3})?$", tax_code):
        errors.append(f"MST không hợp lệ: '{tax_code}' (phải 10 hoặc 13 chữ số)")

    # Invoice number required
    if not fields.get("voucher_no"):
        errors.append("Thiếu số hóa đơn/chứng từ")

    # Amount must be positive
    amount = fields.get("amount", 0)
    if amount and float(amount) <= 0:
        errors.append(f"Số tiền không hợp lệ: {amount}")

    # Currency check
    currency = fields.get("currency", "VND")
    if currency not in ("VND", "USD", "EUR", "JPY", "CNY"):
        warnings.append(f"Đơn vị tiền tệ ít gặp: '{currency}'")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


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
    - ``ocr``: OCR placeholder pipeline (Phase 5 fine-tune)
    - ``erpx_mock`` / ``erpx``: reserved for future ERP integration
    """
    if source == "payload" and "documents" in payload:
        return [_normalize_vn_fixture(d) for d in payload["documents"]]

    if source in ("vn_fixtures", "local_seed", ""):
        return [_normalize_vn_fixture(d) for d in VN_FIXTURES]

    # OCR pipeline placeholder (fine-tune hook)
    if source == "ocr":
        file_paths = payload.get("files", [])
        results = []
        for fpath in file_paths:
            ocr_result = _ocr_extract(fpath)
            text = _normalize_vn_diacritics(ocr_result["text"])
            # Build minimal doc from OCR text (placeholder parser)
            doc: dict[str, Any] = {
                "doc_no": os.path.basename(fpath).split(".")[0],
                "issue_date": "",
                "description": text[:200],
                "amount": 0,
                "currency": "VND",
                "doc_type": "other",
                "_ocr_confidence": ocr_result["confidence"],
                "_ocr_engine": ocr_result["engine"],
                "_ocr_needs_fine_tune": ocr_result["needs_fine_tune"],
            }
            # Validate NĐ123 format
            validation = _validate_nd123(doc)
            doc["_nd123_valid"] = validation["valid"]
            doc["_nd123_errors"] = validation["errors"]
            results.append(_normalize_vn_fixture(doc))
        if not results:
            log.warning("ocr source but no files provided, fallback to vn_fixtures")
            return [_normalize_vn_fixture(d) for d in VN_FIXTURES]
        return results

    # Future: fetch from ERP mock
    log.warning("Unknown source '%s', falling back to vn_fixtures", source)
    return [_normalize_vn_fixture(d) for d in VN_FIXTURES]


def flow_voucher_ingest(
    session: Session,
    run_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute the voucher ingest flow.

    Returns stats dict with count of newly created vouchers,
    plus OCR/fine-tune metadata when applicable.
    """
    payload = payload or {}
    source = payload.get("source", "vn_fixtures")
    docs = _load_documents(source, payload)

    created = 0
    skipped = 0
    ocr_low_confidence = 0
    nd123_invalid = 0

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

        # Merge OCR/fine-tune metadata into raw_payload if present
        raw_payload = doc.get("raw_payload", {})
        if isinstance(raw_payload, dict):
            for k in ("_ocr_confidence", "_ocr_engine", "_ocr_needs_fine_tune",
                       "_nd123_valid", "_nd123_errors"):
                if k in doc:
                    raw_payload[k] = doc[k]

        # Track OCR quality metrics
        if doc.get("_ocr_confidence", 1.0) < 0.7:
            ocr_low_confidence += 1
        if doc.get("_nd123_valid") is False:
            nd123_invalid += 1

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
            raw_payload=raw_payload,
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

    result: dict[str, Any] = {
        "count_new_vouchers": created,
        "skipped_existing": skipped,
        "total_documents": len(docs),
    }

    # Add fine-tune quality metrics when OCR source used
    if source == "ocr":
        result["ocr_metrics"] = {
            "low_confidence_count": ocr_low_confidence,
            "nd123_invalid_count": nd123_invalid,
            "engine": "placeholder",
            "fine_tune_needed": ocr_low_confidence > 0,
        }

    return result
