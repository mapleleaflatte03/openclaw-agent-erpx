"""Add acct_voucher_corrections table for OCR manual edits.

Revision ID: 0010
Revises: 0009
Create Date: 2026-02-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "acct_voucher_corrections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("voucher_id", sa.String(36), nullable=False),
        sa.Column("field_name", sa.String(64), nullable=False),
        sa.Column("old_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("corrected_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=True),
    )
    op.create_index("ix_acct_voucher_corrections_voucher_id", "acct_voucher_corrections", ["voucher_id"])
    op.create_index("ix_acct_voucher_corrections_field_name", "acct_voucher_corrections", ["field_name"])
    op.create_index("ix_acct_voucher_corrections_corrected_by", "acct_voucher_corrections", ["corrected_by"])
    op.create_index("ix_acct_voucher_corrections_created_at", "acct_voucher_corrections", ["created_at"])
    op.create_index("ix_acct_voucher_corrections_run_id", "acct_voucher_corrections", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_acct_voucher_corrections_run_id", table_name="acct_voucher_corrections")
    op.drop_index("ix_acct_voucher_corrections_created_at", table_name="acct_voucher_corrections")
    op.drop_index("ix_acct_voucher_corrections_corrected_by", table_name="acct_voucher_corrections")
    op.drop_index("ix_acct_voucher_corrections_field_name", table_name="acct_voucher_corrections")
    op.drop_index("ix_acct_voucher_corrections_voucher_id", table_name="acct_voucher_corrections")
    op.drop_table("acct_voucher_corrections")
