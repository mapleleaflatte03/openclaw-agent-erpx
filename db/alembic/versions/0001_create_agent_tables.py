"""create agent tables

Revision ID: 0001
Revises: None
Create Date: 2026-02-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.String(length=36), primary_key=True),
        sa.Column("run_type", sa.String(length=64), nullable=False),
        sa.Column("trigger_type", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("cursor_in", sa.JSON(), nullable=True),
        sa.Column("cursor_out", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_runs_idempotency_key"),
    )
    op.create_index("ix_agent_runs_run_type", "agent_runs", ["run_type"])
    op.create_index("ix_agent_runs_trigger_type", "agent_runs", ["trigger_type"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_index("ix_agent_runs_created_at", "agent_runs", ["created_at"])

    op.create_table(
        "agent_tasks",
        sa.Column("task_id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("agent_runs.run_id"), nullable=False),
        sa.Column("task_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("input_ref", sa.JSON(), nullable=True),
        sa.Column("output_ref", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_tasks_run_id", "agent_tasks", ["run_id"])
    op.create_index("ix_agent_tasks_task_name", "agent_tasks", ["task_name"])
    op.create_index("ix_agent_tasks_status", "agent_tasks", ["status"])
    op.create_index("ix_agent_tasks_created_at", "agent_tasks", ["created_at"])

    op.create_table(
        "agent_logs",
        sa.Column("log_id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_logs_run_id", "agent_logs", ["run_id"])
    op.create_index("ix_agent_logs_task_id", "agent_logs", ["task_id"])
    op.create_index("ix_agent_logs_level", "agent_logs", ["level"])
    op.create_index("ix_agent_logs_ts", "agent_logs", ["ts"])

    op.create_table(
        "agent_attachments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("erp_object_type", sa.String(length=32), nullable=False),
        sa.Column("erp_object_id", sa.String(length=64), nullable=False),
        sa.Column("file_uri", sa.String(length=512), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("matched_by", sa.String(length=16), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "file_hash", "erp_object_type", "erp_object_id", name="uq_attach_hash_object"
        ),
    )
    op.create_index("ix_agent_attachments_erp_object_type", "agent_attachments", ["erp_object_type"])
    op.create_index("ix_agent_attachments_erp_object_id", "agent_attachments", ["erp_object_id"])
    op.create_index("ix_agent_attachments_file_hash", "agent_attachments", ["file_hash"])
    op.create_index("ix_agent_attachments_run_id", "agent_attachments", ["run_id"])
    op.create_index("ix_agent_attachments_created_at", "agent_attachments", ["created_at"])

    op.create_table(
        "agent_exports",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("export_type", sa.String(length=32), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("file_uri", sa.String(length=512), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("export_type", "period", "version", name="uq_export_type_period_version"),
    )
    op.create_index("ix_agent_exports_export_type", "agent_exports", ["export_type"])
    op.create_index("ix_agent_exports_period", "agent_exports", ["period"])
    op.create_index("ix_agent_exports_run_id", "agent_exports", ["run_id"])
    op.create_index("ix_agent_exports_created_at", "agent_exports", ["created_at"])

    op.create_table(
        "agent_exceptions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("exception_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("erp_refs", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("signature", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("signature", name="uq_exception_signature"),
    )
    op.create_index("ix_agent_exceptions_exception_type", "agent_exceptions", ["exception_type"])
    op.create_index("ix_agent_exceptions_severity", "agent_exceptions", ["severity"])
    op.create_index("ix_agent_exceptions_signature", "agent_exceptions", ["signature"])
    op.create_index("ix_agent_exceptions_run_id", "agent_exceptions", ["run_id"])
    op.create_index("ix_agent_exceptions_created_at", "agent_exceptions", ["created_at"])

    op.create_table(
        "agent_reminder_log",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("customer_id", sa.String(length=64), nullable=False),
        sa.Column("invoice_id", sa.String(length=64), nullable=False),
        sa.Column("reminder_stage", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("sent_to", sa.String(length=256), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("policy_key", sa.String(length=128), nullable=False),
        sa.UniqueConstraint("policy_key", name="uq_reminder_policy_key"),
    )
    op.create_index("ix_agent_reminder_log_customer_id", "agent_reminder_log", ["customer_id"])
    op.create_index("ix_agent_reminder_log_invoice_id", "agent_reminder_log", ["invoice_id"])
    op.create_index("ix_agent_reminder_log_reminder_stage", "agent_reminder_log", ["reminder_stage"])
    op.create_index("ix_agent_reminder_log_sent_at", "agent_reminder_log", ["sent_at"])
    op.create_index("ix_agent_reminder_log_run_id", "agent_reminder_log", ["run_id"])
    op.create_index("ix_agent_reminder_log_policy_key", "agent_reminder_log", ["policy_key"])

    op.create_table(
        "agent_close_tasks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("task_name", sa.String(length=256), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_nudged_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("period", "task_name", name="uq_close_period_task_name"),
    )
    op.create_index("ix_agent_close_tasks_period", "agent_close_tasks", ["period"])
    op.create_index("ix_agent_close_tasks_owner_user_id", "agent_close_tasks", ["owner_user_id"])
    op.create_index("ix_agent_close_tasks_due_date", "agent_close_tasks", ["due_date"])
    op.create_index("ix_agent_close_tasks_status", "agent_close_tasks", ["status"])

    op.create_table(
        "agent_evidence_packs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("issue_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("pack_uri", sa.String(length=512), nullable=False),
        sa.Column("index_json", sa.JSON(), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("issue_key", "version", name="uq_evidence_issue_version"),
    )
    op.create_index("ix_agent_evidence_packs_issue_key", "agent_evidence_packs", ["issue_key"])
    op.create_index("ix_agent_evidence_packs_run_id", "agent_evidence_packs", ["run_id"])
    op.create_index("ix_agent_evidence_packs_created_at", "agent_evidence_packs", ["created_at"])

    op.create_table(
        "agent_kb_docs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("doc_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("source_uri", sa.String(length=512), nullable=False),
        sa.Column("text_uri", sa.String(length=512), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.UniqueConstraint("file_hash", "version", name="uq_kb_filehash_version"),
    )
    op.create_index("ix_agent_kb_docs_doc_type", "agent_kb_docs", ["doc_type"])
    op.create_index("ix_agent_kb_docs_title", "agent_kb_docs", ["title"])
    op.create_index("ix_agent_kb_docs_version", "agent_kb_docs", ["version"])
    op.create_index("ix_agent_kb_docs_effective_date", "agent_kb_docs", ["effective_date"])
    op.create_index("ix_agent_kb_docs_source_uri", "agent_kb_docs", ["source_uri"])
    op.create_index("ix_agent_kb_docs_text_uri", "agent_kb_docs", ["text_uri"])
    op.create_index("ix_agent_kb_docs_indexed_at", "agent_kb_docs", ["indexed_at"])
    op.create_index("ix_agent_kb_docs_file_hash", "agent_kb_docs", ["file_hash"])

    op.create_table(
        "agent_feedback",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("item_ref", sa.JSON(), nullable=False),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.Column("error_type", sa.String(length=32), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_feedback_task_type", "agent_feedback", ["task_type"])
    op.create_index("ix_agent_feedback_label", "agent_feedback", ["label"])
    op.create_index("ix_agent_feedback_error_type", "agent_feedback", ["error_type"])
    op.create_index("ix_agent_feedback_user_id", "agent_feedback", ["user_id"])
    op.create_index("ix_agent_feedback_created_at", "agent_feedback", ["created_at"])


def downgrade() -> None:
    op.drop_table("agent_feedback")
    op.drop_table("agent_kb_docs")
    op.drop_table("agent_evidence_packs")
    op.drop_table("agent_close_tasks")
    op.drop_table("agent_reminder_log")
    op.drop_table("agent_exceptions")
    op.drop_table("agent_exports")
    op.drop_table("agent_attachments")
    op.drop_table("agent_logs")
    op.drop_table("agent_tasks")
    op.drop_table("agent_runs")
