from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from accounting_agent.common.db import Base, db_session
from accounting_agent.common.models import AgentException, AgentExport, AgentRun
from accounting_agent.common.testutils import get_free_port, run_uvicorn_in_thread, stop_uvicorn
from accounting_agent.common.utils import make_idempotency_key, new_uuid


def test_soft_checks_idempotent_and_exceptions(tmp_path: Path, monkeypatch):
    agent_db = tmp_path / "agent.sqlite"
    monkeypatch.setenv("AGENT_DB_DSN", f"sqlite+pysqlite:///{agent_db}")

    erpx_db = tmp_path / "erpx_mock.sqlite"
    seed_path = Path("data/kaggle/seed/erpx_seed_kaggle.json").resolve()
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

    from accounting_agent.erpx_mock import main as erpx_main

    erpx_main.DbState.conn = None
    server, thread = run_uvicorn_in_thread(erpx_main.app, port=port)

    try:
        import importlib

        from accounting_agent.common.settings import get_settings

        get_settings.cache_clear()
        from accounting_agent.agent_worker import tasks as worker_tasks
        importlib.reload(worker_tasks)
        from accounting_agent.common.storage import S3ObjectRef

        Base.metadata.create_all(worker_tasks.engine)

        def fake_upload_file(
            _settings, bucket: str, key: str, path: str, content_type: str | None = None
        ):
            return S3ObjectRef(bucket="test-bucket", key=key)

        monkeypatch.setattr(worker_tasks, "upload_file", fake_upload_file)

        # Run #1
        run_id_1 = new_uuid()
        with db_session(worker_tasks.engine) as s:
            s.add(
                AgentRun(
                    run_id=run_id_1,
                    run_type="soft_checks",
                    trigger_type="manual",
                    requested_by=None,
                    status="queued",
                    idempotency_key=make_idempotency_key("soft_checks", "2026-01", "t1"),
                    cursor_in={"period": "2026-01"},
                    cursor_out=None,
                    started_at=None,
                    finished_at=None,
                    stats=None,
                )
            )

        worker_tasks.dispatch_run.run(run_id_1)

        with db_session(worker_tasks.engine) as s:
            exc = s.execute(sa.select(AgentException)).scalars().all()
            assert any(e.exception_type == "missing_attachment" for e in exc)
            assert any(e.exception_type == "journal_imbalanced" for e in exc)
            assert any(e.exception_type == "invoice_overdue" for e in exc)

            exports = s.execute(
                sa.select(AgentExport).where(
                    (AgentExport.export_type == "soft_checks") & (AgentExport.period == "2026-01")
                )
            ).scalars().all()
            assert len(exports) == 1

        # Run #2 (reuse report)
        run_id_2 = new_uuid()
        with db_session(worker_tasks.engine) as s:
            s.add(
                AgentRun(
                    run_id=run_id_2,
                    run_type="soft_checks",
                    trigger_type="manual",
                    requested_by=None,
                    status="queued",
                    idempotency_key=make_idempotency_key("soft_checks", "2026-01", "t2"),
                    cursor_in={"period": "2026-01"},
                    cursor_out=None,
                    started_at=None,
                    finished_at=None,
                    stats=None,
                )
            )

        worker_tasks.dispatch_run.run(run_id_2)

        with db_session(worker_tasks.engine) as s:
            exports = s.execute(
                sa.select(AgentExport).where(
                    (AgentExport.export_type == "soft_checks") & (AgentExport.period == "2026-01")
                )
            ).scalars().all()
            assert len(exports) == 1
    finally:
        stop_uvicorn(server, thread)
