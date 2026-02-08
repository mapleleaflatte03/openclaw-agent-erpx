from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from openclaw_agent.common.db import Base, db_session, make_engine
from openclaw_agent.common.models import AgentContractCase, AgentProposal
from openclaw_agent.common.utils import make_idempotency_key, new_uuid


def test_agent_service_contract_approvals_high_risk(tmp_path: Path, monkeypatch):
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

    from openclaw_agent.agent_service import main as svc_main
    from openclaw_agent.common.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(svc_main, "ensure_buckets", lambda _settings: None)
    svc_main.ENGINE = None

    with TestClient(svc_main.app) as client:
        # self-approve => 409
        r = client.post(
            f"/agent/v1/contract/proposals/{proposal_id}/approvals",
            json={"decision": "approve", "approver_id": "maker1", "evidence_ack": True},
        )
        assert r.status_code == 409

        # evidence_ack=false => 400
        r = client.post(
            f"/agent/v1/contract/proposals/{proposal_id}/approvals",
            json={"decision": "approve", "approver_id": "approver1", "evidence_ack": False},
        )
        assert r.status_code == 400

        # high-risk: approve #1 => pending_l2
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

        # duplicate approver attempt => 409
        r = client.post(
            f"/agent/v1/contract/proposals/{proposal_id}/approvals",
            headers={"Idempotency-Key": "idem-1b"},
            json={"decision": "approve", "approver_id": "approver1", "evidence_ack": True},
        )
        assert r.status_code == 409

        # high-risk: approve #2 (different approver) => approved
        r = client.post(
            f"/agent/v1/contract/proposals/{proposal_id}/approvals",
            headers={"Idempotency-Key": "idem-2"},
            json={"decision": "approve", "approver_id": "approver2", "evidence_ack": True},
        )
        assert r.status_code == 200
        body3 = r.json()
        assert body3["proposal_status"] == "approved"
        assert body3["approvals_approved"] == 2

        # After approval finalized: additional attempt → 409
        r = client.post(
            f"/agent/v1/contract/proposals/{proposal_id}/approvals",
            headers={"Idempotency-Key": "idem-3"},
            json={"decision": "approve", "approver_id": "approver3", "evidence_ack": True},
        )
        assert r.status_code == 409
        assert "already finalized" in r.json()["detail"]

        # Re-fetch proposal list: status must reflect approved
        r = client.get(f"/agent/v1/contract/cases/{case_id}/proposals")
        assert r.status_code == 200
        found = [p for p in r.json()["items"] if p["proposal_id"] == proposal_id]
        assert len(found) == 1
        assert found[0]["status"] == "approved"


def test_agent_service_contract_reject_finalizes(tmp_path: Path, monkeypatch):
    """After reject, proposal_status=rejected and further actions → 409."""
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

    engine = make_engine()
    Base.metadata.create_all(engine)

    case_id = new_uuid()
    proposal_id = new_uuid()
    proposal_key = make_idempotency_key("proposal", case_id, None, "reminder", "rej")

    with db_session(engine) as s:
        s.add(AgentContractCase(
            case_id=case_id,
            case_key=make_idempotency_key("contract_case", "reject_test"),
            partner_name=None, partner_tax_id=None, contract_code=None,
            status="open", meta=None,
        ))
        s.add(AgentProposal(
            proposal_id=proposal_id, case_id=case_id, obligation_id=None,
            proposal_type="reminder", title="To be rejected",
            summary="", details={}, risk_level="low", confidence=1.0,
            status="draft", created_by="maker1", tier=1,
            evidence_summary_hash=None, proposal_key=proposal_key, run_id=None,
        ))

    from openclaw_agent.agent_service import main as svc_main
    from openclaw_agent.common.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(svc_main, "ensure_buckets", lambda _settings: None)
    svc_main.ENGINE = None

    with TestClient(svc_main.app) as client:
        r = client.post(
            f"/agent/v1/contract/proposals/{proposal_id}/approvals",
            json={"decision": "reject", "approver_id": "approver1", "evidence_ack": True},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["proposal_status"] == "rejected"

        # Further approve attempt → 409
        r = client.post(
            f"/agent/v1/contract/proposals/{proposal_id}/approvals",
            json={"decision": "approve", "approver_id": "approver2", "evidence_ack": True},
        )
        assert r.status_code == 409
        assert "already finalized" in r.json()["detail"]

