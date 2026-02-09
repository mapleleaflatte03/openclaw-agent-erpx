"""LangGraph-based accounting workflow graphs.

This module provides LangGraph StateGraph wrappers for the accounting flows.
Each graph is a directed acyclic graph of nodes that call existing flow functions.
Graphs are optional — the system falls back to sequential execution if LangGraph
is unavailable.

Usage:
    from openclaw_agent.graphs import get_graph
    graph = get_graph("journal_suggestion")
    result = graph.invoke({"run_id": "...", "session": session, "client": client})
"""
from __future__ import annotations

from openclaw_agent.graphs.registry import get_graph, is_available, list_graphs

__all__ = ["get_graph", "is_available", "list_graphs"]
