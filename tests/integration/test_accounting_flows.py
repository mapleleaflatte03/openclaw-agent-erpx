"""Integration tests for accounting flows: journal suggestion and bank reconciliation.

These tests run the flow functions directly against an in-memory SQLite database
(via SQLAlchemy) with mock ERP voucher/bank data. They validate:
  1. Journal suggestion: vouchers → proposals + lines (Nợ/Có) with confidence
  2. Bank reconciliation: bank txs → matched/anomaly/unmatched flags
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from openclaw_agent.common.db import Base
from openclaw_agent.common.models import (
    AcctAnomalyFlag,
    AcctBankTransaction,
    AcctJournalLine,
    AcctJournalProposal,
    AcctVoucher,
)
from openclaw_agent.flows.bank_reconcile import flow_bank_reconcile
from openclaw_agent.flows.journal_suggestion import flow_journal_suggestion


@pytest.fixture
def db_session():
    """Create an in-memory SQLite session with all accounting tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


# ---- Sample data ----

_VOUCHERS = [
    {
        "voucher_id": "VCH-0001",
        "voucher_no": "PT-000001",
        "voucher_type": "sell_invoice",
        "date": "2026-02-05",
        "amount": 10_000_000,
        "currency": "VND",
        "partner_name": "ACME LLC",
        "description": "Thu tiền bán hàng",
        "has_attachment": True,
    },
    {
        "voucher_id": "VCH-0002",
        "voucher_no": "PT-000002",
        "voucher_type": "buy_invoice",
        "date": "2026-02-06",
        "amount": 5_000_000,
        "currency": "VND",
        "partner_name": "Sunrise Co",
        "description": "Mua văn phòng phẩm",
        "has_attachment": True,
    },
    {
        "voucher_id": "VCH-0003",
        "voucher_no": "PT-000003",
        "voucher_type": "receipt",
        "date": "2026-02-07",
        "amount": 3_000_000,
        "currency": "VND",
        "partner_name": None,
        "description": "Thu tiền mặt",
        "has_attachment": False,  # lower confidence
    },
]

_BANK_TXS = [
    {
        # Matches VCH-0001 exactly
        "tx_ref": "VCB-REF-001",
        "bank_account": "112-VCB-001",
        "date": "2026-02-05",
        "amount": 10_000_000,
        "currency": "VND",
        "counterparty": "ACME LLC",
        "memo": "CK tham chiếu VCH-0001",
    },
    {
        # Amount mismatch with VCH-0002 (2% off > 1% tolerance)
        "tx_ref": "VCB-REF-002",
        "bank_account": "112-VCB-001",
        "date": "2026-02-06",
        "amount": 5_150_000,  # 3% mismatch
        "currency": "VND",
        "counterparty": "Sunrise Co",
        "memo": "CK tham chiếu VCH-0002",
    },
    {
        # No matching voucher (date far off + amount doesn't match any)
        "tx_ref": "VCB-REF-003",
        "bank_account": "112-VCB-001",
        "date": "2026-02-20",
        "amount": 7_500_000,
        "currency": "VND",
        "counterparty": "Unknown Corp",
        "memo": "Giao dịch không rõ",
    },
]


# ---- Flow 1: Journal Suggestion ----


class TestJournalSuggestion:
    def test_creates_proposals_for_vouchers(self, db_session):
        stats = flow_journal_suggestion(db_session, _VOUCHERS, run_id="run-test-001")
        db_session.flush()

        assert stats["proposals_created"] == 3
        assert stats["skipped_existing"] == 0
        assert stats["total_vouchers"] == 3

        # Verify voucher mirrors created
        vouchers = db_session.execute(select(AcctVoucher)).scalars().all()
        assert len(vouchers) == 3

        # Verify proposals created
        proposals = db_session.execute(select(AcctJournalProposal)).scalars().all()
        assert len(proposals) == 3
        assert all(p.status == "pending" for p in proposals)

    def test_proposal_has_debit_credit_lines(self, db_session):
        flow_journal_suggestion(db_session, _VOUCHERS[:1], run_id="run-test-002")
        db_session.flush()

        proposals = db_session.execute(select(AcctJournalProposal)).scalars().all()
        assert len(proposals) == 1
        p = proposals[0]

        lines = db_session.execute(
            select(AcctJournalLine).where(AcctJournalLine.proposal_id == p.id)
        ).scalars().all()
        assert len(lines) == 2  # one debit, one credit

        debit_line = next(ln for ln in lines if ln.debit > 0)
        credit_line = next(ln for ln in lines if ln.credit > 0)

        # sell_invoice → Nợ 131, Có 511
        assert debit_line.account_code == "131"
        assert credit_line.account_code == "511"
        assert debit_line.debit == 10_000_000
        assert credit_line.credit == 10_000_000

    def test_confidence_lower_without_attachment(self, db_session):
        flow_journal_suggestion(db_session, _VOUCHERS, run_id="run-test-003")
        db_session.flush()

        proposals = db_session.execute(
            select(AcctJournalProposal).order_by(AcctJournalProposal.created_at)
        ).scalars().all()

        # VCH-0003 has no attachment → confidence should be lower
        # sell_invoice conf=0.92, buy_invoice conf=0.88, receipt conf=0.95*0.8=0.76
        receipt_proposal = proposals[2]
        assert receipt_proposal.confidence < 0.80

    def test_skips_existing_vouchers(self, db_session):
        flow_journal_suggestion(db_session, _VOUCHERS, run_id="run-test-004a")
        db_session.flush()
        stats = flow_journal_suggestion(db_session, _VOUCHERS, run_id="run-test-004b")
        db_session.flush()

        assert stats["proposals_created"] == 0
        assert stats["skipped_existing"] == 3

    def test_proposal_approve_reject(self, db_session):
        flow_journal_suggestion(db_session, _VOUCHERS[:1], run_id="run-test-005")
        db_session.flush()

        proposal = db_session.execute(select(AcctJournalProposal)).scalars().first()
        assert proposal.status == "pending"

        # Approve
        proposal.status = "approved"
        proposal.reviewed_by = "demo-checker"
        db_session.flush()
        assert proposal.status == "approved"


# ---- Flow 2: Bank Reconciliation ----


class TestBankReconcile:
    def test_matches_and_flags(self, db_session):
        stats = flow_bank_reconcile(db_session, _BANK_TXS, _VOUCHERS, run_id="run-test-101")
        db_session.flush()

        assert stats["total_bank_txs"] == 3
        assert stats["matched"] >= 1
        assert stats["anomalies_created"] >= 1

    def test_exact_match(self, db_session):
        """VCB-REF-001 should match VCH-0001 exactly."""
        flow_bank_reconcile(db_session, _BANK_TXS[:1], _VOUCHERS, run_id="run-test-102")
        db_session.flush()

        txs = db_session.execute(select(AcctBankTransaction)).scalars().all()
        assert len(txs) == 1
        assert txs[0].match_status == "matched"
        assert txs[0].matched_voucher_id == "VCH-0001"

        # No anomaly for exact match
        flags = db_session.execute(select(AcctAnomalyFlag)).scalars().all()
        assert len(flags) == 0

    def test_amount_mismatch_anomaly(self, db_session):
        """VCB-REF-002 has 3% amount mismatch with VCH-0002 → anomaly."""
        flow_bank_reconcile(db_session, _BANK_TXS[1:2], _VOUCHERS, run_id="run-test-103")
        db_session.flush()

        flags = db_session.execute(select(AcctAnomalyFlag)).scalars().all()
        assert len(flags) >= 1
        assert any(f.anomaly_type == "amount_mismatch" for f in flags)

    def test_unmatched_tx_anomaly(self, db_session):
        """VCB-REF-003 has no matching voucher → unmatched_tx anomaly."""
        flow_bank_reconcile(db_session, _BANK_TXS[2:3], _VOUCHERS, run_id="run-test-104")
        db_session.flush()

        txs = db_session.execute(select(AcctBankTransaction)).scalars().all()
        assert len(txs) == 1
        assert txs[0].match_status == "unmatched"

        flags = db_session.execute(select(AcctAnomalyFlag)).scalars().all()
        assert len(flags) == 1
        assert flags[0].anomaly_type == "unmatched_tx"
        assert flags[0].severity == "high"

    def test_anomaly_resolution(self, db_session):
        """Anomaly flags can be resolved."""
        flow_bank_reconcile(db_session, _BANK_TXS[2:3], _VOUCHERS, run_id="run-test-105")
        db_session.flush()

        flag = db_session.execute(select(AcctAnomalyFlag)).scalars().first()
        assert flag.resolution == "open"

        flag.resolution = "resolved"
        flag.resolved_by = "demo-checker"
        db_session.flush()
        assert flag.resolution == "resolved"
