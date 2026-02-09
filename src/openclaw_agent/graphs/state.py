"""State types for LangGraph accounting graphs."""
from __future__ import annotations

from typing import Any

from typing_extensions import TypedDict


class AcctGraphState(TypedDict, total=False):
    """Shared state flowing through accounting graph nodes."""
    run_id: str
    period: str
    # Data fetched from ERP
    vouchers: list[dict[str, Any]]
    journals: list[dict[str, Any]]
    invoices: list[dict[str, Any]]
    bank_txs: list[dict[str, Any]]
    # Flow results
    flow_stats: dict[str, Any]
    errors: list[str]
    # Control flags
    has_data: bool
    step: str
