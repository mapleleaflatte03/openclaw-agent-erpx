"""Tax report flow: generate AcctReportSnapshot from ERP invoice/voucher data.

Creates snapshot records for VAT, trial balance summary.
READ-ONLY — does NOT post anything to ERP.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from openclaw_agent.common.models import AcctReportSnapshot
from openclaw_agent.common.utils import new_uuid


def flow_tax_report(
    session: Session,
    invoices: list[dict[str, Any]],
    vouchers: list[dict[str, Any]],
    period: str,
    run_id: str,
    file_uri: str | None = None,
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

    return {
        "period": period,
        "vat_summary": summary,
        "trial_balance": tb_summary,
        "snapshots_created": 2,
    }
