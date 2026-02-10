"""Flow 1 – Journal Suggestion (Đề xuất bút toán).

Steps:
  1. Fetch vouchers from ERP mock (GET /erp/v1/vouchers)
  2. Classify each voucher → determine debit/credit accounts
  3. Create AcctVoucher mirror rows
  4. Create AcctJournalProposal + AcctJournalLine rows with confidence score
  5. All proposals start as status="pending" for human review

When ``USE_REAL_LLM=true`` the LLM client refines the rule-based result
(re-checks account mapping, adds reasoning).  Falls back silently on error.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy.orm import Session

from openclaw_agent.common.models import AcctJournalLine, AcctJournalProposal, AcctVoucher
from openclaw_agent.common.utils import new_uuid

log = logging.getLogger("openclaw.flows.journal")

def _is_real_llm_enabled() -> bool:
    """Read USE_REAL_LLM at call time (not import time)."""
    return os.getenv("USE_REAL_LLM", "").strip().lower() in ("1", "true", "yes")

# Vietnamese accounting chart (simplified subset)
_ACCOUNT_MAP: dict[str, dict[str, Any]] = {
    "sell_invoice": {
        "debit": ("131", "Phải thu khách hàng"),
        "credit": ("511", "Doanh thu bán hàng"),
        "confidence": 0.92,
    },
    "buy_invoice": {
        "debit": ("621", "Chi phí NVL trực tiếp"),
        "credit": ("331", "Phải trả người bán"),
        "confidence": 0.88,
    },
    "receipt": {
        "debit": ("111", "Tiền mặt"),
        "credit": ("131", "Phải thu khách hàng"),
        "confidence": 0.95,
    },
    "payment": {
        "debit": ("331", "Phải trả người bán"),
        "credit": ("112", "Tiền gửi ngân hàng"),
        "confidence": 0.90,
    },
    "other": {
        "debit": ("642", "Chi phí QLDN"),
        "credit": ("111", "Tiền mặt"),
        "confidence": 0.55,
    },
}


def _classify_voucher(voucher: dict[str, Any]) -> dict[str, Any]:
    """Rule-based voucher classifier.

    TODO: Replace with LLM call via LangGraph node.
    Returns: {debit_account, debit_name, credit_account, credit_name, confidence, reasoning}
    """
    vtype = voucher.get("voucher_type", "other")
    mapping = _ACCOUNT_MAP.get(vtype, _ACCOUNT_MAP["other"])
    debit_code, debit_name = mapping["debit"]
    credit_code, credit_name = mapping["credit"]
    confidence = mapping["confidence"]

    # Lower confidence when no attachment
    if not voucher.get("has_attachment"):
        confidence *= 0.8

    reasoning = (
        f"Voucher type '{vtype}' → Nợ TK {debit_code} ({debit_name}), "
        f"Có TK {credit_code} ({credit_name}). "
        f"Rule-based classification."
    )

    result = {
        "debit_account": debit_code,
        "debit_name": debit_name,
        "credit_account": credit_code,
        "credit_name": credit_name,
        "confidence": round(confidence, 3),
        "reasoning": reasoning,
        "llm_used": False,
    }

    # --- LLM refinement (optional) -----------------------------------------
    if _is_real_llm_enabled():
        try:
            from openclaw_agent.llm.client import get_llm_client
            llm = get_llm_client()
            refined = llm.refine_journal_suggestion(voucher, result)
            if refined is not None:
                # Merge LLM result (keep rule-based as fallback reference)
                result.update({
                    "debit_account": refined.get("debit_account", debit_code),
                    "debit_name": refined.get("debit_name", debit_name),
                    "credit_account": refined.get("credit_account", credit_code),
                    "credit_name": refined.get("credit_name", credit_name),
                    "confidence": round(float(refined.get("confidence", confidence)), 3),
                    "reasoning": refined.get("reasoning", reasoning),
                    "llm_used": True,
                })
        except Exception:
            log.exception("LLM refinement failed — keeping rule-based result")

    return result


def flow_journal_suggestion(
    session: Session,
    vouchers: list[dict[str, Any]],
    run_id: str,
) -> dict[str, Any]:
    """Execute the journal suggestion flow on a batch of vouchers.

    Returns stats dict with counts.
    """
    created = 0
    skipped = 0

    for v in vouchers:
        erp_id = v.get("voucher_id") or v.get("erp_voucher_id", "")

        # Check if voucher already mirrored
        existing = session.query(AcctVoucher).filter_by(erp_voucher_id=erp_id).first()
        if existing:
            skipped += 1
            continue

        # Mirror voucher
        voucher_row = AcctVoucher(
            id=new_uuid(),
            erp_voucher_id=erp_id,
            voucher_no=v.get("voucher_no", ""),
            voucher_type=v.get("voucher_type", "other"),
            date=v.get("date", ""),
            amount=float(v.get("amount", 0)),
            currency=v.get("currency", "VND"),
            partner_name=v.get("partner_name"),
            description=v.get("description"),
            has_attachment=bool(v.get("has_attachment")),
            run_id=run_id,
        )
        session.add(voucher_row)

        # Classify and create proposal
        classification = _classify_voucher(v)
        proposal = AcctJournalProposal(
            id=new_uuid(),
            voucher_id=voucher_row.id,
            description=v.get("description") or f"Bút toán cho {v.get('voucher_no', erp_id)}",
            confidence=classification["confidence"],
            reasoning=classification["reasoning"],
            status="pending",
            run_id=run_id,
        )
        session.add(proposal)

        # Debit line
        session.add(AcctJournalLine(
            id=new_uuid(),
            proposal_id=proposal.id,
            account_code=classification["debit_account"],
            account_name=classification["debit_name"],
            debit=float(v.get("amount", 0)),
            credit=0.0,
        ))
        # Credit line
        session.add(AcctJournalLine(
            id=new_uuid(),
            proposal_id=proposal.id,
            account_code=classification["credit_account"],
            account_name=classification["credit_name"],
            debit=0.0,
            credit=float(v.get("amount", 0)),
        ))

        created += 1

    session.flush()
    log.info("journal_suggestion_done", extra={"records_created": created, "skipped": skipped, "run_id": run_id})
    return {"proposals_created": created, "skipped_existing": skipped, "total_vouchers": len(vouchers)}
