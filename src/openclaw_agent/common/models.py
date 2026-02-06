from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from openclaw_agent.common.db import Base


def _id() -> sa.Column:
    return sa.Column(sa.String(36), primary_key=True)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    run_id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    run_type: Mapped[str] = mapped_column(sa.String(64), index=True)
    trigger_type: Mapped[str] = mapped_column(sa.String(32), index=True)
    requested_by: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    status: Mapped[str] = mapped_column(sa.String(16), index=True)

    # Idempotency for run creation (unique per "logical run request")
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), unique=True, index=True)

    # In/out cursors: delta inputs and workflow outputs (JSON-friendly)
    cursor_in: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    cursor_out: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)

    started_at: Mapped[sa.DateTime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    finished_at: Mapped[sa.DateTime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    stats: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)

    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )

    tasks: Mapped[list[AgentTask]] = relationship(back_populates="run")


class AgentTask(Base):
    __tablename__ = "agent_tasks"

    task_id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(sa.String(36), sa.ForeignKey("agent_runs.run_id"), index=True)

    task_name: Mapped[str] = mapped_column(sa.String(128), index=True)
    status: Mapped[str] = mapped_column(sa.String(16), index=True)

    input_ref: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    output_ref: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    started_at: Mapped[sa.DateTime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    finished_at: Mapped[sa.DateTime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )

    run: Mapped[AgentRun] = relationship(back_populates="tasks")


class AgentLog(Base):
    __tablename__ = "agent_logs"

    log_id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    task_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True, index=True)

    level: Mapped[str] = mapped_column(sa.String(8), index=True)
    message: Mapped[str] = mapped_column(sa.Text)
    context: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    ts: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )


class AgentAttachment(Base):
    __tablename__ = "agent_attachments"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    erp_object_type: Mapped[str] = mapped_column(sa.String(32), index=True)
    erp_object_id: Mapped[str] = mapped_column(sa.String(64), index=True)

    file_uri: Mapped[str] = mapped_column(sa.String(512))
    file_hash: Mapped[str] = mapped_column(sa.String(64), index=True)
    matched_by: Mapped[str] = mapped_column(sa.String(16))

    run_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )

    __table_args__ = (
        sa.UniqueConstraint("file_hash", "erp_object_type", "erp_object_id", name="uq_attach_hash_object"),
    )


class AgentExport(Base):
    __tablename__ = "agent_exports"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    export_type: Mapped[str] = mapped_column(sa.String(32), index=True)
    period: Mapped[str] = mapped_column(sa.String(7), index=True)  # YYYY-MM

    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    file_uri: Mapped[str] = mapped_column(sa.String(512))
    checksum: Mapped[str] = mapped_column(sa.String(64))

    run_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )

    __table_args__ = (
        sa.UniqueConstraint("export_type", "period", "version", name="uq_export_type_period_version"),
    )


class AgentException(Base):
    __tablename__ = "agent_exceptions"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    exception_type: Mapped[str] = mapped_column(sa.String(64), index=True)
    severity: Mapped[str] = mapped_column(sa.String(8), index=True)  # low|med|high

    erp_refs: Mapped[dict] = mapped_column(sa.JSON)
    summary: Mapped[str] = mapped_column(sa.Text)
    details: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)

    signature: Mapped[str] = mapped_column(sa.String(64), unique=True, index=True)

    run_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )


class AgentReminderLog(Base):
    __tablename__ = "agent_reminder_log"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    customer_id: Mapped[str] = mapped_column(sa.String(64), index=True)
    invoice_id: Mapped[str] = mapped_column(sa.String(64), index=True)
    reminder_stage: Mapped[int] = mapped_column(sa.Integer, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(sa.String(16))
    sent_to: Mapped[str] = mapped_column(sa.String(256))
    sent_at: Mapped[sa.DateTime] = mapped_column(sa.DateTime(timezone=True), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(sa.String(36), index=True)

    # windowed idempotency key (policy-defined)
    policy_key: Mapped[str] = mapped_column(sa.String(128), unique=True, index=True)


class AgentCloseTask(Base):
    __tablename__ = "agent_close_tasks"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    period: Mapped[str] = mapped_column(sa.String(7), index=True)  # YYYY-MM
    task_name: Mapped[str] = mapped_column(sa.String(256))
    owner_user_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    due_date: Mapped[sa.Date] = mapped_column(sa.Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(sa.String(16), index=True)  # todo|doing|done|blocked
    last_nudged_at: Mapped[sa.DateTime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    __table_args__ = (
        sa.UniqueConstraint("period", "task_name", name="uq_close_period_task_name"),
    )


class AgentEvidencePack(Base):
    __tablename__ = "agent_evidence_packs"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    issue_key: Mapped[str] = mapped_column(sa.String(128), index=True)
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)

    pack_uri: Mapped[str] = mapped_column(sa.String(512))
    index_json: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)

    run_id: Mapped[str] = mapped_column(sa.String(36), index=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )

    __table_args__ = (
        sa.UniqueConstraint("issue_key", "version", name="uq_evidence_issue_version"),
    )


class AgentKbDoc(Base):
    __tablename__ = "agent_kb_docs"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    doc_type: Mapped[str] = mapped_column(sa.String(32), index=True)  # law|process|template
    title: Mapped[str] = mapped_column(sa.String(512), index=True)
    version: Mapped[str] = mapped_column(sa.String(64), index=True)
    effective_date: Mapped[sa.Date | None] = mapped_column(sa.Date, nullable=True, index=True)
    source_uri: Mapped[str] = mapped_column(sa.String(512))
    text_uri: Mapped[str] = mapped_column(sa.String(512))
    indexed_at: Mapped[sa.DateTime] = mapped_column(sa.DateTime(timezone=True), nullable=False, index=True)
    file_hash: Mapped[str] = mapped_column(sa.String(64), index=True)
    meta: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)

    __table_args__ = (
        sa.UniqueConstraint("file_hash", "version", name="uq_kb_filehash_version"),
    )


class AgentFeedback(Base):
    __tablename__ = "agent_feedback"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    task_type: Mapped[str] = mapped_column(sa.String(64), index=True)
    item_ref: Mapped[dict] = mapped_column(sa.JSON)
    label: Mapped[str] = mapped_column(sa.String(16), index=True)  # correct|wrong|partial
    error_type: Mapped[str | None] = mapped_column(sa.String(32), nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )
