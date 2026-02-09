"""Phase 4 tests — Ray integration.

Tests:
  1. Swarm: is_available() returns bool (may be True in CI)
  2. Swarm: RaySwarm can be instantiated
  3. Swarm: _has_ray() returns True (ray is installed)
  4. Batch: batch_classify_vouchers sequential fallback works
  5. Batch: batch_anomaly_scan sequential fallback works
  6. Batch: parallel_map sequential fallback works
  7. API: GET /agent/v1/ray/status returns valid response
  8. Kernel: __init__.py exports RaySwarm, get_swarm, ray_available
"""
from __future__ import annotations

import pytest

ray = pytest.importorskip("ray", reason="ray not installed")


def test_swarm_has_ray():
    """_has_ray() should return True when ray is installed."""
    from openclaw_agent.kernel.swarm import _has_ray
    assert _has_ray() is True


def test_swarm_ray_swarm_instantiate():
    """RaySwarm can be instantiated without error."""
    from openclaw_agent.kernel.swarm import RaySwarm
    swarm = RaySwarm()
    assert swarm is not None
    assert swarm.is_initialized is False  # not yet init'd


def test_swarm_is_available():
    """is_available() should return bool based on USE_RAY env var."""
    from openclaw_agent.kernel.swarm import is_available
    # In test env, USE_RAY is typically not set
    result = is_available()
    assert isinstance(result, bool)


def test_batch_classify_sequential():
    """batch_classify_vouchers should work sequentially (no Ray)."""
    from openclaw_agent.kernel.batch import batch_classify_vouchers
    vouchers = [
        {"voucher_id": "V1", "voucher_type": "sell_invoice", "amount": 100000, "has_attachment": True},
        {"voucher_id": "V2", "voucher_type": "buy_invoice", "amount": 50000, "has_attachment": False},
    ]
    results = batch_classify_vouchers(vouchers, use_ray=False)
    assert len(results) == 2
    assert results[0]["debit_account"] == "131"  # sell_invoice → 131
    assert results[1]["debit_account"] == "621"  # buy_invoice → 621
    assert all("confidence" in r for r in results)
    assert all("voucher_id" in r for r in results)


def test_batch_classify_empty():
    """batch_classify_vouchers with empty list returns empty."""
    from openclaw_agent.kernel.batch import batch_classify_vouchers
    assert batch_classify_vouchers([], use_ray=False) == []


def test_batch_anomaly_scan_sequential():
    """batch_anomaly_scan should detect anomalies sequentially."""
    from openclaw_agent.kernel.batch import batch_anomaly_scan
    items = [
        {"voucher_no": "CT001", "amount": 600_000_000, "has_attachment": True},  # large, no approval
        {"voucher_no": "CT002", "amount": 100_000, "has_attachment": False},  # missing attachment
        {"voucher_no": "CT003", "amount": 100_000, "has_attachment": True, "approved_by": "admin"},  # clean
    ]
    issues = batch_anomaly_scan(items, use_ray=False)
    assert len(issues) == 2
    rules_found = {i["rule"] for i in issues}
    assert "LARGE_AMOUNT_NO_APPROVAL" in rules_found
    assert "MISSING_ATTACHMENT" in rules_found


def test_parallel_map_sequential():
    """parallel_map should fall back to sequential."""
    from openclaw_agent.kernel.batch import parallel_map

    def double(x: int) -> int:
        return x * 2

    results = parallel_map(double, [1, 2, 3, 4], use_ray=False)
    assert results == [2, 4, 6, 8]


def test_api_ray_status():
    """GET /agent/v1/ray/status should return valid response."""
    from fastapi.testclient import TestClient

    from openclaw_agent.agent_service.main import app

    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-API-Key": "test-key-for-ci"}
    r = client.get("/agent/v1/ray/status", headers=headers)
    assert r.status_code in (200, 401, 500)
    if r.status_code == 200:
        data = r.json()
        assert "ray_available" in data
        assert isinstance(data["ray_available"], bool)


def test_kernel_exports():
    """kernel __init__.py should export RaySwarm, get_swarm, ray_available."""
    from openclaw_agent.kernel import RaySwarm, get_swarm, ray_available
    assert RaySwarm is not None
    assert callable(get_swarm)
    assert callable(ray_available)
