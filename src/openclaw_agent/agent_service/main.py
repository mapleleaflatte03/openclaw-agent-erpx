from __future__ import annotations

import os
import re as _re
import time
from datetime import date, datetime
from typing import Any, Literal

import httpx
import redis
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from openclaw_agent.agent_worker.celery_app import celery_app
from openclaw_agent.common.db import db_session, make_engine
from openclaw_agent.common.logging import configure_logging, get_logger
from openclaw_agent.common.models import (
    AcctAnomalyFlag,
    AcctBankTransaction,
    AcctCashflowForecast,
    AcctJournalLine,
    AcctJournalProposal,
    AcctQnaAudit,
    AcctReportSnapshot,
    AcctSoftCheckResult,
    AcctValidationIssue,
    AcctVoucher,
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
        raise HTTPException(status_code=401, detail="Không có quyền truy cập")


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

_SANITIZE_PATTERNS: list[tuple[_re.Pattern[str], str]] = [
    # Pattern 1: redact value only (keep key) for internal URI fields
    (_re.compile(r'("(?:file_uri|source_uri|stored_uri|pack_uri|text_uri)"\s*:\s*)"[^"]*"'), r'\1"***"'),
    # Patterns 2-4: replace inline URIs inside string values
    (_re.compile(r'https?://(?:agent-service|localhost|minio|redis|postgres)[^"\s,}]*'), "***"),
    (_re.compile(r's3://[^"\s,}]*'), "***"),
    (_re.compile(r'minio://[^"\s,}]*'), "***"),
]


@app.middleware("http")
async def sanitize_response_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Strip internal URIs from JSON API responses."""
    response = await call_next(request)
    ct = response.headers.get("content-type", "")
    if "application/json" not in ct:
        return response
    # Stream body, sanitize, rebuild response
    body_chunks: list[bytes] = []
    async for chunk in response.body_iterator:  # type: ignore[attr-defined]
        body_chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    body_text = b"".join(body_chunks).decode()
    for pat, repl in _SANITIZE_PATTERNS:
        body_text = pat.sub(repl, body_text)
    from starlette.responses import Response as StarletteResponse
    # Rebuild headers WITHOUT stale Content-Length (Starlette will
    # set the correct value from the sanitized body automatically).
    new_headers = {
        k: v for k, v in response.headers.items()
        if k.lower() != "content-length"
    }
    return StarletteResponse(
        content=body_text,
        status_code=response.status_code,
        headers=new_headers,
        media_type="application/json",
    )


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
        log.error("readyz_fail", error=str(e))
        raise HTTPException(status_code=503, detail="Hệ thống chưa sẵn sàng — kiểm tra kết nối DB/Redis/S3") from e
    return {"status": "ready"}


# ---------------------------------------------------------------------------
# Aliases under /agent/v1 prefix (backward-compat: keep root /healthz, /readyz)
# ---------------------------------------------------------------------------
@app.get("/agent/v1/healthz")
def healthz_v1() -> dict[str, str]:
    return healthz()


@app.get("/agent/v1/readyz")
def readyz_v1(
    settings: Settings = Depends(get_settings),
    engine: Engine = Depends(get_engine_dep),
) -> dict[str, Any]:
    return readyz(settings=settings, engine=engine)


def _do_agent_env() -> tuple[str, str, str | None]:
    base_url = (os.getenv("DO_AGENT_BASE_URL") or "").strip().rstrip("/")
    api_key = (os.getenv("DO_AGENT_API_KEY") or "").strip()
    model = (os.getenv("DO_AGENT_MODEL") or "").strip() or None
    if not base_url:
        raise HTTPException(status_code=503, detail="Dịch vụ LLM chưa được cấu hình (thiếu URL)")
    if not api_key:
        raise HTTPException(status_code=503, detail="Dịch vụ LLM chưa được cấu hình (thiếu API key)")
    return base_url, api_key, model


def _do_agent_chat(
    base_url: str,
    api_key: str,
    *,
    prompt: str,
    instruction_override: str | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    # DigitalOcean Agents: OpenAI-like chat endpoint at /api/v1/chat/completions (HTTP Bearer auth).
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0.0,
        "max_completion_tokens": 128,
    }
    if instruction_override:
        payload["instruction_override"] = instruction_override
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(base_url=base_url, timeout=timeout_seconds) as client:
        r = client.post("/api/v1/chat/completions", headers=headers, json=payload)
        r.raise_for_status()
        return r.json()


@app.get("/diagnostics/llm", dependencies=[Depends(require_api_key)])
def diagnostics_llm() -> dict[str, Any]:
    base_url, api_key, model_env = _do_agent_env()
    t0 = time.perf_counter()
    health: dict[str, Any] | None = None
    try:
        with httpx.Client(base_url=base_url, timeout=5.0) as client:
            health = client.get("/health").json()
    except Exception as e:
        log.error("do_agent_health_fail", error=str(e))
        raise HTTPException(status_code=503, detail="Kiểm tra sức khỏe dịch vụ LLM thất bại") from e

    t1 = time.perf_counter()
    try:
        resp = _do_agent_chat(
            base_url,
            api_key,
            prompt="Return exactly this JSON: {\"ok\": true}",
            instruction_override="You must respond with ONLY valid JSON. No commentary. Output: {\"ok\": true}",
        )
    except Exception as e:
        log.error("do_agent_chat_fail", error=str(e))
        raise HTTPException(status_code=503, detail="Gọi LLM thất bại — kiểm tra dịch vụ LLM") from e
    t2 = time.perf_counter()

    choices = resp.get("choices") or []
    msg = choices[0].get("message") if choices else None
    content = (msg.get("content") if isinstance(msg, dict) else None) if msg else None
    if isinstance(content, str) and len(content) > 500:
        content = content[:500]

    return {
        "status": "ok",
        "do_agent": {
            "base_url_masked": "configured",
            "model_name": model_env or resp.get("model") or "unknown",
            "health": health.get("status", "unknown") if isinstance(health, dict) else "unknown",
            "latency_ms": {
                "health": int((t1 - t0) * 1000),
                "chat": int((t2 - t1) * 1000),
                "total": int((t2 - t0) * 1000),
            },
            "response": {
                "id": resp.get("id"),
                "model": resp.get("model"),
                "content_preview": content,
            },
        },
    }


class LlmTestRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


@app.post("/llm/test", dependencies=[Depends(require_api_key)])
def llm_test(body: LlmTestRequest) -> dict[str, Any]:
    base_url, api_key, model_env = _do_agent_env()
    t0 = time.perf_counter()
    resp = _do_agent_chat(
        base_url,
        api_key,
        prompt=body.prompt,
        instruction_override=(
            "Put your final answer in message.content. "
            "Do not include secrets. "
            "If the user requests JSON, respond with ONLY valid JSON."
        ),
        timeout_seconds=15.0,
    )
    t1 = time.perf_counter()

    choices = resp.get("choices") or []
    msg = choices[0].get("message") if choices else None
    content = (msg.get("content") if isinstance(msg, dict) else None) if msg else None
    if isinstance(content, str) and len(content) > 2000:
        content = content[:2000]

    return {
        "status": "ok",
        "model_env": model_env,
        "response": {
            "id": resp.get("id"),
            "model": resp.get("model"),
            "content": content,
        },
        "latency_ms": int((t1 - t0) * 1000),
    }


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
    _VALID_RUN_TYPES = {
        "attachment",
        "tax_export",
        "working_papers",
        "soft_checks",
        "ar_dunning",
        "close_checklist",
        "evidence_pack",
        "kb_index",
        "contract_obligation",
        "journal_suggestion",
        "bank_reconcile",
        "cashflow_forecast",
        "voucher_ingest",
        "voucher_classify",
    }
    if run_type not in _VALID_RUN_TYPES:
        raise HTTPException(status_code=400, detail=f"Loại tác vụ không hợp lệ: '{run_type}'. Hợp lệ: {sorted(_VALID_RUN_TYPES)}")
    if trigger_type not in {"schedule", "event", "manual"}:
        raise HTTPException(status_code=400, detail="Nguồn kích hoạt không hợp lệ (chỉ hỗ trợ: schedule, event, manual)")

    # --- DEV-01: period validation for period-bound run types ---
    _PERIOD_REQUIRED_RUN_TYPES = {
        "voucher_ingest",
        "soft_checks",
        "journal_suggestion",
        "cashflow_forecast",
        "tax_export",
        "bank_reconcile",
    }
    if run_type in _PERIOD_REQUIRED_RUN_TYPES:
        period = (payload.get("period") or "").strip() if isinstance(payload, dict) else ""
        if not period:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"period là bắt buộc cho run_type={run_type}, "
                    "định dạng YYYY-MM (ví dụ 2026-01)."
                ),
            )
        if not _re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"period '{period}' không hợp lệ. "
                    "Định dạng đúng: YYYY-MM (ví dụ 2026-01)."
                ),
            )

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
        "journal_suggestion": "default",
        "bank_reconcile": "default",
        "cashflow_forecast": "default",
        "voucher_ingest": "default",
        "voucher_classify": "default",
    }
    celery_app.send_task(
        "openclaw_agent.agent_worker.tasks.dispatch_run",
        args=[run.run_id],
        queue=queue_map.get(run_type, "default"),
    )
    log.info("run_queued", run_id=run.run_id, run_type=run_type, trigger_type=trigger_type)

    # Build task preview for immediate UI feedback
    task_rows = session.execute(
        select(AgentTask).where(AgentTask.run_id == run.run_id).order_by(AgentTask.created_at.asc())
    ).scalars().all()
    task_preview = [{"task_name": t.task_name, "status": t.status} for t in task_rows]

    return {
        "run_id": run.run_id,
        "run_type": run.run_type,
        "status": run.status,
        "idempotency_key": run.idempotency_key,
        "created_at": run.created_at,
        "cursor_in": run.cursor_in,
        "tasks": task_preview,
    }


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

    # Total count for pagination
    count_q = select(func.count(AgentRun.run_id))
    if run_type:
        count_q = count_q.where(AgentRun.run_type == run_type)
    if status:
        count_q = count_q.where(AgentRun.status == status)
    total = session.execute(count_q).scalar() or 0

    return {
        "total": total,
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
        raise HTTPException(status_code=404, detail="Không tìm thấy tác vụ")

    # Include tasks for end-to-end chain visibility
    task_rows = session.execute(
        select(AgentTask).where(AgentTask.run_id == run_id).order_by(AgentTask.created_at.asc())
    ).scalars().all()

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
        "tasks": [
            {
                "task_id": t.task_id,
                "task_name": t.task_name,
                "status": t.status,
                "started_at": t.started_at,
                "finished_at": t.finished_at,
                "error": t.error,
            }
            for t in task_rows
        ],
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
    return {"id": out.id, "has_file": bool(out.file_uri)}


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
    return {"id": out.id, "has_file": bool(out.file_uri)}


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


def _normalize_risk_level(v: str | None) -> str:
    if not v:
        return "medium"
    vv = str(v).strip().lower()
    if vv == "med":
        return "medium"
    return vv


def _approvals_required(v: str | None) -> int:
    risk = _normalize_risk_level(v)
    return 2 if risk == "high" else 1


def _approved_approver_ids(session: Session, proposal_ids: list[str]) -> dict[str, set[str]]:
    if not proposal_ids:
        return {}
    rows = session.execute(
        select(AgentApproval).where(
            (AgentApproval.proposal_id.in_(proposal_ids))
            & (AgentApproval.decision == "approve")
            & (AgentApproval.evidence_ack.is_(True))
        )
    ).scalars().all()
    out: dict[str, set[str]] = {pid: set() for pid in proposal_ids}
    for a in rows:
        pid = a.proposal_id
        approver = (a.approver_id or a.actor_user_id or "").strip()
        if not approver:
            continue
        out.setdefault(pid, set()).add(approver)
    return out


class ContractCaseOut(BaseModel):
    case_id: str
    case_key: str
    partner_name: str | None = None
    partner_tax_id: str | None = None
    contract_code: str | None = None
    status: str
    meta: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class ContractCaseListResponse(BaseModel):
    items: list[ContractCaseOut]


class ContractSourceOut(BaseModel):
    source_id: str
    case_id: str | None = None
    source_type: str
    file_name: str | None = None
    has_file: bool = False
    file_hash: str
    size_bytes: int | None = None
    content_type: str | None = None
    meta: dict[str, Any] | None = None
    created_at: datetime


class ContractSourceListResponse(BaseModel):
    items: list[ContractSourceOut]


class ContractObligationOut(BaseModel):
    obligation_id: str
    case_id: str
    obligation_type: str
    currency: str
    amount_value: float | None = None
    amount_percent: float | None = None
    due_date: date | None = None
    condition_text: str
    confidence: float
    risk_level: str
    signature: str
    meta: dict[str, Any] | None = None
    created_at: datetime


class ContractObligationListResponse(BaseModel):
    items: list[ContractObligationOut]


class ContractProposalOut(BaseModel):
    proposal_id: str
    case_id: str
    obligation_id: str | None = None
    proposal_type: str
    title: str
    summary: str
    details: dict[str, Any] | None = None
    risk_level: str
    confidence: float
    status: str
    created_by: str
    tier: int
    evidence_summary_hash: str | None = None
    proposal_key: str
    run_id: str | None = None
    approvals_required: int
    approvals_approved: int
    created_at: datetime


class ContractProposalListResponse(BaseModel):
    items: list[ContractProposalOut]


class ContractProposalCreateRequest(BaseModel):
    case_id: str
    obligation_id: str | None = None
    proposal_type: str
    title: str
    summary: str
    details: dict[str, Any] | None = None
    risk_level: str = "medium"
    confidence: float = 0.0
    status: str = "draft"
    created_by: str | None = None
    tier: int = 3
    evidence_summary_hash: str | None = None
    run_id: str | None = None
    proposal_key: str | None = None
    actor_user_id: str | None = Field(default=None, description="Deprecated alias for created_by")


class ContractProposalCreateResponse(BaseModel):
    proposal_id: str
    status: str
    proposal_key: str


class ContractApprovalOut(BaseModel):
    approval_id: str
    proposal_id: str
    decision: Literal["approve", "reject"]
    approver_id: str
    evidence_ack: bool
    decided_at: datetime
    note: str | None = None
    created_at: datetime


class ContractApprovalListResponse(BaseModel):
    items: list[ContractApprovalOut]


class ContractApprovalCreateRequest(BaseModel):
    decision: Literal["approve", "reject"]
    approver_id: str | None = None
    actor_user_id: str | None = Field(default=None, description="Deprecated alias for approver_id")
    evidence_ack: bool = False
    note: str | None = None
    run_id: str | None = None


class ContractApprovalCreateResponse(BaseModel):
    approval_id: str
    proposal_id: str
    decision: Literal["approve", "reject"]
    proposal_status: str
    approvals_required: int
    approvals_approved: int


@app.get(
    "/agent/v1/contract/cases",
    dependencies=[Depends(require_api_key)],
    response_model=ContractCaseListResponse,
)
def list_contract_cases(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    session: Session = Depends(get_session),
) -> ContractCaseListResponse:
    q = (
        select(AgentContractCase)
        .order_by(AgentContractCase.created_at.desc())
        .limit(min(limit, 200))
        .offset(max(offset, 0))
    )
    if status:
        q = q.where(AgentContractCase.status == status)
    rows = session.execute(q).scalars().all()
    return ContractCaseListResponse(
        items=[
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
    )


@app.get(
    "/agent/v1/contract/cases/{case_id}",
    dependencies=[Depends(require_api_key)],
    response_model=ContractCaseOut,
)
def get_contract_case(case_id: str, session: Session = Depends(get_session)) -> ContractCaseOut:
    r = session.get(AgentContractCase, case_id)
    if not r:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ hợp đồng")
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


@app.get(
    "/agent/v1/contract/cases/{case_id}/sources",
    dependencies=[Depends(require_api_key)],
    response_model=ContractSourceListResponse,
)
def list_contract_case_sources(case_id: str, session: Session = Depends(get_session)) -> ContractSourceListResponse:
    rows = session.execute(
        select(AgentSourceFile).where(AgentSourceFile.case_id == case_id).order_by(AgentSourceFile.created_at.desc())
    ).scalars().all()
    return ContractSourceListResponse(
        items=[
            {
                "source_id": r.source_id,
                "case_id": r.case_id,
                "source_type": r.source_type,
                "file_name": (r.source_uri or "").rsplit("/", 1)[-1] if r.source_uri else None,
                "has_file": bool(r.stored_uri),
                "file_hash": r.file_hash,
                "size_bytes": r.size_bytes,
                "content_type": r.content_type,
                "meta": r.meta,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    )


@app.get(
    "/agent/v1/contract/cases/{case_id}/obligations",
    dependencies=[Depends(require_api_key)],
    response_model=ContractObligationListResponse,
)
def list_case_obligations(case_id: str, session: Session = Depends(get_session)) -> ContractObligationListResponse:
    rows = session.execute(
        select(AgentObligation).where(AgentObligation.case_id == case_id).order_by(AgentObligation.created_at.desc())
    ).scalars().all()
    return ContractObligationListResponse(
        items=[
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
                "risk_level": _normalize_risk_level(r.risk_level),
                "signature": r.signature,
                "meta": r.meta,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    )


@app.get(
    "/agent/v1/contract/cases/{case_id}/proposals",
    dependencies=[Depends(require_api_key)],
    response_model=ContractProposalListResponse,
)
def list_case_proposals(case_id: str, session: Session = Depends(get_session)) -> ContractProposalListResponse:
    rows = session.execute(
        select(AgentProposal).where(AgentProposal.case_id == case_id).order_by(AgentProposal.created_at.desc())
    ).scalars().all()
    approved = _approved_approver_ids(session, [r.proposal_id for r in rows])
    return ContractProposalListResponse(
        items=[
            {
                "proposal_id": r.proposal_id,
                "case_id": r.case_id,
                "obligation_id": r.obligation_id,
                "proposal_type": r.proposal_type,
                "title": r.title,
                "summary": r.summary,
                "details": r.details,
                "risk_level": _normalize_risk_level(r.risk_level),
                "confidence": r.confidence,
                "status": r.status,
                "created_by": r.created_by,
                "tier": int(r.tier),
                "evidence_summary_hash": r.evidence_summary_hash,
                "proposal_key": r.proposal_key,
                "run_id": r.run_id,
                "approvals_required": _approvals_required(r.risk_level),
                "approvals_approved": len(approved.get(r.proposal_id, set())),
                "created_at": r.created_at,
            }
            for r in rows
        ]
    )


@app.post(
    "/agent/v1/contract/proposals",
    dependencies=[Depends(require_api_key)],
    response_model=ContractProposalCreateResponse,
)
def post_contract_proposal(
    body: ContractProposalCreateRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> ContractProposalCreateResponse:
    proposal_key = body.proposal_key or request.headers.get("Idempotency-Key")
    if not proposal_key:
        proposal_key = make_idempotency_key(
            "proposal",
            body.case_id,
            body.obligation_id,
            body.proposal_type,
            body.title,
        )

    created_by = (body.created_by or body.actor_user_id or "system").strip() or "system"

    item = AgentProposal(
        proposal_id=new_uuid(),
        case_id=body.case_id,
        obligation_id=body.obligation_id,
        proposal_type=body.proposal_type,
        title=body.title,
        summary=body.summary,
        details=body.details,
        risk_level=_normalize_risk_level(body.risk_level),
        confidence=float(body.confidence or 0.0),
        status=body.status,
        created_by=created_by,
        tier=int(body.tier),
        evidence_summary_hash=body.evidence_summary_hash,
        proposal_key=proposal_key,
        run_id=body.run_id,
    )

    out = _insert_or_get_unique(session, AgentProposal, (AgentProposal.proposal_key == item.proposal_key), item)

    session.add(
        AgentAuditLog(
            audit_id=new_uuid(),
            actor_user_id=created_by,
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
                "created_by": out.created_by,
                "tier": int(out.tier),
                "proposal_key": out.proposal_key,
            },
            run_id=out.run_id,
        )
    )

    return {"proposal_id": out.proposal_id, "status": out.status, "proposal_key": out.proposal_key}


@app.get(
    "/agent/v1/contract/proposals/{proposal_id}/approvals",
    dependencies=[Depends(require_api_key)],
    response_model=ContractApprovalListResponse,
)
def list_contract_proposal_approvals(
    proposal_id: str, session: Session = Depends(get_session)
) -> ContractApprovalListResponse:
    rows = session.execute(
        select(AgentApproval)
        .where(AgentApproval.proposal_id == proposal_id)
        .order_by(AgentApproval.created_at.asc())
    ).scalars().all()
    return ContractApprovalListResponse(
        items=[
            {
                "approval_id": r.approval_id,
                "proposal_id": r.proposal_id,
                "decision": r.decision,
                "approver_id": (r.approver_id or r.actor_user_id or "").strip(),
                "evidence_ack": bool(r.evidence_ack),
                "decided_at": r.decided_at,
                "note": r.note,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    )


@app.post(
    "/agent/v1/contract/proposals/{proposal_id}/approvals",
    dependencies=[Depends(require_api_key)],
    response_model=ContractApprovalCreateResponse,
)
def post_contract_approval(
    proposal_id: str,
    body: ContractApprovalCreateRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> ContractApprovalCreateResponse:
    proposal = session.get(AgentProposal, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Không tìm thấy đề xuất")

    decision = body.decision
    approver_id = (body.approver_id or body.actor_user_id or "").strip()
    if not approver_id:
        raise HTTPException(status_code=400, detail="Cần có mã người duyệt (approver_id)")
    if approver_id == "system":
        raise HTTPException(status_code=400, detail="Mã người duyệt không được là 'system'")

    maker = (proposal.created_by or "").strip()
    if maker and maker == approver_id:
        raise HTTPException(status_code=409, detail="Vi phạm maker-checker: người tạo không được tự duyệt")

    if body.evidence_ack is not True:
        raise HTTPException(status_code=400, detail="Cần xác nhận đã xem bằng chứng (evidence_ack=true)")

    approvals_required = _approvals_required(proposal.risk_level)

    idem = request.headers.get("Idempotency-Key") or make_idempotency_key(
        "approval", proposal_id, approver_id, decision
    )
    existing = session.execute(
        select(AgentApproval).where(AgentApproval.idempotency_key == idem)
    ).scalar_one_or_none()
    if existing:
        approved = _approved_approver_ids(session, [proposal_id]).get(proposal_id, set())
        return {
            "approval_id": existing.approval_id,
            "proposal_id": proposal_id,
            "decision": existing.decision,
            "proposal_status": proposal.status,
            "approvals_required": approvals_required,
            "approvals_approved": len(approved),
        }

    if proposal.status in {"approved", "rejected"}:
        raise HTTPException(status_code=409, detail=f"Đề xuất đã hoàn tất ({proposal.status}). Không thể thay đổi.")

    # Enforce single decision per approver per proposal.
    prior = session.execute(
        select(AgentApproval).where(
            (AgentApproval.proposal_id == proposal_id) & (AgentApproval.approver_id == approver_id)
        )
    ).scalar_one_or_none()
    if prior:
        raise HTTPException(status_code=409, detail="Người duyệt đã ra quyết định trước đó")

    before_status = proposal.status

    approval = AgentApproval(
        approval_id=new_uuid(),
        proposal_id=proposal_id,
        decision=decision,
        actor_user_id=approver_id,
        approver_id=approver_id,
        evidence_ack=bool(body.evidence_ack),
        decided_at=utcnow(),
        idempotency_key=idem,
        note=body.note,
    )
    session.add(approval)
    session.flush()

    action = "proposal.reject" if decision == "reject" else "proposal.approve"
    if decision == "reject":
        proposal.status = "rejected"
    else:
        approved = _approved_approver_ids(session, [proposal_id]).get(proposal_id, set())
        if len(approved) >= approvals_required:
            proposal.status = "approved"
        else:
            proposal.status = "pending_l2" if approvals_required > 1 else "approved"

    session.add(
        AgentAuditLog(
            audit_id=new_uuid(),
            actor_user_id=approver_id,
            action=action,
            object_type="proposal",
            object_id=proposal_id,
            before={"status": before_status},
            after={
                "status": proposal.status,
                "approval_id": approval.approval_id,
                "approver_id": approver_id,
                "evidence_ack": bool(body.evidence_ack),
                "note": body.note,
            },
            run_id=body.run_id or proposal.run_id,
        )
    )

    approved = _approved_approver_ids(session, [proposal_id]).get(proposal_id, set())
    return {
        "approval_id": approval.approval_id,
        "proposal_id": proposal_id,
        "decision": decision,
        "proposal_status": proposal.status,
        "approvals_required": approvals_required,
        "approvals_approved": len(approved),
    }


# ---------------------------------------------------------------------------
# Tier B Feedback (explicit + implicit)
# ---------------------------------------------------------------------------

class TierBFeedbackCreateRequest(BaseModel):
    obligation_id: str
    feedback_type: Literal[
        "explicit_yes", "explicit_no",
        "implicit_accept", "implicit_edit", "implicit_reject",
    ]
    user_id: str | None = None
    delta: dict[str, Any] | None = None


class TierBFeedbackOut(BaseModel):
    id: str
    obligation_id: str
    feedback_type: str
    user_id: str | None = None
    delta: dict[str, Any] | None = None
    created_at: datetime


class TierBFeedbackListResponse(BaseModel):
    items: list[TierBFeedbackOut]


@app.post("/agent/v1/tier-b/feedback", dependencies=[Depends(require_api_key)])
def post_tier_b_feedback(
    body: TierBFeedbackCreateRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    from openclaw_agent.common.models import TierBFeedback

    fb = TierBFeedback(
        id=new_uuid(),
        obligation_id=body.obligation_id,
        user_id=body.user_id,
        feedback_type=body.feedback_type,
        delta=body.delta,
    )
    session.add(fb)
    session.flush()
    return {"id": fb.id, "obligation_id": fb.obligation_id, "feedback_type": fb.feedback_type}


@app.get(
    "/agent/v1/tier-b/feedback",
    dependencies=[Depends(require_api_key)],
    response_model=TierBFeedbackListResponse,
)
def list_tier_b_feedback(
    obligation_id: str | None = None,
    feedback_type: str | None = None,
    limit: int = 200,
    session: Session = Depends(get_session),
) -> TierBFeedbackListResponse:
    from openclaw_agent.common.models import TierBFeedback

    q = select(TierBFeedback).order_by(TierBFeedback.created_at.desc()).limit(min(limit, 1000))
    if obligation_id:
        q = q.where(TierBFeedback.obligation_id == obligation_id)
    if feedback_type:
        q = q.where(TierBFeedback.feedback_type == feedback_type)
    rows = session.execute(q).scalars().all()
    return TierBFeedbackListResponse(
        items=[
            {
                "id": r.id,
                "obligation_id": r.obligation_id,
                "feedback_type": r.feedback_type,
                "user_id": r.user_id,
                "delta": r.delta,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    )


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


# ---------------------------------------------------------------------------
# Accounting endpoints  (ERP-X AI Kế toán)
# ---------------------------------------------------------------------------


# NOTE: Voucher listing moved to Phase 5/6 unified endpoint below (list_vouchers).
# Old list_acct_vouchers removed to avoid duplicate route.


@app.get("/agent/v1/acct/journal_proposals", dependencies=[Depends(require_api_key)])
def list_journal_proposals(
    limit: int = 100,
    status: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    q = select(AcctJournalProposal).order_by(AcctJournalProposal.created_at.desc()).limit(min(limit, 500))
    if status:
        q = q.where(AcctJournalProposal.status == status)
    rows = session.execute(q).scalars().all()
    items = []
    for r in rows:
        lines_q = select(AcctJournalLine).where(AcctJournalLine.proposal_id == r.id)
        lines = session.execute(lines_q).scalars().all()
        items.append({
            "id": r.id,
            "voucher_id": r.voucher_id,
            "description": r.description,
            "confidence": r.confidence,
            "reasoning": r.reasoning,
            "status": r.status,
            "reviewed_by": r.reviewed_by,
            "reviewed_at": r.reviewed_at,
            "created_at": r.created_at,
            "run_id": r.run_id,
            "lines": [
                {
                    "id": ln.id,
                    "account_code": ln.account_code,
                    "account_name": ln.account_name,
                    "debit": ln.debit,
                    "credit": ln.credit,
                }
                for ln in lines
            ],
        })
    return {"items": items}


class JournalProposalReviewIn(BaseModel):
    status: Literal["approved", "rejected"]
    reviewed_by: str


@app.post("/agent/v1/acct/journal_proposals/{proposal_id}/review", dependencies=[Depends(require_api_key)])
def review_journal_proposal(
    proposal_id: str,
    body: JournalProposalReviewIn,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    proposal = session.get(AcctJournalProposal, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Không tìm thấy bút toán đề xuất")
    if proposal.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Bút toán đã được xử lý (trạng thái: {proposal.status}). Không thể thay đổi.",
        )
    proposal.status = body.status
    proposal.reviewed_by = body.reviewed_by
    proposal.reviewed_at = utcnow()
    session.commit()
    return {"id": proposal.id, "status": proposal.status, "reviewed_by": proposal.reviewed_by}


@app.get("/agent/v1/acct/anomaly_flags", dependencies=[Depends(require_api_key)])
def list_anomaly_flags(
    limit: int = 100,
    resolution: str | None = None,
    anomaly_type: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    q = select(AcctAnomalyFlag).order_by(AcctAnomalyFlag.created_at.desc()).limit(min(limit, 500))
    if resolution:
        q = q.where(AcctAnomalyFlag.resolution == resolution)
    if anomaly_type:
        q = q.where(AcctAnomalyFlag.anomaly_type == anomaly_type)
    rows = session.execute(q).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "anomaly_type": r.anomaly_type,
                "severity": r.severity,
                "description": r.description,
                "voucher_id": r.voucher_id,
                "bank_tx_id": r.bank_tx_id,
                "resolution": r.resolution,
                "resolved_by": r.resolved_by,
                "resolved_at": r.resolved_at,
                "created_at": r.created_at,
                "run_id": r.run_id,
            }
            for r in rows
        ]
    }


class AnomalyResolveIn(BaseModel):
    resolution: Literal["resolved", "ignored"]
    resolved_by: str


@app.post("/agent/v1/acct/anomaly_flags/{flag_id}/resolve", dependencies=[Depends(require_api_key)])
def resolve_anomaly_flag(
    flag_id: str,
    body: AnomalyResolveIn,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    flag = session.get(AcctAnomalyFlag, flag_id)
    if not flag:
        raise HTTPException(status_code=404, detail="Không tìm thấy cảnh báo bất thường")
    if flag.resolution != "open":
        raise HTTPException(
            status_code=409,
            detail=f"Giao dịch bất thường đã được xử lý (trạng thái: {flag.resolution}). Không thể thay đổi.",
        )
    flag.resolution = body.resolution
    flag.resolved_by = body.resolved_by
    flag.resolved_at = utcnow()
    session.commit()
    return {"id": flag.id, "resolution": flag.resolution, "resolved_by": flag.resolved_by}


@app.get("/agent/v1/acct/bank_transactions", dependencies=[Depends(require_api_key)])
def list_bank_transactions(
    limit: int = 100,
    match_status: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    q = select(AcctBankTransaction).order_by(AcctBankTransaction.synced_at.desc()).limit(min(limit, 500))
    if match_status:
        q = q.where(AcctBankTransaction.match_status == match_status)
    rows = session.execute(q).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "bank_tx_ref": r.bank_tx_ref,
                "bank_account": r.bank_account,
                "date": r.date,
                "amount": r.amount,
                "currency": r.currency,
                "counterparty": r.counterparty,
                "memo": r.memo,
                "matched_voucher_id": r.matched_voucher_id,
                "match_status": r.match_status,
                "synced_at": r.synced_at,
                "run_id": r.run_id,
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# Phase 2: Extended accounting endpoints
# ---------------------------------------------------------------------------

@app.get("/agent/v1/acct/soft_check_results", dependencies=[Depends(require_api_key)])
def list_soft_check_results(
    period: str | None = None,
    limit: int = 50,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    q = select(AcctSoftCheckResult).order_by(AcctSoftCheckResult.created_at.desc()).limit(min(limit, 200))
    if period:
        q = q.where(AcctSoftCheckResult.period == period)
    rows = session.execute(q).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "period": r.period,
                "total_checks": r.total_checks,
                "passed": r.passed,
                "warnings": r.warnings,
                "errors": r.errors,
                "score": r.score,
                "created_at": r.created_at,
                "run_id": r.run_id,
            }
            for r in rows
        ]
    }


@app.get("/agent/v1/acct/validation_issues", dependencies=[Depends(require_api_key)])
def list_validation_issues(
    resolution: str | None = None,
    severity: str | None = None,
    check_result_id: str | None = None,
    limit: int = 100,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    q = select(AcctValidationIssue).order_by(AcctValidationIssue.created_at.desc()).limit(min(limit, 500))
    if resolution:
        q = q.where(AcctValidationIssue.resolution == resolution)
    if severity:
        q = q.where(AcctValidationIssue.severity == severity)
    if check_result_id:
        q = q.where(AcctValidationIssue.check_result_id == check_result_id)
    rows = session.execute(q).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "check_result_id": r.check_result_id,
                "rule_code": r.rule_code,
                "severity": r.severity,
                "message": r.message,
                "erp_ref": r.erp_ref,
                "resolution": r.resolution,
                "resolved_by": r.resolved_by,
                "resolved_at": r.resolved_at,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


@app.post("/agent/v1/acct/validation_issues/{issue_id}/resolve", dependencies=[Depends(require_api_key)])
def resolve_validation_issue(
    issue_id: str,
    body: dict[str, Any],
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    issue = session.get(AcctValidationIssue, issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Không tìm thấy vấn đề cần kiểm tra")
    if issue.resolution != "open":
        raise HTTPException(
            status_code=409,
            detail=f"Vấn đề đã được xử lý (trạng thái: {issue.resolution}). Không thể thay đổi.",
        )
    action = body.get("action", "resolved")
    if action not in ("resolved", "ignored"):
        raise HTTPException(status_code=400, detail="Hành động phải là 'resolved' hoặc 'ignored'")
    issue.resolution = action
    issue.resolved_by = body.get("resolved_by", "user")
    issue.resolved_at = utcnow()
    session.commit()
    return {"id": issue.id, "resolution": issue.resolution}


@app.get("/agent/v1/acct/report_snapshots", dependencies=[Depends(require_api_key)])
def list_report_snapshots(
    report_type: str | None = None,
    period: str | None = None,
    limit: int = 50,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    q = select(AcctReportSnapshot).order_by(AcctReportSnapshot.created_at.desc()).limit(min(limit, 200))
    if report_type:
        q = q.where(AcctReportSnapshot.report_type == report_type)
    if period:
        q = q.where(AcctReportSnapshot.period == period)
    rows = session.execute(q).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "report_type": r.report_type,
                "period": r.period,
                "version": r.version,
                "has_file": bool(r.file_uri),
                "summary_json": r.summary_json,
                "created_at": r.created_at,
                "run_id": r.run_id,
            }
            for r in rows
        ]
    }


@app.get("/agent/v1/acct/cashflow_forecast", dependencies=[Depends(require_api_key)])
def list_cashflow_forecast(
    direction: str | None = None,
    limit: int = 100,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    q = select(AcctCashflowForecast).order_by(AcctCashflowForecast.forecast_date.asc()).limit(min(limit, 500))
    if direction:
        q = q.where(AcctCashflowForecast.direction == direction)
    rows = session.execute(q).scalars().all()
    total_inflow = sum(r.amount for r in rows if r.direction == "inflow")
    total_outflow = sum(r.amount for r in rows if r.direction == "outflow")
    return {
        "summary": {
            "total_inflow": total_inflow,
            "total_outflow": total_outflow,
            "net": total_inflow - total_outflow,
        },
        "items": [
            {
                "id": r.id,
                "forecast_date": r.forecast_date,
                "direction": r.direction,
                "amount": r.amount,
                "currency": r.currency,
                "source_type": r.source_type,
                "source_ref": r.source_ref,
                "confidence": r.confidence,
                "run_id": r.run_id,
            }
            for r in rows
        ],
    }


@app.get("/agent/v1/acct/qna_audits", dependencies=[Depends(require_api_key)])
def list_qna_audits(
    limit: int = 50,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    q = select(AcctQnaAudit).order_by(AcctQnaAudit.created_at.desc()).limit(min(limit, 200))
    rows = session.execute(q).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "question": r.question,
                "answer": r.answer,
                "sources": r.sources,
                "user_id": r.user_id,
                "feedback": r.feedback,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


@app.patch("/agent/v1/acct/qna_feedback/{audit_id}", dependencies=[Depends(require_api_key)])
def submit_qna_feedback(
    audit_id: str,
    payload: dict[str, Any],
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Submit feedback (helpful/not_helpful) for a Q&A answer.

    Spec §6.2: Log Q&A + feedback for self-learning pipeline.
    Body: {"feedback": "helpful" | "not_helpful", "note": "optional text"}
    """
    row = session.get(AcctQnaAudit, audit_id)
    if row is None:
        raise HTTPException(status_code=404, detail="QnA audit not found")

    feedback_val = str(payload.get("feedback", "")).strip()
    if feedback_val not in ("helpful", "not_helpful"):
        raise HTTPException(status_code=422, detail="feedback must be 'helpful' or 'not_helpful'")

    row.feedback = feedback_val  # type: ignore[assignment]
    session.commit()

    return {"id": audit_id, "feedback": feedback_val, "status": "recorded"}


# ---------------------------------------------------------------------------
# Phase 5 & 6: Voucher listing + classification stats
# ---------------------------------------------------------------------------

@app.get("/agent/v1/acct/vouchers", dependencies=[Depends(require_api_key)])
def list_vouchers(
    classification_tag: str | None = None,
    source: str | None = None,
    limit: int = 50,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """List AcctVoucher rows with optional filter by classification_tag or source."""
    q = select(AcctVoucher).order_by(AcctVoucher.synced_at.desc()).limit(min(limit, 200))
    if classification_tag:
        q = q.where(AcctVoucher.classification_tag == classification_tag)
    if source:
        q = q.where(AcctVoucher.source == source)
    rows = session.execute(q).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "voucher_no": r.voucher_no,
                "voucher_type": r.voucher_type,
                "date": r.date,
                "amount": r.amount,
                "currency": r.currency,
                "partner_name": r.partner_name,
                "partner_tax_code": getattr(r, "partner_tax_code", None),
                "description": r.description,
                "source": getattr(r, "source", None),
                "type_hint": getattr(r, "type_hint", None),
                "classification_tag": getattr(r, "classification_tag", None),
                "has_attachment": r.has_attachment,
                "synced_at": r.synced_at,
                "run_id": r.run_id,
            }
            for r in rows
        ]
    }


@app.get("/agent/v1/acct/voucher_classification_stats", dependencies=[Depends(require_api_key)])
def voucher_classification_stats(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Aggregate voucher counts grouped by classification_tag."""
    from sqlalchemy import func
    q = (
        select(AcctVoucher.classification_tag, func.count(AcctVoucher.id))
        .group_by(AcctVoucher.classification_tag)
    )
    rows = session.execute(q).all()
    return {
        "stats": [
            {"classification_tag": tag or "UNCLASSIFIED", "count": cnt}
            for tag, cnt in rows
        ]
    }


# ---------------------------------------------------------------------------
# Phase 7: Q&A endpoint
# ---------------------------------------------------------------------------

@app.post("/agent/v1/acct/qna", dependencies=[Depends(require_api_key)])
def accounting_qna(
    body: dict[str, Any],
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Answer an accounting question based on Acct* data."""
    from openclaw_agent.flows.qna_accounting import answer_question

    question = body.get("question", "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Vui lòng nhập câu hỏi")

    result = answer_question(session, question)

    # Persist audit
    qna_row = AcctQnaAudit(
        id=new_uuid(),
        question=question,
        answer=result["answer"],
        sources=result.get("meta"),
        user_id=body.get("user_id"),
    )
    session.add(qna_row)
    session.commit()

    return {
        "answer": result["answer"],
        "meta": {
            "source": "acct_db",
            "qna_id": qna_row.id,
            "used_models": result.get("used_models", []),
            "llm_used": result.get("llm_used", False),
        },
    }


# ---------------------------------------------------------------------------
# LangGraph endpoints
# ---------------------------------------------------------------------------

@app.get("/agent/v1/graphs", dependencies=[Depends(require_api_key)])
def list_available_graphs() -> dict[str, Any]:
    """List all available LangGraph workflow graphs."""
    try:
        from openclaw_agent.graphs.registry import is_available, list_graphs
        return {
            "langgraph_available": is_available(),
            "graphs": list_graphs(),
        }
    except ImportError:
        return {"langgraph_available": False, "graphs": []}


@app.get("/agent/v1/graphs/{graph_name}", dependencies=[Depends(require_api_key)])
def get_graph_info(graph_name: str) -> dict[str, Any]:
    """Get info about a specific LangGraph workflow graph."""
    try:
        from openclaw_agent.graphs.registry import get_graph, list_graphs
        available = list_graphs()
        if graph_name not in available:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy đồ thị '{graph_name}'")
        graph = get_graph(graph_name)
        nodes = list(graph.nodes) if hasattr(graph, "nodes") else []
        return {
            "name": graph_name,
            "nodes": nodes,
            "compiled": True,
        }
    except ImportError:
        raise HTTPException(status_code=501, detail="LangGraph chưa được cài đặt") from None
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy đồ thị '{graph_name}'") from None


# ---------------------------------------------------------------------------
# Ray status endpoints
# ---------------------------------------------------------------------------

@app.get("/agent/v1/ray/status", dependencies=[Depends(require_api_key)])
def ray_status() -> dict[str, Any]:
    """Check Ray availability and cluster resources."""
    try:
        from openclaw_agent.kernel.swarm import get_swarm, is_available
        available = is_available()
        resources = {}
        if available:
            swarm = get_swarm()
            if swarm.is_initialized:
                resources = swarm.cluster_resources()
        return {
            "ray_available": available,
            "initialized": available and get_swarm().is_initialized,
            "resources": resources,
        }
    except ImportError:
        return {"ray_available": False, "initialized": False, "resources": {}}


# ---------------------------------------------------------------------------
# Agent Command Center endpoints (P1)
# ---------------------------------------------------------------------------

# Goal → chain mapping: Vietnamese goal phrases → ordered run_types
_GOAL_CHAINS: dict[str, list[str]] = {
    "đóng sổ": [
        "voucher_ingest", "voucher_classify", "journal_suggestion",
        "bank_reconcile", "soft_checks", "tax_export", "cashflow_forecast",
    ],
    "kiểm tra kỳ": [
        "voucher_ingest", "voucher_classify", "soft_checks",
    ],
    "đối chiếu": [
        "bank_reconcile", "soft_checks",
    ],
    "báo cáo thuế": [
        "voucher_ingest", "voucher_classify", "journal_suggestion",
        "tax_export",
    ],
    "nhập chứng từ": [
        "voucher_ingest", "voucher_classify",
    ],
    "phân loại": [
        "voucher_classify",
    ],
    "dự báo dòng tiền": [
        "cashflow_forecast",
    ],
    "hỏi đáp": [],  # special: goes to Q&A, not a run chain
    "bất thường": [
        "soft_checks", "anomaly_scan",
    ],
    "hợp đồng": [
        "contract_obligation",
    ],
}

_GOAL_CHAIN_LABELS: dict[str, str] = {
    "đóng sổ": "Đóng sổ cuối kỳ",
    "kiểm tra kỳ": "Kiểm tra kỳ kế toán",
    "đối chiếu": "Đối chiếu ngân hàng",
    "báo cáo thuế": "Báo cáo thuế",
    "nhập chứng từ": "Nhập & phân loại chứng từ",
    "phân loại": "Phân loại chứng từ",
    "dự báo dòng tiền": "Dự báo dòng tiền",
    "hỏi đáp": "Hỏi đáp kế toán",
    "bất thường": "Phát hiện bất thường",
    "hợp đồng": "Rà soát hợp đồng",
}


def _parse_goal_command(command: str) -> tuple[str, list[str]]:
    """Parse a Vietnamese goal command → (goal_key, chain of run_types)."""
    cmd_lower = command.strip().lower()
    for goal_key, chain in _GOAL_CHAINS.items():
        if goal_key in cmd_lower:
            return goal_key, chain
    # Fallback: try to match common run_type keywords
    _RT_KEYWORDS: dict[str, str] = {
        "bút toán": "journal_suggestion",
        "đề xuất": "journal_suggestion",
        "ngân hàng": "bank_reconcile",
        "chứng từ": "voucher_ingest",
        "hợp đồng": "contract_obligation",
        "kiểm tra": "soft_checks",
        "thuế": "tax_export",
    }
    for kw, rt in _RT_KEYWORDS.items():
        if kw in cmd_lower:
            return rt, [rt]
    return "unknown", []


class AgentCommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=500)
    period: str | None = None
    payload: dict[str, Any] | None = None


@app.post("/agent/v1/agent/commands", dependencies=[Depends(require_api_key)])
def execute_agent_command(
    body: AgentCommandRequest,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Parse a Vietnamese goal command and execute the chain of run_types."""
    goal_key, chain = _parse_goal_command(body.command)

    if not chain:
        return {
            "status": "no_chain",
            "goal": goal_key,
            "message": "Không nhận diện được mục tiêu. Vui lòng thử lại với mô tả cụ thể hơn.",
            "available_goals": list(_GOAL_CHAIN_LABELS.values()),
            "runs": [],
        }

    runs_created: list[dict[str, Any]] = []
    payload = body.payload or {}
    if body.period:
        payload["period"] = body.period

    for run_type in chain:
        idem = make_idempotency_key("cmd", goal_key, run_type, body.period or "")
        existing = session.execute(
            select(AgentRun).where(AgentRun.idempotency_key == idem)
        ).scalar_one_or_none()
        if existing:
            runs_created.append({
                "run_id": existing.run_id,
                "run_type": existing.run_type,
                "status": existing.status,
                "reused": True,
            })
            continue

        run = AgentRun(
            run_id=new_uuid(),
            run_type=run_type,
            trigger_type="manual",
            requested_by="agent-command-center",
            status="queued",
            idempotency_key=idem,
            cursor_in=payload,
            cursor_out=None,
            started_at=None,
            finished_at=None,
            stats=None,
        )
        session.add(run)
        session.flush()

        queue_map = {
            "attachment": "ocr", "kb_index": "ocr",
            "tax_export": "export", "working_papers": "export",
            "soft_checks": "default", "close_checklist": "default",
            "ar_dunning": "io", "evidence_pack": "io",
            "contract_obligation": "ocr",
            "journal_suggestion": "default", "bank_reconcile": "default",
            "cashflow_forecast": "default",
            "voucher_ingest": "default", "voucher_classify": "default",
        }
        celery_app.send_task(
            "openclaw_agent.agent_worker.tasks.dispatch_run",
            args=[run.run_id],
            queue=queue_map.get(run_type, "default"),
        )
        runs_created.append({
            "run_id": run.run_id,
            "run_type": run_type,
            "status": "queued",
            "reused": False,
        })

    log.info("agent_command", command=body.command, goal=goal_key, runs=len(runs_created))
    return {
        "status": "ok",
        "goal": goal_key,
        "goal_label": _GOAL_CHAIN_LABELS.get(goal_key, goal_key),
        "chain": chain,
        "runs": runs_created,
    }


@app.get("/agent/v1/agent/goals", dependencies=[Depends(require_api_key)])
def list_agent_goals() -> dict[str, Any]:
    """Return available goal-centric commands for the Agent Command Center."""
    return {
        "goals": [
            {"key": k, "label": _GOAL_CHAIN_LABELS[k], "chain": v}
            for k, v in _GOAL_CHAINS.items()
        ]
    }


@app.get("/agent/v1/agent/timeline", dependencies=[Depends(require_api_key)])
def agent_timeline(
    limit: int = 50,
    run_id: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Activity timeline for the Agent Command Center — shows recent agent actions."""
    # Combine runs + tasks + logs into a unified timeline
    timeline_items: list[dict[str, Any]] = []

    # Recent runs
    run_q = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(min(limit, 100))
    if run_id:
        run_q = run_q.where(AgentRun.run_id == run_id)
    runs = session.execute(run_q).scalars().all()

    _RUN_TYPE_VN: dict[str, str] = {
        "journal_suggestion": "Đề xuất bút toán",
        "bank_reconcile": "Đối chiếu ngân hàng",
        "cashflow_forecast": "Dự báo dòng tiền",
        "voucher_ingest": "Nhập chứng từ",
        "voucher_classify": "Phân loại chứng từ",
        "tax_export": "Xuất báo cáo thuế",
        "working_papers": "Bảng tính kiểm toán",
        "soft_checks": "Kiểm tra logic",
        "ar_dunning": "Nhắc nợ công nợ",
        "close_checklist": "Danh mục kết kỳ",
        "evidence_pack": "Gói bằng chứng",
        "kb_index": "Cập nhật kho tri thức",
        "contract_obligation": "Nghĩa vụ hợp đồng",
    }

    for r in runs:
        rt_label = _RUN_TYPE_VN.get(r.run_type, r.run_type)
        status_emoji = {"queued": "⏳", "running": "🔄", "completed": "✅", "failed": "❌"}.get(r.status, "❓")

        timeline_items.append({
            "ts": str(r.created_at),
            "type": "run",
            "icon": status_emoji,
            "title": f"Agent thực hiện: {rt_label}",
            "detail": f"Trạng thái: {r.status}",
            "run_id": r.run_id,
            "run_type": r.run_type,
            "status": r.status,
        })

        # Tasks within this run
        tasks = session.execute(
            select(AgentTask).where(AgentTask.run_id == r.run_id).order_by(AgentTask.created_at.asc())
        ).scalars().all()
        for t in tasks:
            t_emoji = {"queued": "⏳", "running": "🔄", "completed": "✅", "failed": "❌"}.get(t.status, "❓")
            timeline_items.append({
                "ts": str(t.created_at),
                "type": "task",
                "icon": t_emoji,
                "title": f"  Bước: {t.task_name}",
                "detail": t.error or "",
                "run_id": r.run_id,
                "task_id": t.task_id,
                "status": t.status,
            })

    return {"items": timeline_items[:limit]}

# ---------------------------------------------------------------------------
# VN Feeder — status & control endpoints
# ---------------------------------------------------------------------------

_VN_FEEDER_CACHE = os.getenv("VN_FEEDER_CACHE_DIR", "/data/vn_feeder_cache")


class VnFeederControlBody(BaseModel):
    action: Literal["start", "stop", "inject_now"] = "start"
    target_events_per_min: int | None = Field(None, ge=1, le=10)


@app.get("/agent/v1/vn_feeder/status", dependencies=[Depends(require_api_key)])
def vn_feeder_status() -> dict[str, Any]:
    """Return current VN feeder status from the status file."""
    import json as _json

    from openclaw_agent.agent_service.vn_feeder_engine import is_running as _feeder_is_running

    status_path = os.path.join(_VN_FEEDER_CACHE, "feeder_status.json")
    if os.path.isfile(status_path):
        try:
            with open(status_path, encoding="utf-8") as fh:
                data = _json.loads(fh.read())
            # Ensure running state reflects actual thread state
            data["running"] = _feeder_is_running()
            return data
        except Exception:
            pass
    return {
        "running": _feeder_is_running(),
        "total_events_today": 0,
        "last_event_at": "",
        "avg_events_per_min": 0,
        "sources": [],
        "updated_at": "",
    }


@app.post("/agent/v1/vn_feeder/control", dependencies=[Depends(require_api_key)])
def vn_feeder_control(body: VnFeederControlBody) -> dict[str, str]:
    """Control the VN feeder background thread — start/stop/inject."""
    from openclaw_agent.agent_service.vn_feeder_engine import (
        inject_now as _feeder_inject,
    )
    from openclaw_agent.agent_service.vn_feeder_engine import (
        start_feeder as _feeder_start,
    )
    from openclaw_agent.agent_service.vn_feeder_engine import (
        stop_feeder as _feeder_stop,
    )

    epm = body.target_events_per_min
    if body.action == "start":
        _feeder_start(target_epm=epm)
    elif body.action == "stop":
        _feeder_stop()
    elif body.action == "inject_now":
        _feeder_inject(target_epm=epm)
    return {"status": "ok", "action": body.action}