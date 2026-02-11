"""Accounting soft-checks flow: rule-based validation of ERP data.

Produces AcctSoftCheckResult + AcctValidationIssue rows per period.
Does NOT modify any ERP data (READ-ONLY principle).

When ``USE_REAL_LLM=true`` the LLM client generates user-friendly
explanations for each flagged issue.  Falls back silently on error.
"""
from __future__ import annotations

import logging
import os
from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from openclaw_agent.common.models import (
    AcctSoftCheckResult,
    AcctValidationIssue,
)
from openclaw_agent.common.utils import new_uuid

log = logging.getLogger("openclaw.flows.soft_checks")

def _is_real_llm_enabled() -> bool:
    """Read USE_REAL_LLM at call time (not import time)."""
    return os.getenv("USE_REAL_LLM", "").strip().lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------

_RULES: list[dict[str, Any]] = [
    {
        "code": "MISSING_ATTACHMENT",
        "severity": "warning",
        "data_key": "vouchers",
        "check": lambda v: not v.get("has_attachment", True),
        "msg": lambda v: f"Chứng từ {v.get('voucher_no', '?')} thiếu file đính kèm",
    },
    {
        "code": "JOURNAL_IMBALANCED",
        "severity": "error",
        "data_key": "journals",
        "check": lambda j: float(j.get("debit_total", 0) or 0) != float(j.get("credit_total", 0) or 0),
        "msg": lambda j: (
            f"Bút toán {j.get('journal_id', '?')} mất cân đối: "
            f"Nợ={j.get('debit_total', 0)} ≠ Có={j.get('credit_total', 0)}"
        ),
    },
    {
        "code": "OVERDUE_INVOICE",
        "severity": "info",
        "data_key": "invoices",
        "check": lambda inv: (
            inv.get("status") == "unpaid"
            and inv.get("due_date")
            and _safe_date(inv["due_date"]) is not None
            and _safe_date(inv["due_date"]) < date.today()  # type: ignore[operator]
        ),
        "msg": lambda inv: (
            f"Hóa đơn {inv.get('invoice_no', inv.get('invoice_id', '?'))} quá hạn "
            f"({(date.today() - _safe_date(inv['due_date'])).days} ngày)"  # type: ignore[operator]
        ),
    },
    {
        "code": "DUPLICATE_VOUCHER",
        "severity": "error",
        "data_key": "vouchers",
        "check": None,  # handled separately (multi-row check)
        "msg": None,
    },
    {
        "code": "LARGE_AMOUNT_NO_APPROVAL",
        "severity": "warning",
        "data_key": "vouchers",
        "check": lambda v: float(v.get("amount", 0) or 0) >= 500_000_000 and not v.get("approved_by"),
        "msg": lambda v: (
            f"Chứng từ {v.get('voucher_no', '?')} số tiền lớn "
            f"({v.get('amount', 0):,.0f}) chưa có phê duyệt"
        ),
    },
]


def _safe_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def flow_soft_checks_acct(
    session: Session,
    vouchers: list[dict[str, Any]],
    journals: list[dict[str, Any]],
    invoices: list[dict[str, Any]],
    period: str,
    run_id: str,
) -> dict[str, Any]:
    """Run all accounting soft-check rules and persist results.

    Returns stats dict: {total_checks, passed, warnings, errors, score}.
    """
    data_map: dict[str, list[dict[str, Any]]] = {
        "vouchers": vouchers,
        "journals": journals,
        "invoices": invoices,
    }

    issues: list[AcctValidationIssue] = []
    counts: Counter[str] = Counter()

    # --- Single-row rules ---
    for rule in _RULES:
        if rule["check"] is None:
            continue  # multi-row rule
        key = rule["data_key"]
        for item in data_map.get(key, []):
            counts["total"] += 1
            try:
                if rule["check"](item):
                    ref = (
                        item.get("voucher_id")
                        or item.get("journal_id")
                        or item.get("invoice_id")
                        or ""
                    )
                    issues.append(
                        AcctValidationIssue(
                            id=new_uuid(),
                            check_result_id="",  # set below
                            rule_code=rule["code"],
                            severity=rule["severity"],
                            message=rule["msg"](item),
                            erp_ref=str(ref),
                            details={"raw": item},
                        )
                    )
                    counts[rule["severity"]] += 1
                else:
                    counts["passed"] += 1
            except Exception:
                counts["passed"] += 1

    # --- Multi-row: DUPLICATE_VOUCHER ---
    seen_voucher_nos: dict[str, list[dict]] = {}
    for v in vouchers:
        vno = v.get("voucher_no", "")
        if vno:
            seen_voucher_nos.setdefault(vno, []).append(v)
    for vno, dupes in seen_voucher_nos.items():
        counts["total"] += 1
        if len(dupes) > 1:
            issues.append(
                AcctValidationIssue(
                    id=new_uuid(),
                    check_result_id="",
                    rule_code="DUPLICATE_VOUCHER",
                    severity="error",
                    message=f"Phát hiện {len(dupes)} chứng từ trùng số: {vno}",
                    erp_ref=vno,
                    details={"voucher_ids": [d.get("voucher_id") for d in dupes]},
                )
            )
            counts["error"] += 1
        else:
            counts["passed"] += 1

    total = counts["total"]
    passed = counts["passed"]
    warnings = counts.get("warning", 0)
    errors = counts.get("error", 0) + counts.get("critical", 0)
    score = round(passed / max(total, 1), 4)

    # Create aggregate result
    result = AcctSoftCheckResult(
        id=new_uuid(),
        period=period,
        total_checks=total,
        passed=passed,
        warnings=warnings,
        errors=errors,
        score=score,
        run_id=run_id,
    )
    session.add(result)
    session.flush()  # get result.id

    # Link issues
    for issue in issues:
        issue.check_result_id = result.id
    session.add_all(issues)

    # --- Optional LLM explanations for flagged issues ----------------------
    llm_explanations: list[str] | None = None
    if _is_real_llm_enabled() and issues:
        try:
            from openclaw_agent.llm.client import get_llm_client
            llm = get_llm_client()
            summary = [{"code": iss.rule_code, "message": iss.message} for iss in issues]
            llm_result = llm.explain_soft_check_issues(summary)
            if llm_result is not None:
                llm_explanations = llm_result.get("explanations")
        except Exception:
            log.exception("LLM soft-check explanations failed — skipping")

    stats: dict[str, Any] = {
        "period": period,
        "total_checks": total,
        "passed": passed,
        "warnings": warnings,
        "errors": errors,
        "score": score,
        "issues_created": len(issues),
    }
    if llm_explanations:
        stats["llm_explanations"] = llm_explanations

    # --- Risk engine enhancement (Milestone 4) ----------------------------
    try:
        from openclaw_agent.risk import assess_risk
        risk_result = assess_risk(
            vouchers=vouchers,
            invoices=invoices,
            bank_txs=[],  # Bank txs not available in soft_checks context
        )
        stats["risk_engine"] = {
            "total_flags": risk_result["total_flags"],
            "high_risk": risk_result.get("high_risk", 0),
            "benford_score": risk_result.get("benford_analysis", {}).get("score", 0),
        }
    except Exception:
        log.warning("Risk engine unavailable \u2014 returning rule-based checks only")

    return stats
