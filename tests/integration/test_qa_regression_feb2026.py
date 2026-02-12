from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openclaw_agent.common.db import Base, db_session, make_engine
from openclaw_agent.common.models import (
    AcctBankTransaction,
    AcctJournalLine,
    AcctJournalProposal,
    AcctQnaAudit,
    AcctVoucher,
    AgentRun,
)
from openclaw_agent.common.settings import get_settings
from openclaw_agent.common.utils import new_uuid

_HEADERS = {"X-API-Key": "test-key"}


@pytest.fixture()
def client_and_engine(tmp_path: Path, monkeypatch):
    agent_db = tmp_path / "agent.sqlite"
    monkeypatch.setenv("AGENT_DB_DSN", f"sqlite+pysqlite:///{agent_db}")
    monkeypatch.setenv("ERPX_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("ERPX_TOKEN", "testtoken")
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("MINIO_SECRET_KEY", "minioadmin")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
    monkeypatch.setenv("AGENT_AUTH_MODE", "api_key")
    monkeypatch.setenv("AGENT_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_UPLOAD_DIR", str(tmp_path / "uploads"))

    engine = make_engine()
    Base.metadata.create_all(engine)

    from openclaw_agent.agent_service import main as svc_main

    get_settings.cache_clear()
    monkeypatch.setattr(svc_main, "ensure_buckets", lambda _settings: None)
    monkeypatch.setattr(svc_main.celery_app, "send_task", lambda *args, **kwargs: None)
    svc_main.ENGINE = engine

    with TestClient(svc_main.app, raise_server_exceptions=False) as c:
        yield c, engine


def test_attachment_upload_creates_ocr_voucher(client_and_engine):
    client, _engine = client_and_engine
    resp = client.post(
        "/agent/v1/attachments",
        files={"file": ("kaggle_receipt.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg")},
        data={"source_tag": "ocr_upload"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "uploaded"
    assert body["source_tag"] == "ocr_upload"
    assert body.get("voucher_id")

    vouchers = client.get("/agent/v1/acct/vouchers?source=ocr_upload", headers=_HEADERS)
    assert vouchers.status_code == 200, vouchers.text
    items = vouchers.json().get("items", [])
    assert any(v.get("id") == body["voucher_id"] for v in items)


def test_logs_accept_filter_entity_id(client_and_engine):
    client, engine = client_and_engine
    voucher_id = new_uuid()
    run_id = new_uuid()

    with db_session(engine) as s:
        s.add(
            AcctVoucher(
                id=voucher_id,
                erp_voucher_id=f"erp-{voucher_id[:8]}",
                voucher_no="INV-LOG-001",
                voucher_type="buy_invoice",
                date="2026-02-12",
                amount=250_000,
                currency="VND",
                partner_name="Demo",
                has_attachment=True,
                source="ocr_upload",
                run_id=run_id,
            )
        )

    resp = client.get(
        "/agent/v1/logs",
        params={"filter_entity_id": voucher_id, "limit": 20},
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body
    assert body.get("run_id") == run_id


def test_reports_endpoints_no_500_for_valid_payload(client_and_engine):
    client, engine = client_and_engine
    voucher_id = new_uuid()
    proposal_id = new_uuid()
    with db_session(engine) as s:
        s.add(
            AcctVoucher(
                id=voucher_id,
                erp_voucher_id=f"erp-{voucher_id[:8]}",
                voucher_no="INV-2026-02-001",
                voucher_type="sell_invoice",
                date="2026-02-10",
                amount=1_000_000,
                currency="VND",
                partner_name="Demo",
                has_attachment=True,
                source="ocr_upload",
            )
        )
        s.add(
            AcctJournalProposal(
                id=proposal_id,
                voucher_id=voucher_id,
                confidence=0.95,
                status="approved",
            )
        )
        s.add(
            AcctJournalLine(
                id=new_uuid(),
                proposal_id=proposal_id,
                account_code="131",
                account_name="Phải thu KH",
                debit=1_000_000,
                credit=0,
            )
        )
        s.add(
            AcctJournalLine(
                id=new_uuid(),
                proposal_id=proposal_id,
                account_code="511",
                account_name="Doanh thu",
                debit=0,
                credit=1_000_000,
            )
        )

    validate = client.get(
        "/agent/v1/reports/validate",
        params={"type": "balance_sheet", "period": "2026-02"},
        headers=_HEADERS,
    )
    assert validate.status_code == 200, validate.text
    assert "checks" in validate.json()

    preview = client.post(
        "/agent/v1/reports/preview",
        json={"type": "balance_sheet", "standard": "VAS", "period": "2026-02"},
        headers=_HEADERS,
    )
    assert preview.status_code == 200, preview.text
    assert "data" in preview.json()

    generate = client.post(
        "/agent/v1/reports/generate",
        json={"type": "balance_sheet", "standard": "VAS", "period": "2026-02", "format": "json", "options": {}},
        headers=_HEADERS,
    )
    assert generate.status_code == 200, generate.text
    generated = generate.json()
    assert generated.get("report_id")

    download = client.get(
        f"/agent/v1/reports/{generated['report_id']}/download",
        params={"format": "json"},
        headers=_HEADERS,
    )
    assert download.status_code == 200, download.text
    assert "application/json" in (download.headers.get("content-type") or "")
    assert b'"report_type"' in download.content


def test_create_run_local_executor_not_stuck_queued(client_and_engine, monkeypatch):
    client, engine = client_and_engine
    monkeypatch.setenv("RUN_EXECUTOR_MODE", "local")
    from openclaw_agent.agent_service import main as svc_main

    def _run_local_stub(run_id: str) -> None:
        with db_session(engine) as s:
            row = s.get(AgentRun, run_id)
            assert row is not None
            row.status = "success"
            row.started_at = row.started_at or row.created_at
            row.finished_at = row.finished_at or row.created_at
            row.stats = {"stub": True}

    monkeypatch.setattr(svc_main, "_dispatch_run_local", _run_local_stub)

    create = client.post(
        "/agent/v1/runs",
        json={
            "run_type": "bank_reconcile",
            "trigger_type": "manual",
            "payload": {"period": "2026-02"},
        },
        headers=_HEADERS,
    )
    assert create.status_code == 200, create.text
    run_id = create.json()["run_id"]

    fetched = client.get(f"/agent/v1/runs/{run_id}", headers=_HEADERS)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["status"] == "success"


def test_voucher_reprocess_run_type_accepted(client_and_engine, monkeypatch):
    client, engine = client_and_engine
    monkeypatch.setenv("RUN_EXECUTOR_MODE", "local")
    from openclaw_agent.agent_service import main as svc_main

    voucher_id = new_uuid()
    with db_session(engine) as s:
        s.add(
            AcctVoucher(
                id=voucher_id,
                erp_voucher_id=f"erp-{voucher_id[:8]}",
                voucher_no="INV-RP-001",
                voucher_type="buy_invoice",
                date="2026-02-12",
                amount=450_000,
                currency="VND",
                partner_name="Demo",
                has_attachment=True,
                source="ocr_upload",
                raw_payload={"status": "uploaded"},
            )
        )

    def _run_local_stub(run_id: str) -> None:
        with db_session(engine) as s:
            run = s.get(AgentRun, run_id)
            assert run is not None
            run.status = "success"
            run.started_at = run.created_at
            run.finished_at = run.created_at

    monkeypatch.setattr(svc_main, "_dispatch_run_local", _run_local_stub)

    resp = client.post(
        "/agent/v1/runs",
        json={
            "run_type": "voucher_reprocess",
            "trigger_type": "manual",
            "payload": {"voucher_id": voucher_id},
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("run_id")


def test_qna_feedback_accepts_string_and_legacy_int(client_and_engine):
    client, engine = client_and_engine
    audit_id = new_uuid()
    with db_session(engine) as s:
        s.add(
            AcctQnaAudit(
                id=audit_id,
                question="Q?",
                answer="A",
                feedback=None,
            )
        )

    helpful = client.patch(
        f"/agent/v1/acct/qna_feedback/{audit_id}",
        json={"feedback": "helpful", "note": "ok"},
        headers=_HEADERS,
    )
    assert helpful.status_code == 200, helpful.text
    assert helpful.json()["feedback"] == "helpful"

    legacy = client.patch(
        f"/agent/v1/acct/qna_feedback/{audit_id}",
        json={"rating": -1, "note": "not good"},
        headers=_HEADERS,
    )
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["feedback"] == "not_helpful"


def test_bank_manual_match_unmatch_and_ignore(client_and_engine):
    client, engine = client_and_engine
    voucher_id = new_uuid()
    bank_id = new_uuid()

    with db_session(engine) as s:
        s.add(
            AcctVoucher(
                id=voucher_id,
                erp_voucher_id=f"erp-{voucher_id[:8]}",
                voucher_no="INV-BANK-001",
                voucher_type="sell_invoice",
                date="2026-02-11",
                amount=500_000,
                currency="VND",
                partner_name="Demo",
                has_attachment=True,
                source="erpx",
            )
        )
        s.add(
            AcctBankTransaction(
                id=bank_id,
                bank_tx_ref="BANK-TX-001",
                bank_account="VCB-001",
                date="2026-02-11",
                amount=500_000,
                currency="VND",
                match_status="unmatched",
            )
        )

    matched = client.post(
        "/agent/v1/acct/bank_match",
        json={"bank_tx_id": bank_id, "voucher_id": voucher_id, "method": "manual"},
        headers=_HEADERS,
    )
    assert matched.status_code == 200, matched.text
    assert matched.json()["match_status"] == "matched_manual"

    unmatch = client.post(
        f"/agent/v1/acct/bank_match/{bank_id}/unmatch",
        json={"unmatched_by": "tester"},
        headers=_HEADERS,
    )
    assert unmatch.status_code == 200, unmatch.text
    assert unmatch.json()["match_status"] == "unmatched"

    ignored = client.post(
        f"/agent/v1/acct/bank_transactions/{bank_id}/ignore",
        json={"ignored_by": "tester"},
        headers=_HEADERS,
    )
    assert ignored.status_code == 200, ignored.text
    assert ignored.json()["match_status"] == "ignored"


def test_vn_feeder_control_update_config_and_status(client_and_engine, monkeypatch):
    client, _engine = client_and_engine
    import openclaw_agent.agent_service.vn_feeder_engine as feeder_engine

    state = {"running": False, "epm": 3}

    def _start(target_epm=None):
        if target_epm is not None:
            state["epm"] = int(target_epm)
        state["running"] = True
        return True

    def _stop():
        state["running"] = False
        return True

    def _inject(target_epm=None):
        if target_epm is not None:
            state["epm"] = int(target_epm)
        return True

    def _set_epm(target_epm):
        state["epm"] = int(target_epm)
        return state["epm"]

    monkeypatch.setattr(feeder_engine, "start_feeder", _start)
    monkeypatch.setattr(feeder_engine, "stop_feeder", _stop)
    monkeypatch.setattr(feeder_engine, "inject_now", _inject)
    monkeypatch.setattr(feeder_engine, "set_target_events_per_min", _set_epm)
    monkeypatch.setattr(feeder_engine, "get_target_events_per_min", lambda: state["epm"])
    monkeypatch.setattr(feeder_engine, "is_running", lambda: state["running"])

    cfg = client.post(
        "/agent/v1/vn_feeder/control",
        json={"action": "update_config", "events_per_min": 5},
        headers=_HEADERS,
    )
    assert cfg.status_code == 200, cfg.text
    assert cfg.json()["events_per_min"] == 5

    start = client.post("/agent/v1/vn_feeder/control", json={"action": "start"}, headers=_HEADERS)
    assert start.status_code == 200, start.text

    status = client.get("/agent/v1/vn_feeder/status", headers=_HEADERS)
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["running"] is True
    assert body["events_per_min"] == 5

    stop = client.post("/agent/v1/vn_feeder/control", json={"action": "stop"}, headers=_HEADERS)
    assert stop.status_code == 200, stop.text
