"""add tier/risk + maker-checker approval fields

Revision ID: 0003
Revises: 0002
Create Date: 2026-02-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_obligations") as batch:
        batch.add_column(
            sa.Column("risk_level", sa.String(length=8), nullable=False, server_default="medium")
        )
        batch.create_index("ix_agent_obligations_risk_level", ["risk_level"])

    with op.batch_alter_table("agent_proposals") as batch:
        batch.add_column(sa.Column("created_by", sa.String(length=64), nullable=False, server_default="system"))
        batch.add_column(sa.Column("tier", sa.Integer(), nullable=False, server_default="3"))
        batch.add_column(sa.Column("evidence_summary_hash", sa.String(length=64), nullable=True))
        batch.create_index("ix_agent_proposals_created_by", ["created_by"])
        batch.create_index("ix_agent_proposals_tier", ["tier"])
        batch.create_index("ix_agent_proposals_evidence_summary_hash", ["evidence_summary_hash"])

    with op.batch_alter_table("agent_approvals") as batch:
        batch.add_column(sa.Column("approver_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("evidence_ack", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(
            sa.Column(
                "decided_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch.add_column(sa.Column("idempotency_key", sa.String(length=64), nullable=True))
        batch.create_index("ix_agent_approvals_approver_id", ["approver_id"])
        batch.create_index("ix_agent_approvals_decided_at", ["decided_at"])
        batch.create_index("ix_agent_approvals_idempotency_key", ["idempotency_key"], unique=True)

    # Backfill: keep existing semantics (actor_user_id is the historical approver id).
    op.execute("UPDATE agent_approvals SET approver_id = actor_user_id WHERE approver_id IS NULL")
    op.execute("UPDATE agent_approvals SET decided_at = created_at WHERE decided_at IS NULL")


def downgrade() -> None:
    # NOTE: uses batch mode so SQLite can drop columns.
    with op.batch_alter_table("agent_approvals") as batch:
        batch.drop_index("ix_agent_approvals_idempotency_key")
        batch.drop_index("ix_agent_approvals_decided_at")
        batch.drop_index("ix_agent_approvals_approver_id")
        batch.drop_column("idempotency_key")
        batch.drop_column("decided_at")
        batch.drop_column("evidence_ack")
        batch.drop_column("approver_id")

    with op.batch_alter_table("agent_proposals") as batch:
        batch.drop_index("ix_agent_proposals_evidence_summary_hash")
        batch.drop_index("ix_agent_proposals_tier")
        batch.drop_index("ix_agent_proposals_created_by")
        batch.drop_column("evidence_summary_hash")
        batch.drop_column("tier")
        batch.drop_column("created_by")

    with op.batch_alter_table("agent_obligations") as batch:
        batch.drop_index("ix_agent_obligations_risk_level")
        batch.drop_column("risk_level")

