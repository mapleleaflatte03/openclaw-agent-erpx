from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_erpx_mock_endpoints(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "erpx_mock.sqlite"
    seed_path = Path("data/kaggle/seed/erpx_seed_kaggle.json").resolve()

    monkeypatch.setenv("ERPX_MOCK_DB_PATH", str(db_path))
    monkeypatch.setenv("ERPX_MOCK_SEED_PATH", str(seed_path))
    monkeypatch.setenv("ERPX_MOCK_TOKEN", "testtoken")

    from accounting_agent.erpx_mock import main as erpx_main

    erpx_main.DbState.conn = None  # force re-init with env above
    client = TestClient(erpx_main.app)

    headers = {"Authorization": "Bearer testtoken"}

    r = client.get("/erp/v1/invoices", params={"period": "2026-01"}, headers=headers)
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    assert "invoice_id" in items[0]  # Kaggle IDs: KG-INV-...

    r = client.get("/erp/v1/ar/aging", params={"as_of": "2026-02-06"}, headers=headers)
    assert r.status_code == 200
    aging = r.json()
    assert len(aging) >= 1  # at least one overdue invoice from Kaggle seed

    r = client.get("/erp/v1/vouchers", headers=headers)
    assert r.status_code == 200
    vouchers = r.json()
    assert len(vouchers) >= 1

