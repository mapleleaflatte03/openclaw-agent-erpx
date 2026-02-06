from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from openclaw_agent.common.db import Base, db_session
from openclaw_agent.common.models import AgentExport, AgentRun
from openclaw_agent.common.testutils import get_free_port, run_uvicorn_in_thread, stop_uvicorn
from openclaw_agent.common.utils import make_idempotency_key, new_uuid


def test_vat_export_idempotent(tmp_path: Path, monkeypatch):
    # Agent DB (sqlite for tests)
    agent_db = tmp_path / "agent.sqlite"
    monkeypatch.setenv("AGENT_DB_DSN", f"sqlite+pysqlite:///{agent_db}")

    # ERPX mock seed
    erpx_db = tmp_path / "erpx_mock.sqlite"
    seed_path = Path("samples/seed/erpx_seed_minimal.json").resolve()
    monkeypatch.setenv("ERPX_MOCK_DB_PATH", str(erpx_db))
    monkeypatch.setenv("ERPX_MOCK_SEED_PATH", str(seed_path))
    monkeypatch.setenv("ERPX_MOCK_TOKEN", "testtoken")

    port = get_free_port()
    base_url = f"http://127.0.0.1:{port}"

    # ErpX client base_url (real HTTP to local uvicorn)
    monkeypatch.setenv("ERPX_BASE_URL", base_url)
    monkeypatch.setenv("ERPX_TOKEN", "testtoken")

    # MinIO vars not used (upload is monkeypatched)
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("MINIO_SECRET_KEY", "minioadmin")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

    # Import ERPX mock app and reset connection, then start server
    from openclaw_agent.erpx_mock import main as erpx_main

    erpx_main.DbState.conn = None
    server, thread = run_uvicorn_in_thread(erpx_main.app, port=port)

    try:
        # Import tasks after env is set (it reads settings at import time)
        import importlib

        from openclaw_agent.common.settings import get_settings

        get_settings.cache_clear()
        from openclaw_agent.agent_worker import tasks as worker_tasks
        importlib.reload(worker_tasks)
        from openclaw_agent.common.storage import S3ObjectRef

        # Initialize agent DB schema
        Base.metadata.create_all(worker_tasks.engine)

        # Patch upload_file to avoid MinIO dependency in tests
        def fake_upload_file(
            _settings, bucket: str, key: str, path: str, content_type: str | None = None
        ):
            return S3ObjectRef(bucket="test-bucket", key=key)

        monkeypatch.setattr(worker_tasks, "upload_file", fake_upload_file)

        # Create run #1
        run_id_1 = new_uuid()
        with db_session(worker_tasks.engine) as s:
            s.add(
                AgentRun(
                    run_id=run_id_1,
                    run_type="tax_export",
                    trigger_type="manual",
                    requested_by=None,
                    status="queued",
                    idempotency_key=make_idempotency_key("tax_export", "2026-01", "t1"),
                    cursor_in={"period": "2026-01"},
                    cursor_out=None,
                    started_at=None,
                    finished_at=None,
                    stats=None,
                )
            )

        worker_tasks.dispatch_run.run(run_id_1)

        # Export exists
        with db_session(worker_tasks.engine) as s:
            exports = s.execute(select(AgentExport).where(AgentExport.export_type == "vat_list")).scalars().all()
            assert len(exports) == 1
            assert exports[0].period == "2026-01"
            assert exports[0].version == 1
            assert exports[0].file_uri.startswith("s3://test-bucket/")

        # Create run #2 (same period) and ensure reuse (no new export record)
        run_id_2 = new_uuid()
        with db_session(worker_tasks.engine) as s:
            s.add(
                AgentRun(
                    run_id=run_id_2,
                    run_type="tax_export",
                    trigger_type="manual",
                    requested_by=None,
                    status="queued",
                    idempotency_key=make_idempotency_key("tax_export", "2026-01", "t2"),
                    cursor_in={"period": "2026-01"},
                    cursor_out=None,
                    started_at=None,
                    finished_at=None,
                    stats=None,
                )
            )

        worker_tasks.dispatch_run.run(run_id_2)

        with db_session(worker_tasks.engine) as s:
            exports = s.execute(select(AgentExport).where(AgentExport.export_type == "vat_list")).scalars().all()
            assert len(exports) == 1
    finally:
        stop_uvicorn(server, thread)


def select(model):
    # avoid importing worker module internals here
    return sa.select(model)
