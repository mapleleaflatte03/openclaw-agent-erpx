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


class AgentContractCase(Base):
    __tablename__ = "agent_contract_cases"

    case_id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    case_key: Mapped[str] = mapped_column(sa.String(128), unique=True, index=True)

    partner_name: Mapped[str | None] = mapped_column(sa.String(256), nullable=True, index=True)
    partner_tax_id: Mapped[str | None] = mapped_column(sa.String(32), nullable=True, index=True)
    contract_code: Mapped[str | None] = mapped_column(sa.String(128), nullable=True, index=True)

    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="open", index=True)
    meta: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)

    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )
    updated_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )

    sources: Mapped[list[AgentSourceFile]] = relationship("AgentSourceFile", back_populates="case")
    obligations: Mapped[list[AgentObligation]] = relationship("AgentObligation", back_populates="case")
    proposals: Mapped[list[AgentProposal]] = relationship("AgentProposal", back_populates="case")


class AgentSourceFile(Base):
    __tablename__ = "agent_source_files"

    source_id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(
        sa.String(36), sa.ForeignKey("agent_contract_cases.case_id"), nullable=True, index=True
    )

    source_type: Mapped[str] = mapped_column(sa.String(32), index=True)  # contract|email|audio
    source_uri: Mapped[str] = mapped_column(sa.String(512))
    stored_uri: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)
    file_hash: Mapped[str] = mapped_column(sa.String(64), index=True)
    size_bytes: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    meta: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)

    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )

    __table_args__ = (
        sa.UniqueConstraint("file_hash", "source_type", name="uq_source_hash_type"),
    )

    case: Mapped[AgentContractCase | None] = relationship("AgentContractCase", back_populates="sources")
    extracted_text: Mapped[AgentExtractedText | None] = relationship(
        "AgentExtractedText", back_populates="source"
    )
    email_thread: Mapped[AgentEmailThread | None] = relationship("AgentEmailThread", back_populates="source")
    audio_transcript: Mapped[AgentAudioTranscript | None] = relationship(
        "AgentAudioTranscript", back_populates="source"
    )


class AgentExtractedText(Base):
    __tablename__ = "agent_extracted_text"

    text_id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("agent_source_files.source_id"), unique=True, index=True
    )

    engine: Mapped[str] = mapped_column(sa.String(32))  # pdfplumber|tesseract|whisper|email
    text: Mapped[str] = mapped_column(sa.Text)
    page_confidence: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    extracted_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )

    source: Mapped[AgentSourceFile] = relationship("AgentSourceFile", back_populates="extracted_text")


class AgentAudioTranscript(Base):
    __tablename__ = "agent_audio_transcripts"

    transcript_id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("agent_source_files.source_id"), unique=True, index=True
    )

    engine: Mapped[str] = mapped_column(sa.String(32))
    transcript: Mapped[str] = mapped_column(sa.Text)
    segments_json: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )

    source: Mapped[AgentSourceFile] = relationship("AgentSourceFile", back_populates="audio_transcript")


class AgentEmailThread(Base):
    __tablename__ = "agent_email_threads"

    thread_id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("agent_source_files.source_id"), unique=True, index=True
    )

    subject: Mapped[str | None] = mapped_column(sa.String(512), nullable=True, index=True)
    from_addr: Mapped[str | None] = mapped_column(sa.String(256), nullable=True, index=True)
    to_addrs: Mapped[list[str] | None] = mapped_column(sa.JSON, nullable=True)
    clean_text: Mapped[str] = mapped_column(sa.Text)
    highlights: Mapped[list[dict] | None] = mapped_column(sa.JSON, nullable=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )

    source: Mapped[AgentSourceFile] = relationship("AgentSourceFile", back_populates="email_thread")


class AgentObligation(Base):
    __tablename__ = "agent_obligations"

    obligation_id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(sa.String(36), sa.ForeignKey("agent_contract_cases.case_id"), index=True)

    obligation_type: Mapped[str] = mapped_column(sa.String(64), index=True)
    currency: Mapped[str] = mapped_column(sa.String(8), nullable=False, default="VND")
    amount_value: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    amount_percent: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    due_date: Mapped[sa.Date | None] = mapped_column(sa.Date, nullable=True, index=True)
    condition_text: Mapped[str] = mapped_column(sa.Text)
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0, index=True)
    risk_level: Mapped[str] = mapped_column(sa.String(8), nullable=False, default="medium", index=True)

    signature: Mapped[str] = mapped_column(sa.String(64), unique=True, index=True)
    meta: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )

    case: Mapped[AgentContractCase] = relationship("AgentContractCase", back_populates="obligations")
    evidence: Mapped[list[AgentObligationEvidence]] = relationship(
        "AgentObligationEvidence", back_populates="obligation"
    )


class AgentObligationEvidence(Base):
    __tablename__ = "agent_obligation_evidence"

    evidence_id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    obligation_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("agent_obligations.obligation_id"), index=True
    )
    source_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("agent_source_files.source_id"), index=True
    )

    evidence_type: Mapped[str] = mapped_column(sa.String(32), index=True)  # quote|email|audio
    snippet: Mapped[str] = mapped_column(sa.Text)
    meta: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )

    obligation: Mapped[AgentObligation] = relationship("AgentObligation", back_populates="evidence")


class AgentErpXLink(Base):
    __tablename__ = "agent_erpx_links"

    link_id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(
        sa.String(36), sa.ForeignKey("agent_contract_cases.case_id"), nullable=True, index=True
    )
    obligation_id: Mapped[str | None] = mapped_column(
        sa.String(36), sa.ForeignKey("agent_obligations.obligation_id"), nullable=True, index=True
    )
    erpx_object_type: Mapped[str] = mapped_column(sa.String(32), index=True)
    erpx_object_id: Mapped[str] = mapped_column(sa.String(64), index=True)
    match_confidence: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    meta: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )


class AgentProposal(Base):
    __tablename__ = "agent_proposals"

    proposal_id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(sa.String(36), sa.ForeignKey("agent_contract_cases.case_id"), index=True)
    obligation_id: Mapped[str | None] = mapped_column(
        sa.String(36), sa.ForeignKey("agent_obligations.obligation_id"), nullable=True, index=True
    )

    proposal_type: Mapped[str] = mapped_column(sa.String(64), index=True)
    title: Mapped[str] = mapped_column(sa.String(512))
    summary: Mapped[str] = mapped_column(sa.Text)
    details: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    risk_level: Mapped[str] = mapped_column(sa.String(8), nullable=False, default="medium", index=True)
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0, index=True)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="pending", index=True)

    created_by: Mapped[str] = mapped_column(sa.String(64), nullable=False, default="system", index=True)
    tier: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=3, index=True)
    evidence_summary_hash: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)

    proposal_key: Mapped[str] = mapped_column(sa.String(128), unique=True, index=True)
    run_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True, index=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )

    case: Mapped[AgentContractCase] = relationship("AgentContractCase", back_populates="proposals")
    approvals: Mapped[list[AgentApproval]] = relationship("AgentApproval", back_populates="proposal")


class AgentApproval(Base):
    __tablename__ = "agent_approvals"

    approval_id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(sa.String(36), sa.ForeignKey("agent_proposals.proposal_id"), index=True)

    decision: Mapped[str] = mapped_column(sa.String(16), index=True)  # approve|reject
    actor_user_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    approver_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    evidence_ack: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    decided_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, unique=True, index=True)
    note: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )

    proposal: Mapped[AgentProposal] = relationship("AgentProposal", back_populates="approvals")


class AgentAuditLog(Base):
    __tablename__ = "agent_audit_log"

    audit_id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    actor_user_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    action: Mapped[str] = mapped_column(sa.String(64), index=True)
    object_type: Mapped[str] = mapped_column(sa.String(64), index=True)
    object_id: Mapped[str] = mapped_column(sa.String(64), index=True)
    before: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    run_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True, index=True)
    ts: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )


class TierBFeedback(Base):
    __tablename__ = "tier_b_feedback"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    obligation_id: Mapped[str] = mapped_column(sa.String(36), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    feedback_type: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    delta: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )


# ---------------------------------------------------------------------------
# Accounting domain  (OpenClaw ERP-X AI Kế toán)
# ---------------------------------------------------------------------------

class AcctVoucher(Base):
    """Cached copy of an ERP voucher – READ-ONLY mirror for analysis."""
    __tablename__ = "acct_vouchers"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    erp_voucher_id: Mapped[str] = mapped_column(sa.String(64), unique=True, index=True)
    voucher_no: Mapped[str] = mapped_column(sa.String(64), index=True)
    voucher_type: Mapped[str] = mapped_column(
        sa.String(32), index=True,
        comment="sell_invoice|buy_invoice|receipt|payment|other",
    )
    date: Mapped[str] = mapped_column(sa.String(10), index=True, comment="YYYY-MM-DD")
    amount: Mapped[float] = mapped_column(sa.Float, nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), server_default="VND")
    partner_name: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    partner_tax_code: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    has_attachment: Mapped[bool] = mapped_column(sa.Boolean, server_default="0")
    raw_payload: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True, comment="Original document JSON")
    source: Mapped[str | None] = mapped_column(
        sa.String(64), nullable=True, server_default="erpx",
        comment="mock_vn_fixture|erpx|ocr_upload",
    )
    type_hint: Mapped[str | None] = mapped_column(
        sa.String(32), nullable=True,
        comment="invoice_vat|cash_disbursement|cash_receipt|payroll|other",
    )
    classification_tag: Mapped[str | None] = mapped_column(
        sa.String(32), nullable=True, index=True,
        comment="PURCHASE_INVOICE|SALES_INVOICE|CASH_DISBURSEMENT|CASH_RECEIPT|PAYROLL|FIXED_ASSET|OTHER",
    )
    synced_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False,
    )
    run_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True, index=True)


class AcctBankTransaction(Base):
    """Bank statement line – READ-ONLY mirror for reconciliation."""
    __tablename__ = "acct_bank_transactions"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    bank_tx_ref: Mapped[str] = mapped_column(sa.String(128), unique=True, index=True)
    bank_account: Mapped[str] = mapped_column(sa.String(64), index=True)
    date: Mapped[str] = mapped_column(sa.String(10), index=True, comment="YYYY-MM-DD")
    amount: Mapped[float] = mapped_column(sa.Float, nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), server_default="VND")
    counterparty: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    memo: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    matched_voucher_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True, index=True)
    match_status: Mapped[str] = mapped_column(
        sa.String(16), server_default="unmatched", index=True,
        comment="unmatched|matched|anomaly",
    )
    synced_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False,
    )
    run_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True, index=True)


class AcctJournalProposal(Base):
    """AI-suggested journal entry – awaits human approve/reject (READ-ONLY principle)."""
    __tablename__ = "acct_journal_proposals"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    voucher_id: Mapped[str] = mapped_column(sa.String(36), index=True, comment="FK to acct_vouchers.id")
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False, comment="0.0–1.0")
    reasoning: Mapped[str | None] = mapped_column(sa.Text, nullable=True, comment="LLM reasoning trace")
    status: Mapped[str] = mapped_column(
        sa.String(16), server_default="pending", index=True,
        comment="pending|approved|rejected",
    )
    reviewed_by: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    reviewed_at: Mapped[sa.DateTime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True,
    )
    run_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True, index=True)

    lines: Mapped[list[AcctJournalLine]] = relationship(
        "AcctJournalLine", back_populates="proposal", cascade="all, delete-orphan",
    )


class AcctJournalLine(Base):
    """A single debit/credit line inside a journal proposal."""
    __tablename__ = "acct_journal_lines"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("acct_journal_proposals.id"), nullable=False, index=True,
    )
    account_code: Mapped[str] = mapped_column(sa.String(20), nullable=False, comment="e.g. 111, 131, 511")
    account_name: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    debit: Mapped[float] = mapped_column(sa.Float, server_default="0")
    credit: Mapped[float] = mapped_column(sa.Float, server_default="0")

    proposal: Mapped[AcctJournalProposal] = relationship("AcctJournalProposal", back_populates="lines")


class AcctAnomalyFlag(Base):
    """Anomaly detected during bank reconciliation or soft-checks."""
    __tablename__ = "acct_anomaly_flags"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    anomaly_type: Mapped[str] = mapped_column(
        sa.String(32), index=True,
        comment="amount_mismatch|date_gap|unmatched_tx|duplicate_voucher|other",
    )
    severity: Mapped[str] = mapped_column(
        sa.String(16), server_default="medium", index=True,
        comment="low|medium|high|critical",
    )
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    voucher_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True, index=True)
    bank_tx_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True, index=True)
    resolution: Mapped[str] = mapped_column(
        sa.String(16), server_default="open", index=True,
        comment="open|resolved|ignored",
    )
    resolved_by: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    resolved_at: Mapped[sa.DateTime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True,
    )
    run_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True, index=True)


# ---------------------------------------------------------------------------
# Phase 2: Extended accounting domain models
# ---------------------------------------------------------------------------

class AcctSoftCheckResult(Base):
    """Aggregate result of a soft-check run for a given period."""
    __tablename__ = "acct_soft_check_results"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    period: Mapped[str] = mapped_column(sa.String(7), index=True, comment="YYYY-MM")
    total_checks: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    passed: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    warnings: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    errors: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="0")
    score: Mapped[float] = mapped_column(
        sa.Float, nullable=False, server_default="0",
        comment="Health score 0.0–1.0",
    )
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True,
    )
    run_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True, index=True)

    issues: Mapped[list[AcctValidationIssue]] = relationship(
        "AcctValidationIssue", back_populates="check_result", cascade="all, delete-orphan",
    )


class AcctValidationIssue(Base):
    """Individual validation issue found during soft-check."""
    __tablename__ = "acct_validation_issues"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    check_result_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("acct_soft_check_results.id"), nullable=False, index=True,
    )
    rule_code: Mapped[str] = mapped_column(
        sa.String(64), nullable=False, index=True,
        comment="e.g. MISSING_ATTACHMENT, JOURNAL_IMBALANCED, OVERDUE_INVOICE, DUPLICATE_VOUCHER",
    )
    severity: Mapped[str] = mapped_column(
        sa.String(16), server_default="warning", index=True,
        comment="info|warning|error|critical",
    )
    message: Mapped[str] = mapped_column(sa.Text, nullable=False)
    erp_ref: Mapped[str | None] = mapped_column(sa.String(128), nullable=True, index=True, comment="e.g. voucher_id, journal_id")
    details: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    resolution: Mapped[str] = mapped_column(
        sa.String(16), server_default="open", index=True,
        comment="open|resolved|ignored",
    )
    resolved_by: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    resolved_at: Mapped[sa.DateTime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True,
    )

    check_result: Mapped[AcctSoftCheckResult] = relationship("AcctSoftCheckResult", back_populates="issues")


class AcctReportSnapshot(Base):
    """Saved snapshot of a generated accounting report (VAT, P&L, trial-balance)."""
    __tablename__ = "acct_report_snapshots"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    report_type: Mapped[str] = mapped_column(
        sa.String(32), nullable=False, index=True,
        comment="vat_list|trial_balance|pnl|balance_sheet",
    )
    period: Mapped[str] = mapped_column(sa.String(7), index=True, comment="YYYY-MM")
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default="1")
    file_uri: Mapped[str | None] = mapped_column(sa.String(512), nullable=True, comment="S3/MinIO URI")
    summary_json: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True, comment="Quick numbers for dashboard")
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True,
    )
    run_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True, index=True)


class AcctCashflowForecast(Base):
    """Cash-flow forecast line – projected in/out per day/week."""
    __tablename__ = "acct_cashflow_forecasts"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    forecast_date: Mapped[str] = mapped_column(sa.String(10), nullable=False, index=True, comment="YYYY-MM-DD")
    direction: Mapped[str] = mapped_column(
        sa.String(8), nullable=False, index=True,
        comment="inflow|outflow",
    )
    amount: Mapped[float] = mapped_column(sa.Float, nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), server_default="VND")
    source_type: Mapped[str] = mapped_column(
        sa.String(32), nullable=False,
        comment="invoice_receivable|invoice_payable|recurring|manual",
    )
    source_ref: Mapped[str | None] = mapped_column(sa.String(128), nullable=True, index=True)
    confidence: Mapped[float] = mapped_column(sa.Float, server_default="1.0", comment="0.0–1.0")
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True,
    )
    run_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True, index=True)


class AcctQnaAudit(Base):
    """Audit trail for accounting Q&A queries answered by the AI."""
    __tablename__ = "acct_qna_audits"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True)
    question: Mapped[str] = mapped_column(sa.Text, nullable=False)
    answer: Mapped[str] = mapped_column(sa.Text, nullable=False)
    sources: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True, comment="KB doc refs used")
    user_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, index=True)
    feedback: Mapped[str | None] = mapped_column(
        sa.String(16), nullable=True,
        comment="helpful|not_helpful|null",
    )
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True,
    )
    run_id: Mapped[str | None] = mapped_column(sa.String(36), nullable=True, index=True)
