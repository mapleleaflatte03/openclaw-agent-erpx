"""LangGraph: Cashflow Forecast workflow.

Graph: fetch_data → forecast → (end)
Pulls invoices + bank txs → projects 30-day inflow/outflow → persists.
"""
from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from openclaw_agent.common.db import db_session, make_engine
from openclaw_agent.common.erpx_client import ErpXClient
from openclaw_agent.common.settings import get_settings
from openclaw_agent.flows.cashflow_forecast import flow_cashflow_forecast
from openclaw_agent.graphs.state import AcctGraphState

log = logging.getLogger("openclaw.graphs.cashflow_forecast")


def _fetch_data(state: AcctGraphState) -> dict[str, Any]:
    """Node: pull invoices + bank transactions from ERP mock."""
    settings = get_settings()
    period = state.get("period", "")
    try:
        client = ErpXClient(settings)
        invoices = client.get_invoices(period) if period else []
        bank_txs = client.get_bank_transactions()
        client.close()
        has_data = (len(invoices) + len(bank_txs)) > 0
        log.info("fetched %d invoices, %d bank_txs", len(invoices), len(bank_txs))
        return {
            "invoices": invoices,
            "bank_txs": bank_txs,
            "has_data": has_data,
            "step": "fetch_data",
        }
    except Exception as e:
        log.warning("fetch_data failed: %s", e)
        return {"has_data": False, "errors": [str(e)], "step": "fetch_data"}


def _should_continue(state: AcctGraphState) -> str:
    if state.get("has_data"):
        return "forecast"
    return "end"


def _forecast(state: AcctGraphState) -> dict[str, Any]:
    """Node: build cashflow forecast and persist rows."""
    settings = get_settings()
    engine = make_engine(settings.agent_db_dsn)
    run_id = state["run_id"]
    try:
        with db_session(engine) as s:
            stats = flow_cashflow_forecast(
                s,
                state.get("invoices", []),
                state.get("bank_txs", []),
                run_id,
            )
            s.commit()
        return {"flow_stats": stats, "step": "forecast"}
    except Exception as e:
        log.error("forecast failed: %s", e)
        return {"errors": state.get("errors", []) + [str(e)], "step": "forecast"}


def build_cashflow_forecast_graph() -> Any:
    """Build and compile the cashflow_forecast LangGraph."""
    graph = StateGraph(AcctGraphState)
    graph.add_node("fetch", _fetch_data)
    graph.add_node("forecast", _forecast)
    graph.set_entry_point("fetch")
    graph.add_conditional_edges("fetch", _should_continue, {"forecast": "forecast", "end": END})
    graph.add_edge("forecast", END)
    return graph.compile()
