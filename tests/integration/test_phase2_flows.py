"""Tests for Phase 2 accounting flows: soft_checks_acct, tax_report, cashflow_forecast."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from accounting_agent.common.models import (
    AcctCashflowForecast,
    AcctReportSnapshot,
    AcctSoftCheckResult,
    AcctValidationIssue,
    Base,
)
from accounting_agent.flows.cashflow_forecast import flow_cashflow_forecast
from accounting_agent.flows.soft_checks_acct import flow_soft_checks_acct
from accounting_agent.flows.tax_report import flow_tax_report


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# Soft-checks flow
# ---------------------------------------------------------------------------

def _make_vouchers(n: int = 3, missing_attachment: bool = False) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        out.append({
            "voucher_id": f"v-{i}",
            "voucher_no": f"PT-{i:04d}",
            "voucher_type": "sell_invoice",
            "date": date.today().isoformat(),
            "amount": 10_000_000,
            "has_attachment": not missing_attachment,
        })
    return out


def _make_journals(n: int = 2, imbalanced: bool = False) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        out.append({
            "journal_id": f"j-{i}",
            "date": date.today().isoformat(),
            "debit_total": 5_000_000,
            "credit_total": 5_000_000 if not imbalanced else 4_000_000,
        })
    return out


def _make_invoices(n: int = 2, overdue: bool = False) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        due = (date.today() - timedelta(days=30)) if overdue else (date.today() + timedelta(days=30))
        out.append({
            "invoice_id": f"inv-{i}",
            "invoice_no": f"HD-{i:04d}",
            "date": date.today().isoformat(),
            "amount": 8_000_000,
            "type": "sell",
            "status": "unpaid",
            "due_date": due.isoformat(),
        })
    return out


def test_soft_checks_all_clean(db_session: Session):
    stats = flow_soft_checks_acct(
        db_session,
        vouchers=_make_vouchers(3),
        journals=_make_journals(2),
        invoices=_make_invoices(2),
        period="2026-01",
        run_id="test-run-sc1",
    )
    db_session.commit()

    assert stats["passed"] > 0
    assert stats["warnings"] == 0
    assert stats["errors"] == 0
    assert stats["score"] == 1.0

    results = db_session.execute(select(AcctSoftCheckResult)).scalars().all()
    assert len(results) == 1
    assert results[0].period == "2026-01"

    issues = db_session.execute(select(AcctValidationIssue)).scalars().all()
    assert len(issues) == 0


def test_soft_checks_detects_issues(db_session: Session):
    stats = flow_soft_checks_acct(
        db_session,
        vouchers=_make_vouchers(2, missing_attachment=True),
        journals=_make_journals(2, imbalanced=True),
        invoices=_make_invoices(2, overdue=True),
        period="2026-02",
        run_id="test-run-sc2",
    )
    db_session.commit()

    assert stats["issues_created"] > 0
    assert stats["warnings"] > 0 or stats["errors"] > 0
    assert stats["score"] < 1.0

    issues = db_session.execute(select(AcctValidationIssue)).scalars().all()
    rule_codes = {i.rule_code for i in issues}
    assert "MISSING_ATTACHMENT" in rule_codes
    assert "JOURNAL_IMBALANCED" in rule_codes
    assert "OVERDUE_INVOICE" in rule_codes


def test_soft_checks_duplicate_voucher(db_session: Session):
    vouchers = _make_vouchers(2)
    # Make duplicates
    vouchers[1]["voucher_no"] = vouchers[0]["voucher_no"]
    flow_soft_checks_acct(
        db_session,
        vouchers=vouchers,
        journals=[],
        invoices=[],
        period="2026-03",
        run_id="test-run-sc3",
    )
    db_session.commit()

    issues = db_session.execute(select(AcctValidationIssue)).scalars().all()
    dup_issues = [i for i in issues if i.rule_code == "DUPLICATE_VOUCHER"]
    assert len(dup_issues) == 1


# ---------------------------------------------------------------------------
# Tax report flow
# ---------------------------------------------------------------------------

def test_tax_report_creates_snapshots(db_session: Session):
    invoices = _make_invoices(3)
    vouchers = _make_vouchers(2)
    stats = flow_tax_report(db_session, invoices, vouchers, "2026-01", "test-run-tr1")
    db_session.commit()

    assert stats["snapshots_created"] == 2
    assert stats["vat_summary"]["sell_invoices"] == 3

    snapshots = db_session.execute(select(AcctReportSnapshot)).scalars().all()
    assert len(snapshots) == 2
    types = {s.report_type for s in snapshots}
    assert "vat_list" in types
    assert "trial_balance" in types


def test_tax_report_version_increments(db_session: Session):
    invoices = _make_invoices(2)
    vouchers = _make_vouchers(1)
    flow_tax_report(db_session, invoices, vouchers, "2026-01", "test-run-tr2a")
    db_session.commit()

    flow_tax_report(db_session, invoices, vouchers, "2026-01", "test-run-tr2b")
    db_session.commit()

    vat_snaps = db_session.execute(
        select(AcctReportSnapshot)
        .where(AcctReportSnapshot.report_type == "vat_list")
        .order_by(AcctReportSnapshot.version)
    ).scalars().all()
    assert len(vat_snaps) == 2
    assert vat_snaps[0].version == 1
    assert vat_snaps[1].version == 2


# ---------------------------------------------------------------------------
# Cashflow forecast flow
# ---------------------------------------------------------------------------

def test_cashflow_forecast_from_invoices(db_session: Session):
    invoices = _make_invoices(3, overdue=True)  # overdue → forecast today
    stats = flow_cashflow_forecast(db_session, invoices, [], "test-run-cf1", horizon_days=30)
    db_session.commit()

    assert stats["forecast_items"] > 0
    assert stats["total_inflow"] > 0

    rows = db_session.execute(select(AcctCashflowForecast)).scalars().all()
    assert len(rows) > 0
    assert all(r.direction == "inflow" for r in rows)  # sell invoices = inflow


def test_cashflow_forecast_recurring_detection(db_session: Session):
    bank_txs = [
        {"counterparty": "VNPT", "amount": 5_000_000, "date": "2026-01-01"},
        {"counterparty": "VNPT", "amount": 5_000_000, "date": "2026-01-15"},
        {"counterparty": "One-off", "amount": 1_000_000, "date": "2026-01-10"},
    ]
    flow_cashflow_forecast(db_session, [], bank_txs, "test-run-cf2", horizon_days=30)
    db_session.commit()

    rows = db_session.execute(select(AcctCashflowForecast)).scalars().all()
    recurring = [r for r in rows if r.source_type == "recurring"]
    assert len(recurring) >= 1
    assert recurring[0].source_ref == "VNPT"
