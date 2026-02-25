"""Add accounting domain tables: vouchers, bank_transactions, journal_proposals,
journal_lines, anomaly_flags.

Accounting Agent Layer ERP AI Kế toán – READ-ONLY principles:
  - acct_vouchers & acct_bank_transactions are mirrors of ERP data
  - acct_journal_proposals/lines are AI suggestions awaiting human review
  - acct_anomaly_flags capture reconciliation/audit anomalies

Revision ID: 0006
Revises: 0005
Create Date: 2026-02-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- Voucher mirror --
    op.create_table(
        "acct_vouchers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("erp_voucher_id", sa.String(64), unique=True, index=True, nullable=False),
        sa.Column("voucher_no", sa.String(64), index=True, nullable=False),
        sa.Column(
            "voucher_type", sa.String(32), index=True, nullable=False,
            comment="sell_invoice|buy_invoice|receipt|payment|other",
        ),
        sa.Column("date", sa.String(10), index=True, nullable=False, comment="YYYY-MM-DD"),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("currency", sa.String(3), server_default="VND", nullable=False),
        sa.Column("partner_name", sa.String(256), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("has_attachment", sa.Boolean, server_default="0", nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=True, index=True),
    )

    # -- Bank transactions mirror --
    op.create_table(
        "acct_bank_transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("bank_tx_ref", sa.String(128), unique=True, index=True, nullable=False),
        sa.Column("bank_account", sa.String(64), index=True, nullable=False),
        sa.Column("date", sa.String(10), index=True, nullable=False, comment="YYYY-MM-DD"),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("currency", sa.String(3), server_default="VND", nullable=False),
        sa.Column("counterparty", sa.String(256), nullable=True),
        sa.Column("memo", sa.Text, nullable=True),
        sa.Column("matched_voucher_id", sa.String(36), nullable=True, index=True),
        sa.Column(
            "match_status", sa.String(16), server_default="unmatched", index=True, nullable=False,
            comment="unmatched|matched|anomaly",
        ),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=True, index=True),
    )

    # -- Journal proposals (AI suggestions) --
    op.create_table(
        "acct_journal_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("voucher_id", sa.String(36), index=True, nullable=False, comment="FK to acct_vouchers.id"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False, comment="0.0–1.0"),
        sa.Column("reasoning", sa.Text, nullable=True, comment="LLM reasoning trace"),
        sa.Column(
            "status", sa.String(16), server_default="pending", index=True, nullable=False,
            comment="pending|approved|rejected",
        ),
        sa.Column("reviewed_by", sa.String(64), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
        sa.Column("run_id", sa.String(36), nullable=True, index=True),
    )

    # -- Journal lines (debit/credit per proposal) --
    op.create_table(
        "acct_journal_lines",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "proposal_id", sa.String(36),
            sa.ForeignKey("acct_journal_proposals.id"),
            nullable=False, index=True,
        ),
        sa.Column("account_code", sa.String(20), nullable=False, comment="e.g. 111, 131, 511"),
        sa.Column("account_name", sa.String(256), nullable=True),
        sa.Column("debit", sa.Float, server_default="0", nullable=False),
        sa.Column("credit", sa.Float, server_default="0", nullable=False),
    )

    # -- Anomaly flags --
    op.create_table(
        "acct_anomaly_flags",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "anomaly_type", sa.String(32), index=True, nullable=False,
            comment="amount_mismatch|date_gap|unmatched_tx|duplicate_voucher|other",
        ),
        sa.Column(
            "severity", sa.String(16), server_default="medium", index=True, nullable=False,
            comment="low|medium|high|critical",
        ),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("voucher_id", sa.String(36), nullable=True, index=True),
        sa.Column("bank_tx_id", sa.String(36), nullable=True, index=True),
        sa.Column(
            "resolution", sa.String(16), server_default="open", index=True, nullable=False,
            comment="open|resolved|ignored",
        ),
        sa.Column("resolved_by", sa.String(64), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
        sa.Column("run_id", sa.String(36), nullable=True, index=True),
    )


def downgrade() -> None:
    op.drop_table("acct_anomaly_flags")
    op.drop_table("acct_journal_lines")
    op.drop_table("acct_journal_proposals")
    op.drop_table("acct_bank_transactions")
    op.drop_table("acct_vouchers")
