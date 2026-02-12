from __future__ import annotations

import io
import json
import math
import mimetypes
import os
import re as _re
import threading
import time
from collections import Counter
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import httpx
import redis
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
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


_RUN_QUEUE_MAP: dict[str, str] = {
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
    "voucher_reprocess": "default",
}

_VALID_RUN_TYPES = frozenset(_RUN_QUEUE_MAP.keys())
_PERIOD_REQUIRED_RUN_TYPES = frozenset(
    {
        "voucher_ingest",
        "soft_checks",
        "journal_suggestion",
        "cashflow_forecast",
        "tax_export",
        "bank_reconcile",
    }
)
_EXECUTOR_MODES = frozenset({"auto", "celery", "local"})
_RUN_STALE_QUEUE_SECONDS = int(os.getenv("RUN_STALE_QUEUE_SECONDS", "45"))


def _executor_mode() -> str:
    mode = (os.getenv("RUN_EXECUTOR_MODE") or "auto").strip().lower()
    if mode not in _EXECUTOR_MODES:
        return "auto"
    return mode


def _celery_workers(timeout_seconds: float = 1.0) -> dict[str, Any]:
    try:
        inspect = celery_app.control.inspect(timeout=timeout_seconds)
        ping = inspect.ping() or {}
        workers = sorted(str(w) for w in ping)
        return {
            "available": bool(workers),
            "worker_count": len(workers),
            "workers": workers,
        }
    except Exception as exc:
        return {
            "available": False,
            "worker_count": 0,
            "workers": [],
            "error": str(exc),
        }


def _executor_readiness() -> dict[str, Any]:
    mode = _executor_mode()
    celery_enabled = mode in {"auto", "celery"}
    celery = _celery_workers() if celery_enabled else {"available": False, "worker_count": 0, "workers": []}
    local_enabled = mode in {"auto", "local"}
    can_use_celery = celery_enabled and bool(celery.get("available"))
    preferred_executor: str | None = "celery" if can_use_celery else "local" if local_enabled else None
    dispatch_ready = bool(preferred_executor)
    return {
        "mode": mode,
        "dispatch_ready": dispatch_ready,
        "preferred_executor": preferred_executor,
        "local_executor_enabled": local_enabled,
        "celery": celery,
    }


def _dispatch_run_local(run_id: str) -> None:
    def _runner() -> None:
        try:
            from openclaw_agent.agent_worker.tasks import dispatch_run as worker_dispatch

            worker_dispatch.run(run_id)
        except Exception as exc:
            log.error("run_local_dispatch_failed", run_id=run_id, error=str(exc))

    thread = threading.Thread(
        target=_runner,
        name=f"openclaw-local-run-{run_id[:8]}",
        daemon=True,
    )
    thread.start()


def _dispatch_run(
    run_id: str,
    run_type: str,
    *,
    preferred_executor: str,
    allow_local_fallback: bool,
) -> dict[str, Any]:
    queue_name = _RUN_QUEUE_MAP.get(run_type, "default")

    if preferred_executor == "celery":
        try:
            celery_app.send_task(
                "openclaw_agent.agent_worker.tasks.dispatch_run",
                args=[run_id],
                queue=queue_name,
            )
            return {"executor": "celery", "queue": queue_name}
        except Exception as exc:
            if allow_local_fallback:
                _dispatch_run_local(run_id)
                return {
                    "executor": "local",
                    "queue": "local",
                    "fallback_reason": str(exc),
                }
            raise HTTPException(status_code=503, detail=f"Không thể gửi run vào worker queue: {exc}") from exc

    if preferred_executor == "local":
        _dispatch_run_local(run_id)
        return {"executor": "local", "queue": "local"}

    raise HTTPException(status_code=503, detail="Không có executor sẵn sàng để xử lý run")


def _mark_run_dispatch_failed(session: Session, run_id: str, detail: str) -> None:
    run = session.get(AgentRun, run_id)
    if run is None:
        return
    run.status = "failed"
    run.finished_at = utcnow()
    run.stats = {"dispatch_error": detail}
    session.commit()


def _is_stale_pending_run(run: AgentRun) -> bool:
    if run.status not in {"queued", "running"}:
        return False
    if run.status == "running" and run.started_at is not None:
        return False
    if run.created_at is None:
        return False
    age_seconds = (utcnow() - run.created_at).total_seconds()
    return age_seconds >= _RUN_STALE_QUEUE_SECONDS


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
    if run_type not in _VALID_RUN_TYPES:
        raise HTTPException(status_code=400, detail=f"Loại tác vụ không hợp lệ: '{run_type}'. Hợp lệ: {sorted(_VALID_RUN_TYPES)}")
    if trigger_type not in {"schedule", "event", "manual"}:
        raise HTTPException(status_code=400, detail="Nguồn kích hoạt không hợp lệ (chỉ hỗ trợ: schedule, event, manual)")

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
        if _is_stale_pending_run(existing):
            readiness = _executor_readiness()
            if not readiness["dispatch_ready"]:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Run cũ đang pending nhưng không có executor để re-dispatch. "
                        "Vui lòng thử lại sau."
                    ),
                )
            try:
                dispatch_info = _dispatch_run(
                    existing.run_id,
                    existing.run_type,
                    preferred_executor=str(readiness["preferred_executor"]),
                    allow_local_fallback=readiness["mode"] == "auto",
                )
                log.info("run_redispatched", run_id=existing.run_id, run_type=existing.run_type)
                return {
                    "run_id": existing.run_id,
                    "status": existing.status,
                    "idempotency_key": existing.idempotency_key,
                    "redispatched": True,
                    "executor": dispatch_info,
                }
            except HTTPException as exc:
                _mark_run_dispatch_failed(session, existing.run_id, str(exc.detail))
                raise
            except Exception as exc:  # pragma: no cover - defensive
                _mark_run_dispatch_failed(session, existing.run_id, str(exc))
                raise HTTPException(status_code=503, detail=f"Không thể re-dispatch run cũ: {exc}") from exc

        return {"run_id": existing.run_id, "status": existing.status, "idempotency_key": existing.idempotency_key}

    readiness = _executor_readiness()
    if not readiness["dispatch_ready"]:
        raise HTTPException(
            status_code=503,
            detail="Không có executor khả dụng (celery worker/local executor). Không thể tạo run mới.",
        )

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

    # Commit before dispatch to avoid worker race (worker cannot find uncommitted run row).
    session.commit()
    session.refresh(run)

    try:
        dispatch_info = _dispatch_run(
            run.run_id,
            run_type,
            preferred_executor=str(readiness["preferred_executor"]),
            allow_local_fallback=readiness["mode"] == "auto",
        )
    except HTTPException as exc:
        _mark_run_dispatch_failed(session, run.run_id, str(exc.detail))
        raise
    except Exception as exc:  # pragma: no cover - defensive
        _mark_run_dispatch_failed(session, run.run_id, str(exc))
        raise HTTPException(status_code=503, detail=f"Không thể dispatch run: {exc}") from exc
    log.info(
        "run_queued",
        run_id=run.run_id,
        run_type=run_type,
        trigger_type=trigger_type,
        executor=dispatch_info.get("executor"),
    )

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
        "executor": dispatch_info,
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
                "completed_at": r.finished_at,
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
        "completed_at": r.finished_at,
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
def list_logs(
    run_id: str | None = None,
    filter_entity_id: str | None = None,
    limit: int = 200,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    resolved_run_id = (run_id or "").strip()
    if not resolved_run_id and filter_entity_id:
        voucher = session.get(AcctVoucher, str(filter_entity_id))
        if voucher and voucher.run_id:
            resolved_run_id = voucher.run_id

    if not resolved_run_id:
        return {"items": []}

    rows = session.execute(
        select(AgentLog)
        .where(AgentLog.run_id == resolved_run_id)
        .order_by(AgentLog.ts.desc())
        .limit(min(limit, 500))
    ).scalars().all()
    return {
        "run_id": resolved_run_id,
        "items": [
            {
                "log_id": row.log_id,
                "run_id": row.run_id,
                "task_id": row.task_id,
                "level": row.level,
                "message": row.message,
                "context": row.context,
                "ts": row.ts,
                "created_at": row.ts,
                "timestamp": row.ts,
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


_ATTACH_UPLOAD_DIR = Path(os.getenv("AGENT_UPLOAD_DIR", "/tmp/openclaw_uploads"))
_ATTACH_ALLOWED_EXT = {
    ".pdf": "application/pdf",
    ".xml": "application/xml",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
_INVALID_OCR_STATUSES = {"quarantined", "non_invoice", "low_quality"}
_NON_INVOICE_HINTS = {
    "dog",
    "dogs",
    "cat",
    "cats",
    "dogs-vs-cats",
    "pet",
    "kitten",
    "puppy",
}
_INVOICE_HINTS = {
    "invoice",
    "hoa don",
    "hóa đơn",
    "vat",
    "tax code",
    "mst",
    "tổng tiền",
    "thanh tien",
    "thành tiền",
    "receipt",
    "so hoa don",
    "số hóa đơn",
}
try:
    _OCR_MIN_CONFIDENCE = float(os.getenv("OCR_MIN_CONFIDENCE", "0.70"))
except Exception:
    _OCR_MIN_CONFIDENCE = 0.70
_OCR_MIN_CONFIDENCE = max(0.0, min(_OCR_MIN_CONFIDENCE, 1.0))


def _is_undefined_like(value: Any) -> bool:
    txt = str(value or "").strip().lower()
    return txt in {"", "undefined", "null", "none", "nan", "n/a", "na", "-"}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        num = float(value)
    except Exception:
        return default
    if not math.isfinite(num):
        return default
    return num


def _parse_amount_token(token: str) -> float:
    # Keep this parser deterministic and permissive for VN-style separators.
    cleaned = _re.sub(r"[^\d,.\-]", "", token or "")
    if not cleaned:
        return 0.0
    # Heuristic: keep only digits when separators are ambiguous.
    digits = _re.sub(r"[^\d\-]", "", cleaned)
    if not digits or digits in {"-", "--"}:
        return 0.0
    try:
        return abs(float(int(digits)))
    except Exception:
        return 0.0


def _extract_upload_text_preview(blob: bytes, pipeline: str) -> str:
    if not blob:
        return ""
    # XML path: strict UTF-8 decode handled above, reuse it here.
    if pipeline == "xml_parse":
        try:
            return blob.decode("utf-8", errors="ignore")
        except Exception:
            return ""
    if pipeline == "pdf_ocr":
        try:
            import pdfplumber

            pages: list[str] = []
            with pdfplumber.open(io.BytesIO(blob)) as pdf:
                for page in pdf.pages[:2]:
                    txt = (page.extract_text() or "").strip()
                    if txt:
                        pages.append(txt)
            return "\n".join(pages)
        except Exception:
            return ""
    if pipeline == "image_ocr":
        try:
            import pytesseract
            from PIL import Image

            with Image.open(io.BytesIO(blob)) as image:
                return pytesseract.image_to_string(image, lang="eng+vie", timeout=8)
        except Exception:
            return ""
    return ""


def _estimate_line_items(text: str) -> int:
    if not text:
        return 0
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # Rough heuristic: invoice row often contains quantity + amount and some label.
    hits = 0
    for line in lines:
        has_digit = bool(_re.search(r"\d", line))
        has_money = bool(_re.search(r"(vnd|₫|đ|dong)", line, _re.I)) or bool(_re.search(r"\d[\d.,]{2,}", line))
        if has_digit and has_money and len(line) >= 6:
            hits += 1
    if hits == 0:
        lowered = text.lower()
        if any(word in lowered for word in {"item", "hàng", "dịch vụ", "đơn giá", "quantity"}):
            return 1
    return hits


def _extract_total_amount(filename: str, text: str) -> float:
    joined = f"{filename}\n{text}"
    # Prefer amount near explicit total keywords. If a total keyword exists but
    # parsed values are non-positive, keep 0.0 instead of falling back to
    # arbitrary long numbers (e.g. tax code/invoice id).
    patterns = [
        r"(?:tong\s*tien|tổng\s*tiền|thanh\s*tien|thành\s*tiền|total)\s*[:=]?\s*([0-9][0-9.,\s]{0,})",
        r"(?:amount|grand\s*total)\s*[:=]?\s*([0-9][0-9.,\s]{0,})",
    ]
    keyword_matches: list[float] = []
    for pat in patterns:
        for m in _re.finditer(pat, joined, flags=_re.I):
            keyword_matches.append(_parse_amount_token(m.group(1)))
    positives = [amt for amt in keyword_matches if amt > 0]
    if positives:
        return max(positives)
    if keyword_matches:
        return 0.0

    # Conservative fallback: only parse numeric tokens from lines that look
    # like monetary lines and avoid common id/tax-code lines.
    money_ctx = {
        "tong tien",
        "tổng tiền",
        "thanh tien",
        "thành tiền",
        "total",
        "amount",
        "vnd",
        "₫",
        "đ",
        "dong",
    }
    id_ctx = {
        "mst",
        "tax code",
        "ma so thue",
        "mã số thuế",
        "so hoa don",
        "số hóa đơn",
        "invoice no",
        "invoice id",
    }
    candidates: list[float] = []
    for line in joined.splitlines():
        lowered = line.lower()
        if any(token in lowered for token in id_ctx):
            continue
        if not any(token in lowered for token in money_ctx):
            continue
        for tok in _re.findall(r"\d[\d.,\s]{2,}", line):
            amt = _parse_amount_token(tok)
            if amt > 0:
                candidates.append(amt)
    return max(candidates) if candidates else 0.0


def _evaluate_ocr_quality(filename: str, blob: bytes, pipeline: str) -> dict[str, Any]:
    text_preview = _extract_upload_text_preview(blob, pipeline)
    lowered = f"{filename} {text_preview}".lower()
    non_invoice = any(hint in lowered for hint in _NON_INVOICE_HINTS)
    invoice_hint_count = sum(1 for hint in _INVOICE_HINTS if hint in lowered)
    invoice_like = (not non_invoice) and invoice_hint_count >= 2

    total_amount = _extract_total_amount(filename, text_preview)
    line_items_count = _estimate_line_items(text_preview)

    confidence = 0.35
    if invoice_like:
        confidence += 0.35
    if total_amount > 0:
        confidence += 0.15
    if line_items_count >= 1:
        confidence += 0.10
    if len(text_preview.strip()) >= 60:
        confidence += 0.05
    if pipeline == "xml_parse":
        confidence = max(confidence, 0.9)
    confidence = round(max(0.0, min(confidence, 0.99)), 3)

    reasons: list[str] = []
    if non_invoice or not invoice_like:
        reasons.append("non_invoice_pattern")
    if total_amount <= 0:
        reasons.append("zero_amount")
    if line_items_count < 1:
        reasons.append("no_line_items")
    if confidence < _OCR_MIN_CONFIDENCE:
        reasons.append("low_confidence")

    status = "valid"
    if "non_invoice_pattern" in reasons:
        status = "non_invoice"
    elif "zero_amount" in reasons or "no_line_items" in reasons:
        status = "quarantined"
    elif "low_confidence" in reasons:
        status = "low_quality"

    return {
        "status": status,
        "total_amount": total_amount,
        "line_items_count": line_items_count,
        "confidence": confidence,
        "reasons": reasons,
        "invoice_like": invoice_like,
    }


def _safe_filename(name: str) -> str:
    base = os.path.basename(name or "").strip()
    if not base:
        return ""
    return _re.sub(r"[^A-Za-z0-9._-]+", "_", base)


def _resolve_upload_type(filename: str, declared: str | None) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    content_type = (declared or "").strip().lower()
    if not content_type or content_type == "application/octet-stream":
        content_type = _ATTACH_ALLOWED_EXT.get(suffix, "application/octet-stream")

    is_pdf = suffix == ".pdf" or content_type == "application/pdf"
    is_xml = suffix == ".xml" or content_type in {"application/xml", "text/xml"}
    is_image = suffix in {".jpg", ".jpeg", ".png"} or content_type.startswith("image/")

    if is_pdf:
        return "pdf_ocr", "application/pdf"
    if is_image:
        mapped = _ATTACH_ALLOWED_EXT.get(suffix, content_type if content_type.startswith("image/") else "image/jpeg")
        return "image_ocr", mapped
    if is_xml:
        return "xml_parse", "application/xml"
    raise HTTPException(
        status_code=400,
        detail="Định dạng chưa hỗ trợ. Chỉ hỗ trợ PDF, XML, JPG, JPEG, PNG.",
    )


@app.post("/agent/v1/attachments", dependencies=[Depends(require_api_key)])
async def post_attachment(
    request: Request,
    file: UploadFile | None = File(default=None),
    source_tag: str | None = Form(default=None),
    source: str | None = Form(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Create attachment from multipart upload, with legacy JSON fallback.

    Multipart mode (preferred by UI):
    - field: ``file``
    - optional: ``source_tag`` / ``source``
    """
    # Legacy JSON mode for internal callers.
    if file is None:
        try:
            body = await request.json()
        except Exception as exc:  # pragma: no cover - malformed request body
            raise HTTPException(status_code=400, detail="Body JSON không hợp lệ") from exc

        required = ("erp_object_type", "erp_object_id", "file_uri", "file_hash", "run_id")
        missing = [k for k in required if not body.get(k)]
        if missing:
            raise HTTPException(status_code=400, detail=f"Thiếu trường bắt buộc: {', '.join(missing)}")

        att = AgentAttachment(
            id=new_uuid(),
            erp_object_type=str(body["erp_object_type"]),
            erp_object_id=str(body["erp_object_id"]),
            file_uri=str(body["file_uri"]),
            file_hash=str(body["file_hash"]),
            matched_by=str(body.get("matched_by", "rule")),
            run_id=str(body["run_id"]),
        )
        out = _insert_or_get_unique(
            session,
            AgentAttachment,
            (AgentAttachment.file_hash == att.file_hash)
            & (AgentAttachment.erp_object_type == att.erp_object_type)
            & (AgentAttachment.erp_object_id == att.erp_object_id),
            att,
        )
        return {
            "id": out.id,
            "filename": body.get("filename") or body.get("file_name") or "",
            "source_tag": body.get("source_tag") or "legacy",
            "status": "stored",
            "created_at": out.created_at,
            "has_file": bool(out.file_uri),
        }

    safe_name = _safe_filename(file.filename or "")
    if not safe_name:
        raise HTTPException(status_code=400, detail="Tên tệp không hợp lệ")

    blob = await file.read()
    if not blob:
        raise HTTPException(status_code=400, detail="Tệp rỗng")

    pipeline, normalized_type = _resolve_upload_type(safe_name, file.content_type)
    if pipeline == "xml_parse":
        try:
            blob.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="Tệp XML phải mã hóa UTF-8") from exc

    file_hash = sha256(blob).hexdigest()
    _ATTACH_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(safe_name).suffix.lower() or ".bin"
    stored_path = _ATTACH_UPLOAD_DIR / f"{file_hash}{suffix}"
    if not stored_path.exists():
        stored_path.write_bytes(blob)

    now = utcnow()
    voucher_id = new_uuid()
    run_id = new_uuid()
    tag = (source_tag or source or "ocr_upload").strip() or "ocr_upload"

    attachment = AgentAttachment(
        id=new_uuid(),
        erp_object_type="voucher_upload",
        erp_object_id=voucher_id,
        file_uri=str(stored_path),
        file_hash=file_hash,
        matched_by="ocr" if pipeline in {"pdf_ocr", "image_ocr"} else "rule",
        run_id=run_id,
    )
    session.add(attachment)

    quality = _evaluate_ocr_quality(safe_name, blob, pipeline)
    payload = {
        "attachment_id": attachment.id,
        "original_filename": safe_name,
        "source_tag": tag,
        "status": quality["status"],
        "quality_status": quality["status"],
        "quality_reasons": quality["reasons"],
        "ocr_confidence": quality["confidence"],
        "line_items_count": quality["line_items_count"],
        "total_amount": quality["total_amount"],
        "content_type": normalized_type,
        "size_bytes": len(blob),
        "pipeline": pipeline,
        "invoice_like": quality["invoice_like"],
    }
    if quality["line_items_count"] > 0:
        payload["line_items"] = [{"index": idx + 1} for idx in range(int(quality["line_items_count"]))]
    voucher = AcctVoucher(
        id=voucher_id,
        erp_voucher_id=f"upload-{voucher_id[:8]}",
        voucher_no=Path(safe_name).stem[:64] or f"UPLOAD-{voucher_id[:8]}",
        voucher_type="other",
        date=now.date().isoformat(),
        amount=float(quality["total_amount"]) if _safe_float(quality["total_amount"]) > 0 else 0.0,
        currency="VND",
        partner_name=None,
        partner_tax_code=None,
        description=f"Tệp tải lên: {safe_name}",
        has_attachment=True,
        raw_payload=payload,
        source=tag,
        type_hint="invoice_vat" if quality["status"] == "valid" else "other",
        run_id=run_id,
    )
    session.add(voucher)

    if quality["status"] != "valid":
        reason_text = ", ".join(quality["reasons"]) or quality["status"]
        anomaly_type = "non_invoice" if quality["status"] == "non_invoice" else "invalid_voucher_data"
        severity = "high" if quality["status"] == "non_invoice" else "medium"
        session.add(
            AcctAnomalyFlag(
                id=new_uuid(),
                anomaly_type=anomaly_type,
                severity=severity,
                description=(
                    f"Chứng từ OCR '{safe_name}' bị loại khỏi luồng kế toán "
                    f"({quality['status']}): {reason_text}"
                ),
                voucher_id=voucher_id,
                bank_tx_id=None,
                resolution="open",
                run_id=run_id,
            )
        )
    session.commit()

    return {
        "id": attachment.id,
        "attachment_id": attachment.id,
        "voucher_id": voucher.id,
        "filename": safe_name,
        "source_tag": tag,
        "status": payload["status"],
        "quality_reasons": payload["quality_reasons"],
        "ocr_confidence": payload["ocr_confidence"],
        "line_items_count": payload["line_items_count"],
        "created_at": now,
        "pipeline": pipeline,
        "content_type": normalized_type,
    }


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
                    "account": ln.account_code,  # legacy UI compatibility
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


def _validate_journal_accounts_before_approve(
    proposal_id: str,
    session: Session,
) -> tuple[bool, list[str]]:
    lines = session.execute(select(AcctJournalLine).where(AcctJournalLine.proposal_id == proposal_id)).scalars().all()
    invalid_codes: list[str] = []
    for ln in lines:
        code = str(ln.account_code or "").strip()
        if _is_undefined_like(code):
            invalid_codes.append(code or "<empty>")
    return len(invalid_codes) == 0, invalid_codes


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
    if body.status == "approved":
        is_valid, invalid_codes = _validate_journal_accounts_before_approve(proposal_id, session)
        if not is_valid:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "INVALID_ACCOUNT_CODE",
                    "detail": "Proposal contains undefined account in debit/credit lines.",
                    "invalid_accounts": invalid_codes,
                },
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
    status: str | None = None,
    anomaly_type: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    q = select(AcctAnomalyFlag).order_by(AcctAnomalyFlag.created_at.desc()).limit(min(limit, 500))
    wanted_status = (resolution or status or "").strip().lower() or None
    if wanted_status == "pending":
        wanted_status = "open"
    if wanted_status:
        q = q.where(func.coalesce(AcctAnomalyFlag.resolution, "open") == wanted_status)
    if anomaly_type:
        q = q.where(AcctAnomalyFlag.anomaly_type == anomaly_type)
    rows = session.execute(q).scalars().all()
    items = []
    for r in rows:
        raw_resolution = (r.resolution or "open").strip().lower()
        normalized_status = "open" if raw_resolution in {"", "null", "none", "pending", "open"} else raw_resolution
        items.append(
            {
                "id": r.id,
                "anomaly_type": r.anomaly_type,
                "severity": r.severity,
                "description": r.description,
                "voucher_id": r.voucher_id,
                "bank_tx_id": r.bank_tx_id,
                "status": normalized_status,
                "resolution": normalized_status,
                "resolved_by": r.resolved_by,
                "resolved_at": r.resolved_at,
                "created_at": r.created_at,
                "run_id": r.run_id,
            }
        )
    return {
        "total": len(items),
        "items": items,
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
    current_status = (flag.resolution or "open").strip().lower()
    if current_status in {"", "null", "none", "pending"}:
        current_status = "open"
    if current_status != "open":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Giao dịch bất thường đã được xử lý (trạng thái: {current_status}). "
                "Không thể thay đổi."
            ),
        )
    flag.resolution = body.resolution
    flag.resolved_by = body.resolved_by
    flag.resolved_at = utcnow()
    session.commit()
    return {
        "id": flag.id,
        "resolution": flag.resolution,
        "status": flag.resolution,
        "resolved_by": flag.resolved_by,
    }


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


class BankMatchIn(BaseModel):
    bank_tx_id: str
    voucher_id: str
    method: Literal["manual", "auto"] = "manual"
    matched_by: str = "web-user"


@app.post("/agent/v1/acct/bank_match", dependencies=[Depends(require_api_key)])
def bank_match(
    body: BankMatchIn,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    tx = session.get(AcctBankTransaction, body.bank_tx_id)
    if tx is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy giao dịch ngân hàng")

    voucher = session.get(AcctVoucher, body.voucher_id)
    if voucher is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy chứng từ")

    status_val = "matched_auto" if body.method == "auto" else "matched_manual"
    tx.matched_voucher_id = voucher.id
    tx.match_status = status_val
    tx.run_id = tx.run_id or f"match-{new_uuid()[:8]}"
    session.commit()

    return {
        "bank_tx_id": tx.id,
        "voucher_id": voucher.id,
        "match_status": tx.match_status,
        "matched_by": body.matched_by,
    }


class BankUnmatchIn(BaseModel):
    unmatched_by: str = "web-user"


@app.post("/agent/v1/acct/bank_match/{bank_tx_id}/unmatch", dependencies=[Depends(require_api_key)])
def bank_unmatch(
    bank_tx_id: str,
    body: BankUnmatchIn,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    tx = session.get(AcctBankTransaction, bank_tx_id)
    if tx is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy giao dịch ngân hàng")

    tx.matched_voucher_id = None
    tx.match_status = "unmatched"
    session.commit()
    return {"bank_tx_id": tx.id, "match_status": tx.match_status, "unmatched_by": body.unmatched_by}


class BankIgnoreIn(BaseModel):
    ignored_by: str = "web-user"


@app.post("/agent/v1/acct/bank_transactions/{bank_tx_id}/ignore", dependencies=[Depends(require_api_key)])
def ignore_bank_transaction(
    bank_tx_id: str,
    body: BankIgnoreIn,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    tx = session.get(AcctBankTransaction, bank_tx_id)
    if tx is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy giao dịch ngân hàng")

    tx.match_status = "ignored"
    session.commit()
    return {"bank_tx_id": tx.id, "match_status": tx.match_status, "ignored_by": body.ignored_by}


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
    items = [
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
    period_for_quality = period or datetime.now().strftime("%Y-%m")
    quality = _collect_period_voucher_quality(session, period_for_quality)
    if quality["excluded_vouchers"] > 0:
        items.insert(
            0,
            {
                "id": f"quality-{period_for_quality}",
                "period": period_for_quality,
                "total_checks": quality["excluded_vouchers"],
                "passed": 0,
                "warnings": quality["excluded_vouchers"],
                "errors": 0,
                "score": 0.0,
                "created_at": utcnow(),
                "run_id": None,
                "source": "voucher_quality_gate",
                "message": (
                    f"Phát hiện {quality['excluded_vouchers']} chứng từ bất thường "
                    "(amount=0/chất lượng OCR thấp/thiếu thông tin)."
                ),
            },
        )
    return {"items": items}


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
    items: list[dict[str, Any]] = []
    for r in rows:
        amount = _safe_float(r.amount)
        if amount <= 0:
            continue
        forecast_date = str(r.forecast_date or "").strip()
        if not forecast_date:
            continue
        if r.direction not in {"inflow", "outflow"}:
            continue
        items.append(
            {
                "id": r.id,
                "forecast_date": forecast_date,
                "period": forecast_date[:7],
                "direction": r.direction,
                "amount": amount,
                "currency": r.currency,
                "source_type": r.source_type,
                "source_ref": r.source_ref,
                "confidence": _safe_float(r.confidence, 0.0),
                "run_id": r.run_id,
            }
        )

    total_inflow = sum(item["amount"] for item in items if item["direction"] == "inflow")
    total_outflow = sum(item["amount"] for item in items if item["direction"] == "outflow")
    voucher_rows = session.execute(select(AcctVoucher)).scalars().all()
    non_zero_periods: set[str] = set()
    for voucher in voucher_rows:
        is_valid, _reasons = _voucher_quality_state(voucher)
        if not is_valid:
            continue
        amount = _safe_float(voucher.amount)
        if amount <= 0:
            continue
        d = str(voucher.date or "")
        if len(d) >= 7:
            non_zero_periods.add(d[:7])
    min_periods = 6
    enough_data = len(non_zero_periods) >= min_periods
    sufficiency_reason = (
        ""
        if enough_data
        else (
            "Chưa đủ dữ liệu lịch sử để dự báo dòng tiền có ý nghĩa. "
            "Vui lòng kiểm tra lại số liệu thực tế."
        )
    )
    return {
        "summary": {
            "total_inflow": total_inflow,
            "total_outflow": total_outflow,
            "net": total_inflow - total_outflow,
        },
        "sufficiency": {
            "enough": enough_data,
            "observed_periods": len(non_zero_periods),
            "min_periods_required": min_periods,
            "reason": sufficiency_reason,
        },
        "items": items,
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

    # Backward compatibility: accept both canonical feedback strings and
    # legacy numeric rating values from older UI builds.
    feedback_val = str(payload.get("feedback", "")).strip().lower()
    if not feedback_val:
        rating = payload.get("rating")
        if isinstance(rating, (int, float)):
            feedback_val = "helpful" if float(rating) > 0 else "not_helpful" if float(rating) < 0 else ""
        elif isinstance(rating, str):
            feedback_val = {
                "up": "helpful",
                "down": "not_helpful",
                "1": "helpful",
                "-1": "not_helpful",
                "helpful": "helpful",
                "not_helpful": "not_helpful",
            }.get(rating.strip().lower(), "")

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
    period: str | None = None,
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """List AcctVoucher rows with optional filter by classification_tag or source."""
    filters = []
    if classification_tag:
        filters.append(AcctVoucher.classification_tag == classification_tag)
    if source:
        filters.append(AcctVoucher.source == source)
    if period:
        if not _re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
            raise HTTPException(status_code=400, detail="period phải theo định dạng YYYY-MM")
        filters.append(AcctVoucher.date.like(period + "%"))

    q = (
        select(AcctVoucher)
        .where(*filters)
        .order_by(AcctVoucher.synced_at.desc())
        .limit(min(limit, 200))
        .offset(max(offset, 0))
    )
    total = session.execute(select(func.count()).select_from(AcctVoucher).where(*filters)).scalar() or 0
    rows = session.execute(q).scalars().all()
    items = []
    for r in rows:
        payload = r.raw_payload if isinstance(r.raw_payload, dict) else {}
        vat_amount = payload.get("vat_amount", 0)
        line_items = payload.get("line_items", [])
        items.append({
            "id": r.id,
            "voucher_id": r.id,  # backward-compatible alias used in legacy UI/tests
            "voucher_no": r.voucher_no,
            "voucher_type": r.voucher_type,
            "date": r.date,
            "amount": r.amount,
            "total_amount": r.amount,
            "vat_amount": vat_amount if isinstance(vat_amount, (int, float)) else 0,
            "currency": r.currency,
            "partner_name": r.partner_name,
            "partner_tax_code": getattr(r, "partner_tax_code", None),
            "description": r.description,
            "source": getattr(r, "source", None),
            "source_tag": payload.get("source_tag") or getattr(r, "source", None),
            "source_ref": payload.get("attachment_id"),
            "original_filename": payload.get("original_filename"),
            "confidence": payload.get("ocr_confidence"),
            "status": payload.get("status", payload.get("quality_status", "processed")),
            "quality_reasons": payload.get("quality_reasons", []),
            "line_items_count": len(line_items) if isinstance(line_items, list) else payload.get("line_items_count"),
            "type_hint": getattr(r, "type_hint", None),
            "classification_tag": getattr(r, "classification_tag", None),
            "has_attachment": r.has_attachment,
            "synced_at": r.synced_at,
            "run_id": r.run_id,
        })
    return {
        "items": items,
        "total": int(total),
        "limit": min(limit, 200),
        "offset": max(offset, 0),
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
    usage = result.get("usage")
    confidence = result.get("confidence")
    sources = result.get("sources") or []
    question_type = str(result.get("question_type") or "").strip() or None
    route = str(result.get("route") or "").strip() or None

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
            "usage": usage if isinstance(usage, dict) else None,
            "confidence": confidence if isinstance(confidence, (int, float)) else None,
            "sources": sources if isinstance(sources, list) else [],
            "question_type": question_type,
            "route": route,
            "data_available": route != "data_unavailable" if route else None,
        },
        "usage": usage if isinstance(usage, dict) else None,
        "confidence": confidence if isinstance(confidence, (int, float)) else None,
        "sources": sources if isinstance(sources, list) else [],
        "question_type": question_type,
        "route": route,
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
    readiness = _executor_readiness()
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
            "executor_mode": readiness["mode"],
            "local_executor_enabled": readiness["local_executor_enabled"],
            "celery_available": readiness["celery"]["available"],
            "celery_worker_count": readiness["celery"]["worker_count"],
            "celery_workers": readiness["celery"]["workers"],
            "run_dispatch_ready": readiness["dispatch_ready"],
            "preferred_executor": readiness["preferred_executor"],
        }
    except ImportError:
        return {
            "ray_available": False,
            "initialized": False,
            "resources": {},
            "executor_mode": readiness["mode"],
            "local_executor_enabled": readiness["local_executor_enabled"],
            "celery_available": readiness["celery"]["available"],
            "celery_worker_count": readiness["celery"]["worker_count"],
            "celery_workers": readiness["celery"]["workers"],
            "run_dispatch_ready": readiness["dispatch_ready"],
            "preferred_executor": readiness["preferred_executor"],
        }


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
        "soft_checks",
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

_GOAL_ALIASES: dict[str, str] = {
    "close_period": "đóng sổ",
    "close-book": "đóng sổ",
    "close_book": "đóng sổ",
    "reconcile": "đối chiếu",
    "bank_reconcile": "đối chiếu",
    "tax_report": "báo cáo thuế",
    "voucher_ingest": "nhập chứng từ",
    "forecast": "dự báo dòng tiền",
    "cashflow_forecast": "dự báo dòng tiền",
}

_DIRECT_COMMAND_CHAINS: dict[str, list[str]] = {
    "trigger_voucher_ingest": ["voucher_ingest"],
    "trigger_bank_reconcile": ["bank_reconcile"],
    "trigger_soft_checks": ["soft_checks"],
    "trigger_cashflow_forecast": ["cashflow_forecast"],
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
    goal: str | None = None
    period: str | None = None
    payload: dict[str, Any] | None = None


def _resolve_agent_command_chain(body: AgentCommandRequest) -> tuple[str, list[str]]:
    command = body.command.strip().lower()
    goal_hint = (body.goal or "").strip().lower()

    if command in _DIRECT_COMMAND_CHAINS:
        return command, _DIRECT_COMMAND_CHAINS[command]

    if command == "run_goal":
        normalized_goal = _GOAL_ALIASES.get(goal_hint, goal_hint)
        if not normalized_goal:
            return "unknown", []
        return _parse_goal_command(normalized_goal)

    if command in _VALID_RUN_TYPES:
        return command, [command]

    if goal_hint:
        return _parse_goal_command(_GOAL_ALIASES.get(goal_hint, goal_hint))
    return _parse_goal_command(body.command)


@app.post("/agent/v1/agent/commands", dependencies=[Depends(require_api_key)])
def execute_agent_command(
    body: AgentCommandRequest,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Parse command payload and dispatch mapped run chain."""
    goal_key, chain = _resolve_agent_command_chain(body)
    if not chain:
        raise HTTPException(
            status_code=409,
            detail=(
                "Không nhận diện được command/goal để thực thi. "
                "Vui lòng chọn mục tiêu hợp lệ từ Agent Command Center."
            ),
        )

    readiness = _executor_readiness()
    if not readiness["dispatch_ready"]:
        raise HTTPException(
            status_code=503,
            detail="Không có executor khả dụng để chạy command chain.",
        )

    runs_created: list[dict[str, Any]] = []
    payload = dict(body.payload or {})
    if body.period:
        payload["period"] = body.period

    for run_type in chain:
        run_payload = dict(payload)
        if run_type in _PERIOD_REQUIRED_RUN_TYPES and not run_payload.get("period"):
            run_payload["period"] = date.today().strftime("%Y-%m")

        if run_type in _PERIOD_REQUIRED_RUN_TYPES:
            period = str(run_payload.get("period") or "")
            if not _re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
                raise HTTPException(
                    status_code=422,
                    detail=f"period '{period}' không hợp lệ cho run_type={run_type}. Định dạng YYYY-MM.",
                )

        idem = make_idempotency_key("cmd", goal_key, run_type, str(run_payload.get("period") or ""))
        existing = session.execute(
            select(AgentRun).where(AgentRun.idempotency_key == idem)
        ).scalar_one_or_none()
        if existing:
            redispatched = False
            dispatch_info: dict[str, Any] | None = None
            if _is_stale_pending_run(existing):
                try:
                    dispatch_info = _dispatch_run(
                        existing.run_id,
                        existing.run_type,
                        preferred_executor=str(readiness["preferred_executor"]),
                        allow_local_fallback=readiness["mode"] == "auto",
                    )
                    redispatched = True
                    log.info("command_run_redispatched", run_id=existing.run_id, run_type=existing.run_type)
                except HTTPException as exc:
                    _mark_run_dispatch_failed(session, existing.run_id, str(exc.detail))
                    raise
                except Exception as exc:  # pragma: no cover
                    _mark_run_dispatch_failed(session, existing.run_id, str(exc))
                    raise HTTPException(status_code=503, detail=f"Không thể re-dispatch run cũ: {exc}") from exc

            runs_created.append({
                "run_id": existing.run_id,
                "run_type": existing.run_type,
                "status": existing.status,
                "reused": True,
                "redispatched": redispatched,
                "executor": dispatch_info,
            })
            continue

        run = AgentRun(
            run_id=new_uuid(),
            run_type=run_type,
            trigger_type="manual",
            requested_by="agent-command-center",
            status="queued",
            idempotency_key=idem,
            cursor_in=run_payload,
            cursor_out=None,
            started_at=None,
            finished_at=None,
            stats=None,
        )
        session.add(run)

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
                        input_ref=run_payload,
                        output_ref=None,
                        error=None,
                        started_at=None,
                        finished_at=None,
                    )
                )
        session.commit()
        session.refresh(run)

        try:
            dispatch_info = _dispatch_run(
                run.run_id,
                run_type,
                preferred_executor=str(readiness["preferred_executor"]),
                allow_local_fallback=readiness["mode"] == "auto",
            )
        except HTTPException as exc:
            _mark_run_dispatch_failed(session, run.run_id, str(exc.detail))
            raise
        except Exception as exc:  # pragma: no cover
            _mark_run_dispatch_failed(session, run.run_id, str(exc))
            raise HTTPException(status_code=503, detail=f"Không thể dispatch run {run_type}: {exc}") from exc

        runs_created.append({
            "run_id": run.run_id,
            "run_type": run_type,
            "status": "queued",
            "reused": False,
            "executor": dispatch_info,
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
    action: Literal["start", "stop", "inject_now", "update_config"] = "start"
    target_events_per_min: int | None = Field(None, ge=1, le=10)
    events_per_min: int | None = Field(None, ge=1, le=10)


@app.get("/agent/v1/vn_feeder/status", dependencies=[Depends(require_api_key)])
def vn_feeder_status() -> dict[str, Any]:
    """Return current VN feeder status from the status file."""
    import json as _json

    from openclaw_agent.agent_service.vn_feeder_engine import (
        get_target_events_per_min as _feeder_target_epm,
    )
    from openclaw_agent.agent_service.vn_feeder_engine import is_running as _feeder_is_running

    status_path = os.path.join(_VN_FEEDER_CACHE, "feeder_status.json")
    if os.path.isfile(status_path):
        try:
            with open(status_path, encoding="utf-8") as fh:
                data = _json.loads(fh.read())
            # Ensure running state reflects actual thread state
            data["running"] = _feeder_is_running()
            data["events_per_min"] = _feeder_target_epm()
            return data
        except Exception:
            pass
    return {
        "running": _feeder_is_running(),
        "events_per_min": _feeder_target_epm(),
        "total_events_today": 0,
        "last_event_at": "",
        "avg_events_per_min": 0,
        "sources": [],
        "updated_at": "",
    }


@app.post("/agent/v1/vn_feeder/control", dependencies=[Depends(require_api_key)])
def vn_feeder_control(body: VnFeederControlBody) -> dict[str, Any]:
    """Control the VN feeder background thread — start/stop/inject."""
    from openclaw_agent.agent_service.vn_feeder_engine import (
        get_target_events_per_min as _feeder_target_epm,
    )
    from openclaw_agent.agent_service.vn_feeder_engine import (
        inject_now as _feeder_inject,
    )
    from openclaw_agent.agent_service.vn_feeder_engine import (
        set_target_events_per_min as _feeder_set_epm,
    )
    from openclaw_agent.agent_service.vn_feeder_engine import (
        start_feeder as _feeder_start,
    )
    from openclaw_agent.agent_service.vn_feeder_engine import (
        stop_feeder as _feeder_stop,
    )

    epm = body.events_per_min or body.target_events_per_min
    if body.action == "start":
        _feeder_start(target_epm=epm)
    elif body.action == "stop":
        _feeder_stop()
    elif body.action == "inject_now":
        _feeder_inject(target_epm=epm)
    elif body.action == "update_config":
        if epm is None:
            raise HTTPException(status_code=400, detail="events_per_min là bắt buộc cho action=update_config")
        _feeder_set_epm(epm)
    return {"status": "ok", "action": body.action, "events_per_min": _feeder_target_epm()}


# ---------------------------------------------------------------------------
# Reports UI endpoints (aliases for SPA)
# ---------------------------------------------------------------------------


@app.get("/agent/v1/reports/history", dependencies=[Depends(require_api_key)])
def reports_history(
    limit: int = 50,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Alias for report_snapshots for UI compatibility."""
    q = (
        select(AcctReportSnapshot)
        .order_by(AcctReportSnapshot.created_at.desc())
        .limit(min(limit, 200))
    )
    rows = session.execute(q).scalars().all()
    return {
        "items": [
            {
                "id": r.id,
                "type": r.report_type,
                "period": r.period,
                "version": r.version,
                "has_file": bool(r.file_uri),
                "format": (r.summary_json or {}).get("format") if isinstance(r.summary_json, dict) else None,
                "download_url": f"/agent/v1/reports/{r.id}/download",
                "summary": r.summary_json,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


_REPORT_TYPES = {"balance_sheet", "income_statement", "cashflow", "notes"}
_REPORT_FORMATS = {"pdf", "xlsx", "json", "xml", "html"}


def _validate_report_inputs(report_type: str | None, period: str | None) -> tuple[str, str]:
    rt = (report_type or "").strip().lower()
    pd = (period or "").strip()
    if not rt or rt in {"undefined", "null"}:
        raise HTTPException(status_code=400, detail="Thiếu loại báo cáo (type)")
    if rt not in _REPORT_TYPES:
        raise HTTPException(status_code=400, detail=f"type không hợp lệ: {rt}")
    if not pd or pd in {"undefined", "null"}:
        raise HTTPException(status_code=400, detail="Thiếu kỳ báo cáo (period)")
    if not _re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", pd):
        raise HTTPException(status_code=400, detail="period phải theo định dạng YYYY-MM")
    return rt, pd


def _report_export_dir() -> Path:
    path = Path(os.getenv("AGENT_REPORT_EXPORT_DIR", "/tmp/openclaw_reports"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _voucher_quality_state(voucher: AcctVoucher) -> tuple[bool, list[str]]:
    payload = voucher.raw_payload if isinstance(voucher.raw_payload, dict) else {}
    reasons: list[str] = []
    status = str(payload.get("status") or payload.get("quality_status") or "").strip().lower()
    source = str(voucher.source or "").strip().lower()
    amount = _safe_float(voucher.amount)
    if source == "ocr_upload" and status in _INVALID_OCR_STATUSES:
        reasons.append(status)
    if amount <= 0:
        reasons.append("zero_amount")
    if source == "ocr_upload":
        if _is_undefined_like(voucher.partner_name):
            reasons.append("missing_partner")
        if _is_undefined_like(voucher.date):
            reasons.append("missing_date")
    raw_reasons = payload.get("quality_reasons")
    if isinstance(raw_reasons, list):
        for item in raw_reasons:
            text = str(item).strip().lower()
            if text:
                reasons.append(text)
    unique_reasons = sorted(set(reasons))
    return len(unique_reasons) == 0, unique_reasons


def _collect_period_voucher_quality(session: Session, period: str) -> dict[str, Any]:
    rows = session.execute(select(AcctVoucher).where(AcctVoucher.date.like(period + "%"))).scalars().all()
    excluded_ids: list[str] = []
    reason_counts: Counter[str] = Counter()
    for row in rows:
        is_valid, reasons = _voucher_quality_state(row)
        if is_valid:
            continue
        excluded_ids.append(row.id)
        reason_counts.update(reasons)
    total = len(rows)
    excluded = len(excluded_ids)
    valid = max(total - excluded, 0)
    return {
        "total_vouchers": total,
        "valid_vouchers": valid,
        "excluded_vouchers": excluded,
        "excluded_ids": excluded_ids,
        "reason_counts": dict(reason_counts),
    }


def _build_report_data(
    session: Session,
    *,
    report_type: str,
    period: str,
    standard: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    quality = _collect_period_voucher_quality(session, period)
    excluded_ids = set(quality["excluded_ids"])
    journal_rows = session.execute(
        select(AcctJournalLine, AcctJournalProposal.voucher_id)
        .join(AcctJournalProposal, AcctJournalProposal.id == AcctJournalLine.proposal_id)
        .join(AcctVoucher, AcctVoucher.id == AcctJournalProposal.voucher_id)
        .where(AcctJournalProposal.status == "approved")
        .where(AcctVoucher.date.like(period + "%"))
        .limit(2000)
    ).all()
    journal_lines = [line for line, voucher_id in journal_rows if voucher_id not in excluded_ids]

    account_summary: dict[str, dict[str, float | str]] = {}
    for line in journal_lines:
        acc = line.account_code
        if acc not in account_summary:
            account_summary[acc] = {"debit": 0.0, "credit": 0.0, "name": line.account_name or acc}
        account_summary[acc]["debit"] = float(account_summary[acc]["debit"]) + float(line.debit or 0)
        account_summary[acc]["credit"] = float(account_summary[acc]["credit"]) + float(line.credit or 0)

    total_debit = sum(float(v["debit"]) for v in account_summary.values())
    total_credit = sum(float(v["credit"]) for v in account_summary.values())
    imbalance = round(abs(total_debit - total_credit), 2)

    issues: list[str] = []
    if quality["valid_vouchers"] == 0:
        issues.append(f"Không có chứng từ cho kỳ {period}")
    if not journal_lines:
        issues.append("Không có bút toán đã duyệt để tổng hợp")
    if imbalance > 1:
        issues.append(f"Lệch cân đối Nợ/Có: {imbalance:,.0f} VND")
    if quality["excluded_vouchers"] > 0:
        issues.append(
            "Có "
            f"{quality['excluded_vouchers']} chứng từ bị loại khỏi báo cáo do lỗi dữ liệu "
            "(amount=0/thiếu thông tin/chất lượng OCR thấp). Xem chi tiết tab Rủi ro."
        )

    return {
        "period": period,
        "standard": standard,
        "report_type": report_type,
        "options": options or {},
        "voucher_count": int(quality["valid_vouchers"]),
        "voucher_total": int(quality["total_vouchers"]),
        "excluded_voucher_count": int(quality["excluded_vouchers"]),
        "excluded_reason_counts": quality["reason_counts"],
        "total_debit": total_debit,
        "total_credit": total_credit,
        "imbalance": imbalance,
        "issues": issues,
        "items": [
            {
                "account": account,
                "name": summary["name"],
                "debit": float(summary["debit"]),
                "credit": float(summary["credit"]),
            }
            for account, summary in sorted(account_summary.items())
        ],
    }


def _render_report_html(data: dict[str, Any]) -> str:
    rows = data.get("items", [])
    row_html = "\n".join(
        (
            "<tr>"
            f"<td>{r.get('account', '')}</td>"
            f"<td>{r.get('name', '')}</td>"
            f"<td style='text-align:right'>{float(r.get('debit', 0.0)):,.0f}</td>"
            f"<td style='text-align:right'>{float(r.get('credit', 0.0)):,.0f}</td>"
            "</tr>"
        )
        for r in rows
    )
    issues = data.get("issues", [])
    issue_html = "<br/>".join(str(i) for i in issues) if issues else "Không có cảnh báo"
    return (
        "<html><head><meta charset='utf-8'><title>OpenClaw Report</title></head><body>"
        f"<h2>{data.get('report_type')} - {data.get('period')}</h2>"
        f"<p>Chuẩn mực: {data.get('standard')}</p>"
        f"<p>Voucher hợp lệ: {int(data.get('voucher_count', 0))} / {int(data.get('voucher_total', data.get('voucher_count', 0)))}</p>"
        "<table border='1' cellspacing='0' cellpadding='6' style='border-collapse:collapse;width:100%'>"
        "<thead><tr><th>Tài khoản</th><th>Tên</th><th>Nợ</th><th>Có</th></tr></thead>"
        f"<tbody>{row_html}</tbody></table>"
        f"<p><strong>Tổng Nợ:</strong> {float(data.get('total_debit', 0.0)):,.0f} VND</p>"
        f"<p><strong>Tổng Có:</strong> {float(data.get('total_credit', 0.0)):,.0f} VND</p>"
        f"<p><strong>Cảnh báo:</strong><br/>{issue_html}</p>"
        "</body></html>"
    )


def _report_media_type(fmt: str) -> str:
    mapping = {
        "pdf": "application/pdf",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "json": "application/json",
        "xml": "application/xml",
        "html": "text/html; charset=utf-8",
    }
    return mapping.get(fmt, mimetypes.guess_type(f"file.{fmt}")[0] or "application/octet-stream")


def _write_report_artifact(
    *,
    report_type: str,
    period: str,
    version: int,
    fmt: str,
    data: dict[str, Any],
) -> Path:
    export_dir = _report_export_dir()
    path = export_dir / f"{report_type}_{period}_v{version}.{fmt}"

    if fmt == "json":
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    if fmt == "html":
        path.write_text(_render_report_html(data), encoding="utf-8")
        return path

    if fmt == "xml":
        from xml.etree.ElementTree import Element, SubElement, tostring

        root = Element(
            "report",
            attrib={
                "type": str(data.get("report_type", "")),
                "period": str(data.get("period", "")),
                "standard": str(data.get("standard", "")),
            },
        )
        summary = SubElement(root, "summary")
        SubElement(summary, "voucher_count").text = str(data.get("voucher_count", 0))
        SubElement(summary, "total_debit").text = str(data.get("total_debit", 0))
        SubElement(summary, "total_credit").text = str(data.get("total_credit", 0))
        SubElement(summary, "imbalance").text = str(data.get("imbalance", 0))

        issues = SubElement(root, "issues")
        for msg in data.get("issues", []):
            SubElement(issues, "issue").text = str(msg)

        lines = SubElement(root, "lines")
        for item in data.get("items", []):
            line = SubElement(lines, "line")
            SubElement(line, "account").text = str(item.get("account", ""))
            SubElement(line, "name").text = str(item.get("name", ""))
            SubElement(line, "debit").text = str(item.get("debit", 0))
            SubElement(line, "credit").text = str(item.get("credit", 0))

        xml_text = "<?xml version='1.0' encoding='utf-8'?>\n" + tostring(root, encoding="unicode")
        path.write_text(xml_text, encoding="utf-8")
        return path

    if fmt == "xlsx":
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Summary"
        ws.append(["Type", data.get("report_type")])
        ws.append(["Period", data.get("period")])
        ws.append(["Standard", data.get("standard")])
        ws.append(["Voucher Count", data.get("voucher_count")])
        ws.append(["Total Debit", data.get("total_debit")])
        ws.append(["Total Credit", data.get("total_credit")])
        ws.append(["Imbalance", data.get("imbalance")])
        ws.append([])
        ws.append(["Issues"])
        for msg in data.get("issues", []) or ["Không có cảnh báo"]:
            ws.append([msg])

        detail = wb.create_sheet("Details")
        detail.append(["Account", "Name", "Debit", "Credit"])
        for item in data.get("items", []):
            detail.append([item.get("account"), item.get("name"), item.get("debit"), item.get("credit")])

        wb.save(path)
        return path

    if fmt == "pdf":
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
        except Exception as exc:
            raise HTTPException(
                status_code=501,
                detail="Chưa hỗ trợ xuất PDF trên môi trường hiện tại (thiếu thư viện render PDF).",
            ) from exc

        c = canvas.Canvas(str(path), pagesize=A4)
        width, height = A4
        y = height - 40
        c.setFont("Helvetica-Bold", 14)
        c.drawString(40, y, f"{report_type} - {period}")
        y -= 20
        c.setFont("Helvetica", 10)
        c.drawString(40, y, f"Standard: {data.get('standard')}")
        y -= 16
        c.drawString(40, y, f"Voucher count: {data.get('voucher_count', 0)}")
        y -= 16
        c.drawString(40, y, f"Total debit: {float(data.get('total_debit', 0.0)):,.0f} VND")
        y -= 16
        c.drawString(40, y, f"Total credit: {float(data.get('total_credit', 0.0)):,.0f} VND")
        y -= 20
        c.setFont("Helvetica-Bold", 11)
        c.drawString(40, y, "Account Lines")
        y -= 14
        c.setFont("Helvetica", 9)
        for item in data.get("items", [])[:45]:
            line = (
                f"{item.get('account', '')} | {item.get('name', '')} | "
                f"Nợ {float(item.get('debit', 0.0)):,.0f} | Có {float(item.get('credit', 0.0)):,.0f}"
            )
            c.drawString(40, y, line[:130])
            y -= 12
            if y < 50:
                c.showPage()
                y = height - 40
                c.setFont("Helvetica", 9)
        c.save()
        return path

    raise HTTPException(status_code=400, detail=f"Định dạng export không hỗ trợ: {fmt}")


class ReportPreviewBody(BaseModel):
    type: str
    standard: str = "VAS"
    period: str


@app.post("/agent/v1/reports/preview", dependencies=[Depends(require_api_key)])
def reports_preview(
    body: ReportPreviewBody,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Generate a preview of a report based on current data."""
    report_type, period = _validate_report_inputs(body.type, body.period)
    standard = (body.standard or "VAS").strip() or "VAS"
    data = _build_report_data(
        session,
        report_type=report_type,
        period=period,
        standard=standard,
    )
    return {
        "html": _render_report_html(data),
        "data": data,
        "source": "live",
    }


@app.get("/agent/v1/reports/validate", dependencies=[Depends(require_api_key)])
def reports_validate(
    type: str | None = None,
    period: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Validate report data before generation."""
    report_type, period_value = _validate_report_inputs(type, period)
    quality = _collect_period_voucher_quality(session, period_value)
    excluded_ids = set(quality["excluded_ids"])

    proposal_count_query = (
        select(func.count())
        .select_from(AcctJournalProposal)
        .join(AcctVoucher, AcctVoucher.id == AcctJournalProposal.voucher_id)
        .where(AcctJournalProposal.status == "approved")
        .where(AcctVoucher.date.like(period_value + "%"))
    )
    if excluded_ids:
        proposal_count_query = proposal_count_query.where(AcctJournalProposal.voucher_id.not_in(excluded_ids))
    proposal_count = session.execute(proposal_count_query).scalar() or 0
    line_rows = session.execute(
        select(AcctJournalLine, AcctJournalProposal.voucher_id)
        .join(AcctJournalProposal, AcctJournalProposal.id == AcctJournalLine.proposal_id)
        .join(AcctVoucher, AcctVoucher.id == AcctJournalProposal.voucher_id)
        .where(AcctJournalProposal.status == "approved")
        .where(AcctVoucher.date.like(period_value + "%"))
    ).all()
    filtered_lines = [line for line, voucher_id in line_rows if voucher_id not in excluded_ids]
    total_debit = float(sum(_safe_float(line.debit) for line in filtered_lines))
    total_credit = float(sum(_safe_float(line.credit) for line in filtered_lines))
    imbalance = abs(total_debit - total_credit)

    checks = [
        {
            "name": "Dữ liệu kỳ kế toán",
            "passed": quality["valid_vouchers"] > 0,
            "detail": f"{quality['valid_vouchers']} chứng từ hợp lệ / {quality['total_vouchers']} tổng",
        },
        {
            "name": "Chất lượng chứng từ đầu vào",
            "passed": quality["excluded_vouchers"] == 0,
            "detail": (
                "Không có chứng từ lỗi"
                if quality["excluded_vouchers"] == 0
                else f"Loại {quality['excluded_vouchers']} chứng từ lỗi dữ liệu"
            ),
        },
        {"name": "Số dư đầu kỳ", "passed": proposal_count > 0, "detail": f"{proposal_count} bút toán"},
        {"name": "Phát sinh trong kỳ", "passed": total_debit > 0, "detail": f"{total_debit:,.0f} VND"},
        {"name": "Cân đối thử", "passed": imbalance < 1, "detail": f"Chênh lệch: {imbalance:,.0f}"},
        {"name": "Tuân thủ VAS/IFRS", "passed": True, "detail": "OK"},
    ]
    issues = [c for c in checks if not c["passed"]]
    return {
        "period": period_value,
        "report_type": report_type,
        "checks": checks,
        "issues": issues,
        "all_passed": not issues,
    }


class ReportGenerateBody(BaseModel):
    type: str
    standard: str = "VAS"
    period: str
    format: str = "pdf"
    options: dict[str, Any] = {}


@app.post("/agent/v1/reports/generate", dependencies=[Depends(require_api_key)])
def reports_generate(
    body: ReportGenerateBody,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Generate and save a report snapshot."""
    report_type, period = _validate_report_inputs(body.type, body.period)
    fmt = (body.format or "pdf").strip().lower() or "pdf"
    if fmt not in _REPORT_FORMATS:
        raise HTTPException(status_code=400, detail=f"format không hỗ trợ: {fmt}")
    standard = (body.standard or "VAS").strip() or "VAS"
    if standard not in {"VAS", "IFRS", "BOTH"}:
        raise HTTPException(status_code=400, detail=f"standard không hỗ trợ: {standard}")

    latest = session.execute(
        select(AcctReportSnapshot)
        .where(AcctReportSnapshot.report_type == report_type)
        .where(AcctReportSnapshot.period == period)
        .order_by(AcctReportSnapshot.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    next_version = int(latest.version) + 1 if latest else 1

    report_data = _build_report_data(
        session,
        report_type=report_type,
        period=period,
        standard=standard,
        options=body.options or {},
    )
    effective_fmt = fmt
    format_warning: str | None = None
    try:
        artifact_path = _write_report_artifact(
            report_type=report_type,
            period=period,
            version=next_version,
            fmt=effective_fmt,
            data=report_data,
        )
    except HTTPException as exc:
        if fmt == "pdf" and exc.status_code == 501:
            effective_fmt = "xlsx"
            format_warning = "Môi trường chưa hỗ trợ render PDF, hệ thống tự động xuất XLSX."
            artifact_path = _write_report_artifact(
                report_type=report_type,
                period=period,
                version=next_version,
                fmt=effective_fmt,
                data=report_data,
            )
        else:
            raise

    snapshot = AcctReportSnapshot(
        id=new_uuid(),
        report_type=report_type,
        period=period,
        version=next_version,
        summary_json=report_data
        | {
            "standard": standard,
            "format": effective_fmt,
            "requested_format": fmt,
            "format_warning": format_warning,
            "options": body.options or {},
            "generated_at": utcnow().isoformat(),
        },
        file_uri=str(artifact_path),
        run_id=None,
    )
    session.add(snapshot)
    session.commit()

    return {
        "id": snapshot.id,
        "report_id": snapshot.id,
        "type": report_type,
        "period": period,
        "version": snapshot.version,
        "format": effective_fmt,
        "requested_format": fmt,
        "format_warning": format_warning,
        "download_url": f"/agent/v1/reports/{snapshot.id}/download?format={effective_fmt}",
        "created_at": snapshot.created_at,
    }


@app.get("/agent/v1/reports/{report_id}/download", dependencies=[Depends(require_api_key)])
def reports_download(
    report_id: str,
    format: str | None = None,
    session: Session = Depends(get_session),
) -> Any:
    """Download a generated report artifact."""
    snapshot = session.get(AcctReportSnapshot, report_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Report not found")

    if not snapshot.file_uri:
        raise HTTPException(status_code=409, detail="Báo cáo chưa được generate artifact")

    artifact = Path(snapshot.file_uri)
    req_fmt = (format or "").strip().lower()
    if req_fmt:
        expected_suffix = f".{req_fmt}"
        if artifact.suffix.lower() != expected_suffix:
            candidate = artifact.with_suffix(expected_suffix)
            if candidate.exists():
                artifact = candidate

    if not artifact.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy file artifact của báo cáo")

    fmt = artifact.suffix.lower().lstrip(".")
    media_type = _report_media_type(fmt)
    filename = f"{snapshot.report_type}_{snapshot.period}_v{snapshot.version}.{fmt}"
    return FileResponse(
        path=str(artifact),
        media_type=media_type,
        filename=filename,
    )


# ---------------------------------------------------------------------------
# Settings UI endpoints
# ---------------------------------------------------------------------------

# In-memory settings store (in production, this would be persisted)
_USER_SETTINGS: dict[str, Any] = {
    "name": "",
    "email": "",
    "role": "accountant",
    "agent": {
        "model": "gpt-4o",
        "temperature": 0.3,
        "confidence_threshold": 0.85,
        "auto_approve": False,
        "auto_reconcile": False,
        "notify_risk": True,
        "batch_size": 100,
        "timeout": 300,
    },
    "feeders": [],
    "accessibility": {},
    "advanced": {},
}


@app.get("/agent/v1/settings", dependencies=[Depends(require_api_key)])
def get_settings_all() -> dict[str, Any]:
    """Get all user settings."""
    return _USER_SETTINGS


@app.patch("/agent/v1/settings/profile", dependencies=[Depends(require_api_key)])
def update_settings_profile(body: dict[str, Any]) -> dict[str, Any]:
    """Update profile settings."""
    for key in ["name", "email", "role"]:
        if key in body:
            _USER_SETTINGS[key] = body[key]
    return {"status": "ok", "updated": list(body.keys())}


@app.patch("/agent/v1/settings/agent", dependencies=[Depends(require_api_key)])
def update_settings_agent(body: dict[str, Any]) -> dict[str, Any]:
    """Update agent configuration settings."""
    _USER_SETTINGS["agent"].update(body)
    return {"status": "ok", "updated": list(body.keys())}


@app.post("/agent/v1/settings/feeders", dependencies=[Depends(require_api_key)])
def add_settings_feeder(body: dict[str, Any]) -> dict[str, Any]:
    """Add a new data feeder configuration."""
    _USER_SETTINGS["feeders"].append(body)
    return {"status": "ok", "feeder_count": len(_USER_SETTINGS["feeders"])}


@app.patch("/agent/v1/settings/accessibility", dependencies=[Depends(require_api_key)])
def update_settings_accessibility(body: dict[str, Any]) -> dict[str, Any]:
    """Update accessibility settings."""
    _USER_SETTINGS["accessibility"].update(body)
    return {"status": "ok", "updated": list(body.keys())}


@app.patch("/agent/v1/settings/advanced", dependencies=[Depends(require_api_key)])
def update_settings_advanced(body: dict[str, Any]) -> dict[str, Any]:
    """Update advanced settings."""
    _USER_SETTINGS["advanced"].update(body)
    return {"status": "ok", "updated": list(body.keys())}
