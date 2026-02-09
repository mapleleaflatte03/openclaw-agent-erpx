"""LangGraph: Bank Reconciliation workflow.

Graph: fetch_data → reconcile → (end)
Fetches bank txs + vouchers, then runs matching/anomaly-flagging flow.
"""
from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from openclaw_agent.common.db import db_session, make_engine
from openclaw_agent.common.erpx_client import ErpXClient
from openclaw_agent.common.settings import get_settings
from openclaw_agent.flows.bank_reconcile import flow_bank_reconcile
from openclaw_agent.graphs.state import AcctGraphState

log = logging.getLogger("openclaw.graphs.bank_reconcile")


def _fetch_data(state: AcctGraphState) -> dict[str, Any]:
    """Node: pull bank transactions + vouchers from ERP mock."""
    settings = get_settings()
    try:
        client = ErpXClient(settings)
        bank_txs = client.get_bank_transactions()
        vouchers = client.get_vouchers()
        client.close()
        has_data = len(bank_txs) > 0
        log.info("fetched %d bank_txs, %d vouchers", len(bank_txs), len(vouchers))
        return {
            "bank_txs": bank_txs,
            "vouchers": vouchers,
            "has_data": has_data,
            "step": "fetch_data",
        }
    except Exception as e:
        log.warning("fetch_data failed: %s", e)
        return {"bank_txs": [], "vouchers": [], "has_data": False, "errors": [str(e)], "step": "fetch_data"}


def _should_continue(state: AcctGraphState) -> str:
    if state.get("has_data"):
        return "reconcile"
    return "end"


def _reconcile(state: AcctGraphState) -> dict[str, Any]:
    """Node: run bank reconciliation flow — match txs, flag anomalies."""
    settings = get_settings()
    engine = make_engine(settings.agent_db_dsn)
    run_id = state["run_id"]
    bank_txs = state.get("bank_txs", [])
    vouchers = state.get("vouchers", [])
    try:
        with db_session(engine) as s:
            stats = flow_bank_reconcile(s, bank_txs, vouchers, run_id)
            s.commit()
        return {"flow_stats": stats, "step": "reconcile"}
    except Exception as e:
        log.error("reconcile failed: %s", e)
        return {"errors": state.get("errors", []) + [str(e)], "step": "reconcile"}


def build_bank_reconcile_graph() -> Any:
    """Build and compile the bank_reconcile LangGraph."""
    graph = StateGraph(AcctGraphState)
    graph.add_node("fetch", _fetch_data)
    graph.add_node("reconcile", _reconcile)
    graph.set_entry_point("fetch")
    graph.add_conditional_edges("fetch", _should_continue, {"reconcile": "reconcile", "end": END})
    graph.add_edge("reconcile", END)
    return graph.compile()
