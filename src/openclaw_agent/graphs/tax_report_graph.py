"""LangGraph: Tax Report workflow.

Graph: fetch_data → generate_report → (end)
Pulls invoices + vouchers → creates VAT and trial-balance snapshots.
"""
from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from openclaw_agent.common.db import db_session, make_engine
from openclaw_agent.common.erpx_client import ErpXClient
from openclaw_agent.common.settings import get_settings
from openclaw_agent.flows.tax_report import flow_tax_report
from openclaw_agent.graphs.state import AcctGraphState

log = logging.getLogger("openclaw.graphs.tax_report")


def _fetch_data(state: AcctGraphState) -> dict[str, Any]:
    """Node: pull invoices + vouchers from ERP mock."""
    settings = get_settings()
    period = state.get("period", "")
    try:
        client = ErpXClient(settings)
        invoices = client.get_invoices(period) if period else []
        vouchers = client.get_vouchers()
        client.close()
        has_data = (len(invoices) + len(vouchers)) > 0
        log.info("fetched %d invoices, %d vouchers", len(invoices), len(vouchers))
        return {
            "invoices": invoices,
            "vouchers": vouchers,
            "has_data": has_data,
            "step": "fetch_data",
        }
    except Exception as e:
        log.warning("fetch_data failed: %s", e)
        return {"has_data": False, "errors": [str(e)], "step": "fetch_data"}


def _should_continue(state: AcctGraphState) -> str:
    if state.get("has_data"):
        return "report"
    return "end"


def _generate_report(state: AcctGraphState) -> dict[str, Any]:
    """Node: create VAT + trial balance report snapshots."""
    settings = get_settings()
    engine = make_engine(settings.agent_db_dsn)
    run_id = state["run_id"]
    period = state.get("period", "")
    try:
        with db_session(engine) as s:
            stats = flow_tax_report(
                s,
                state.get("invoices", []),
                state.get("vouchers", []),
                period,
                run_id,
            )
            s.commit()
        return {"flow_stats": stats, "step": "generate_report"}
    except Exception as e:
        log.error("generate_report failed: %s", e)
        return {"errors": state.get("errors", []) + [str(e)], "step": "generate_report"}


def build_tax_report_graph() -> Any:
    """Build and compile the tax_report LangGraph."""
    graph = StateGraph(AcctGraphState)
    graph.add_node("fetch", _fetch_data)
    graph.add_node("report", _generate_report)
    graph.set_entry_point("fetch")
    graph.add_conditional_edges("fetch", _should_continue, {"report": "report", "end": END})
    graph.add_edge("report", END)
    return graph.compile()
