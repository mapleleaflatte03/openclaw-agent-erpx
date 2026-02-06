"""create contract obligation + proposals tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_contract_cases",
        sa.Column("case_id", sa.String(length=36), primary_key=True),
        sa.Column("case_key", sa.String(length=128), nullable=False),
        sa.Column("partner_name", sa.String(length=256), nullable=True),
        sa.Column("partner_tax_id", sa.String(length=32), nullable=True),
        sa.Column("contract_code", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("case_key", name="uq_contract_cases_case_key"),
    )
    op.create_index("ix_agent_contract_cases_case_key", "agent_contract_cases", ["case_key"])
    op.create_index("ix_agent_contract_cases_partner_name", "agent_contract_cases", ["partner_name"])
    op.create_index("ix_agent_contract_cases_partner_tax_id", "agent_contract_cases", ["partner_tax_id"])
    op.create_index("ix_agent_contract_cases_contract_code", "agent_contract_cases", ["contract_code"])
    op.create_index("ix_agent_contract_cases_status", "agent_contract_cases", ["status"])
    op.create_index("ix_agent_contract_cases_created_at", "agent_contract_cases", ["created_at"])
    op.create_index("ix_agent_contract_cases_updated_at", "agent_contract_cases", ["updated_at"])

    op.create_table(
        "agent_source_files",
        sa.Column("source_id", sa.String(length=36), primary_key=True),
        sa.Column("case_id", sa.String(length=36), sa.ForeignKey("agent_contract_cases.case_id"), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_uri", sa.String(length=512), nullable=False),
        sa.Column("stored_uri", sa.String(length=512), nullable=True),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=64), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("file_hash", "source_type", name="uq_source_hash_type"),
    )
    op.create_index("ix_agent_source_files_case_id", "agent_source_files", ["case_id"])
    op.create_index("ix_agent_source_files_source_type", "agent_source_files", ["source_type"])
    op.create_index("ix_agent_source_files_file_hash", "agent_source_files", ["file_hash"])
    op.create_index("ix_agent_source_files_created_at", "agent_source_files", ["created_at"])

    op.create_table(
        "agent_extracted_text",
        sa.Column("text_id", sa.String(length=36), primary_key=True),
        sa.Column("source_id", sa.String(length=36), sa.ForeignKey("agent_source_files.source_id"), nullable=False),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page_confidence", sa.JSON(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_id", name="uq_extracted_text_source_id"),
    )
    op.create_index("ix_agent_extracted_text_source_id", "agent_extracted_text", ["source_id"])
    op.create_index("ix_agent_extracted_text_extracted_at", "agent_extracted_text", ["extracted_at"])

    op.create_table(
        "agent_audio_transcripts",
        sa.Column("transcript_id", sa.String(length=36), primary_key=True),
        sa.Column("source_id", sa.String(length=36), sa.ForeignKey("agent_source_files.source_id"), nullable=False),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("segments_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_id", name="uq_audio_transcript_source_id"),
    )
    op.create_index("ix_agent_audio_transcripts_source_id", "agent_audio_transcripts", ["source_id"])
    op.create_index("ix_agent_audio_transcripts_created_at", "agent_audio_transcripts", ["created_at"])

    op.create_table(
        "agent_email_threads",
        sa.Column("thread_id", sa.String(length=36), primary_key=True),
        sa.Column("source_id", sa.String(length=36), sa.ForeignKey("agent_source_files.source_id"), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=True),
        sa.Column("from_addr", sa.String(length=256), nullable=True),
        sa.Column("to_addrs", sa.JSON(), nullable=True),
        sa.Column("clean_text", sa.Text(), nullable=False),
        sa.Column("highlights", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_id", name="uq_email_thread_source_id"),
    )
    op.create_index("ix_agent_email_threads_source_id", "agent_email_threads", ["source_id"])
    op.create_index("ix_agent_email_threads_subject", "agent_email_threads", ["subject"])
    op.create_index("ix_agent_email_threads_from_addr", "agent_email_threads", ["from_addr"])
    op.create_index("ix_agent_email_threads_created_at", "agent_email_threads", ["created_at"])

    op.create_table(
        "agent_obligations",
        sa.Column("obligation_id", sa.String(length=36), primary_key=True),
        sa.Column("case_id", sa.String(length=36), sa.ForeignKey("agent_contract_cases.case_id"), nullable=False),
        sa.Column("obligation_type", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="VND"),
        sa.Column("amount_value", sa.Float(), nullable=True),
        sa.Column("amount_percent", sa.Float(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("condition_text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("signature", sa.String(length=64), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("signature", name="uq_obligation_signature"),
    )
    op.create_index("ix_agent_obligations_case_id", "agent_obligations", ["case_id"])
    op.create_index("ix_agent_obligations_obligation_type", "agent_obligations", ["obligation_type"])
    op.create_index("ix_agent_obligations_due_date", "agent_obligations", ["due_date"])
    op.create_index("ix_agent_obligations_confidence", "agent_obligations", ["confidence"])
    op.create_index("ix_agent_obligations_signature", "agent_obligations", ["signature"])
    op.create_index("ix_agent_obligations_created_at", "agent_obligations", ["created_at"])

    op.create_table(
        "agent_obligation_evidence",
        sa.Column("evidence_id", sa.String(length=36), primary_key=True),
        sa.Column("obligation_id", sa.String(length=36), sa.ForeignKey("agent_obligations.obligation_id"), nullable=False),
        sa.Column("source_id", sa.String(length=36), sa.ForeignKey("agent_source_files.source_id"), nullable=False),
        sa.Column("evidence_type", sa.String(length=32), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_obligation_evidence_obligation_id", "agent_obligation_evidence", ["obligation_id"])
    op.create_index("ix_agent_obligation_evidence_source_id", "agent_obligation_evidence", ["source_id"])
    op.create_index("ix_agent_obligation_evidence_evidence_type", "agent_obligation_evidence", ["evidence_type"])
    op.create_index("ix_agent_obligation_evidence_created_at", "agent_obligation_evidence", ["created_at"])

    op.create_table(
        "agent_erpx_links",
        sa.Column("link_id", sa.String(length=36), primary_key=True),
        sa.Column("case_id", sa.String(length=36), sa.ForeignKey("agent_contract_cases.case_id"), nullable=True),
        sa.Column("obligation_id", sa.String(length=36), sa.ForeignKey("agent_obligations.obligation_id"), nullable=True),
        sa.Column("erpx_object_type", sa.String(length=32), nullable=False),
        sa.Column("erpx_object_id", sa.String(length=64), nullable=False),
        sa.Column("match_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_erpx_links_case_id", "agent_erpx_links", ["case_id"])
    op.create_index("ix_agent_erpx_links_obligation_id", "agent_erpx_links", ["obligation_id"])
    op.create_index("ix_agent_erpx_links_erpx_object_type", "agent_erpx_links", ["erpx_object_type"])
    op.create_index("ix_agent_erpx_links_erpx_object_id", "agent_erpx_links", ["erpx_object_id"])
    op.create_index("ix_agent_erpx_links_created_at", "agent_erpx_links", ["created_at"])

    op.create_table(
        "agent_proposals",
        sa.Column("proposal_id", sa.String(length=36), primary_key=True),
        sa.Column("case_id", sa.String(length=36), sa.ForeignKey("agent_contract_cases.case_id"), nullable=False),
        sa.Column("obligation_id", sa.String(length=36), sa.ForeignKey("agent_obligations.obligation_id"), nullable=True),
        sa.Column("proposal_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("risk_level", sa.String(length=8), nullable=False, server_default="med"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("proposal_key", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("proposal_key", name="uq_proposal_key"),
    )
    op.create_index("ix_agent_proposals_case_id", "agent_proposals", ["case_id"])
    op.create_index("ix_agent_proposals_obligation_id", "agent_proposals", ["obligation_id"])
    op.create_index("ix_agent_proposals_proposal_type", "agent_proposals", ["proposal_type"])
    op.create_index("ix_agent_proposals_risk_level", "agent_proposals", ["risk_level"])
    op.create_index("ix_agent_proposals_confidence", "agent_proposals", ["confidence"])
    op.create_index("ix_agent_proposals_status", "agent_proposals", ["status"])
    op.create_index("ix_agent_proposals_proposal_key", "agent_proposals", ["proposal_key"])
    op.create_index("ix_agent_proposals_run_id", "agent_proposals", ["run_id"])
    op.create_index("ix_agent_proposals_created_at", "agent_proposals", ["created_at"])

    op.create_table(
        "agent_approvals",
        sa.Column("approval_id", sa.String(length=36), primary_key=True),
        sa.Column("proposal_id", sa.String(length=36), sa.ForeignKey("agent_proposals.proposal_id"), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("actor_user_id", sa.String(length=64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_approvals_proposal_id", "agent_approvals", ["proposal_id"])
    op.create_index("ix_agent_approvals_decision", "agent_approvals", ["decision"])
    op.create_index("ix_agent_approvals_actor_user_id", "agent_approvals", ["actor_user_id"])
    op.create_index("ix_agent_approvals_created_at", "agent_approvals", ["created_at"])

    op.create_table(
        "agent_audit_log",
        sa.Column("audit_id", sa.String(length=36), primary_key=True),
        sa.Column("actor_user_id", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("object_id", sa.String(length=64), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_audit_log_actor_user_id", "agent_audit_log", ["actor_user_id"])
    op.create_index("ix_agent_audit_log_action", "agent_audit_log", ["action"])
    op.create_index("ix_agent_audit_log_object_type", "agent_audit_log", ["object_type"])
    op.create_index("ix_agent_audit_log_object_id", "agent_audit_log", ["object_id"])
    op.create_index("ix_agent_audit_log_run_id", "agent_audit_log", ["run_id"])
    op.create_index("ix_agent_audit_log_ts", "agent_audit_log", ["ts"])


def downgrade() -> None:
    op.drop_table("agent_audit_log")
    op.drop_table("agent_approvals")
    op.drop_table("agent_proposals")
    op.drop_table("agent_erpx_links")
    op.drop_table("agent_obligation_evidence")
    op.drop_table("agent_obligations")
    op.drop_table("agent_email_threads")
    op.drop_table("agent_audio_transcripts")
    op.drop_table("agent_extracted_text")
    op.drop_table("agent_source_files")
    op.drop_table("agent_contract_cases")

