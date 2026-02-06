from __future__ import annotations

import os
from typing import Any

import redis
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from openclaw_agent.agent_worker.celery_app import celery_app
from openclaw_agent.common.db import db_session, make_engine
from openclaw_agent.common.logging import configure_logging, get_logger
from openclaw_agent.common.models import (
    AgentApproval,
    AgentAttachment,
    AgentAuditLog,
    AgentCloseTask,
    AgentContractCase,
    AgentEvidencePack,
    AgentException,
    AgentExport,
    AgentKbDoc,
    AgentLog,
    AgentObligation,
    AgentProposal,
    AgentReminderLog,
    AgentRun,
    AgentSourceFile,
    AgentTask,
)
from openclaw_agent.common.settings import Settings, get_settings
from openclaw_agent.common.storage import ensure_buckets
from openclaw_agent.common.utils import make_idempotency_key, new_uuid, utcnow
from openclaw_agent.openclaw.config import load_workflows

log = get_logger("agent-service")

ENGINE: Engine | None = None


def _auth(settings: Settings, api_key: str | None) -> None:
    if settings.agent_auth_mode == "none":
        return
    if not api_key or api_key != settings.agent_api_key:
        raise HTTPException(status_code=401, detail="unauthorized")


def get_engine_dep(settings: Settings = Depends(get_settings)) -> Engine:
    if ENGINE is not None:
        return ENGINE
    # Fallback for import-time usage (tests).
    return make_engine(settings.agent_db_dsn)


def get_session(engine: Engine = Depends(get_engine_dep)) -> Session:
    with db_session(engine) as s:
        yield s


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    _auth(settings, x_api_key)


app = FastAPI(title="OpenClaw Agent Service", version=os.getenv("APP_VERSION", "0.1.0"))


@app.on_event("startup")
def _startup() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    global ENGINE
    ENGINE = make_engine(settings.agent_db_dsn)
    ensure_buckets(settings)
    log.info("startup", agent_env=settings.agent_env)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz(settings: Settings = Depends(get_settings), engine: Engine = Depends(get_engine_dep)) -> dict[str, Any]:
    try:
        with engine.connect() as c:
            c.exec_driver_sql("SELECT 1")
        r = redis.Redis.from_url(settings.redis_url)
        r.ping()
        ensure_buckets(settings)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"status": "ready"}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics(settings: Settings = Depends(get_settings), engine: Engine = Depends(get_engine_dep)) -> PlainTextResponse:
    registry = CollectorRegistry()

    g_queue = Gauge("agent_queue_backlog", "Redis backlog (LLEN) per queue", ["queue"], registry=registry)
    g_runs = Gauge("agent_runs", "Runs by status/run_type", ["status", "run_type"], registry=registry)
    g_tasks = Gauge("agent_tasks", "Tasks by status/task_name", ["status", "task_name"], registry=registry)
    g_ocr_timeouts = Gauge(
        "agent_ocr_timeouts_last_5m", "OCR timeouts in last 5 minutes", registry=registry
    )
    g_mismatch = Gauge(
        "agent_attachment_mismatch_last_5m", "Attachment low-confidence mismatches in last 5 minutes", registry=registry
    )

    # Redis backlog (best-effort; depends on Celery transport)
    r = redis.Redis.from_url(settings.redis_url)
    for q in ["default", "ocr", "export", "io", "index"]:
        try:
            g_queue.labels(queue=q).set(r.llen(q))
        except Exception:
            g_queue.labels(queue=q).set(0)

    with engine.connect() as c:
        # Runs by status/run_type
        rows = c.execute(sa_text("SELECT status, run_type, COUNT(*) FROM agent_runs GROUP BY status, run_type"))
        for status, run_type, cnt in rows:
            g_runs.labels(status=status, run_type=run_type).set(cnt)

        # Tasks by status/task_name
        rows = c.execute(
            sa_text("SELECT status, task_name, COUNT(*) FROM agent_tasks GROUP BY status, task_name")
        )
        for status, task_name, cnt in rows:
            g_tasks.labels(status=status, task_name=task_name).set(cnt)

        # OCR timeouts last 5m (heuristic)
        rows = c.execute(
            sa_text(
                "SELECT COUNT(*) FROM agent_tasks "
                "WHERE status='failed' AND (error ILIKE '%timeout%' OR error ILIKE '%TimeLimit%') "
                "AND created_at > (NOW() - INTERVAL '5 minutes')"
            )
        )
        g_ocr_timeouts.set(int(rows.scalar() or 0))

        rows = c.execute(
            sa_text(
                "SELECT COUNT(*) FROM agent_exceptions "
                "WHERE exception_type='attachment_mismatch' "
                "AND created_at > (NOW() - INTERVAL '5 minutes')"
            )
        )
        g_mismatch.set(int(rows.scalar() or 0))

    data = generate_latest(registry)
    return PlainTextResponse(content=data.decode("utf-8"), media_type=CONTENT_TYPE_LATEST)


def sa_text(sql: str):
    # local import to keep agent-service fast import in unit tests
    import sqlalchemy as sa

    return sa.text(sql)


@app.post("/agent/v1/runs", dependencies=[Depends(require_api_key)])
def create_run(
    body: dict[str, Any],
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    run_type = body.get("run_type")
    trigger_type = body.get("trigger_type")
    payload = body.get("payload") or {}
    requested_by = body.get("requested_by")
    if run_type not in {
        "attachment",
        "tax_export",
        "working_papers",
        "soft_checks",
        "ar_dunning",
        "close_checklist",
        "evidence_pack",
        "kb_index",
        "contract_obligation",
    }:
        raise HTTPException(status_code=400, detail="invalid run_type")
    if trigger_type not in {"schedule", "event", "manual"}:
        raise HTTPException(status_code=400, detail="invalid trigger_type")

    idem = request.headers.get("Idempotency-Key")
    if not idem:
        idem = make_idempotency_key(run_type, trigger_type, payload)

    existing = session.execute(select(AgentRun).where(AgentRun.idempotency_key == idem)).scalar_one_or_none()
    if existing:
        return {"run_id": existing.run_id, "status": existing.status, "idempotency_key": existing.idempotency_key}

    run = AgentRun(
        run_id=new_uuid(),
        run_type=run_type,
        trigger_type=trigger_type,
        requested_by=requested_by,
        status="queued",
        idempotency_key=idem,
        cursor_in=payload,
        cursor_out=None,
        started_at=None,
        finished_at=None,
        stats=None,
    )
    session.add(run)

    # Pre-create tasks per workflow definition for UI visibility (queued)
    workflows = load_workflows()
    wf = next((w for w in workflows.values() if w.run_type == run_type), None)
    if wf:
        for step in wf.steps:
            session.add(
                AgentTask(
                    task_id=new_uuid(),
                    run_id=run.run_id,
                    task_name=step.name,
                    status="queued",
                    input_ref=payload,
                    output_ref=None,
                    error=None,
                    started_at=None,
                    finished_at=None,
                )
            )

    session.flush()

    queue_map = {
        "attachment": "ocr",
        "kb_index": "ocr",
        "tax_export": "export",
        "working_papers": "export",
        "soft_checks": "default",
        "close_checklist": "default",
        "ar_dunning": "io",
        "evidence_pack": "io",
        "contract_obligation": "ocr",
    }
    celery_app.send_task(
        "openclaw_agent.agent_worker.tasks.dispatch_run",
        args=[run.run_id],
        queue=queue_map.get(run_type, "default"),
    )
    log.info("run_queued", run_id=run.run_id, run_type=run_type, trigger_type=trigger_type)
    return {"run_id": run.run_id, "status": run.status, "idempotency_key": run.idempotency_key}


@app.get("/agent/v1/runs", dependencies=[Depends(require_api_key)])
def list_runs(
    limit: int = 50,
    offset: int = 0,
    run_type: str | None = None,
    status: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    q = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(min(limit, 200)).offset(max(offset, 0))
    if run_type:
        q = q.where(AgentRun.run_type == run_type)
    if status:
        q = q.where(AgentRun.status == status)
    rows = session.execute(q).scalars().all()
    return {
        "items": [
            {
                "run_id": r.run_id,
                "run_type": r.run_type,
                "trigger_type": r.trigger_type,
                "requested_by": r.requested_by,
                "status": r.status,
                "cursor_in": r.cursor_in,
                "cursor_out": r.cursor_out,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "stats": r.stats,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


@app.get("/agent/v1/runs/{run_id}", dependencies=[Depends(require_api_key)])
def get_run(run_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    r = session.get(AgentRun, run_id)
    if not r:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "run_id": r.run_id,
        "run_type": r.run_type,
        "trigger_type": r.trigger_type,
        "requested_by": r.requested_by,
        "status": r.status,
        "cursor_in": r.cursor_in,
        "cursor_out": r.cursor_out,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "stats": r.stats,
        "created_at": r.created_at,
    }


@app.get("/agent/v1/tasks", dependencies=[Depends(require_api_key)])
def list_tasks(run_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    rows = session.execute(
        select(AgentTask).where(AgentTask.run_id == run_id).order_by(AgentTask.created_at.asc())
    ).scalars().all()
    return {
        "items": [
            {
                "task_id": t.task_id,
                "run_id": t.run_id,
                "task_name": t.task_name,
                "status": t.status,
                "input_ref": t.input_ref,
                "output_ref": t.output_ref,
                "error": t.error,
                "started_at": t.started_at,
                "finished_at": t.finished_at,
                "created_at": t.created_at,
            }
            for t in rows
        ]
    }


@app.get("/agent/v1/logs", dependencies=[Depends(require_api_key)])
def list_logs(run_id: str, limit: int = 200, session: Session = Depends(get_session)) -> dict[str, Any]:
    rows = session.execute(
        select(AgentLog)
        .where(AgentLog.run_id == run_id)
        .order_by(AgentLog.ts.desc())
        .limit(min(limit, 500))
    ).scalars().all()
    return {
        "items": [
            {
                "log_id": row.log_id,
                "run_id": row.run_id,
                "task_id": row.task_id,
                "level": row.level,
                "message": row.message,
                "context": row.context,
                "ts": row.ts,
            }
            for row in rows
        ]
    }


def _insert_or_get_unique(session: Session, model, unique_filter, data: Any) -> Any:
    existing = session.execute(select(model).where(unique_filter)).scalar_one_or_none()
    if existing:
        return existing
    session.add(data)
    session.flush()
    return data


@app.post("/agent/v1/attachments", dependencies=[Depends(require_api_key)])
def post_attachment(body: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    att = AgentAttachment(
        id=new_uuid(),
        erp_object_type=body["erp_object_type"],
        erp_object_id=body["erp_object_id"],
        file_uri=body["file_uri"],
        file_hash=body["file_hash"],
        matched_by=body.get("matched_by", "rule"),
        run_id=body["run_id"],
    )
    out = _insert_or_get_unique(
        session,
        AgentAttachment,
        (AgentAttachment.file_hash == att.file_hash)
        & (AgentAttachment.erp_object_type == att.erp_object_type)
        & (AgentAttachment.erp_object_id == att.erp_object_id),
        att,
    )
    return {"id": out.id, "file_uri": out.file_uri}


@app.post("/agent/v1/exports", dependencies=[Depends(require_api_key)])
def post_export(body: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    exp = AgentExport(
        id=new_uuid(),
        export_type=body["export_type"],
        period=body["period"],
        version=int(body.get("version", 1)),
        file_uri=body["file_uri"],
        checksum=body["checksum"],
        run_id=body["run_id"],
    )
    out = _insert_or_get_unique(
        session,
        AgentExport,
        (AgentExport.export_type == exp.export_type)
        & (AgentExport.period == exp.period)
        & (AgentExport.version == exp.version),
        exp,
    )
    return {"id": out.id, "file_uri": out.file_uri}


@app.post("/agent/v1/exceptions", dependencies=[Depends(require_api_key)])
def post_exception(body: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    ex = AgentException(
        id=new_uuid(),
        exception_type=body["exception_type"],
        severity=body["severity"],
        erp_refs=body["erp_refs"],
        summary=body["summary"],
        details=body.get("details"),
        signature=body["signature"],
        run_id=body["run_id"],
    )
    out = _insert_or_get_unique(session, AgentException, (AgentException.signature == ex.signature), ex)
    return {"id": out.id}


@app.post("/agent/v1/reminders/log", dependencies=[Depends(require_api_key)])
def post_reminder_log(body: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    item = AgentReminderLog(
        id=new_uuid(),
        customer_id=body["customer_id"],
        invoice_id=body["invoice_id"],
        reminder_stage=int(body["reminder_stage"]),
        channel=body["channel"],
        sent_to=body["sent_to"],
        sent_at=body.get("sent_at") or utcnow(),
        run_id=body["run_id"],
        policy_key=body["policy_key"],
    )
    out = _insert_or_get_unique(session, AgentReminderLog, (AgentReminderLog.policy_key == item.policy_key), item)
    return {"id": out.id}


@app.post("/agent/v1/close/tasks", dependencies=[Depends(require_api_key)])
def post_close_task(body: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    item = AgentCloseTask(
        id=new_uuid(),
        period=body["period"],
        task_name=body["task_name"],
        owner_user_id=body.get("owner_user_id"),
        due_date=body["due_date"],
        status=body.get("status", "todo"),
        last_nudged_at=body.get("last_nudged_at"),
    )
    out = _insert_or_get_unique(
        session,
        AgentCloseTask,
        (AgentCloseTask.period == item.period) & (AgentCloseTask.task_name == item.task_name),
        item,
    )
    return {"id": out.id}


@app.post("/agent/v1/evidence", dependencies=[Depends(require_api_key)])
def post_evidence(body: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    item = AgentEvidencePack(
        id=new_uuid(),
        issue_key=body["issue_key"],
        version=int(body.get("version", 1)),
        pack_uri=body["pack_uri"],
        index_json=body.get("index_json"),
        run_id=body["run_id"],
    )
    out = _insert_or_get_unique(
        session,
        AgentEvidencePack,
        (AgentEvidencePack.issue_key == item.issue_key) & (AgentEvidencePack.version == item.version),
        item,
    )
    return {"id": out.id}


@app.post("/agent/v1/kb/index", dependencies=[Depends(require_api_key)])
def post_kb_doc(body: dict[str, Any], session: Session = Depends(get_session)) -> dict[str, Any]:
    item = AgentKbDoc(
        id=new_uuid(),
        doc_type=body["doc_type"],
        title=body["title"],
        version=body["version"],
        effective_date=body.get("effective_date"),
        source_uri=body["source_uri"],
        text_uri=body["text_uri"],
        indexed_at=body.get("indexed_at") or utcnow(),
        file_hash=body["file_hash"],
        meta=body.get("meta"),
    )
    out = _insert_or_get_unique(
        session,
        AgentKbDoc,
        (AgentKbDoc.file_hash == item.file_hash) & (AgentKbDoc.version == item.version),
        item,
    )
    return {"id": out.id}


@app.get("/agent/v1/contract/cases", dependencies=[Depends(require_api_key)])
def list_contract_cases(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    q = (
        select(AgentContractCase)
        .order_by(AgentContractCase.created_at.desc())
        .limit(min(limit, 200))
        .offset(max(offset, 0))
    )
    if status:
        q = q.where(AgentContractCase.status == status)
    rows = session.execute(q).scalars().all()
    return {
        "items": [
            {
                "case_id": r.case_id,
                "case_key": r.case_key,
                "partner_name": r.partner_name,
                "partner_tax_id": r.partner_tax_id,
                "contract_code": r.contract_code,
                "status": r.status,
                "meta": r.meta,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in rows
        ]
    }


@app.get("/agent/v1/contract/cases/{case_id}", dependencies=[Depends(require_api_key)])
def get_contract_case(case_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    r = session.get(AgentContractCase, case_id)
    if not r:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "case_id": r.case_id,
        "case_key": r.case_key,
        "partner_name": r.partner_name,
        "partner_tax_id": r.partner_tax_id,
        "contract_code": r.contract_code,
        "status": r.status,
        "meta": r.meta,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


@app.get("/agent/v1/contract/cases/{case_id}/sources", dependencies=[Depends(require_api_key)])
def list_contract_case_sources(case_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    rows = session.execute(
        select(AgentSourceFile).where(AgentSourceFile.case_id == case_id).order_by(AgentSourceFile.created_at.desc())
    ).scalars().all()
    return {
        "items": [
            {
                "source_id": r.source_id,
                "case_id": r.case_id,
                "source_type": r.source_type,
                "source_uri": r.source_uri,
                "stored_uri": r.stored_uri,
                "file_hash": r.file_hash,
                "size_bytes": r.size_bytes,
                "content_type": r.content_type,
                "meta": r.meta,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


@app.get("/agent/v1/contract/cases/{case_id}/obligations", dependencies=[Depends(require_api_key)])
def list_case_obligations(case_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    rows = session.execute(
        select(AgentObligation).where(AgentObligation.case_id == case_id).order_by(AgentObligation.created_at.desc())
    ).scalars().all()
    return {
        "items": [
            {
                "obligation_id": r.obligation_id,
                "case_id": r.case_id,
                "obligation_type": r.obligation_type,
                "currency": r.currency,
                "amount_value": r.amount_value,
                "amount_percent": r.amount_percent,
                "due_date": r.due_date,
                "condition_text": r.condition_text,
                "confidence": r.confidence,
                "signature": r.signature,
                "meta": r.meta,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


@app.get("/agent/v1/contract/cases/{case_id}/proposals", dependencies=[Depends(require_api_key)])
def list_case_proposals(case_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    rows = session.execute(
        select(AgentProposal).where(AgentProposal.case_id == case_id).order_by(AgentProposal.created_at.desc())
    ).scalars().all()
    return {
        "items": [
            {
                "proposal_id": r.proposal_id,
                "case_id": r.case_id,
                "obligation_id": r.obligation_id,
                "proposal_type": r.proposal_type,
                "title": r.title,
                "summary": r.summary,
                "details": r.details,
                "risk_level": r.risk_level,
                "confidence": r.confidence,
                "status": r.status,
                "proposal_key": r.proposal_key,
                "run_id": r.run_id,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


@app.post("/agent/v1/contract/proposals", dependencies=[Depends(require_api_key)])
def post_contract_proposal(
    body: dict[str, Any],
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    proposal_key = body.get("proposal_key") or request.headers.get("Idempotency-Key")
    if not proposal_key:
        proposal_key = make_idempotency_key(
            "proposal",
            body.get("case_id"),
            body.get("obligation_id"),
            body.get("proposal_type"),
            body.get("title"),
        )

    item = AgentProposal(
        proposal_id=new_uuid(),
        case_id=body["case_id"],
        obligation_id=body.get("obligation_id"),
        proposal_type=body["proposal_type"],
        title=body["title"],
        summary=body["summary"],
        details=body.get("details"),
        risk_level=body.get("risk_level", "med"),
        confidence=float(body.get("confidence", 0.0)),
        status=body.get("status", "pending"),
        proposal_key=proposal_key,
        run_id=body.get("run_id"),
    )

    out = _insert_or_get_unique(session, AgentProposal, (AgentProposal.proposal_key == item.proposal_key), item)

    session.add(
        AgentAuditLog(
            audit_id=new_uuid(),
            actor_user_id=body.get("actor_user_id"),
            action="proposal.create",
            object_type="proposal",
            object_id=out.proposal_id,
            before=None,
            after={
                "proposal_id": out.proposal_id,
                "case_id": out.case_id,
                "obligation_id": out.obligation_id,
                "proposal_type": out.proposal_type,
                "title": out.title,
                "risk_level": out.risk_level,
                "confidence": out.confidence,
                "status": out.status,
                "proposal_key": out.proposal_key,
            },
            run_id=out.run_id,
        )
    )

    return {"proposal_id": out.proposal_id, "status": out.status, "proposal_key": out.proposal_key}


@app.post("/agent/v1/contract/proposals/{proposal_id}/approvals", dependencies=[Depends(require_api_key)])
def post_contract_approval(
    proposal_id: str,
    body: dict[str, Any],
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    proposal = session.get(AgentProposal, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="not found")

    decision = body.get("decision")
    if decision not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="invalid decision")

    actor_user_id = body.get("actor_user_id")
    note = body.get("note")

    existing = session.execute(
        select(AgentApproval).where(
            (AgentApproval.proposal_id == proposal_id)
            & (AgentApproval.decision == decision)
            & (AgentApproval.actor_user_id == actor_user_id)
            & (AgentApproval.note == note)
        )
    ).scalar_one_or_none()
    if existing:
        return {"approval_id": existing.approval_id, "proposal_id": proposal_id, "decision": existing.decision}

    before_status = proposal.status
    if proposal.status == "pending":
        proposal.status = "approved" if decision == "approve" else "rejected"

    approval = AgentApproval(
        approval_id=new_uuid(),
        proposal_id=proposal_id,
        decision=decision,
        actor_user_id=actor_user_id,
        note=note,
    )
    session.add(approval)
    session.flush()

    session.add(
        AgentAuditLog(
            audit_id=new_uuid(),
            actor_user_id=actor_user_id,
            action="proposal.approve" if decision == "approve" else "proposal.reject",
            object_type="proposal",
            object_id=proposal_id,
            before={"status": before_status},
            after={"status": proposal.status, "approval_id": approval.approval_id, "note": note},
            run_id=body.get("run_id") or proposal.run_id,
        )
    )

    return {"approval_id": approval.approval_id, "proposal_id": proposal_id, "decision": decision}


@app.get("/agent/v1/contract/audit", dependencies=[Depends(require_api_key)])
def list_contract_audit_log(
    limit: int = 200,
    object_type: str | None = None,
    object_id: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    q = select(AgentAuditLog).order_by(AgentAuditLog.ts.desc()).limit(min(limit, 500))
    if object_type:
        q = q.where(AgentAuditLog.object_type == object_type)
    if object_id:
        q = q.where(AgentAuditLog.object_id == object_id)
    rows = session.execute(q).scalars().all()
    return {
        "items": [
            {
                "audit_id": r.audit_id,
                "actor_user_id": r.actor_user_id,
                "action": r.action,
                "object_type": r.object_type,
                "object_id": r.object_id,
                "before": r.before,
                "after": r.after,
                "run_id": r.run_id,
                "ts": r.ts,
            }
            for r in rows
        ]
    }
