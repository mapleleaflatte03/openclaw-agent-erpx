"""Add extended accounting tables (Phase 2).

Revision ID: 0007
Revises: 0006
Create Date: 2026-02-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- acct_soft_check_results ---
    op.create_table(
        "acct_soft_check_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("period", sa.String(7), nullable=False, index=True, comment="YYYY-MM"),
        sa.Column("total_checks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("passed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("warnings", sa.Integer, nullable=False, server_default="0"),
        sa.Column("errors", sa.Integer, nullable=False, server_default="0"),
        sa.Column("score", sa.Float, nullable=False, server_default="0", comment="Health score 0.0–1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
        sa.Column("run_id", sa.String(36), nullable=True, index=True),
    )

    # --- acct_validation_issues ---
    op.create_table(
        "acct_validation_issues",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "check_result_id", sa.String(36),
            sa.ForeignKey("acct_soft_check_results.id"), nullable=False, index=True,
        ),
        sa.Column(
            "rule_code", sa.String(64), nullable=False, index=True,
            comment="e.g. MISSING_ATTACHMENT, JOURNAL_IMBALANCED, OVERDUE_INVOICE, DUPLICATE_VOUCHER",
        ),
        sa.Column(
            "severity", sa.String(16), server_default="warning", nullable=False, index=True,
            comment="info|warning|error|critical",
        ),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("erp_ref", sa.String(128), nullable=True, index=True, comment="e.g. voucher_id, journal_id"),
        sa.Column("details", sa.JSON, nullable=True),
        sa.Column(
            "resolution", sa.String(16), server_default="open", nullable=False, index=True,
            comment="open|resolved|ignored",
        ),
        sa.Column("resolved_by", sa.String(64), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
    )

    # --- acct_report_snapshots ---
    op.create_table(
        "acct_report_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "report_type", sa.String(32), nullable=False, index=True,
            comment="vat_list|trial_balance|pnl|balance_sheet",
        ),
        sa.Column("period", sa.String(7), nullable=False, index=True, comment="YYYY-MM"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("file_uri", sa.String(512), nullable=True, comment="S3/MinIO URI"),
        sa.Column("summary_json", sa.JSON, nullable=True, comment="Quick numbers for dashboard"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
        sa.Column("run_id", sa.String(36), nullable=True, index=True),
    )

    # --- acct_cashflow_forecasts ---
    op.create_table(
        "acct_cashflow_forecasts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("forecast_date", sa.String(10), nullable=False, index=True, comment="YYYY-MM-DD"),
        sa.Column(
            "direction", sa.String(8), nullable=False, index=True,
            comment="inflow|outflow",
        ),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("currency", sa.String(3), server_default="VND"),
        sa.Column(
            "source_type", sa.String(32), nullable=False,
            comment="invoice_receivable|invoice_payable|recurring|manual",
        ),
        sa.Column("source_ref", sa.String(128), nullable=True, index=True),
        sa.Column("confidence", sa.Float, server_default="1.0", comment="0.0–1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
        sa.Column("run_id", sa.String(36), nullable=True, index=True),
    )

    # --- acct_qna_audits ---
    op.create_table(
        "acct_qna_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("answer", sa.Text, nullable=False),
        sa.Column("sources", sa.JSON, nullable=True, comment="KB doc refs used"),
        sa.Column("user_id", sa.String(64), nullable=True, index=True),
        sa.Column("feedback", sa.String(16), nullable=True, comment="helpful|not_helpful|null"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
        sa.Column("run_id", sa.String(36), nullable=True, index=True),
    )


def downgrade() -> None:
    op.drop_table("acct_qna_audits")
    op.drop_table("acct_cashflow_forecasts")
    op.drop_table("acct_report_snapshots")
    op.drop_table("acct_validation_issues")
    op.drop_table("acct_soft_check_results")
