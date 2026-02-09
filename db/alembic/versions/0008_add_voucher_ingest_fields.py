"""Add ingest-related fields to acct_vouchers (partner_tax_code, raw_payload, source, type_hint).

Revision ID: 0008
Revises: 0007
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("acct_vouchers", sa.Column("partner_tax_code", sa.String(32), nullable=True))
    op.add_column("acct_vouchers", sa.Column("raw_payload", sa.JSON(), nullable=True))
    op.add_column("acct_vouchers", sa.Column("source", sa.String(64), nullable=True, server_default="erpx"))
    op.add_column("acct_vouchers", sa.Column("type_hint", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("acct_vouchers", "type_hint")
    op.drop_column("acct_vouchers", "source")
    op.drop_column("acct_vouchers", "raw_payload")
    op.drop_column("acct_vouchers", "partner_tax_code")
