"""Cash-flow forecast flow: project future cash in/out from ERP data.

Uses receivable invoices (inflow) and payable invoices (outflow) to build
a 30-day forecast. READ-ONLY — does NOT modify ERP.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from openclaw_agent.common.models import AcctCashflowForecast
from openclaw_agent.common.utils import new_uuid


def _safe_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def flow_cashflow_forecast(
    session: Session,
    invoices: list[dict[str, Any]],
    bank_txs: list[dict[str, Any]],
    run_id: str,
    horizon_days: int = 30,
) -> dict[str, Any]:
    """Build a simple cash-flow forecast and persist rows.

    Returns stats dict.
    """
    today = date.today()
    cutoff = today + timedelta(days=horizon_days)
    forecasts: list[AcctCashflowForecast] = []

    # --- From unpaid invoices ---
    for inv in invoices:
        if inv.get("status") != "unpaid":
            continue
        due = _safe_date(inv.get("due_date"))
        if due is None or due > cutoff:
            continue
        forecast_dt = max(due, today)

        inv_type = str(inv.get("type", inv.get("invoice_type", "sell"))).lower()
        if inv_type in ("sell", "receivable", "ar", "sell_invoice"):
            direction, source_type = "inflow", "invoice_receivable"
        else:
            direction, source_type = "outflow", "invoice_payable"

        forecasts.append(
            AcctCashflowForecast(
                id=new_uuid(),
                forecast_date=forecast_dt.isoformat(),
                direction=direction,
                amount=float(inv.get("amount", 0) or 0),
                currency=str(inv.get("currency", "VND")),
                source_type=source_type,
                source_ref=str(inv.get("invoice_id", "")),
                confidence=0.8 if due >= today else 0.6,  # overdue = less certain
                run_id=run_id,
            )
        )

    # --- Recurring patterns from recent bank txs (simple heuristic) ---
    # Group by counterparty and detect regular amounts
    from collections import Counter

    counterparty_amounts: dict[str, list[float]] = {}
    for tx in bank_txs:
        cp = tx.get("counterparty") or tx.get("memo") or "unknown"
        amt = float(tx.get("amount", 0) or 0)
        counterparty_amounts.setdefault(cp, []).append(amt)

    for cp, amounts in counterparty_amounts.items():
        if len(amounts) < 2:
            continue
        # If same amount appears 2+ times, treat as recurring
        amount_counts = Counter(round(a, 0) for a in amounts)
        for rounded_amt, cnt in amount_counts.items():
            if cnt >= 2 and rounded_amt != 0:
                direction = "inflow" if rounded_amt > 0 else "outflow"
                # Project next occurrence ~30 days from today
                forecasts.append(
                    AcctCashflowForecast(
                        id=new_uuid(),
                        forecast_date=(today + timedelta(days=15)).isoformat(),
                        direction=direction,
                        amount=abs(rounded_amt),
                        currency="VND",
                        source_type="recurring",
                        source_ref=cp[:128],
                        confidence=0.5,
                        run_id=run_id,
                    )
                )

    session.add_all(forecasts)

    total_inflow = sum(f.amount for f in forecasts if f.direction == "inflow")
    total_outflow = sum(f.amount for f in forecasts if f.direction == "outflow")

    return {
        "forecast_items": len(forecasts),
        "total_inflow": total_inflow,
        "total_outflow": total_outflow,
        "net": total_inflow - total_outflow,
        "horizon_days": horizon_days,
    }
