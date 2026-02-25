"""LangGraph: Journal Suggestion workflow.

Graph: fetch_vouchers → classify_and_propose → (end)
Each node operates on AcctGraphState. Session management is handled
internally per-node using db_session().
"""
from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from accounting_agent.common.db import db_session, make_engine
from accounting_agent.common.erpx_client import ErpXClient
from accounting_agent.common.settings import get_settings
from accounting_agent.flows.journal_suggestion import flow_journal_suggestion
from accounting_agent.graphs.state import AcctGraphState

log = logging.getLogger("accounting_agent.graphs.journal_suggestion")


def _fetch_vouchers(state: AcctGraphState) -> dict[str, Any]:
    """Node: pull vouchers from ERP mock."""
    settings = get_settings()
    try:
        client = ErpXClient(settings)
        vouchers = client.get_vouchers()
        client.close()
        log.info("fetched %d vouchers", len(vouchers))
        return {"vouchers": vouchers, "has_data": len(vouchers) > 0, "step": "fetch_vouchers"}
    except Exception as e:
        log.warning("fetch_vouchers failed: %s", e)
        return {"vouchers": [], "has_data": False, "errors": [str(e)], "step": "fetch_vouchers"}


def _should_continue(state: AcctGraphState) -> str:
    """Conditional edge: skip classification if no vouchers."""
    if state.get("has_data"):
        return "classify"
    return "end"


def _classify_and_propose(state: AcctGraphState) -> dict[str, Any]:
    """Node: run journal suggestion flow — creates proposals in DB."""
    settings = get_settings()
    engine = make_engine(settings.agent_db_dsn)
    run_id = state["run_id"]
    vouchers = state.get("vouchers", [])
    try:
        with db_session(engine) as s:
            stats = flow_journal_suggestion(s, vouchers, run_id)
            s.commit()
        return {"flow_stats": stats, "step": "classify_and_propose"}
    except Exception as e:
        log.error("classify_and_propose failed: %s", e)
        return {"errors": state.get("errors", []) + [str(e)], "step": "classify_and_propose"}


def build_journal_suggestion_graph() -> Any:
    """Build and compile the journal_suggestion LangGraph."""
    graph = StateGraph(AcctGraphState)
    graph.add_node("fetch", _fetch_vouchers)
    graph.add_node("classify", _classify_and_propose)
    graph.set_entry_point("fetch")
    graph.add_conditional_edges("fetch", _should_continue, {"classify": "classify", "end": END})
    graph.add_edge("classify", END)
    return graph.compile()
