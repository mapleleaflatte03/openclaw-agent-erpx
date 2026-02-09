"""Add classification_tag to acct_vouchers.

Revision ID: 0009
Revises: 0008
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "acct_vouchers",
        sa.Column("classification_tag", sa.String(32), nullable=True, index=True),
    )


def downgrade() -> None:
    op.drop_column("acct_vouchers", "classification_tag")
