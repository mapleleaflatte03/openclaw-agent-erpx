"""Hardening tests — rate limiter + audit append-only guard."""
from __future__ import annotations

import time


def test_rate_limiter_enforces_qps():
    """_RateLimiter should enforce the configured QPS limit."""
    from openclaw_agent.common.erpx_client import _RateLimiter

    qps = 10.0
    limiter = _RateLimiter.create(qps)

    # Rapidly acquire 5 tokens and measure elapsed time
    start = time.monotonic()
    for _ in range(5):
        limiter.acquire()
    elapsed = time.monotonic() - start

    # With 10 qps, 5 acquisitions should take at least 0.4s (4 intervals of 0.1s)
    # Allow some tolerance for scheduling jitter
    assert elapsed >= 0.35, f"Rate limiter too fast: {elapsed:.3f}s for 5 tokens at {qps} qps"


def test_rate_limiter_zero_qps_no_delay():
    """QPS=0 should disable rate limiting (no delay)."""
    from openclaw_agent.common.erpx_client import _RateLimiter

    limiter = _RateLimiter.create(0.0)
    start = time.monotonic()
    for _ in range(10):
        limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.1, f"Zero-QPS limiter should not delay: {elapsed:.3f}s"


def test_audit_log_model_has_no_update_method():
    """AgentAuditLog should not expose update helpers that could bypass append-only."""
    from openclaw_agent.common.models import AgentAuditLog

    # The model class should not have custom update/delete methods
    assert not hasattr(AgentAuditLog, "update"), "AgentAuditLog must not have an update() method"
    assert not hasattr(AgentAuditLog, "soft_delete"), "AgentAuditLog must not have a soft_delete() method"


def test_audit_table_name():
    """Confirm the audit table name used by the Postgres trigger migration."""
    from openclaw_agent.common.models import AgentAuditLog

    assert AgentAuditLog.__tablename__ == "agent_audit_log"


def test_erpx_client_retry_max_3():
    """Default retry max attempts should be <= 3."""
    import os

    from openclaw_agent.common.settings import Settings

    # Create settings with minimal required env
    env = {
        "AGENT_DB_DSN": "sqlite:///:memory:",
        "REDIS_URL": "redis://localhost:6379/0",
        "CELERY_BROKER_URL": "redis://localhost:6379/0",
        "CELERY_RESULT_BACKEND": "redis://localhost:6379/1",
        "MINIO_ENDPOINT": "localhost:9000",
        "MINIO_ACCESS_KEY": "test",
        "MINIO_SECRET_KEY": "test",
        "ERPX_BASE_URL": "http://localhost:8001",
    }
    for k, v in env.items():
        os.environ.setdefault(k, v)

    s = Settings()
    assert s.erpx_retry_max_attempts <= 3, f"Retry max should be <=3, got {s.erpx_retry_max_attempts}"
    assert s.erpx_rate_limit_qps == 10.0, f"Rate limit should be 10 qps, got {s.erpx_rate_limit_qps}"
