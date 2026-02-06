from __future__ import annotations

import os
from datetime import date
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException

from openclaw_agent.common.logging import configure_logging, get_logger
from openclaw_agent.erpx_mock.db import connect, init_schema, rows_to_dicts, seed_if_empty

log = get_logger("erpx-mock")


def _require_token(authorization: str | None, token: str) -> None:
    if not token:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    if authorization.split(" ", 1)[1].strip() != token:
        raise HTTPException(status_code=403, detail="invalid token")


class DbState:
    conn = None


def get_conn() -> Any:
    if DbState.conn is None:
        db_path = os.getenv("ERPX_MOCK_DB_PATH", "/data/erpx_mock.sqlite")
        DbState.conn = connect(db_path)
        init_schema(DbState.conn)
        seed_if_empty(DbState.conn, seed_path=os.getenv("ERPX_MOCK_SEED_PATH"))
    return DbState.conn


app = FastAPI(title="ERPX Mock API", version=os.getenv("APP_VERSION", "0.1.0"))


@app.on_event("startup")
def _startup() -> None:
    configure_logging(os.getenv("LOG_LEVEL", "INFO"))
    get_conn()
    log.info("startup", db=os.getenv("ERPX_MOCK_DB_PATH", "/data/erpx_mock.sqlite"))


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def auth_dep(authorization: str | None = Header(default=None)) -> None:
    _require_token(authorization, os.getenv("ERPX_MOCK_TOKEN", os.getenv("ERPX_TOKEN", "")))


@app.get("/erp/v1/journals", dependencies=[Depends(auth_dep)])
def get_journals(updated_after: str | None = None, conn=Depends(get_conn)) -> list[dict[str, Any]]:
    q = "SELECT * FROM journals"
    params: tuple[Any, ...] = ()
    if updated_after:
        q += " WHERE updated_at > ?"
        params = (updated_after,)
    q += " ORDER BY date DESC"
    return rows_to_dicts(conn.execute(q, params).fetchall())


@app.get("/erp/v1/vouchers", dependencies=[Depends(auth_dep)])
def get_vouchers(updated_after: str | None = None, conn=Depends(get_conn)) -> list[dict[str, Any]]:
    q = "SELECT * FROM vouchers"
    params: tuple[Any, ...] = ()
    if updated_after:
        q += " WHERE updated_at > ?"
        params = (updated_after,)
    q += " ORDER BY date DESC"
    rows = rows_to_dicts(conn.execute(q, params).fetchall())
    # normalize bool
    for r in rows:
        r["has_attachment"] = bool(r.get("has_attachment"))
    return rows


@app.get("/erp/v1/invoices", dependencies=[Depends(auth_dep)])
def get_invoices(period: str, conn=Depends(get_conn)) -> list[dict[str, Any]]:
    # period: YYYY-MM
    q = "SELECT * FROM invoices WHERE substr(date,1,7) = ? ORDER BY date DESC"
    return rows_to_dicts(conn.execute(q, (period,)).fetchall())


@app.get("/erp/v1/ar/aging", dependencies=[Depends(auth_dep)])
def get_ar_aging(as_of: str, conn=Depends(get_conn)) -> list[dict[str, Any]]:
    # MVP: compute from invoices (unpaid + overdue)
    as_of_date = date.fromisoformat(as_of)
    invs = rows_to_dicts(conn.execute("SELECT * FROM invoices").fetchall())
    out: list[dict[str, Any]] = []
    for inv in invs:
        if inv.get("status") != "unpaid":
            continue
        try:
            due = date.fromisoformat(inv["due_date"])
        except Exception:
            continue
        overdue_days = (as_of_date - due).days
        if overdue_days <= 0:
            continue
        out.append(
            {
                "customer_id": inv["customer_id"],
                "invoice_id": inv["invoice_id"],
                "overdue_days": overdue_days,
                "amount": inv["amount"],
                "email": inv.get("email"),
            }
        )
    return out


@app.get("/erp/v1/assets", dependencies=[Depends(auth_dep)])
def get_assets(updated_after: str | None = None, conn=Depends(get_conn)) -> list[dict[str, Any]]:
    q = "SELECT * FROM assets"
    params: tuple[Any, ...] = ()
    if updated_after:
        q += " WHERE updated_at > ?"
        params = (updated_after,)
    q += " ORDER BY acquisition_date DESC"
    return rows_to_dicts(conn.execute(q, params).fetchall())


@app.get("/erp/v1/close/calendar", dependencies=[Depends(auth_dep)])
def get_close_calendar(period: str, conn=Depends(get_conn)) -> list[dict[str, Any]]:
    q = "SELECT * FROM close_calendar WHERE period = ? ORDER BY due_date ASC"
    return rows_to_dicts(conn.execute(q, (period,)).fetchall())

