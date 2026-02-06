from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from openclaw_agent.common.db import Base, db_session, make_engine
from openclaw_agent.common.models import AgentContractCase, AgentObligation, AgentProposal, AgentRun
from openclaw_agent.common.testutils import get_free_port, run_uvicorn_in_thread, stop_uvicorn
from openclaw_agent.common.utils import make_idempotency_key, new_uuid


def _make_contract_pdf(path: Path, lines: list[str]) -> None:
    c = canvas.Canvas(str(path))
    y = 800
    for line in lines:
        c.drawString(40, y, line)
        y -= 18
    c.save()


def _make_email_eml(path: Path, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "buyer@example.local"
    msg["To"] = "ap@acme.example.local"
    msg.set_content(body)
    path.write_bytes(msg.as_bytes())


def test_contract_obligation_idempotent_high_confidence(tmp_path: Path, monkeypatch):
    # Agent DB (sqlite for tests)
    agent_db = tmp_path / "agent.sqlite"
    monkeypatch.setenv("AGENT_DB_DSN", f"sqlite+pysqlite:///{agent_db}")

    # ERPX mock seed (contracts/partners/payments endpoints)
    erpx_db = tmp_path / "erpx_mock.sqlite"
    seed_path = Path("samples/seed/erpx_seed_contract_obligation_minimal.json").resolve()
    monkeypatch.setenv("ERPX_MOCK_DB_PATH", str(erpx_db))
    monkeypatch.setenv("ERPX_MOCK_SEED_PATH", str(seed_path))
    monkeypatch.setenv("ERPX_MOCK_TOKEN", "testtoken")

    port = get_free_port()
    base_url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("ERPX_BASE_URL", base_url)
    monkeypatch.setenv("ERPX_TOKEN", "testtoken")

    # Required settings (not used directly in this golden test path)
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("MINIO_SECRET_KEY", "minioadmin")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

    # Inputs: obligations split across PDF + email
    contract_pdf = tmp_path / "contract.pdf"
    _make_contract_pdf(
        contract_pdf,
        [
            "Milestone payment: 30% within 10 days.",
            "Late payment penalty: 0.05% per day if late.",
        ],
    )
    email_eml = tmp_path / "thread.eml"
    _make_email_eml(email_eml, "Re: HD-ACME-2026-0001", "Early payment discount: 2% if paid within 5 days.")

    from openclaw_agent.erpx_mock import main as erpx_main

    erpx_main.DbState.conn = None
    server, thread = run_uvicorn_in_thread(erpx_main.app, port=port)

    try:
        import importlib

        from openclaw_agent.common.settings import get_settings

        get_settings.cache_clear()
        from openclaw_agent.agent_worker import tasks as worker_tasks

        importlib.reload(worker_tasks)
        from openclaw_agent.common.storage import S3ObjectRef

        Base.metadata.create_all(worker_tasks.engine)

        def fake_upload_file(_settings, bucket: str, key: str, path: str, content_type: str | None = None):
            return S3ObjectRef(bucket="test-bucket", key=key)

        monkeypatch.setattr(worker_tasks, "upload_file", fake_upload_file)

        # Run #1
        run_id_1 = new_uuid()
        with db_session(worker_tasks.engine) as s:
            s.add(
                AgentRun(
                    run_id=run_id_1,
                    run_type="contract_obligation",
                    trigger_type="manual",
                    requested_by=None,
                    status="queued",
                    idempotency_key=make_idempotency_key("contract_obligation", "t1"),
                    cursor_in={
                        "contract_files": [str(contract_pdf)],
                        "email_files": [str(email_eml)],
                    },
                    cursor_out=None,
                    started_at=None,
                    finished_at=None,
                    stats=None,
                )
            )

        worker_tasks.dispatch_run.run(run_id_1)

        with db_session(worker_tasks.engine) as s:
            obligations = s.execute(sa.select(AgentObligation)).scalars().all()
            assert len(obligations) == 3

            proposals = s.execute(sa.select(AgentProposal)).scalars().all()
            assert len(proposals) == 4
            # Tier 1 milestone payment generates an accrual_template draft (aux output only).
            assert any((p.proposal_type == "accrual_template") and (p.tier == 1) for p in proposals)
            assert not any(p.proposal_type == "review_needed" for p in proposals)

        # Run #2 (same inputs) should be idempotent (no duplicates)
        run_id_2 = new_uuid()
        with db_session(worker_tasks.engine) as s:
            s.add(
                AgentRun(
                    run_id=run_id_2,
                    run_type="contract_obligation",
                    trigger_type="manual",
                    requested_by=None,
                    status="queued",
                    idempotency_key=make_idempotency_key("contract_obligation", "t2"),
                    cursor_in={
                        "contract_files": [str(contract_pdf)],
                        "email_files": [str(email_eml)],
                    },
                    cursor_out=None,
                    started_at=None,
                    finished_at=None,
                    stats=None,
                )
            )

        worker_tasks.dispatch_run.run(run_id_2)

        with db_session(worker_tasks.engine) as s:
            obligations = s.execute(sa.select(AgentObligation)).scalars().all()
            proposals = s.execute(sa.select(AgentProposal)).scalars().all()
            assert len(obligations) == 3
            assert len(proposals) == 4
    finally:
        stop_uvicorn(server, thread)


def test_contract_obligation_gating_low_confidence(tmp_path: Path, monkeypatch):
    agent_db = tmp_path / "agent.sqlite"
    monkeypatch.setenv("AGENT_DB_DSN", f"sqlite+pysqlite:///{agent_db}")

    erpx_db = tmp_path / "erpx_mock.sqlite"
    seed_path = Path("samples/seed/erpx_seed_contract_obligation_minimal.json").resolve()
    monkeypatch.setenv("ERPX_MOCK_DB_PATH", str(erpx_db))
    monkeypatch.setenv("ERPX_MOCK_SEED_PATH", str(seed_path))
    monkeypatch.setenv("ERPX_MOCK_TOKEN", "testtoken")

    port = get_free_port()
    base_url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("ERPX_BASE_URL", base_url)
    monkeypatch.setenv("ERPX_TOKEN", "testtoken")

    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("MINIO_SECRET_KEY", "minioadmin")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

    contract_pdf = tmp_path / "contract_low_conf.pdf"
    _make_contract_pdf(contract_pdf, ["Payment terms: to be discussed."])

    from openclaw_agent.erpx_mock import main as erpx_main

    erpx_main.DbState.conn = None
    server, thread = run_uvicorn_in_thread(erpx_main.app, port=port)

    try:
        import importlib

        from openclaw_agent.common.settings import get_settings

        get_settings.cache_clear()
        from openclaw_agent.agent_worker import tasks as worker_tasks

        importlib.reload(worker_tasks)
        from openclaw_agent.common.storage import S3ObjectRef

        Base.metadata.create_all(worker_tasks.engine)

        def fake_upload_file(_settings, bucket: str, key: str, path: str, content_type: str | None = None):
            return S3ObjectRef(bucket="test-bucket", key=key)

        monkeypatch.setattr(worker_tasks, "upload_file", fake_upload_file)

        run_id = new_uuid()
        with db_session(worker_tasks.engine) as s:
            s.add(
                AgentRun(
                    run_id=run_id,
                    run_type="contract_obligation",
                    trigger_type="manual",
                    requested_by=None,
                    status="queued",
                    idempotency_key=make_idempotency_key("contract_obligation", "lowconf"),
                    cursor_in={"contract_files": [str(contract_pdf)]},
                    cursor_out=None,
                    started_at=None,
                    finished_at=None,
                    stats=None,
                )
            )

        worker_tasks.dispatch_run.run(run_id)

        with db_session(worker_tasks.engine) as s:
            proposals = s.execute(sa.select(AgentProposal)).scalars().all()
            assert len(proposals) == 1
            assert proposals[0].proposal_type == "missing_data"
            assert int(proposals[0].tier) == 3
    finally:
        stop_uvicorn(server, thread)


def test_contract_obligation_conflict_drops_to_tier2(tmp_path: Path, monkeypatch):
    agent_db = tmp_path / "agent.sqlite"
    monkeypatch.setenv("AGENT_DB_DSN", f"sqlite+pysqlite:///{agent_db}")

    erpx_db = tmp_path / "erpx_mock.sqlite"
    seed_path = Path("samples/seed/erpx_seed_contract_obligation_minimal.json").resolve()
    monkeypatch.setenv("ERPX_MOCK_DB_PATH", str(erpx_db))
    monkeypatch.setenv("ERPX_MOCK_SEED_PATH", str(seed_path))
    monkeypatch.setenv("ERPX_MOCK_TOKEN", "testtoken")

    port = get_free_port()
    base_url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("ERPX_BASE_URL", base_url)
    monkeypatch.setenv("ERPX_TOKEN", "testtoken")

    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("MINIO_SECRET_KEY", "minioadmin")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

    contract_pdf = tmp_path / "contract_conflict.pdf"
    _make_contract_pdf(contract_pdf, ["Milestone payment: 30% within 10 days."])
    email_eml = tmp_path / "thread_conflict.eml"
    _make_email_eml(email_eml, "Re: HD-ACME-2026-0001", "Milestone payment: 30% within 12 days.")

    from openclaw_agent.erpx_mock import main as erpx_main

    erpx_main.DbState.conn = None
    server, thread = run_uvicorn_in_thread(erpx_main.app, port=port)

    try:
        import importlib

        from openclaw_agent.common.settings import get_settings

        get_settings.cache_clear()
        from openclaw_agent.agent_worker import tasks as worker_tasks

        importlib.reload(worker_tasks)
        from openclaw_agent.common.storage import S3ObjectRef

        Base.metadata.create_all(worker_tasks.engine)

        def fake_upload_file(_settings, bucket: str, key: str, path: str, content_type: str | None = None):
            return S3ObjectRef(bucket="test-bucket", key=key)

        monkeypatch.setattr(worker_tasks, "upload_file", fake_upload_file)

        run_id = new_uuid()
        with db_session(worker_tasks.engine) as s:
            s.add(
                AgentRun(
                    run_id=run_id,
                    run_type="contract_obligation",
                    trigger_type="manual",
                    requested_by=None,
                    status="queued",
                    idempotency_key=make_idempotency_key("contract_obligation", "conflict"),
                    cursor_in={"contract_files": [str(contract_pdf)], "email_files": [str(email_eml)]},
                    cursor_out=None,
                    started_at=None,
                    finished_at=None,
                    stats=None,
                )
            )

        worker_tasks.dispatch_run.run(run_id)

        with db_session(worker_tasks.engine) as s:
            proposals = s.execute(sa.select(AgentProposal)).scalars().all()
            assert len(proposals) == 1
            p = proposals[0]
            assert p.proposal_type == "review_confirm"
            assert int(p.tier) == 2
            assert isinstance(p.details, dict)
            assert isinstance(p.details.get("conflicts"), dict)
            assert "within_days" in (p.details.get("conflicts") or {})
            assert not any(x.proposal_type == "accrual_template" for x in proposals)
    finally:
        stop_uvicorn(server, thread)


def test_contract_approvals_high_risk_two_step_maker_checker(tmp_path: Path, monkeypatch):
    agent_db = tmp_path / "agent.sqlite"
    monkeypatch.setenv("AGENT_DB_DSN", f"sqlite+pysqlite:///{agent_db}")

    # Required settings for Settings validation (values not used by this test).
    monkeypatch.setenv("ERPX_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("ERPX_TOKEN", "testtoken")
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("MINIO_SECRET_KEY", "minioadmin")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

    engine = make_engine()
    Base.metadata.create_all(engine)

    case_id = new_uuid()
    proposal_id = new_uuid()
    proposal_key = make_idempotency_key("proposal", case_id, None, "reminder", "t")

    with db_session(engine) as s:
        s.add(
            AgentContractCase(
                case_id=case_id,
                case_key=make_idempotency_key("contract_case", "approvals"),
                partner_name=None,
                partner_tax_id=None,
                contract_code=None,
                status="open",
                meta=None,
            )
        )
        s.add(
            AgentProposal(
                proposal_id=proposal_id,
                case_id=case_id,
                obligation_id=None,
                proposal_type="reminder",
                title="High-risk proposal",
                summary="",
                details={"tier": 1, "risk_level": "high"},
                risk_level="high",
                confidence=1.0,
                status="draft",
                created_by="maker1",
                tier=1,
                evidence_summary_hash=None,
                proposal_key=proposal_key,
                run_id=None,
            )
        )

    # Agent service API: maker-checker + evidence_ack + 2-step for high-risk
    from openclaw_agent.agent_service import main as svc_main
    from openclaw_agent.common.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(svc_main, "ensure_buckets", lambda _settings: None)
    svc_main.ENGINE = None

    with TestClient(svc_main.app) as client:
        # maker-checker: creator cannot approve
        r = client.post(
            f"/agent/v1/contract/proposals/{proposal_id}/approvals",
            json={"decision": "approve", "approver_id": "maker1", "evidence_ack": True},
        )
        assert r.status_code == 409

        # evidence_ack must be true
        r = client.post(
            f"/agent/v1/contract/proposals/{proposal_id}/approvals",
            json={"decision": "approve", "approver_id": "approver1", "evidence_ack": False},
        )
        assert r.status_code == 400

        # approve #1 => pending_l2
        r = client.post(
            f"/agent/v1/contract/proposals/{proposal_id}/approvals",
            headers={"Idempotency-Key": "idem-1"},
            json={"decision": "approve", "approver_id": "approver1", "evidence_ack": True},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["proposal_status"] == "pending_l2"
        assert body["approvals_required"] == 2
        assert body["approvals_approved"] == 1
        approval_id_1 = body["approval_id"]

        # idempotency repeat => same approval_id/status
        r = client.post(
            f"/agent/v1/contract/proposals/{proposal_id}/approvals",
            headers={"Idempotency-Key": "idem-1"},
            json={"decision": "approve", "approver_id": "approver1", "evidence_ack": True},
        )
        assert r.status_code == 200
        body2 = r.json()
        assert body2["approval_id"] == approval_id_1
        assert body2["proposal_status"] == "pending_l2"

        # duplicate approver attempt (different idempotency key) => 409
        r = client.post(
            f"/agent/v1/contract/proposals/{proposal_id}/approvals",
            headers={"Idempotency-Key": "idem-1b"},
            json={"decision": "approve", "approver_id": "approver1", "evidence_ack": True},
        )
        assert r.status_code == 409

        # approve #2 (distinct approver) => approved
        r = client.post(
            f"/agent/v1/contract/proposals/{proposal_id}/approvals",
            headers={"Idempotency-Key": "idem-2"},
            json={"decision": "approve", "approver_id": "approver2", "evidence_ack": True},
        )
        assert r.status_code == 200
        body3 = r.json()
        assert body3["proposal_status"] == "approved"
        assert body3["approvals_approved"] == 2
