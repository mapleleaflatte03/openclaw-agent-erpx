"""Tax report flow: generate AcctReportSnapshot from ERP invoice/voucher data.

Creates snapshot records for VAT, trial balance summary, and
VAS financial statements (B01-DN, B02-DN, B03-DN) per Milestone 7.
READ-ONLY — does NOT post anything to ERP.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from accounting_agent.common.models import AcctReportSnapshot
from accounting_agent.common.utils import new_uuid

log = logging.getLogger("accounting_agent.flows.tax_report")


def flow_tax_report(
    session: Session,
    invoices: list[dict[str, Any]],
    vouchers: list[dict[str, Any]],
    period: str,
    run_id: str,
    file_uri: str | None = None,
    journals: list[dict[str, Any]] | None = None,
    bank_txs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize VAT-relevant data and create an AcctReportSnapshot.

    Returns stats dict.
    """
    # --- VAT summary ---
    total_revenue = 0.0
    total_vat_out = 0.0
    total_purchase = 0.0
    total_vat_in = 0.0
    sell_count = 0
    buy_count = 0

    for inv in invoices:
        amount = float(inv.get("amount", 0) or 0)
        vat = float(inv.get("vat_amount", amount * 0.08) or 0)  # default 8% VAT
        inv_type = str(inv.get("type", inv.get("invoice_type", "sell"))).lower()
        if inv_type in ("sell", "receivable", "ar", "sell_invoice"):
            total_revenue += amount
            total_vat_out += vat
            sell_count += 1
        else:
            total_purchase += amount
            total_vat_in += vat
            buy_count += 1

    vat_payable = total_vat_out - total_vat_in

    summary = {
        "period": period,
        "sell_invoices": sell_count,
        "buy_invoices": buy_count,
        "total_revenue": round(total_revenue, 2),
        "total_vat_out": round(total_vat_out, 2),
        "total_purchase": round(total_purchase, 2),
        "total_vat_in": round(total_vat_in, 2),
        "vat_payable": round(vat_payable, 2),
    }

    # Find existing version for this period
    from sqlalchemy import select

    max_version = session.execute(
        select(AcctReportSnapshot.version)
        .where(
            (AcctReportSnapshot.report_type == "vat_list")
            & (AcctReportSnapshot.period == period)
        )
        .order_by(AcctReportSnapshot.version.desc())
        .limit(1)
    ).scalar()
    version = (max_version or 0) + 1

    snapshot = AcctReportSnapshot(
        id=new_uuid(),
        report_type="vat_list",
        period=period,
        version=version,
        file_uri=file_uri,
        summary_json=summary,
        run_id=run_id,
    )
    session.add(snapshot)

    # --- Trial balance summary (from vouchers) ---
    total_debit = 0.0
    total_credit = 0.0
    for v in vouchers:
        amt = float(v.get("amount", 0) or 0)
        vtype = str(v.get("voucher_type", "")).lower()
        if vtype in ("sell_invoice", "receipt"):
            total_debit += amt
        else:
            total_credit += amt

    tb_summary = {
        "period": period,
        "total_debit": round(total_debit, 2),
        "total_credit": round(total_credit, 2),
        "balance": round(total_debit - total_credit, 2),
        "voucher_count": len(vouchers),
    }

    tb_version = session.execute(
        select(AcctReportSnapshot.version)
        .where(
            (AcctReportSnapshot.report_type == "trial_balance")
            & (AcctReportSnapshot.period == period)
        )
        .order_by(AcctReportSnapshot.version.desc())
        .limit(1)
    ).scalar()
    tb_version = (tb_version or 0) + 1

    tb_snapshot = AcctReportSnapshot(
        id=new_uuid(),
        report_type="trial_balance",
        period=period,
        version=tb_version,
        file_uri=None,
        summary_json=tb_summary,
        run_id=run_id,
    )
    session.add(tb_snapshot)

    # --- VAS Financial Statements (Milestone 7) ----------------------------
    vas_reports = {}
    try:
        from accounting_agent.reports import generate_audit_pack
        if journals:
            audit_pack = generate_audit_pack(
                journals=journals,
                bank_txs=bank_txs,
                invoices=invoices,
                vouchers=vouchers,
                period=period,
            )
            vas_reports = {
                "B01_DN": audit_pack["reports"].get("B01-DN", {}),
                "B02_DN": audit_pack["reports"].get("B02-DN", {}),
                "B03_DN": audit_pack["reports"].get("B03-DN", {}),
                "cross_checks": audit_pack.get("cross_checks", []),
                "all_checks_pass": audit_pack.get("all_checks_pass", False),
            }
            # Persist VAS report snapshot
            vas_version = session.execute(
                select(AcctReportSnapshot.version)
                .where(
                    (AcctReportSnapshot.report_type == "vas_audit_pack")
                    & (AcctReportSnapshot.period == period)
                )
                .order_by(AcctReportSnapshot.version.desc())
                .limit(1)
            ).scalar()
            vas_version = (vas_version or 0) + 1
            session.add(AcctReportSnapshot(
                id=new_uuid(),
                report_type="vas_audit_pack",
                period=period,
                version=vas_version,
                file_uri=None,
                summary_json=vas_reports,
                run_id=run_id,
            ))
            log.info("VAS audit pack generated for period %s", period)
    except Exception:
        log.warning("VAS report module unavailable — returning basic reports only")

    result = {
        "period": period,
        "vat_summary": summary,
        "trial_balance": tb_summary,
        "snapshots_created": 2 + (1 if vas_reports else 0),
    }
    if vas_reports:
        result["vas_reports"] = vas_reports
    return result
