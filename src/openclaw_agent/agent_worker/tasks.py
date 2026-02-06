from __future__ import annotations

import csv
import re
import shutil
import tempfile
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pdfplumber
import pytesseract
from botocore.exceptions import BotoCoreError, ClientError
from celery import Task
from openpyxl import Workbook
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from openclaw_agent.agent_worker.celery_app import celery_app
from openclaw_agent.common.db import db_session, make_engine
from openclaw_agent.common.erpx_client import ErpXClient
from openclaw_agent.common.logging import configure_logging, get_logger
from openclaw_agent.common.models import (
    AgentAttachment,
    AgentCloseTask,
    AgentEvidencePack,
    AgentException,
    AgentExport,
    AgentKbDoc,
    AgentLog,
    AgentReminderLog,
    AgentRun,
    AgentTask,
)
from openclaw_agent.common.settings import get_settings
from openclaw_agent.common.storage import (
    download_file,
    parse_s3_uri,
    sha256_file,
    upload_file,
)
from openclaw_agent.common.utils import (
    json_dumps_canonical,
    make_idempotency_key,
    new_uuid,
    sha256_text,
    utcnow,
)

settings = get_settings()
configure_logging(settings.log_level)
log = get_logger("agent-worker")
engine = make_engine(settings.agent_db_dsn)


def _is_transient_error(e: Exception) -> bool:
    return isinstance(
        e,
        (
            httpx.TimeoutException,
            httpx.TransportError,
            BotoCoreError,
            ClientError,
            OperationalError,
        ),
    )


def _db_log(run_id: str, task_id: str | None, level: str, message: str, context: dict | None = None) -> None:
    with db_session(engine) as s:
        s.add(
            AgentLog(
                log_id=new_uuid(),
                run_id=run_id,
                task_id=task_id,
                level=level,
                message=message,
                context=context or None,
            )
        )


def _update_run(run_id: str, **fields: Any) -> None:
    with db_session(engine) as s:
        r = s.get(AgentRun, run_id)
        if not r:
            raise RuntimeError(f"run not found: {run_id}")
        for k, v in fields.items():
            setattr(r, k, v)


def _get_task_by_name(s, run_id: str, task_name: str) -> AgentTask | None:
    return s.execute(
        select(AgentTask).where((AgentTask.run_id == run_id) & (AgentTask.task_name == task_name))
    ).scalar_one_or_none()


def _task_start(run_id: str, task_name: str, input_ref: dict | None = None) -> str:
    with db_session(engine) as s:
        t = _get_task_by_name(s, run_id, task_name)
        if not t:
            t = AgentTask(
                task_id=new_uuid(),
                run_id=run_id,
                task_name=task_name,
                status="running",
                input_ref=input_ref,
                output_ref=None,
                error=None,
                started_at=utcnow(),
                finished_at=None,
            )
            s.add(t)
        else:
            t.status = "running"
            t.started_at = utcnow()
            t.error = None
        return t.task_id


def _task_finish(run_id: str, task_name: str, status: str, output_ref: dict | None = None, error: str | None = None) -> None:
    with db_session(engine) as s:
        t = _get_task_by_name(s, run_id, task_name)
        if not t:
            return
        t.status = status
        t.output_ref = output_ref
        t.error = error
        t.finished_at = utcnow()


def _safe_period_from_date_str(d: str | None) -> str | None:
    if not d:
        return None
    try:
        dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m")
    except Exception:
        return None


def _extract_pdf_text(path: str) -> str:
    text = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            if t.strip():
                text.append(t)
    return "\n".join(text)


def _ocr_image(path: str) -> str:
    # pytesseract requires the tesseract binary installed in the container/host.
    return pytesseract.image_to_string(path, lang="eng+vie", timeout=settings.ocr_timeout_seconds)


def _ocr_pdf(path: str, max_pages: int) -> str:
    # Requires poppler utils (`pdftoppm`) inside the container.
    from pdf2image import convert_from_path

    pages = convert_from_path(path, first_page=1, last_page=max(max_pages, 1))
    texts: list[str] = []
    for img in pages:
        texts.append(pytesseract.image_to_string(img, lang="eng+vie", timeout=settings.ocr_timeout_seconds))
    return "\n".join(t for t in texts if t.strip())


def _parse_doc_keys(text: str) -> dict[str, Any]:
    # Minimal, deterministic rules for demo/golden tests.
    norm = " ".join(text.split())
    out: dict[str, Any] = {}

    m = re.search(r"(?:S[oố]\s*h[oó]a\s*đ[oơ]n|Invoice\s*No)\s*[:#]?\s*([A-Z0-9\\-/]+)", norm, re.I)
    if m:
        out["invoice_no"] = m.group(1).strip()

    m = re.search(r"(?:MST|Tax\s*ID)\s*[:#]?\s*([0-9]{10,13})", norm, re.I)
    if m:
        out["tax_id"] = m.group(1).strip()

    m = re.search(r"(?:Ng[aà]y|Date)\s*[:#]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{4})", norm, re.I)
    if m:
        out["doc_date"] = m.group(1).strip()

    m = re.search(r"(?:T[oổng]\s*ti[eề]n|Total)\s*[:#]?\s*([0-9][0-9\\.,]*)", norm, re.I)
    if m:
        out["amount_raw"] = m.group(1).strip()

    m = re.search(r"(?:M[aã]\s*KH|Customer\s*Code)\s*[:#]?\s*([A-Z0-9\\-]+)", norm, re.I)
    if m:
        out["customer_code"] = m.group(1).strip()

    return out


def _match_invoice(parsed: dict[str, Any], invoices: list[dict], threshold: float) -> tuple[dict | None, float]:
    invoice_no = parsed.get("invoice_no")
    tax_id = parsed.get("tax_id")
    if invoice_no:
        for inv in invoices:
            if str(inv.get("invoice_no", "")).strip().upper() == str(invoice_no).strip().upper():
                if tax_id and str(inv.get("tax_id", "")).strip() != str(tax_id).strip():
                    # hard mismatch
                    continue
                return inv, 1.0

    best: dict | None = None
    best_score = 0.0
    # Fuzzy fallback: invoice_no similarity
    if invoice_no:
        for inv in invoices:
            score = fuzz.ratio(str(inv.get("invoice_no", "")), str(invoice_no)) / 100.0
            if tax_id and inv.get("tax_id") == tax_id:
                score += 0.05
            if score > best_score:
                best_score = score
                best = inv

    if best_score >= threshold:
        return best, best_score
    return None, best_score


def _ensure_export_unique(s, export_type: str, period: str, force_new_version: bool) -> int:
    rows = s.execute(
        select(AgentExport)
        .where((AgentExport.export_type == export_type) & (AgentExport.period == period))
        .order_by(AgentExport.version.desc())
    ).scalars().all()
    if not rows:
        return 1
    if force_new_version:
        return int(rows[0].version) + 1
    # idempotent: keep latest
    return int(rows[0].version)


def _csv_write(path: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        Path(path).write_text("", encoding="utf-8")
        return
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _xlsx_vat_list(path: str, invoices: list[dict[str, Any]]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "VAT_List"
    ws.append(["invoice_id", "invoice_no", "tax_id", "date", "amount", "customer_id", "status"])
    for inv in invoices:
        ws.append(
            [
                inv.get("invoice_id"),
                inv.get("invoice_no"),
                inv.get("tax_id"),
                inv.get("date"),
                inv.get("amount"),
                inv.get("customer_id"),
                inv.get("status"),
            ]
        )
    wb.save(path)


def _xlsx_working_papers(path: str, balances: dict[str, Any]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["period", balances.get("period")])
    ws.append(["gl_total", balances.get("gl_total")])
    ws.append(["ar_total", balances.get("ar_total")])
    ws.append(["ap_total", balances.get("ap_total")])

    ws2 = wb.create_sheet("AR_Aging")
    ws2.append(["customer_id", "invoice_id", "overdue_days", "amount"])
    for row in balances.get("ar_aging", []):
        ws2.append([row.get("customer_id"), row.get("invoice_id"), row.get("overdue_days"), row.get("amount")])

    wb.save(path)


def _write_summary_md(path: str, title: str, lines: list[str]) -> None:
    Path(path).write_text("# " + title + "\n\n" + "\n".join(lines) + "\n", encoding="utf-8")


def _send_email(to_addr: str, subject: str, body: str) -> None:
    # Optional; if SMTP not configured, caller should skip.
    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    if settings.smtp_tls:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10)
        server.starttls()
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10)

    try:
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)
    finally:
        server.quit()


@celery_app.task(name="openclaw_agent.agent_worker.tasks.dispatch_run", bind=True)
def dispatch_run(self: Task, run_id: str) -> dict[str, Any]:
    with db_session(engine) as s:
        run = s.get(AgentRun, run_id)
        if not run:
            raise RuntimeError(f"run not found: {run_id}")
        if run.status in {"success", "failed", "canceled"}:
            return {"run_id": run_id, "status": run.status}
        run_type = run.run_type
        run.status = "running"
        run.started_at = utcnow()

    _db_log(run_id, None, "info", "run_started", {"run_id": run_id})

    try:
        if run_type == "attachment":
            stats = _wf_attachment(run_id)
        elif run_type == "tax_export":
            stats = _wf_tax_export(run_id)
        elif run_type == "working_papers":
            stats = _wf_working_papers(run_id)
        elif run_type == "soft_checks":
            stats = _wf_soft_checks(run_id)
        elif run_type == "ar_dunning":
            stats = _wf_ar_dunning(run_id)
        elif run_type == "close_checklist":
            stats = _wf_close_checklist(run_id)
        elif run_type == "evidence_pack":
            stats = _wf_evidence_pack(run_id)
        elif run_type == "kb_index":
            stats = _wf_kb_index(run_id)
        else:
            raise RuntimeError(f"unsupported run_type: {run_type}")

        _update_run(run_id, status="success", finished_at=utcnow(), stats=stats)
        _db_log(run_id, None, "info", "run_success", {"stats": stats})
        return {"run_id": run_id, "status": "success", "stats": stats}
    except Exception as e:
        max_attempts = max(1, int(settings.task_retry_max_attempts))
        max_retries = max_attempts - 1
        retries = int(getattr(getattr(self, "request", None), "retries", 0) or 0)
        if _is_transient_error(e) and retries < max_retries:
            countdown = int(settings.task_retry_backoff_seconds) * (2 ** retries)
            _db_log(
                run_id,
                None,
                "warn",
                "run_retrying",
                {
                    "error": str(e),
                    "retries": retries,
                    "max_retries": max_retries,
                    "retry_in_seconds": countdown,
                },
            )
            raise self.retry(exc=e, countdown=countdown, max_retries=max_retries) from e

        _update_run(run_id, status="failed", finished_at=utcnow(), stats={"error": str(e)})
        _db_log(run_id, None, "error", "run_failed", {"error": str(e)})
        raise


def _run_payload(run_id: str) -> dict[str, Any]:
    with db_session(engine) as s:
        r = s.get(AgentRun, run_id)
        if not r:
            raise RuntimeError(f"run not found: {run_id}")
        return r.cursor_in or {}


def _wf_attachment(run_id: str) -> dict[str, Any]:
    payload = _run_payload(run_id)
    file_uri = payload.get("file_uri")
    if not file_uri:
        raise RuntimeError("payload.file_uri is required")

    workdir = Path(tempfile.mkdtemp(prefix=f"agent-{run_id}-"))
    try:
        t_id = _task_start(run_id, "extract_text", {"file_uri": file_uri})
        if str(file_uri).startswith("s3://"):
            ref = parse_s3_uri(file_uri)
            suffix = Path(ref.key).suffix or ".bin"
            local_path = str(workdir / f"input{suffix}")
            download_file(settings, ref, local_path)
        else:
            suffix = Path(str(file_uri)).suffix or ".bin"
            local_path = str(workdir / f"input{suffix}")
            shutil.copyfile(file_uri, local_path)

        file_hash = sha256_file(local_path)
        ext = Path(local_path).suffix.lower()
        if ext == ".pdf":
            text = _extract_pdf_text(local_path)
            if not text.strip():
                text = _ocr_pdf(local_path, max_pages=settings.ocr_pdf_max_pages)
        elif ext in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
            text = _ocr_image(local_path)
        else:
            text = ""
        _task_finish(run_id, "extract_text", "success", {"file_hash": file_hash, "text_len": len(text)})
        _db_log(run_id, t_id, "info", "text_extracted", {"file_hash": file_hash, "text_len": len(text)})

        t_id = _task_start(run_id, "parse_keys", {"text_len": len(text)})
        parsed = _parse_doc_keys(text)
        _task_finish(run_id, "parse_keys", "success", {"parsed": parsed})
        _db_log(run_id, t_id, "info", "keys_parsed", {"parsed": parsed})

        t_id = _task_start(run_id, "match", parsed)
        client = ErpXClient(settings)
        try:
            period = payload.get("period") or _safe_period_from_date_str(payload.get("doc_ts"))
            # If document date is in dd/mm/yyyy -> derive period.
            if not period and parsed.get("doc_date"):
                try:
                    dd, mm, yyyy = re.split(r"[/-]", parsed["doc_date"])
                    period = f"{int(yyyy):04d}-{int(mm):02d}"
                except Exception:
                    period = None
            if not period:
                period = datetime.now(timezone.utc).strftime("%Y-%m")
            invoices = client.get_invoices(period=period)
        finally:
            client.close()

        inv, score = _match_invoice(parsed, invoices, settings.match_confidence_threshold)
        if not inv:
            # Safety-first: do not attach. Create exception.
            signature = sha256_text(json_dumps_canonical(["attachment_mismatch", file_hash, parsed]))[:64]
            with db_session(engine) as s:
                existing = s.execute(select(AgentException).where(AgentException.signature == signature)).scalar_one_or_none()
                if not existing:
                    s.add(
                        AgentException(
                            id=new_uuid(),
                            exception_type="attachment_mismatch",
                            severity="med",
                            erp_refs={"file_uri": file_uri},
                            summary="Could not confidently match attachment to ERP object",
                            details={"parsed": parsed, "confidence": score},
                            signature=signature,
                            run_id=run_id,
                        )
                    )
            _task_finish(run_id, "match", "failed", {"confidence": score}, "no confident match")
            raise RuntimeError(f"no confident match (score={score:.2f})")

        match_info = {"erp_object_type": "invoice", "erp_object_id": inv["invoice_id"], "confidence": score}
        _task_finish(run_id, "match", "success", match_info)
        _db_log(run_id, t_id, "info", "matched", match_info)

        t_id = _task_start(run_id, "attach", match_info)
        # Upload original file into attachments bucket (content-addressed)
        ext = ".pdf" if local_path.lower().endswith(".pdf") else Path(local_path).suffix or ".bin"
        key = f"{match_info['erp_object_type']}/{match_info['erp_object_id']}/{file_hash}{ext}"
        obj = upload_file(settings, settings.minio_bucket_attachments, key, local_path)

        with db_session(engine) as s:
            existing = s.execute(
                select(AgentAttachment).where(
                    (AgentAttachment.file_hash == file_hash)
                    & (AgentAttachment.erp_object_type == match_info["erp_object_type"])
                    & (AgentAttachment.erp_object_id == match_info["erp_object_id"])
                )
            ).scalar_one_or_none()
            if not existing:
                s.add(
                    AgentAttachment(
                        id=new_uuid(),
                        erp_object_type=match_info["erp_object_type"],
                        erp_object_id=match_info["erp_object_id"],
                        file_uri=obj.uri(),
                        file_hash=file_hash,
                        matched_by="rule" if score >= 0.99 else "ocr",
                        run_id=run_id,
                    )
                )

        _task_finish(run_id, "attach", "success", {"file_uri": obj.uri()})
        _db_log(run_id, t_id, "info", "attached", {"file_uri": obj.uri()})

        _update_run(run_id, cursor_out={"file_hash": file_hash, **match_info})
        return {"attachments": 1, "matched_confidence": score}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _wf_tax_export(run_id: str) -> dict[str, Any]:
    payload = _run_payload(run_id)
    period = payload.get("period")
    if not period:
        raise RuntimeError("payload.period is required (YYYY-MM)")
    force_new = bool(payload.get("force_new_version", False))

    _task_start(run_id, "pull_invoices", {"period": period})
    client = ErpXClient(settings)
    try:
        invoices = client.get_invoices(period=period)
    finally:
        client.close()
    _task_finish(run_id, "pull_invoices", "success", {"count": len(invoices)})

    _task_start(run_id, "validate", {"count": len(invoices)})
    errors = []
    required = ["invoice_id", "invoice_no", "tax_id", "date", "amount"]
    for inv in invoices:
        missing = [k for k in required if not inv.get(k)]
        if missing:
            errors.append({"invoice_id": inv.get("invoice_id"), "missing": missing})
    if errors:
        signature = sha256_text(json_dumps_canonical(["vat_export_missing_fields", period, errors]))[:64]
        with db_session(engine) as s:
            if not s.execute(select(AgentException).where(AgentException.signature == signature)).scalar_one_or_none():
                s.add(
                    AgentException(
                        id=new_uuid(),
                        exception_type="vat_export_missing_fields",
                        severity="high",
                        erp_refs={"period": period},
                        summary="Invoices missing required fields for VAT export",
                        details={"errors": errors[:50]},
                        signature=signature,
                        run_id=run_id,
                    )
                )
        _task_finish(run_id, "validate", "failed", {"errors": len(errors)}, "missing fields")
        raise RuntimeError(f"VAT export validation failed: {len(errors)} invoices missing fields")
    _task_finish(run_id, "validate", "success", {"validated": len(invoices)})

    _task_start(run_id, "export_xlsx", {"period": period})
    workdir = Path(tempfile.mkdtemp(prefix=f"agent-{run_id}-"))
    try:
        with db_session(engine) as s:
            version = _ensure_export_unique(s, "vat_list", period, force_new)
            existing = s.execute(
                select(AgentExport).where(
                    (AgentExport.export_type == "vat_list")
                    & (AgentExport.period == period)
                    & (AgentExport.version == version)
                )
            ).scalar_one_or_none()
            if existing and not force_new:
                _task_finish(run_id, "export_xlsx", "success", {"file_uri": existing.file_uri, "version": version})
                _update_run(run_id, cursor_out={"period": period, "file_uri": existing.file_uri, "version": version})
                return {"export": 0, "reused": 1, "version": version}

        out_path = str(workdir / f"vat_list_{period}_v{version}.xlsx")
        _xlsx_vat_list(out_path, invoices)
        checksum = sha256_file(out_path)
        key = f"vat_list/{period}/vat_list_v{version}.xlsx"
        obj = upload_file(settings, settings.minio_bucket_exports, key, out_path)

        with db_session(engine) as s:
            s.add(
                AgentExport(
                    id=new_uuid(),
                    export_type="vat_list",
                    period=period,
                    version=version,
                    file_uri=obj.uri(),
                    checksum=checksum,
                    run_id=run_id,
                )
            )

        _task_finish(run_id, "export_xlsx", "success", {"file_uri": obj.uri(), "version": version})
        _update_run(run_id, cursor_out={"period": period, "file_uri": obj.uri(), "version": version})
        return {"export": 1, "version": version}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _wf_working_papers(run_id: str) -> dict[str, Any]:
    payload = _run_payload(run_id)
    period = payload.get("period")
    if not period:
        raise RuntimeError("payload.period is required (YYYY-MM)")
    force_new = bool(payload.get("force_new_version", False))

    _task_start(run_id, "pull_balances", {"period": period})
    client = ErpXClient(settings)
    try:
        # MVP: use AR aging as "balances" payload for template.
        ar = client.get_ar_aging(as_of=f"{period}-28")
    finally:
        client.close()
    balances = {
        "period": period,
        "gl_total": 0,
        "ar_total": sum(float(x.get("amount", 0) or 0) for x in ar),
        "ap_total": 0,
        "ar_aging": ar,
    }
    _task_finish(run_id, "pull_balances", "success", {"ar_rows": len(ar)})

    _task_start(run_id, "fill_templates", {"period": period})
    _task_finish(run_id, "fill_templates", "success")

    _task_start(run_id, "export_bundle", {"period": period})
    workdir = Path(tempfile.mkdtemp(prefix=f"agent-{run_id}-"))
    try:
        with db_session(engine) as s:
            version = _ensure_export_unique(s, "working_paper", period, force_new)
            existing = s.execute(
                select(AgentExport).where(
                    (AgentExport.export_type == "working_paper")
                    & (AgentExport.period == period)
                    & (AgentExport.version == version)
                )
            ).scalar_one_or_none()
            if existing and not force_new:
                _task_finish(run_id, "export_bundle", "success", {"file_uri": existing.file_uri, "version": version})
                _update_run(run_id, cursor_out={"period": period, "file_uri": existing.file_uri, "version": version})
                return {"export": 0, "reused": 1, "version": version}

        out_path = str(workdir / f"working_papers_{period}_v{version}.xlsx")
        _xlsx_working_papers(out_path, balances)
        checksum = sha256_file(out_path)
        key = f"working_paper/{period}/working_papers_v{version}.xlsx"
        obj = upload_file(settings, settings.minio_bucket_exports, key, out_path)

        with db_session(engine) as s:
            s.add(
                AgentExport(
                    id=new_uuid(),
                    export_type="working_paper",
                    period=period,
                    version=version,
                    file_uri=obj.uri(),
                    checksum=checksum,
                    run_id=run_id,
                )
            )

        _task_finish(run_id, "export_bundle", "success", {"file_uri": obj.uri(), "version": version})
        _update_run(run_id, cursor_out={"period": period, "file_uri": obj.uri(), "version": version})
        return {"export": 1, "version": version}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _wf_soft_checks(run_id: str) -> dict[str, Any]:
    payload = _run_payload(run_id)
    updated_after = payload.get("updated_after")  # ISO string
    period = payload.get("period") or datetime.now(timezone.utc).strftime("%Y-%m")
    force_new = bool(payload.get("force_new_version", False))

    _task_start(run_id, "pull_delta", {"updated_after": updated_after})
    client = ErpXClient(settings)
    try:
        vouchers = client.get_vouchers(updated_after=updated_after)
        journals = client.get_journals(updated_after=updated_after)
        invoices = client.get_invoices(period=period)
    finally:
        client.close()
    _task_finish(
        run_id, "pull_delta", "success", {"vouchers": len(vouchers), "journals": len(journals), "invoices": len(invoices)}
    )

    _task_start(run_id, "checks", {"period": period})
    exceptions: list[AgentException] = []

    # Check: voucher missing attachment flag
    for v in vouchers:
        if not v.get("has_attachment", True):
            signature = sha256_text(json_dumps_canonical(["missing_attachment", v.get("voucher_id"), period]))[:64]
            exceptions.append(
                AgentException(
                    id=new_uuid(),
                    exception_type="missing_attachment",
                    severity="med",
                    erp_refs={"voucher_id": v.get("voucher_id")},
                    summary="Voucher missing supporting attachment",
                    details={"voucher": v},
                    signature=signature,
                    run_id=run_id,
                )
            )

    # Check: journal out of balance
    for j in journals:
        if float(j.get("debit_total", 0) or 0) != float(j.get("credit_total", 0) or 0):
            signature = sha256_text(json_dumps_canonical(["journal_imbalanced", j.get("journal_id"), period]))[:64]
            exceptions.append(
                AgentException(
                    id=new_uuid(),
                    exception_type="journal_imbalanced",
                    severity="high",
                    erp_refs={"journal_id": j.get("journal_id")},
                    summary="Journal entry is not balanced (debit != credit)",
                    details={"journal": j},
                    signature=signature,
                    run_id=run_id,
                )
            )

    # Check: overdue invoices
    today = date.today()
    for inv in invoices:
        if inv.get("status") == "unpaid" and inv.get("due_date"):
            try:
                due = date.fromisoformat(inv["due_date"])
                if due < today:
                    signature = sha256_text(json_dumps_canonical(["invoice_overdue", inv.get("invoice_id"), period]))[:64]
                    exceptions.append(
                        AgentException(
                            id=new_uuid(),
                            exception_type="invoice_overdue",
                            severity="low",
                            erp_refs={"invoice_id": inv.get("invoice_id")},
                            summary="Invoice is overdue",
                            details={"invoice": inv, "overdue_days": (today - due).days},
                            signature=signature,
                            run_id=run_id,
                        )
                    )
            except Exception:
                continue

    with db_session(engine) as s:
        inserted = 0
        for ex in exceptions:
            if not s.execute(select(AgentException).where(AgentException.signature == ex.signature)).scalar_one_or_none():
                s.add(ex)
                inserted += 1

    _task_finish(run_id, "checks", "success", {"exceptions": len(exceptions)})

    # Export report
    _task_start(run_id, "export_report", {"period": period, "exceptions": len(exceptions)})
    workdir = Path(tempfile.mkdtemp(prefix=f"agent-{run_id}-"))
    try:
        with db_session(engine) as s:
            version = _ensure_export_unique(s, "soft_checks", period, force_new)
            existing = s.execute(
                select(AgentExport).where(
                    (AgentExport.export_type == "soft_checks")
                    & (AgentExport.period == period)
                    & (AgentExport.version == version)
                )
            ).scalar_one_or_none()
            if existing and not force_new:
                _task_finish(run_id, "export_report", "success", {"file_uri": existing.file_uri, "version": version})
                _update_run(
                    run_id,
                    cursor_out={"period": period, "exceptions": len(exceptions), "report_uri": existing.file_uri},
                )
                return {"exceptions": len(exceptions), "reused_report": 1}

        report_path = str(workdir / f"soft_checks_{period}.csv")
        _csv_write(
            report_path,
            [
                {
                    "exception_type": e.exception_type,
                    "severity": e.severity,
                    "summary": e.summary,
                    "signature": e.signature,
                }
                for e in exceptions
            ],
        )
        checksum = sha256_file(report_path)
        key = f"soft_checks/{period}/soft_checks_v{version}.csv"
        obj = upload_file(settings, settings.minio_bucket_exports, key, report_path)

        with db_session(engine) as s:
            s.add(
                AgentExport(
                    id=new_uuid(),
                    export_type="soft_checks",
                    period=period,
                    version=version,
                    file_uri=obj.uri(),
                    checksum=checksum,
                    run_id=run_id,
                )
            )

        _task_finish(run_id, "export_report", "success", {"file_uri": obj.uri(), "version": version})
        _update_run(run_id, cursor_out={"period": period, "exceptions": len(exceptions), "report_uri": obj.uri()})
        return {"exceptions": len(exceptions)}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _wf_ar_dunning(run_id: str) -> dict[str, Any]:
    payload = _run_payload(run_id)
    as_of = payload.get("as_of") or date.today().isoformat()
    window_days = int(payload.get("policy_window_days", 30))

    _task_start(run_id, "pull_ar_aging", {"as_of": as_of})
    client = ErpXClient(settings)
    try:
        rows = client.get_ar_aging(as_of=as_of)
    finally:
        client.close()
    _task_finish(run_id, "pull_ar_aging", "success", {"rows": len(rows)})

    _task_start(run_id, "apply_policy", {"rows": len(rows)})
    candidates = []
    for r in rows:
        overdue = int(r.get("overdue_days") or 0)
        stage = 0
        if overdue >= 7:
            stage = 1
        if overdue >= 14:
            stage = 2
        if overdue >= 30:
            stage = 3
        if stage:
            candidates.append({**r, "stage": stage})
    _task_finish(run_id, "apply_policy", "success", {"candidates": len(candidates)})

    _task_start(run_id, "notify", {"candidates": len(candidates)})
    sent = 0
    skipped = 0
    now = utcnow()
    cutoff = now - timedelta(days=window_days)
    with db_session(engine) as s:
        for c in candidates:
            invoice_id = str(c.get("invoice_id") or "")
            if not invoice_id:
                continue
            stage = int(c["stage"])
            sent_to = str(c.get("email") or c.get("customer_email") or "internal")
            # Idempotency: only 1 reminder per invoice_id+stage within policy window.
            existing_recent = (
                s.execute(
                    select(AgentReminderLog)
                    .where(
                        (AgentReminderLog.invoice_id == invoice_id)
                        & (AgentReminderLog.reminder_stage == stage)
                        & (AgentReminderLog.sent_at >= cutoff)
                    )
                    .order_by(AgentReminderLog.sent_at.desc())
                    .limit(1)
                )
                .scalar_one_or_none()
            )
            if existing_recent:
                skipped += 1
                continue

            # Stable key for uniqueness in DB (handles concurrent sends).
            window_bucket = int(now.date().toordinal() // max(window_days, 1))
            policy_key = make_idempotency_key("ar_dunning", invoice_id, stage, window_days, window_bucket)

            existing = s.execute(select(AgentReminderLog).where(AgentReminderLog.policy_key == policy_key)).scalar_one_or_none()
            if existing:
                skipped += 1
                continue

            # send (email optional)
            if settings.smtp_host and sent_to != "internal":
                _send_email(
                    sent_to,
                    subject=f"[AR Reminder] Invoice {invoice_id} - Stage {stage}",
                    body=f"Reminder stage {stage} for invoice {invoice_id}. Overdue days: {c.get('overdue_days')}",
                )
                channel = "email"
            else:
                channel = "internal"

            s.add(
                AgentReminderLog(
                    id=new_uuid(),
                    customer_id=str(c.get("customer_id") or ""),
                    invoice_id=invoice_id,
                    reminder_stage=stage,
                    channel=channel,
                    sent_to=sent_to,
                    sent_at=now,
                    run_id=run_id,
                    policy_key=policy_key,
                )
            )
            sent += 1

    _task_finish(run_id, "notify", "success", {"sent": sent, "skipped": skipped})
    _update_run(run_id, cursor_out={"as_of": as_of, "sent": sent, "skipped": skipped})
    return {"sent": sent, "skipped": skipped}


def _wf_close_checklist(run_id: str) -> dict[str, Any]:
    payload = _run_payload(run_id)
    period = payload.get("period")
    if not period:
        raise RuntimeError("payload.period is required (YYYY-MM)")

    _task_start(run_id, "pull_close_calendar", {"period": period})
    client = ErpXClient(settings)
    try:
        items = client.get_close_calendar(period=period)
    finally:
        client.close()
    _task_finish(run_id, "pull_close_calendar", "success", {"items": len(items)})

    _task_start(run_id, "upsert_close_tasks", {"items": len(items)})
    upserted = 0
    with db_session(engine) as s:
        for it in items:
            task_name = str(it.get("task_name"))
            due_date_str = it.get("due_date")
            if not task_name or not due_date_str:
                continue
            due = date.fromisoformat(due_date_str)
            existing = s.execute(
                select(AgentCloseTask).where(
                    (AgentCloseTask.period == period) & (AgentCloseTask.task_name == task_name)
                )
            ).scalar_one_or_none()
            if existing:
                existing.owner_user_id = it.get("owner_user_id")
                existing.due_date = due
                existing.status = existing.status or "todo"
            else:
                s.add(
                    AgentCloseTask(
                        id=new_uuid(),
                        period=period,
                        task_name=task_name,
                        owner_user_id=it.get("owner_user_id"),
                        due_date=due,
                        status="todo",
                        last_nudged_at=None,
                    )
                )
            upserted += 1
    _task_finish(run_id, "upsert_close_tasks", "success", {"upserted": upserted})

    _task_start(run_id, "nudge", {"period": period})
    nudged = 0
    now = utcnow()
    with db_session(engine) as s:
        tasks = s.execute(select(AgentCloseTask).where(AgentCloseTask.period == period)).scalars().all()
        for t in tasks:
            if t.status in {"done"}:
                continue
            if t.due_date <= date.today() + timedelta(days=2):
                # Nudge at most once per day
                if t.last_nudged_at and t.last_nudged_at.date() == date.today():
                    continue
                t.last_nudged_at = now
                nudged += 1
    _task_finish(run_id, "nudge", "success", {"nudged": nudged})
    _update_run(run_id, cursor_out={"period": period, "upserted": upserted, "nudged": nudged})
    return {"upserted": upserted, "nudged": nudged}


def _wf_evidence_pack(run_id: str) -> dict[str, Any]:
    payload = _run_payload(run_id)
    exception_id = payload.get("exception_id")
    issue_id = payload.get("issue_id")
    if not exception_id and not issue_id:
        raise RuntimeError("payload.exception_id or payload.issue_id is required")
    issue_key = f"exception:{exception_id}" if exception_id else f"issue:{issue_id}"

    _task_start(run_id, "collect", {"issue_key": issue_key})
    with db_session(engine) as s:
        existing = s.execute(
            select(AgentEvidencePack).where((AgentEvidencePack.issue_key == issue_key) & (AgentEvidencePack.version == 1))
        ).scalar_one_or_none()
        if existing:
            _task_finish(run_id, "collect", "success", {"reused": True})
            _update_run(run_id, cursor_out={"issue_key": issue_key, "pack_uri": existing.pack_uri})
            return {"reused": 1}

        ex = s.get(AgentException, exception_id) if exception_id else None
        refs = (ex.erp_refs if ex else {}) if exception_id else {"issue_id": issue_id}

    _task_finish(run_id, "collect", "success", {"refs": refs})

    _task_start(run_id, "pack", {"issue_key": issue_key})
    workdir = Path(tempfile.mkdtemp(prefix=f"agent-{run_id}-"))
    try:
        summary_path = str(workdir / "summary.md")
        _write_summary_md(summary_path, "Evidence Pack", [f"- issue_key: {issue_key}", f"- refs: {refs}"])

        zip_path = str(workdir / "evidence.zip")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(summary_path, arcname="summary.md")

        checksum = sha256_file(zip_path)
        key = f"evidence/{issue_key}/v1/evidence.zip"
        obj = upload_file(settings, settings.minio_bucket_evidence, key, zip_path)

        with db_session(engine) as s:
            s.add(
                AgentEvidencePack(
                    id=new_uuid(),
                    issue_key=issue_key,
                    version=1,
                    pack_uri=obj.uri(),
                    index_json={"checksum": checksum, "refs": refs},
                    run_id=run_id,
                )
            )

        _task_finish(run_id, "pack", "success", {"pack_uri": obj.uri()})
        _task_start(run_id, "register", {"pack_uri": obj.uri()})
        _task_finish(run_id, "register", "success")

        _update_run(run_id, cursor_out={"issue_key": issue_key, "pack_uri": obj.uri()})
        return {"packed": 1}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _wf_kb_index(run_id: str) -> dict[str, Any]:
    payload = _run_payload(run_id)
    file_uri = payload.get("file_uri")
    if not file_uri:
        raise RuntimeError("payload.file_uri is required")

    workdir = Path(tempfile.mkdtemp(prefix=f"agent-{run_id}-"))
    try:
        _task_start(run_id, "extract_text", {"file_uri": file_uri})
        if str(file_uri).startswith("s3://"):
            ref = parse_s3_uri(file_uri)
            suffix = Path(ref.key).suffix or ".bin"
            local_path = str(workdir / f"kb_input{suffix}")
            download_file(settings, ref, local_path)
        else:
            suffix = Path(str(file_uri)).suffix or ".bin"
            local_path = str(workdir / f"kb_input{suffix}")
            shutil.copyfile(file_uri, local_path)

        file_hash = sha256_file(local_path)
        ext = Path(local_path).suffix.lower()
        if ext == ".pdf":
            text = _extract_pdf_text(local_path)
            if not text.strip():
                text = _ocr_pdf(local_path, max_pages=settings.ocr_pdf_max_pages)
        elif ext in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
            text = _ocr_image(local_path)
        else:
            text = ""
        _task_finish(run_id, "extract_text", "success", {"file_hash": file_hash, "text_len": len(text)})

        _task_start(run_id, "extract_meta", {"text_len": len(text)})
        title = payload.get("title") or (text.strip().splitlines()[0][:120] if text.strip() else "Untitled")
        doc_type = payload.get("doc_type") or "process"
        version = payload.get("version") or "v1"
        effective_date = payload.get("effective_date")
        meta = {"keywords": list({w.lower() for w in re.findall(r"[A-Za-z0-9]{4,}", text)[:50]})}
        _task_finish(run_id, "extract_meta", "success", {"title": title, "doc_type": doc_type, "version": version})

        _task_start(run_id, "index", {"keywords": len(meta["keywords"])})
        _task_finish(run_id, "index", "success")

        _task_start(run_id, "register", {"file_hash": file_hash})
        # store extracted text
        text_path = str(workdir / "doc.txt")
        Path(text_path).write_text(text, encoding="utf-8")
        key = f"kb/text/{file_hash}.txt"
        obj = upload_file(settings, settings.minio_bucket_kb, key, text_path, content_type="text/plain")

        with db_session(engine) as s:
            existing = s.execute(
                select(AgentKbDoc).where((AgentKbDoc.file_hash == file_hash) & (AgentKbDoc.version == version))
            ).scalar_one_or_none()
            if not existing:
                s.add(
                    AgentKbDoc(
                        id=new_uuid(),
                        doc_type=doc_type,
                        title=title,
                        version=version,
                        effective_date=date.fromisoformat(effective_date) if effective_date else None,
                        source_uri=file_uri,
                        text_uri=obj.uri(),
                        indexed_at=utcnow(),
                        file_hash=file_hash,
                        meta=meta,
                    )
                )

        _task_finish(run_id, "register", "success", {"text_uri": obj.uri()})
        _update_run(run_id, cursor_out={"file_hash": file_hash, "text_uri": obj.uri()})
        return {"indexed": 1}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
