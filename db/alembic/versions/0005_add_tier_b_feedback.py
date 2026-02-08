"""Add tier_b_feedback table for explicit and implicit obligation feedback.

Revision ID: 0005
Revises: 0004
Create Date: 2026-02-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tier_b_feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("obligation_id", sa.String(36), nullable=False, index=True),
        sa.Column("user_id", sa.String(64), nullable=True, index=True),
        sa.Column(
            "feedback_type",
            sa.String(32),
            nullable=False,
            index=True,
            comment="explicit_yes|explicit_no|implicit_accept|implicit_edit|implicit_reject",
        ),
        sa.Column("delta", sa.JSON, nullable=True, comment="Optional edit diff"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("tier_b_feedback")
