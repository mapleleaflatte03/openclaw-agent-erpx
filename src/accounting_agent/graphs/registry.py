"""Graph registry — factory for compiled LangGraph instances.

get_graph(name) returns a compiled StateGraph for the given workflow.
list_graphs() returns all available graph names.

Gracefully handles missing LangGraph dependency: if langgraph is not
installed, get_graph() raises ImportError and list_graphs() returns [].
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("accounting_agent.graphs.registry")

# ---------------------------------------------------------------------------
# Lazy builders — each returns a compiled LangGraph when called
# ---------------------------------------------------------------------------
_GRAPH_BUILDERS: dict[str, str] = {
    "journal_suggestion": "accounting_agent.graphs.journal_suggestion_graph:build_journal_suggestion_graph",
    "bank_reconcile": "accounting_agent.graphs.bank_reconcile_graph:build_bank_reconcile_graph",
    "soft_checks": "accounting_agent.graphs.soft_checks_graph:build_soft_checks_graph",
    "cashflow_forecast": "accounting_agent.graphs.cashflow_forecast_graph:build_cashflow_forecast_graph",
    "tax_report": "accounting_agent.graphs.tax_report_graph:build_tax_report_graph",
}

# Cache compiled graphs (they are stateless once compiled)
_compiled: dict[str, Any] = {}


def _has_langgraph() -> bool:
    """Check if langgraph is importable."""
    try:
        import langgraph  # noqa: F401
        return True
    except ImportError:
        return False


def _resolve_builder(entry: str) -> Any:
    """Import a 'module:function' entry-point string and call it."""
    module_path, func_name = entry.rsplit(":", 1)
    import importlib
    mod = importlib.import_module(module_path)
    builder = getattr(mod, func_name)
    return builder()


def get_graph(name: str) -> Any:
    """Get a compiled LangGraph by workflow name.

    Returns a compiled StateGraph that can be invoked with:
        graph.invoke({"run_id": "...", "period": "...", ...})

    Raises:
        ImportError: if langgraph is not installed
        KeyError: if name is not a registered graph
    """
    if not _has_langgraph():
        raise ImportError("langgraph is not installed — install with: pip install 'accounting-agent-layer[graphs]'")

    if name not in _GRAPH_BUILDERS:
        raise KeyError(f"Unknown graph: {name!r}. Available: {list(_GRAPH_BUILDERS.keys())}")

    if name not in _compiled:
        log.info("compiling graph: %s", name)
        _compiled[name] = _resolve_builder(_GRAPH_BUILDERS[name])

    return _compiled[name]


def list_graphs() -> list[str]:
    """Return names of all available graphs.

    Returns empty list if langgraph is not installed.
    """
    if not _has_langgraph():
        return []
    return sorted(_GRAPH_BUILDERS.keys())


def is_available() -> bool:
    """Return True if LangGraph runtime is available."""
    return _has_langgraph()
