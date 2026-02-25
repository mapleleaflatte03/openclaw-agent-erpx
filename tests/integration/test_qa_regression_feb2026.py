from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from openclaw_agent.common.db import Base, db_session, make_engine
from openclaw_agent.common.models import (
    AcctBankTransaction,
    AcctCashflowForecast,
    AcctJournalLine,
    AcctJournalProposal,
    AcctQnaAudit,
    AcctReportSnapshot,
    AcctVoucher,
    AcctVoucherCorrection,
    AgentAttachment,
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
    assert body["status"] in {"valid", "review", "quarantined", "non_invoice", "low_quality"}
    assert body["source_tag"] == "ocr_upload"
    assert body.get("voucher_id")

    vouchers = client.get("/agent/v1/acct/vouchers?source=ocr_upload", headers=_HEADERS)
    assert vouchers.status_code == 200, vouchers.text
    items = vouchers.json().get("items", [])
    assert any(v.get("id") == body["voucher_id"] for v in items)


def test_attachment_content_preview_and_urls(client_and_engine):
    client, _engine = client_and_engine
    uploaded = client.post(
        "/agent/v1/attachments",
        files={"file": ("invoice-preview.xml", b"<invoice><total>500000</total></invoice>", "application/xml")},
        data={"source_tag": "ocr_upload"},
        headers=_HEADERS,
    )
    assert uploaded.status_code == 200, uploaded.text
    payload = uploaded.json()
    assert payload.get("attachment_id")

    vouchers = client.get("/agent/v1/acct/vouchers?source=ocr_upload&limit=20", headers=_HEADERS)
    assert vouchers.status_code == 200, vouchers.text
    row = next((item for item in vouchers.json().get("items", []) if item.get("id") == payload.get("voucher_id")), None)
    assert row is not None
    assert row.get("preview_url")
    assert row.get("file_url")

    preview = client.get(row["preview_url"], headers=_HEADERS)
    assert preview.status_code == 200, preview.text
    assert preview.headers.get("content-type")


def test_patch_ocr_fields_writes_correction_log(client_and_engine):
    client, engine = client_and_engine
    uploaded = client.post(
        "/agent/v1/attachments",
        files={"file": ("invoice-edit.xml", b"<invoice><total>0</total></invoice>", "application/xml")},
        data={"source_tag": "ocr_upload"},
        headers=_HEADERS,
    )
    assert uploaded.status_code == 200, uploaded.text
    voucher_id = uploaded.json()["voucher_id"]

    patch_resp = client.patch(
        f"/agent/v1/acct/vouchers/{voucher_id}/fields",
        json={
            "fields": {
                "partner_name": "Cong ty Test",
                "partner_tax_code": "0109999999",
                "invoice_no": "INV-EDIT-001",
                "invoice_date": "2026-02-20",
                "total_amount": 750000,
                "vat_amount": 75000,
                "line_items_count": 2,
                "doc_type": "invoice",
            },
            "reason": "manual_fix_for_uat",
            "corrected_by": "tester",
        },
        headers=_HEADERS,
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json().get("updated") is True

    mark_valid = client.post(
        f"/agent/v1/acct/vouchers/{voucher_id}/mark_valid",
        json={"marked_by": "tester"},
        headers=_HEADERS,
    )
    assert mark_valid.status_code == 200, mark_valid.text
    assert mark_valid.json()["status"] == "valid"

    with db_session(engine) as s:
        voucher = s.get(AcctVoucher, voucher_id)
        assert voucher is not None
        assert voucher.partner_name == "Cong ty Test"
        assert float(voucher.amount or 0) == 750000
        corrections = s.execute(
            select(AcctVoucherCorrection).where(AcctVoucherCorrection.voucher_id == voucher_id)
        ).scalars().all()
        assert len(corrections) >= 2


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


def test_voucher_reprocess_wrapper_endpoint(client_and_engine, monkeypatch):
    client, engine = client_and_engine
    monkeypatch.setenv("RUN_EXECUTOR_MODE", "local")
    from openclaw_agent.agent_service import main as svc_main

    uploaded = client.post(
        "/agent/v1/attachments",
        files={"file": ("invoice-reprocess.xml", b"<invoice><total>500000</total></invoice>", "application/xml")},
        data={"source_tag": "ocr_upload"},
        headers=_HEADERS,
    )
    assert uploaded.status_code == 200, uploaded.text
    voucher_id = uploaded.json()["voucher_id"]

    def _run_local_stub(run_id: str) -> None:
        with db_session(engine) as s:
            run = s.get(AgentRun, run_id)
            assert run is not None
            run.status = "success"
            run.started_at = run.created_at
            run.finished_at = run.created_at

    monkeypatch.setattr(svc_main, "_dispatch_run_local", _run_local_stub)

    resp = client.post(
        f"/agent/v1/acct/vouchers/{voucher_id}/reprocess",
        json={"reason": "test_wrapper"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("run_id")
    assert body.get("run_type") == "voucher_reprocess"


def test_voucher_reprocess_allows_zero_amount_review(client_and_engine, monkeypatch):
    client, engine = client_and_engine
    monkeypatch.setenv("RUN_EXECUTOR_MODE", "local")
    from openclaw_agent.agent_service import main as svc_main

    zero_xml = b"""
    <invoice>
      <meta>Hoa don VAT MST 0101234567</meta>
      <invoice_no>INV-ZERO-REPROCESS-001</invoice_no>
      <summary>Tong tien: 0</summary>
    </invoice>
    """.strip()
    uploaded = client.post(
        "/agent/v1/attachments",
        files={"file": ("invoice-zero-reprocess.xml", zero_xml, "application/xml")},
        data={"source_tag": "ocr_upload"},
        headers=_HEADERS,
    )
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["status"] in {"review", "quarantined", "non_invoice", "low_quality"}
    assert "zero_amount" in (body.get("quality_reasons") or [])
    voucher_id = body["voucher_id"]

    def _run_local_stub(run_id: str) -> None:
        with db_session(engine) as s:
            run = s.get(AgentRun, run_id)
            assert run is not None
            run.status = "success"
            run.started_at = run.created_at
            run.finished_at = run.created_at

    monkeypatch.setattr(svc_main, "_dispatch_run_local", _run_local_stub)

    reprocess = client.post(
        f"/agent/v1/acct/vouchers/{voucher_id}/reprocess",
        json={"reason": "retry_zero_amount"},
        headers=_HEADERS,
    )
    assert reprocess.status_code == 200, reprocess.text
    out = reprocess.json()
    assert out.get("run_id")
    assert out.get("run_type") == "voucher_reprocess"
    assert out.get("voucher_id") == voucher_id


def test_voucher_reprocess_can_restore_missing_attachment(client_and_engine, monkeypatch, tmp_path: Path):
    client, engine = client_and_engine
    monkeypatch.setenv("RUN_EXECUTOR_MODE", "local")
    from openclaw_agent.agent_service import main as svc_main

    source_xml = b"<invoice><meta>MST 0101234567</meta><summary>Tong tien: 350000</summary></invoice>"
    uploaded = client.post(
        "/agent/v1/attachments",
        files={"file": ("invoice-restore.xml", source_xml, "application/xml")},
        data={"source_tag": "ocr_upload"},
        headers=_HEADERS,
    )
    assert uploaded.status_code == 200, uploaded.text
    voucher_id = uploaded.json()["voucher_id"]
    attachment_id = uploaded.json()["attachment_id"]

    # Simulate lost original file in production pod.
    with db_session(engine) as s:
        att = s.get(AgentAttachment, attachment_id)
        assert att is not None
        att.file_uri = str(tmp_path / "missing-source.xml")

    def _run_local_stub(run_id: str) -> None:
        with db_session(engine) as s:
            run = s.get(AgentRun, run_id)
            assert run is not None
            run.status = "success"
            run.started_at = run.created_at
            run.finished_at = run.created_at

    monkeypatch.setattr(svc_main, "_dispatch_run_local", _run_local_stub)

    blocked = client.post(
        f"/agent/v1/acct/vouchers/{voucher_id}/reprocess",
        json={"reason": "missing_attachment"},
        headers=_HEADERS,
    )
    assert blocked.status_code == 422, blocked.text
    assert "File gốc của attachment đã mất" in blocked.text

    restored = client.post(
        f"/agent/v1/acct/vouchers/{voucher_id}/reprocess",
        json={
            "reason": "restore_attachment",
            "filename": "invoice-restore.xml",
            "content_type": "application/xml",
            "file_content_b64": base64.b64encode(source_xml).decode("ascii"),
        },
        headers=_HEADERS,
    )
    assert restored.status_code == 200, restored.text
    assert restored.json().get("run_type") == "voucher_reprocess"

    with db_session(engine) as s:
        att = s.get(AgentAttachment, attachment_id)
        assert att is not None
        assert Path(str(att.file_uri)).exists()


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


def test_journal_review_blocks_invalid_account_code(client_and_engine):
    client, engine = client_and_engine
    voucher_id = new_uuid()
    proposal_id = new_uuid()
    line_debit = new_uuid()
    line_credit = new_uuid()

    with db_session(engine) as s:
        s.add(
            AcctVoucher(
                id=voucher_id,
                erp_voucher_id=f"erp-{voucher_id[:8]}",
                voucher_no="INV-JRN-001",
                voucher_type="buy_invoice",
                date="2026-02-12",
                amount=585_000,
                currency="VND",
                partner_name="Demo",
                has_attachment=True,
                source="ocr_upload",
                raw_payload={"status": "valid"},
            )
        )
        s.add(
            AcctJournalProposal(
                id=proposal_id,
                voucher_id=voucher_id,
                confidence=0.82,
                status="pending",
            )
        )
        s.add(
            AcctJournalLine(
                id=line_debit,
                proposal_id=proposal_id,
                account_code="undefined",
                account_name="Invalid",
                debit=585_000,
                credit=0,
            )
        )
        s.add(
            AcctJournalLine(
                id=line_credit,
                proposal_id=proposal_id,
                account_code="331",
                account_name="Phải trả",
                debit=0,
                credit=585_000,
            )
        )

    blocked = client.post(
        f"/agent/v1/acct/journal_proposals/{proposal_id}/review",
        json={"status": "approved", "reviewed_by": "tester"},
        headers=_HEADERS,
    )
    assert blocked.status_code == 422, blocked.text
    payload = blocked.json().get("detail", {})
    assert payload.get("error") == "INVALID_ACCOUNT_CODE"

    with db_session(engine) as s:
        line = s.get(AcctJournalLine, line_debit)
        assert line is not None
        line.account_code = "621"
        line.account_name = "Chi phí NVL trực tiếp"

    approved = client.post(
        f"/agent/v1/acct/journal_proposals/{proposal_id}/review",
        json={"status": "approved", "reviewed_by": "tester"},
        headers=_HEADERS,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"


def test_ocr_quality_gate_valid_vs_non_invoice(client_and_engine):
    client, _engine = client_and_engine
    valid_xml = b"""
    <invoice>
      <meta>Hoa don VAT MST 1234567890</meta>
      <line>1 x Dich vu A 750000 VND</line>
      <summary>Tong tien: 750000</summary>
    </invoice>
    """.strip()
    valid_upload = client.post(
        "/agent/v1/attachments",
        files={"file": ("invoice-vat-001.xml", valid_xml, "application/xml")},
        data={"source_tag": "ocr_upload"},
        headers=_HEADERS,
    )
    assert valid_upload.status_code == 200, valid_upload.text
    assert valid_upload.json()["status"] == "valid"

    noisy_upload = client.post(
        "/agent/v1/attachments",
        files={"file": ("dogs-vs-cats__sample.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg")},
        data={"source_tag": "ocr_upload"},
        headers=_HEADERS,
    )
    assert noisy_upload.status_code == 200, noisy_upload.text
    noisy_body = noisy_upload.json()
    assert noisy_body["status"] in {"non_invoice", "review", "quarantined", "low_quality"}
    assert noisy_body.get("quality_reasons")

    vouchers = client.get("/agent/v1/acct/vouchers?source=ocr_upload&limit=10", headers=_HEADERS)
    assert vouchers.status_code == 200, vouchers.text
    items = vouchers.json().get("items", [])
    by_id = {item["id"]: item for item in items}
    assert by_id[valid_upload.json()["voucher_id"]]["status"] == "valid"
    assert by_id[noisy_body["voucher_id"]]["status"] in {"non_invoice", "review", "quarantined", "low_quality"}


def test_ocr_zero_total_not_overridden_by_tax_code(client_and_engine):
    client, _engine = client_and_engine
    xml = b"""
    <invoice>
      <meta>Hoa don VAT MST 0101234567</meta>
      <invoice_no>INV-2026-0002</invoice_no>
      <summary>Tong tien: 0</summary>
    </invoice>
    """.strip()
    uploaded = client.post(
        "/agent/v1/attachments",
        files={"file": ("invoice-zero-total.xml", xml, "application/xml")},
        data={"source_tag": "ocr_upload"},
        headers=_HEADERS,
    )
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["status"] in {"review", "quarantined", "non_invoice", "low_quality"}
    assert "zero_amount" in (body.get("quality_reasons") or [])

    vouchers = client.get("/agent/v1/acct/vouchers?source=ocr_upload&limit=20", headers=_HEADERS)
    assert vouchers.status_code == 200, vouchers.text
    items = vouchers.json().get("items", [])
    row = next((item for item in items if item.get("id") == body.get("voucher_id")), None)
    assert row is not None
    assert float(row.get("total_amount") or 0) == 0
    assert "zero_amount" in (row.get("quality_reasons") or [])


def test_qna_data_driven_vs_knowledge_routing(client_and_engine):
    client, _engine = client_and_engine
    no_data = client.post(
        "/agent/v1/acct/qna",
        json={"question": "Doanh thu tháng này là bao nhiêu và 3 khoản chi lớn nhất?"},
        headers=_HEADERS,
    )
    assert no_data.status_code == 200, no_data.text
    body = no_data.json()
    assert body.get("meta", {}).get("route") == "data_unavailable"
    assert "chưa được kết nối dữ liệu doanh thu/chi phí thực tế" in body.get("answer", "").lower()

    knowledge = client.post(
        "/agent/v1/acct/qna",
        json={"question": "Phân biệt TT200 và TT133 về ghi nhận doanh thu?"},
        headers=_HEADERS,
    )
    assert knowledge.status_code == 200, knowledge.text
    knowledge_body = knowledge.json()
    assert knowledge_body.get("meta", {}).get("route") == "knowledge"
    assert "thông tư" in knowledge_body.get("answer", "").lower() or "tt200" in knowledge_body.get("answer", "").lower()


def test_qna_data_driven_includes_sources_and_confidence(client_and_engine):
    client, engine = client_and_engine
    with db_session(engine) as s:
        s.add(
            AcctVoucher(
                id=new_uuid(),
                erp_voucher_id="erp-qna-sell-1",
                voucher_no="INV-QNA-001",
                voucher_type="sell_invoice",
                date="2026-02-12",
                amount=900_000,
                currency="VND",
                partner_name="Demo A",
                has_attachment=True,
                source="ocr_upload",
                raw_payload={"status": "valid"},
            )
        )
        s.add(
            AcctVoucher(
                id=new_uuid(),
                erp_voucher_id="erp-qna-exp-1",
                voucher_no="PT-QNA-001",
                voucher_type="payment",
                date="2026-02-12",
                amount=620_000,
                currency="VND",
                partner_name="Demo B",
                has_attachment=True,
                source="ocr_upload",
                raw_payload={"status": "valid"},
            )
        )

    resp = client.post(
        "/agent/v1/acct/qna",
        json={"question": "Doanh thu tháng này và 3 khoản chi phí lớn nhất tháng này là gì?"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    meta = body.get("meta", {})
    assert meta.get("route") == "data"
    assert isinstance(meta.get("confidence"), (int, float))
    assert meta.get("confidence") > 0
    assert isinstance(meta.get("sources"), list)
    assert len(meta.get("sources")) >= 1


def test_cashflow_forecast_sufficiency_and_clean_items(client_and_engine):
    client, engine = client_and_engine
    forecast_id_ok = new_uuid()
    forecast_id_zero = new_uuid()
    voucher_id = new_uuid()

    with db_session(engine) as s:
        s.add(
            AcctVoucher(
                id=voucher_id,
                erp_voucher_id=f"erp-{voucher_id[:8]}",
                voucher_no="INV-FC-001",
                voucher_type="sell_invoice",
                date="2026-02-12",
                amount=500_000,
                currency="VND",
                partner_name="Demo",
                has_attachment=True,
                source="ocr_upload",
                raw_payload={"status": "valid"},
            )
        )
        s.add(
            AcctCashflowForecast(
                id=forecast_id_ok,
                forecast_date="2026-03-10",
                direction="inflow",
                amount=500_000,
                currency="VND",
                source_type="invoice_receivable",
                source_ref="INV-FC-001",
                confidence=0.8,
                run_id=new_uuid(),
            )
        )
        s.add(
            AcctCashflowForecast(
                id=forecast_id_zero,
                forecast_date="2026-03-11",
                direction="inflow",
                amount=0,
                currency="VND",
                source_type="invoice_receivable",
                source_ref="INV-FC-002",
                confidence=0.8,
                run_id=new_uuid(),
            )
        )

    resp = client.get("/agent/v1/acct/cashflow_forecast?limit=10", headers=_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sufficiency"]["enough"] is False
    assert body["sufficiency"]["observed_periods"] < body["sufficiency"]["min_periods_required"]
    assert all(item["amount"] > 0 for item in body["items"])
    assert all(item.get("period") for item in body["items"])


def test_reports_warn_when_invalid_voucher_exists(client_and_engine):
    client, engine = client_and_engine
    valid_voucher_id = new_uuid()
    invalid_voucher_id = new_uuid()
    proposal_id = new_uuid()
    with db_session(engine) as s:
        s.add(
            AcctVoucher(
                id=valid_voucher_id,
                erp_voucher_id=f"erp-{valid_voucher_id[:8]}",
                voucher_no="INV-RPT-VALID",
                voucher_type="sell_invoice",
                date="2026-02-10",
                amount=1_200_000,
                currency="VND",
                partner_name="Demo",
                has_attachment=True,
                source="ocr_upload",
                raw_payload={"status": "valid"},
            )
        )
        s.add(
            AcctVoucher(
                id=invalid_voucher_id,
                erp_voucher_id=f"erp-{invalid_voucher_id[:8]}",
                voucher_no="INV-RPT-BAD",
                voucher_type="other",
                date="2026-02-11",
                amount=0,
                currency="VND",
                partner_name=None,
                has_attachment=True,
                source="ocr_upload",
                raw_payload={"status": "quarantined", "quality_reasons": ["zero_amount"]},
            )
        )
        s.add(
            AcctJournalProposal(
                id=proposal_id,
                voucher_id=valid_voucher_id,
                confidence=0.9,
                status="approved",
            )
        )
        s.add(
            AcctJournalLine(
                id=new_uuid(),
                proposal_id=proposal_id,
                account_code="131",
                account_name="Phải thu KH",
                debit=1_200_000,
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
                credit=1_200_000,
            )
        )

    validate = client.get(
        "/agent/v1/reports/validate",
        params={"type": "balance_sheet", "period": "2026-02"},
        headers=_HEADERS,
    )
    assert validate.status_code == 200, validate.text
    checks = validate.json()["checks"]
    quality_check = next(c for c in checks if c["name"] == "Chất lượng chứng từ đầu vào")
    assert quality_check["passed"] is False

    preview = client.post(
        "/agent/v1/reports/preview",
        json={"type": "balance_sheet", "standard": "VAS", "period": "2026-02"},
        headers=_HEADERS,
    )
    assert preview.status_code == 200, preview.text
    report_data = preview.json()["data"]
    assert report_data["voucher_count"] == 1
    assert report_data["excluded_voucher_count"] >= 1
    assert any("bị loại khỏi báo cáo" in issue for issue in report_data["issues"])


def test_reports_generate_requires_risk_approval_when_critical_fail(client_and_engine):
    client, engine = client_and_engine
    valid_voucher_id = new_uuid()
    fixture_voucher_id = new_uuid()
    proposal_id = new_uuid()
    with db_session(engine) as s:
        s.add(
            AcctVoucher(
                id=valid_voucher_id,
                erp_voucher_id=f"erp-{valid_voucher_id[:8]}",
                voucher_no="INV-RPT-GATE-OK",
                voucher_type="sell_invoice",
                date="2026-02-12",
                amount=900_000,
                currency="VND",
                partner_name="Demo",
                has_attachment=True,
                source="ocr_upload",
                raw_payload={"status": "valid", "original_filename": "invoice-vat-001.xml"},
            )
        )
        s.add(
            AcctVoucher(
                id=fixture_voucher_id,
                erp_voucher_id=f"erp-{fixture_voucher_id[:8]}",
                voucher_no="INV-RPT-GATE-FIXTURE",
                voucher_type="sell_invoice",
                date="2026-02-12",
                amount=500_000,
                currency="VND",
                partner_name="Fixture",
                has_attachment=True,
                source="ocr_upload",
                raw_payload={"status": "valid", "original_filename": "dogs-vs-cats__sample.jpg"},
            )
        )
        s.add(
            AcctJournalProposal(
                id=proposal_id,
                voucher_id=valid_voucher_id,
                confidence=0.9,
                status="approved",
            )
        )
        s.add(
            AcctJournalLine(
                id=new_uuid(),
                proposal_id=proposal_id,
                account_code="131",
                account_name="Phải thu KH",
                debit=900_000,
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
                credit=900_000,
            )
        )

    validate = client.get(
        "/agent/v1/reports/validate",
        params={"type": "balance_sheet", "period": "2026-02"},
        headers=_HEADERS,
    )
    assert validate.status_code == 200, validate.text
    quality_check = next(c for c in validate.json()["checks"] if c["name"] == "Chất lượng chứng từ đầu vào")
    assert quality_check["passed"] is False

    blocked = client.post(
        "/agent/v1/reports/generate",
        json={"type": "balance_sheet", "standard": "VAS", "period": "2026-02", "format": "json", "options": {}},
        headers=_HEADERS,
    )
    assert blocked.status_code == 409, blocked.text
    detail = blocked.json().get("detail", {})
    assert detail.get("error") == "RISK_APPROVAL_REQUIRED"

    allowed = client.post(
        "/agent/v1/reports/generate",
        json={
            "type": "balance_sheet",
            "standard": "VAS",
            "period": "2026-02",
            "format": "json",
            "options": {"risk_approval": {"approved_by": "chief-accountant", "reason": "chấp nhận rủi ro dữ liệu tạm thời"}},
        },
        headers=_HEADERS,
    )
    assert allowed.status_code == 200, allowed.text
    report_id = allowed.json()["report_id"]
    with db_session(engine) as s:
        snapshot = s.get(AcctReportSnapshot, report_id)
        assert snapshot is not None
        summary = snapshot.summary_json or {}
        assert isinstance(summary.get("risk_approval"), dict)
        assert summary["risk_approval"].get("approved_by") == "chief-accountant"


def test_vouchers_operational_scope_excludes_test_fixture(client_and_engine):
    client, engine = client_and_engine
    valid_id = new_uuid()
    fixture_id = new_uuid()

    with db_session(engine) as s:
        s.add(
            AcctVoucher(
                id=valid_id,
                erp_voucher_id=f"erp-{valid_id[:8]}",
                voucher_no="INV-SCOPE-OK",
                voucher_type="sell_invoice",
                date="2026-02-20",
                amount=450_000,
                currency="VND",
                partner_name="Demo",
                has_attachment=True,
                source="ocr_upload",
                raw_payload={"status": "valid", "original_filename": "invoice-vat-002.xml"},
            )
        )
        s.add(
            AcctVoucher(
                id=fixture_id,
                erp_voucher_id=f"erp-{fixture_id[:8]}",
                voucher_no="INV-SCOPE-FIXTURE",
                voucher_type="sell_invoice",
                date="2026-02-20",
                amount=350_000,
                currency="VND",
                partner_name="Fixture",
                has_attachment=True,
                source="ocr_upload",
                raw_payload={"status": "valid", "original_filename": "smoke-ocr.png"},
            )
        )

    all_rows = client.get(
        "/agent/v1/acct/vouchers",
        params={"source": "ocr_upload", "period": "2026-02", "limit": 100},
        headers=_HEADERS,
    )
    assert all_rows.status_code == 200, all_rows.text
    all_ids = {item["id"] for item in all_rows.json()["items"]}
    assert valid_id in all_ids and fixture_id in all_ids

    operational = client.get(
        "/agent/v1/acct/vouchers",
        params={"source": "ocr_upload", "period": "2026-02", "quality_scope": "operational", "limit": 100},
        headers=_HEADERS,
    )
    assert operational.status_code == 200, operational.text
    op_items = operational.json()["items"]
    op_ids = {item["id"] for item in op_items}
    assert valid_id in op_ids
    assert fixture_id not in op_ids


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


def test_bank_manual_match_rejects_zero_amount_rows(client_and_engine):
    client, engine = client_and_engine
    voucher_id = new_uuid()
    bank_id = new_uuid()

    with db_session(engine) as s:
        s.add(
            AcctVoucher(
                id=voucher_id,
                erp_voucher_id=f"erp-{voucher_id[:8]}",
                voucher_no="INV-BANK-000",
                voucher_type="sell_invoice",
                date="2026-02-11",
                amount=0,
                currency="VND",
                partner_name="Demo",
                has_attachment=True,
                source="erpx",
            )
        )
        s.add(
            AcctBankTransaction(
                id=bank_id,
                bank_tx_ref="BANK-TX-000",
                bank_account="VCB-001",
                date="2026-02-11",
                amount=0,
                currency="VND",
                match_status="unmatched",
            )
        )

    blocked = client.post(
        "/agent/v1/acct/bank_match",
        json={"bank_tx_id": bank_id, "voucher_id": voucher_id, "method": "manual"},
        headers=_HEADERS,
    )
    assert blocked.status_code == 422, blocked.text
    payload = blocked.json().get("detail", {})
    assert payload.get("error") == "INVALID_MATCH_AMOUNT"


def test_bank_manual_match_rejects_large_amount_mismatch(client_and_engine):
    client, engine = client_and_engine
    voucher_id = new_uuid()
    bank_id = new_uuid()

    with db_session(engine) as s:
        s.add(
            AcctVoucher(
                id=voucher_id,
                erp_voucher_id=f"erp-{voucher_id[:8]}",
                voucher_no="INV-BANK-MISMATCH",
                voucher_type="sell_invoice",
                date="2026-02-11",
                amount=580_000,
                currency="VND",
                partner_name="Demo",
                has_attachment=True,
                source="erpx",
            )
        )
        s.add(
            AcctBankTransaction(
                id=bank_id,
                bank_tx_ref="BANK-TX-2230K",
                bank_account="VCB-001",
                date="2026-02-11",
                amount=2_230_000,
                currency="VND",
                match_status="unmatched",
            )
        )

    blocked = client.post(
        "/agent/v1/acct/bank_match",
        json={"bank_tx_id": bank_id, "voucher_id": voucher_id, "method": "manual"},
        headers=_HEADERS,
    )
    assert blocked.status_code == 422, blocked.text
    payload = blocked.json().get("detail", {})
    assert payload.get("error") == "BANK_MATCH_AMOUNT_MISMATCH"


def test_bank_transactions_marks_large_mismatch_as_anomaly(client_and_engine):
    client, engine = client_and_engine
    voucher_id = new_uuid()
    bank_id = new_uuid()

    with db_session(engine) as s:
        s.add(
            AcctVoucher(
                id=voucher_id,
                erp_voucher_id=f"erp-{voucher_id[:8]}",
                voucher_no="INV-BANK-MARK-ANOMALY",
                voucher_type="sell_invoice",
                date="2026-02-11",
                amount=580_000,
                currency="VND",
                partner_name="Demo",
                has_attachment=True,
                source="erpx",
            )
        )
        s.add(
            AcctBankTransaction(
                id=bank_id,
                bank_tx_ref="BANK-TX-WRONG-MATCH",
                bank_account="VCB-001",
                date="2026-02-11",
                amount=2_230_000,
                currency="VND",
                match_status="matched_manual",
                matched_voucher_id=voucher_id,
            )
        )

    listed = client.get(
        "/agent/v1/acct/bank_transactions",
        params={"period": "2026-02", "limit": 100},
        headers=_HEADERS,
    )
    assert listed.status_code == 200, listed.text
    row = next((item for item in listed.json().get("items", []) if item.get("id") == bank_id), None)
    assert row is not None
    assert row.get("match_status") == "anomaly"
    assert row.get("anomaly_reason") == "amount_mismatch_large"


def test_settings_profile_rejects_invalid_email(client_and_engine):
    client, _engine = client_and_engine
    invalid = client.patch(
        "/agent/v1/settings/profile",
        json={"name": "Tester", "email": "abc", "role": "accountant"},
        headers=_HEADERS,
    )
    assert invalid.status_code == 422, invalid.text
    detail = invalid.json().get("detail", {})
    assert detail.get("error") == "INVALID_EMAIL_FORMAT"


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
