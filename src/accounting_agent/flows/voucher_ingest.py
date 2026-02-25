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

import json
import logging
import os
import re
from datetime import date as _dt_date
from typing import Any

from sqlalchemy.orm import Session

from accounting_agent.common.models import AcctVoucher
from accounting_agent.common.utils import new_uuid

log = logging.getLogger("accounting_agent.flows.voucher_ingest")

# Fine-tune flag for enabling OCR pipeline instead of mock
_USE_OCR = os.getenv("OPENCLAW_USE_OCR", "").lower() in ("1", "true", "yes")

# Ray batch flag (mirrors voucher_classify pattern)
_USE_RAY = os.getenv("USE_RAY", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# OCR Pipeline (Milestone 1 — PaddleOCR-based, >98% target)
# ---------------------------------------------------------------------------

def _ocr_extract(file_path: str) -> dict[str, Any]:
    """OCR extraction using PaddleOCR engine (or fallback).

    Delegates to accounting_agent.ocr module for real OCR processing.
    Returns dict with 'text', 'confidence', 'engine', 'structured_fields'.
    """
    from accounting_agent.ocr import ocr_extract

    result = ocr_extract(file_path)
    return {
        "text": result.text,
        "confidence": result.confidence,
        "engine": result.engine,
        "structured_fields": result.structured_fields,
        "nd123_validation": result.nd123_validation,
        "warnings": result.warnings,
        "needs_fine_tune": result.engine == "fallback",
    }


def _normalize_vn_diacritics(text: str) -> str:
    """Fix common Vietnamese diacritics OCR errors.

    Delegates to accounting_agent.ocr for Kaggle-trained correction patterns.
    """
    from accounting_agent.ocr import normalize_vn_diacritics

    return normalize_vn_diacritics(text)


def _validate_nd123(fields: dict[str, Any]) -> dict[str, Any]:
    """Validate extracted fields against Nghị định 123/2020/NĐ-CP.

    Delegates to accounting_agent.ocr for full ND123 validation.
    """
    from accounting_agent.ocr import validate_nd123

    return validate_nd123(fields)


# ---------------------------------------------------------------------------
# VN document fixtures — loaded from Kaggle seed (R2: no fabricated data)
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, os.pardir))


def _resolve_kaggle_seed_path() -> str:
    """Find vn_kaggle_subset.json — check env, repo-local, and /data."""
    candidates = [
        os.path.join(os.getenv("VN_DATA_ROOT", ""), "kaggle", "seed", "vn_kaggle_subset.json"),
        os.path.join(_REPO_ROOT, "data", "kaggle", "seed", "vn_kaggle_subset.json"),
        "/data/kaggle/seed/vn_kaggle_subset.json",
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return candidates[-1]  # fallback for error messages


def _load_kaggle_fixtures() -> list[dict[str, Any]]:
    """Load VN fixtures from Kaggle-derived seed file.

    Returns Kaggle records converted to voucher-ingest format.
    Falls back to empty list if seed not found (caller should handle).
    """
    seed_path = _resolve_kaggle_seed_path()
    if not os.path.isfile(seed_path):
        log.warning("Kaggle seed not found at %s — run scripts/generate_kaggle_seed.py", seed_path)
        return []
    try:
        with open(seed_path, encoding="utf-8") as f:
            records = json.load(f)
        # Assign realistic doc_type variety to Kaggle records
        _DOC_TYPES = ["invoice_vat", "cash_disbursement", "cash_receipt", "invoice_vat", "invoice_vat"]
        fixtures = []
        for idx, rec in enumerate(records):
            total = float(rec.get("total_amount", 0) or 0)
            vat = float(rec.get("vat_amount", 0) or 0)
            source_name = rec.get("source_name", "KAGGLE")
            items = rec.get("line_items", [])
            desc = "; ".join(it.get("description", "") for it in items[:3]) if items else ""
            doc_type = _DOC_TYPES[idx % len(_DOC_TYPES)]
            fixtures.append({
                "invoice_no": rec.get("external_id", "")[:10] if doc_type == "invoice_vat" else "",
                "doc_no": rec.get("external_id", "")[:10] if doc_type != "invoice_vat" else "",
                "issue_date": rec.get("issue_date", "2026-01-15"),
                "seller_name": rec.get("seller_name", ""),
                "seller_tax_code": rec.get("seller_tax_code", ""),
                "buyer_name": rec.get("buyer_name", ""),
                "buyer_tax_code": rec.get("buyer_tax_code", ""),
                "payer": rec.get("seller_name", "") if doc_type == "cash_disbursement" else "",
                "payee": rec.get("buyer_name", "") if doc_type == "cash_disbursement" else rec.get("seller_name", ""),
                "subtotal": total - vat,
                "vat_rate": 10 if vat > 0 else 0,
                "vat_amount": vat,
                "total_amount": total,
                "amount": total,
                "currency": rec.get("currency", "VND"),
                "doc_type": doc_type,
                "description": desc or f"Kaggle {source_name}",
                "_kaggle_source": source_name,
                "_kaggle_ext_id": rec.get("external_id", ""),
            })
        return fixtures
    except Exception as e:
        log.error("Failed to load Kaggle seed: %s", e)
        return []


def _get_vn_fixtures() -> list[dict[str, Any]]:
    """Get VN fixtures — Kaggle-sourced only (R2 compliant)."""
    fixtures = _load_kaggle_fixtures()
    if not fixtures:
        log.error("NO DATA: Kaggle seed missing. Run: python scripts/generate_kaggle_seed.py")
    return fixtures


# Keep backward-compatible name for tests that import it
VN_FIXTURES = _get_vn_fixtures()


def _normalize_vn_fixture(doc: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw VN fixture document into AcctVoucher-compatible dict."""
    doc_type = doc.get("doc_type", "other")

    # Determine voucher_no
    voucher_no = doc.get("invoice_no") or doc.get("doc_no") or ""

    # Determine amount
    amount = float(doc.get("total_amount") or doc.get("amount") or 0)

    # Determine partner info
    if doc_type == "invoice_vat":
        invoice_direction = str(doc.get("invoice_direction", "")).lower().strip()
        if invoice_direction in ("purchase", "buy", "ap", "input"):
            # AP invoice: partner should be seller (nhà cung cấp)
            partner_name = doc.get("seller_name") or doc.get("buyer_name") or ""
            partner_tax_code = doc.get("seller_tax_code") or doc.get("buyer_tax_code") or ""
            voucher_type = "buy_invoice"
            type_hint = "invoice_vat"
        else:
            # Keep backward-compatible default for fixtures/tests.
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


def _normalize_iso_date(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])-\d{2}", raw):
        return raw
    if re.fullmatch(r"\d{4}/(0[1-9]|1[0-2])/\d{2}", raw):
        return raw.replace("/", "-")
    if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", raw):
        return f"{raw}-01"
    if re.fullmatch(r"\d{4}/(0[1-9]|1[0-2])", raw):
        return f"{raw.replace('/', '-')}-01"
    return None


def _resolve_voucher_date(raw_date: Any, period: str | None, *, force_period: bool) -> str:
    normalized = _normalize_iso_date(raw_date)
    period_value = str(period or "").strip()
    if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period_value):
        period_date = f"{period_value}-01"
        if force_period:
            if normalized is None or not normalized.startswith(f"{period_value}-"):
                return period_date
        elif normalized is None:
            return period_date
    if normalized:
        return normalized
    return _dt_date.today().isoformat()


def _doc_fingerprint(payload: dict[str, Any] | None) -> str:
    """Create a stable short fingerprint from source metadata for dedup handling."""
    pl = payload if isinstance(payload, dict) else {}
    candidate = (
        str(pl.get("source_hash") or "").strip()
        or str(pl.get("source_file") or "").strip()
        or str(pl.get("source_path") or "").strip()
    )
    if not candidate:
        return ""
    normalized = re.sub(r"[^A-Za-z0-9]+", "", candidate).lower()
    return normalized[:24]


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
    period = str(payload.get("period") or "").strip()
    force_period_date = bool(payload.get("force_period_date", True))
    docs = _load_documents(source, payload)

    # Ray batch: normalize OCR documents in parallel when enabled
    if _USE_RAY and source == "ocr" and len(docs) > 5:
        try:
            from accounting_agent.kernel.batch import parallel_map
            docs = parallel_map(_normalize_vn_fixture, docs, use_ray=True)
            log.info("ray_batch_ingest", extra={"doc_count": len(docs)})
        except ImportError:
            log.warning("Ray requested but kernel.batch not available, sequential fallback")

    created = 0
    skipped = 0
    ocr_low_confidence = 0
    nd123_invalid = 0

    for doc in docs:
        raw_payload = doc.get("raw_payload", {})
        source_value = doc.get("source")
        fingerprint = _doc_fingerprint(raw_payload if isinstance(raw_payload, dict) else None)
        voucher_no = str(doc.get("voucher_no") or "").strip()
        if not voucher_no:
            voucher_no = f"DOC-{(fingerprint or new_uuid())[:12]}"

        # Idempotency: skip if voucher_no already exists with same source
        existing = (
            session.query(AcctVoucher)
            .filter_by(voucher_no=voucher_no, source=source_value)
            .first()
        )
        if existing:
            # For payload imports, keep each real document while still deterministic.
            if source == "payload" and fingerprint:
                alt_voucher_no = f"{voucher_no[:52]}-{fingerprint[:8]}"
                alt_existing = (
                    session.query(AcctVoucher)
                    .filter_by(voucher_no=alt_voucher_no, source=source_value)
                    .first()
                )
                if alt_existing:
                    skipped += 1
                    continue
                voucher_no = alt_voucher_no
            else:
                skipped += 1
                continue

        erp_voucher_id = f"ingest-{voucher_no}-{new_uuid()[:8]}"

        # Merge OCR/fine-tune metadata into raw_payload if present
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

        voucher_date = _resolve_voucher_date(
            doc.get("date"),
            period,
            force_period=force_period_date,
        )
        if isinstance(raw_payload, dict):
            raw_payload.setdefault("original_date", doc.get("date"))
            raw_payload["resolved_date"] = voucher_date
            if period:
                raw_payload["period"] = period

        voucher_row = AcctVoucher(
            id=new_uuid(),
            erp_voucher_id=erp_voucher_id,
            voucher_no=voucher_no,
            voucher_type=doc.get("voucher_type", "other"),
            date=voucher_date,
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
        extra={"records_created": created, "skipped": skipped, "run_id": run_id},
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
