"""Tax Reconciliation Module — Spec §4.1 Agent_Recon_Tax.

Reconciles e-invoice data (thuế điện tử) against internal accounting records.
Supports Vietnamese e-invoice XML format per Nghị định 123/2020/NĐ-CP.

Features:
  - Parse Vietnamese e-invoice XML (Hóa đơn điện tử)
  - Match invoices against vouchers/journals by tax code + amount + date
  - Detect discrepancies (amount mismatch, missing counterpart, tax rate diff)
  - Flag basic fraud patterns: phantom invoices, tax evasion indicators
  - Generate remediation suggestions
"""
from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

log = logging.getLogger("openclaw.recon.tax")


@dataclass
class TaxReconMatch:
    """A single reconciliation match/mismatch between e-invoice and internal record."""

    einvoice_id: str
    internal_id: str | None = None
    status: str = "unmatched"  # matched, partial, unmatched, discrepancy
    amount_einvoice: float = 0.0
    amount_internal: float = 0.0
    amount_diff: float = 0.0
    tax_code: str = ""
    date_einvoice: str = ""
    date_internal: str = ""
    discrepancy_type: str | None = None  # amount_mismatch, date_mismatch, missing_internal, tax_rate_diff
    suggestion: str = ""


@dataclass
class TaxReconResult:
    """Aggregate result from tax reconciliation run."""

    total_einvoices: int = 0
    total_matched: int = 0
    total_unmatched: int = 0
    total_discrepancies: int = 0
    matches: list[TaxReconMatch] = field(default_factory=list)
    fraud_flags: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Vietnamese E-Invoice XML Parser
# ---------------------------------------------------------------------------

_EINVOICE_FIELD_RES = {
    "invoice_no": re.compile(
        r"<(?:So|InvNo|SHDon|TTChung/SHDon)>([^<]+)</", re.IGNORECASE
    ),
    "tax_code_seller": re.compile(
        r"<(?:MST|TINSeller|NBan/MST)>(\d{10,13})</", re.IGNORECASE
    ),
    "tax_code_buyer": re.compile(
        r"<(?:MSTNMua|TINBuyer|NMua/MST)>(\d{10,13})</", re.IGNORECASE
    ),
    "total_amount": re.compile(
        r"<(?:TToan|TgTTTBSo|TongTien|ThTien)>([0-9.,]+)</", re.IGNORECASE
    ),
    "vat_amount": re.compile(
        r"<(?:TTienThue|TSThue|TgTThue)>([0-9.,]+)</", re.IGNORECASE
    ),
    "vat_rate": re.compile(
        r"<(?:TSuat|TLThue|TSThue)>(\d+)</", re.IGNORECASE
    ),
    "issue_date": re.compile(
        r"<(?:NLap|NgayLap|NKy|TDLap)>(\d{4}-\d{2}-\d{2})</", re.IGNORECASE
    ),
    "seller_name": re.compile(
        r"<(?:TenNBan|NBan/Ten)>([^<]+)</", re.IGNORECASE
    ),
}


def parse_einvoice_xml(xml_text: str) -> dict[str, Any]:
    """Parse a Vietnamese e-invoice XML string into structured fields.

    Handles formats per Nghị định 123/2020/NĐ-CP and Thông tư 78/2021/TT-BTC.
    """
    fields: dict[str, Any] = {}

    for field_name, pattern in _EINVOICE_FIELD_RES.items():
        match = pattern.search(xml_text)
        if match:
            value = match.group(1).strip()
            if field_name in ("total_amount", "vat_amount"):
                with contextlib.suppress(ValueError):
                    value = float(value.replace(",", "").replace(".", ""))
            elif field_name == "vat_rate":
                with contextlib.suppress(ValueError):
                    value = int(value)
            fields[field_name] = value

    return fields


# ---------------------------------------------------------------------------
# Reconciliation Engine
# ---------------------------------------------------------------------------

_AMOUNT_TOLERANCE = 0.01  # 1% tolerance for amount matching
_DATE_TOLERANCE_DAYS = 3  # ±3 days for date matching


def reconcile_tax(
    einvoices: list[dict[str, Any]],
    internal_records: list[dict[str, Any]],
    amount_tolerance: float = _AMOUNT_TOLERANCE,
    date_tolerance_days: int = _DATE_TOLERANCE_DAYS,
) -> TaxReconResult:
    """Reconcile e-invoices against internal accounting records.

    Matching criteria:
      1. Tax code (exact match)
      2. Amount (within tolerance %)
      3. Date (within ±days tolerance)

    Args:
        einvoices: List of e-invoice records with keys:
            invoice_no, tax_code_seller, total_amount, issue_date
        internal_records: List of internal voucher/journal records with keys:
            voucher_id, tax_id, amount, date
    """
    result = TaxReconResult(total_einvoices=len(einvoices))

    # Index internal records by tax code for fast lookup
    internal_by_tax: dict[str, list[dict]] = {}
    for rec in internal_records:
        tax_id = str(rec.get("tax_id", "") or rec.get("tax_code_seller", ""))
        if tax_id:
            internal_by_tax.setdefault(tax_id, []).append(rec)

    used_internal: set[str] = set()

    for einv in einvoices:
        einv_id = str(einv.get("invoice_no", ""))
        einv_tax = str(einv.get("tax_code_seller", "") or einv.get("tax_id", ""))
        einv_amount = float(einv.get("total_amount", 0) or einv.get("amount", 0))
        einv_date_str = str(einv.get("issue_date", "") or einv.get("date", ""))

        match = TaxReconMatch(
            einvoice_id=einv_id,
            amount_einvoice=einv_amount,
            tax_code=einv_tax,
            date_einvoice=einv_date_str,
        )

        # Find candidates by tax code
        candidates = internal_by_tax.get(einv_tax, [])
        best_match = None
        best_score = 0.0

        for rec in candidates:
            rec_id = str(rec.get("voucher_id", "") or rec.get("id", ""))
            if rec_id in used_internal:
                continue

            rec_amount = float(rec.get("amount", 0))
            rec_date_str = str(rec.get("date", ""))

            # Score: amount match
            if einv_amount > 0 and rec_amount > 0:
                amt_diff_pct = abs(einv_amount - rec_amount) / max(einv_amount, rec_amount)
            else:
                amt_diff_pct = 1.0

            # Score: date match
            date_match = False
            try:
                einv_date = datetime.strptime(einv_date_str[:10], "%Y-%m-%d")
                rec_date = datetime.strptime(rec_date_str[:10], "%Y-%m-%d")
                date_diff = abs((einv_date - rec_date).days)
                date_match = date_diff <= date_tolerance_days
            except (ValueError, TypeError):
                date_diff = 999

            score = 0.0
            if amt_diff_pct <= amount_tolerance:
                score += 0.6
            if date_match:
                score += 0.4

            if score > best_score:
                best_score = score
                best_match = {
                    "rec": rec,
                    "rec_id": rec_id,
                    "rec_amount": rec_amount,
                    "rec_date": rec_date_str,
                    "amt_diff_pct": amt_diff_pct,
                    "date_match": date_match,
                }

        if best_match and best_score >= 0.6:
            used_internal.add(best_match["rec_id"])
            match.internal_id = best_match["rec_id"]
            match.amount_internal = best_match["rec_amount"]
            match.date_internal = best_match["rec_date"]
            match.amount_diff = einv_amount - best_match["rec_amount"]

            if best_score >= 1.0:
                match.status = "matched"
                result.total_matched += 1
            else:
                match.status = "partial"
                result.total_discrepancies += 1
                if best_match["amt_diff_pct"] > amount_tolerance:
                    match.discrepancy_type = "amount_mismatch"
                    match.suggestion = (
                        f"Amount difference: {match.amount_diff:,.0f} VND. "
                        "Verify invoice/payment terms."
                    )
                elif not best_match["date_match"]:
                    match.discrepancy_type = "date_mismatch"
                    match.suggestion = "Date difference exceeds tolerance. Check accrual timing."
        else:
            match.status = "unmatched"
            match.discrepancy_type = "missing_internal"
            match.suggestion = (
                f"No matching internal record for e-invoice {einv_id}. "
                "Verify if invoice was recorded or is a phantom invoice."
            )
            result.total_unmatched += 1

        result.matches.append(match)

    # Fraud detection: phantom invoices (no internal match)
    if result.total_unmatched > 0:
        phantom_ratio = result.total_unmatched / max(result.total_einvoices, 1)
        if phantom_ratio > 0.3:
            result.fraud_flags.append({
                "flag": "high_unmatched_ratio",
                "severity": "high",
                "detail": f"{result.total_unmatched}/{result.total_einvoices} "
                          f"({phantom_ratio:.0%}) e-invoices have no internal match",
                "suggestion": "Review unmatched e-invoices for potential phantom invoices",
            })

    # Fraud detection: duplicate e-invoice numbers
    inv_nos = [str(e.get("invoice_no", "")) for e in einvoices if e.get("invoice_no")]
    seen: set[str] = set()
    dupes: set[str] = set()
    for inv_no in inv_nos:
        if inv_no in seen:
            dupes.add(inv_no)
        seen.add(inv_no)
    if dupes:
        result.fraud_flags.append({
            "flag": "duplicate_einvoice_numbers",
            "severity": "high",
            "detail": f"Duplicate e-invoice numbers: {sorted(dupes)}",
            "suggestion": "Investigate duplicate e-invoices — possible re-use or fraud",
        })

    result.summary = {
        "total": result.total_einvoices,
        "matched": result.total_matched,
        "unmatched": result.total_unmatched,
        "discrepancies": result.total_discrepancies,
        "match_rate": (
            round(result.total_matched / result.total_einvoices, 4)
            if result.total_einvoices > 0 else 0.0
        ),
        "fraud_flags": len(result.fraud_flags),
    }

    return result
