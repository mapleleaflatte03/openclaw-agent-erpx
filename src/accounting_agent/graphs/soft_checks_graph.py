"""LangGraph: Soft Checks workflow.

Graph: fetch_data → run_checks → (end)
Pulls vouchers, journals, invoices → runs soft-check rules → persists results.
"""
from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from accounting_agent.common.db import db_session, make_engine
from accounting_agent.common.erpx_client import ErpXClient
from accounting_agent.common.settings import get_settings
from accounting_agent.flows.soft_checks_acct import flow_soft_checks_acct
from accounting_agent.graphs.state import AcctGraphState

log = logging.getLogger("accounting_agent.graphs.soft_checks")


def _fetch_data(state: AcctGraphState) -> dict[str, Any]:
    """Node: pull vouchers, journals, invoices from ERP mock."""
    settings = get_settings()
    period = state.get("period", "")
    try:
        client = ErpXClient(settings)
        vouchers = client.get_vouchers()
        journals = client.get_journals()
        invoices = client.get_invoices(period) if period else []
        client.close()
        has_data = (len(vouchers) + len(journals) + len(invoices)) > 0
        log.info("fetched %d vouchers, %d journals, %d invoices", len(vouchers), len(journals), len(invoices))
        return {
            "vouchers": vouchers,
            "journals": journals,
            "invoices": invoices,
            "has_data": has_data,
            "step": "fetch_data",
        }
    except Exception as e:
        log.warning("fetch_data failed: %s", e)
        return {"has_data": False, "errors": [str(e)], "step": "fetch_data"}


def _should_continue(state: AcctGraphState) -> str:
    if state.get("has_data"):
        return "check"
    return "end"


def _run_checks(state: AcctGraphState) -> dict[str, Any]:
    """Node: run all soft-check rules and persist results."""
    settings = get_settings()
    engine = make_engine(settings.agent_db_dsn)
    run_id = state["run_id"]
    period = state.get("period", "")
    try:
        with db_session(engine) as s:
            stats = flow_soft_checks_acct(
                s,
                state.get("vouchers", []),
                state.get("journals", []),
                state.get("invoices", []),
                period,
                run_id,
            )
            s.commit()
        return {"flow_stats": stats, "step": "run_checks"}
    except Exception as e:
        log.error("run_checks failed: %s", e)
        return {"errors": state.get("errors", []) + [str(e)], "step": "run_checks"}


def build_soft_checks_graph() -> Any:
    """Build and compile the soft_checks LangGraph."""
    graph = StateGraph(AcctGraphState)
    graph.add_node("fetch", _fetch_data)
    graph.add_node("check", _run_checks)
    graph.set_entry_point("fetch")
    graph.add_conditional_edges("fetch", _should_continue, {"check": "check", "end": END})
    graph.add_edge("check", END)
    return graph.compile()
