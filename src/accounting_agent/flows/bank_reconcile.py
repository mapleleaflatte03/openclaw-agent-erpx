"""Flow 2 – Bank Reconciliation (Đối chiếu ngân hàng).

Steps:
  1. Fetch bank_transactions from ERP mock
  2. Fetch vouchers (already mirrored or from ERP)
  3. Match each bank tx to a voucher: ±3 days date tolerance, ±1% amount tolerance
  4. Flag anomalies: amount_mismatch, date_gap, unmatched_tx
  5. Store AcctBankTransaction mirrors + AcctAnomalyFlag rows

This is a rule-based matcher.
TODO: Replace with LangGraph node for fuzzy matching + LLM-assisted resolution.
"""
from __future__ import annotations

import logging
from datetime import date as date_type
from typing import Any

from sqlalchemy.orm import Session

from accounting_agent.common.models import AcctAnomalyFlag, AcctBankTransaction
from accounting_agent.common.utils import new_uuid

log = logging.getLogger("accounting_agent.flows.bank_reconcile")

DATE_TOLERANCE_DAYS = 3
AMOUNT_TOLERANCE_PCT = 0.01  # 1%


def _parse_date(s: str) -> date_type | None:
    try:
        return date_type.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _amount_close(a: float, b: float) -> bool:
    # Operational accounting rule: zero/negative amount rows are never
    # auto-matched as "valid reconciled" records.
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / abs(b) <= AMOUNT_TOLERANCE_PCT


def _date_close(d1: date_type, d2: date_type) -> bool:
    return abs((d1 - d2).days) <= DATE_TOLERANCE_DAYS


def flow_bank_reconcile(
    session: Session,
    bank_txs: list[dict[str, Any]],
    vouchers: list[dict[str, Any]],
    run_id: str,
) -> dict[str, Any]:
    """Match bank transactions against vouchers, flag anomalies.

    Returns stats dict.
    """
    matched = 0
    anomalies_created = 0
    unmatched = 0

    # Build voucher lookup by id
    voucher_lookup: dict[str, dict[str, Any]] = {}
    for v in vouchers:
        vid = v.get("voucher_id") or v.get("erp_voucher_id", "")
        voucher_lookup[vid] = v

    for tx in bank_txs:
        tx_ref = tx.get("tx_ref") or tx.get("bank_tx_ref", "")

        # Check if already mirrored
        existing = session.query(AcctBankTransaction).filter_by(bank_tx_ref=tx_ref).first()
        if existing:
            if existing.amount <= 0 and str(existing.match_status or "").lower() in {
                "matched",
                "matched_auto",
                "matched_manual",
            }:
                existing.match_status = "anomaly"
                existing.matched_voucher_id = None
                session.add(AcctAnomalyFlag(
                    id=new_uuid(),
                    anomaly_type="other",
                    severity="high",
                    description=(
                        f"Bank tx {tx_ref} có amount <= 0 nhưng từng được đánh dấu matched. "
                        "Hệ thống đã hạ trạng thái về anomaly để rà soát."
                    ),
                    bank_tx_id=existing.id,
                    run_id=run_id,
                ))
                anomalies_created += 1
            continue

        tx_amount = float(tx.get("amount", 0))
        tx_date = _parse_date(tx.get("date", ""))
        tx_id = new_uuid()

        # Try to find matching voucher (brute-force search — OK for mock scale)
        best_match: dict[str, Any] | None = None
        match_quality = "none"

        for v in vouchers:
            v_amount = float(v.get("amount", 0))
            if v_amount <= 0:
                continue
            v_date = _parse_date(v.get("date", ""))

            if v_date and tx_date:
                if _amount_close(tx_amount, v_amount) and _date_close(tx_date, v_date):
                    best_match = v
                    match_quality = "matched"
                    break  # exact match — stop searching
                elif _date_close(tx_date, v_date) and not _amount_close(tx_amount, v_amount):
                    # Prefer closer amount diff when multiple date-close candidates
                    if best_match is None or match_quality != "amount_mismatch":
                        best_match = v
                        match_quality = "amount_mismatch"
                    else:
                        # Pick better candidate by amount proximity
                        prev_diff = abs(tx_amount - float(best_match.get("amount", 0)))
                        curr_diff = abs(tx_amount - v_amount)
                        if curr_diff < prev_diff:
                            best_match = v
                            match_quality = "amount_mismatch"
                elif _amount_close(tx_amount, v_amount) and not _date_close(tx_date, v_date):
                    if best_match is None or match_quality == "none":
                        best_match = v
                        match_quality = "date_gap"

        # Determine match status for the bank tx row
        matched_voucher_id = None
        if best_match:
            matched_voucher_id = best_match.get("voucher_id") or best_match.get("erp_voucher_id")

        if tx_amount <= 0:
            status = "anomaly"
            match_quality = "invalid_amount"
        elif match_quality == "matched":
            status = "matched"
            matched += 1
        elif match_quality in ("date_gap", "amount_mismatch"):
            status = "anomaly"
        else:
            status = "unmatched"
            unmatched += 1

        # Store bank tx mirror
        bank_row = AcctBankTransaction(
            id=tx_id,
            bank_tx_ref=tx_ref,
            bank_account=tx.get("bank_account", "112-VCB-001"),
            date=tx.get("date", ""),
            amount=tx_amount,
            currency=tx.get("currency", "VND"),
            counterparty=tx.get("counterparty"),
            memo=tx.get("memo"),
            matched_voucher_id=matched_voucher_id,
            match_status=status,
            run_id=run_id,
        )
        session.add(bank_row)

        # Create anomaly flags
        if match_quality == "amount_mismatch" and best_match:
            v_amount = float(best_match.get("amount", 0))
            session.add(AcctAnomalyFlag(
                id=new_uuid(),
                anomaly_type="amount_mismatch",
                severity="high" if abs(tx_amount - v_amount) > 100000 else "medium",
                description=(
                    f"Bank tx {tx_ref} amount {tx_amount:,.0f} vs voucher "
                    f"{best_match.get('voucher_no', '?')} amount {v_amount:,.0f} "
                    f"(diff: {abs(tx_amount - v_amount):,.0f})"
                ),
                voucher_id=matched_voucher_id,
                bank_tx_id=tx_id,
                run_id=run_id,
            ))
            anomalies_created += 1

        elif match_quality == "date_gap" and best_match:
            v_date = _parse_date(best_match.get("date", ""))
            gap = abs((tx_date - v_date).days) if tx_date and v_date else 0
            session.add(AcctAnomalyFlag(
                id=new_uuid(),
                anomaly_type="date_gap",
                severity="medium",
                description=(
                    f"Bank tx {tx_ref} date {tx.get('date')} vs voucher "
                    f"{best_match.get('voucher_no', '?')} date {best_match.get('date')} "
                    f"(gap: {gap} days)"
                ),
                voucher_id=matched_voucher_id,
                bank_tx_id=tx_id,
                run_id=run_id,
            ))
            anomalies_created += 1

        elif match_quality == "none":
            session.add(AcctAnomalyFlag(
                id=new_uuid(),
                anomaly_type="unmatched_tx",
                severity="high",
                description=(
                    f"Bank tx {tx_ref} ({tx_amount:,.0f} {tx.get('currency', 'VND')}) "
                    f"has no matching voucher"
                ),
                bank_tx_id=tx_id,
                run_id=run_id,
            ))
            anomalies_created += 1
        elif match_quality == "invalid_amount":
            session.add(AcctAnomalyFlag(
                id=new_uuid(),
                anomaly_type="other",
                severity="high",
                description=(
                    f"Bank tx {tx_ref} có amount {tx_amount:,.0f} {tx.get('currency', 'VND')} "
                    "không hợp lệ để auto-match (<= 0)."
                ),
                bank_tx_id=tx_id,
                run_id=run_id,
            ))
            anomalies_created += 1

    session.flush()
    log.info("bank_reconcile_done", extra={
        "matched": matched, "anomalies": anomalies_created,
        "unmatched": unmatched, "run_id": run_id,
    })
    return {
        "matched": matched,
        "anomalies_created": anomalies_created,
        "unmatched": unmatched,
        "total_bank_txs": len(bank_txs),
    }
