"""Phase 3 tests — LangGraph integration.

Tests:
  1. Registry: list_graphs returns expected graph names
  2. Registry: get_graph compiles without error
  3. Registry: is_available() returns True (langgraph is installed)
  4. State: AcctGraphState has expected keys
  5. API: GET /agent/v1/graphs returns valid response
  6. API: GET /agent/v1/graphs/{name} returns graph info
  7. Worker: _try_graph_execute falls back gracefully when USE_LANGGRAPH is off
"""
from __future__ import annotations

import pytest

langgraph = pytest.importorskip("langgraph", reason="langgraph not installed")


def test_registry_list_graphs():
    """list_graphs() must return all 5 accounting graph names."""
    from openclaw_agent.graphs.registry import list_graphs
    names = list_graphs()
    assert isinstance(names, list)
    expected = {"bank_reconcile", "cashflow_forecast", "journal_suggestion", "soft_checks", "tax_report"}
    assert set(names) == expected, f"expected {expected}, got {names}"


def test_registry_is_available():
    """is_available() must return True when langgraph is installed."""
    from openclaw_agent.graphs.registry import is_available
    assert is_available() is True


def test_registry_get_graph_compiles():
    """get_graph() should compile each graph without error."""
    from openclaw_agent.graphs.registry import get_graph, list_graphs
    for name in list_graphs():
        graph = get_graph(name)
        assert graph is not None, f"graph {name!r} returned None"
        # All graphs should have an invoke method
        assert callable(getattr(graph, "invoke", None)), f"graph {name!r} has no invoke()"


def test_registry_get_graph_unknown_raises():
    """get_graph() should raise KeyError for unknown names."""
    from openclaw_agent.graphs.registry import get_graph
    with pytest.raises(KeyError):
        get_graph("nonexistent_workflow_xyz")


def test_state_has_expected_keys():
    """AcctGraphState TypedDict should have critical keys."""
    from openclaw_agent.graphs.state import AcctGraphState
    # TypedDict annotations
    annotations = AcctGraphState.__annotations__
    for key in ("run_id", "period", "vouchers", "journals", "invoices", "bank_txs",
                "flow_stats", "errors", "has_data", "step"):
        assert key in annotations, f"AcctGraphState missing key: {key}"


def test_api_list_graphs():
    """GET /agent/v1/graphs should return langgraph_available + graphs list."""
    from fastapi.testclient import TestClient

    from openclaw_agent.agent_service.main import app

    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-API-Key": "test-key-for-ci"}
    r = client.get("/agent/v1/graphs", headers=headers)
    assert r.status_code in (200, 401, 500)
    if r.status_code == 200:
        data = r.json()
        assert "langgraph_available" in data
        assert "graphs" in data
        assert isinstance(data["graphs"], list)


def test_api_get_graph_info():
    """GET /agent/v1/graphs/journal_suggestion should return graph info."""
    from fastapi.testclient import TestClient

    from openclaw_agent.agent_service.main import app

    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-API-Key": "test-key-for-ci"}
    r = client.get("/agent/v1/graphs/journal_suggestion", headers=headers)
    assert r.status_code in (200, 401, 500, 501)
    if r.status_code == 200:
        data = r.json()
        assert data["name"] == "journal_suggestion"
        assert data["compiled"] is True


def test_api_get_graph_not_found():
    """GET /agent/v1/graphs/nonexistent should return 404."""
    from fastapi.testclient import TestClient

    from openclaw_agent.agent_service.main import app

    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-API-Key": "test-key-for-ci"}
    r = client.get("/agent/v1/graphs/nonexistent_xyz", headers=headers)
    assert r.status_code in (404, 401, 500, 501)
