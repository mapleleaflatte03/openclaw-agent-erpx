from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Iterable
from typing import Any

log = logging.getLogger("accounting_agent.erpx_mock.db")


def connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS partners (
          partner_id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          tax_id TEXT,
          email TEXT,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS contracts (
          contract_id TEXT PRIMARY KEY,
          contract_code TEXT NOT NULL,
          partner_id TEXT NOT NULL,
          start_date TEXT,
          end_date TEXT,
          currency TEXT NOT NULL,
          total_amount REAL,
          status TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS payments (
          payment_id TEXT PRIMARY KEY,
          contract_id TEXT NOT NULL,
          date TEXT NOT NULL,
          amount REAL NOT NULL,
          currency TEXT NOT NULL,
          method TEXT,
          note TEXT,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS invoices (
          invoice_id TEXT PRIMARY KEY,
          invoice_no TEXT NOT NULL,
          tax_id TEXT NOT NULL,
          date TEXT NOT NULL,
          amount REAL NOT NULL,
          customer_id TEXT NOT NULL,
          due_date TEXT NOT NULL,
          status TEXT NOT NULL,
          email TEXT,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vouchers (
          voucher_id TEXT PRIMARY KEY,
          voucher_no TEXT NOT NULL,
          voucher_type TEXT NOT NULL DEFAULT 'other',
          date TEXT NOT NULL,
          amount REAL NOT NULL,
          currency TEXT NOT NULL DEFAULT 'VND',
          partner_name TEXT,
          description TEXT,
          has_attachment INTEGER NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bank_transactions (
          tx_id TEXT PRIMARY KEY,
          tx_ref TEXT NOT NULL UNIQUE,
          bank_account TEXT NOT NULL DEFAULT '112-VCB-001',
          date TEXT NOT NULL,
          amount REAL NOT NULL,
          currency TEXT NOT NULL DEFAULT 'VND',
          counterparty TEXT,
          memo TEXT,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS journals (
          journal_id TEXT PRIMARY KEY,
          journal_no TEXT NOT NULL,
          date TEXT NOT NULL,
          debit_total REAL NOT NULL,
          credit_total REAL NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS assets (
          asset_id TEXT PRIMARY KEY,
          asset_no TEXT NOT NULL,
          acquisition_date TEXT NOT NULL,
          cost REAL NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS close_calendar (
          id TEXT PRIMARY KEY,
          period TEXT NOT NULL,
          task_name TEXT NOT NULL,
          owner_user_id TEXT,
          due_date TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, os.pardir))


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])


def _find_kaggle_seed() -> str | None:
    """Search for erpx_seed_kaggle.json in known locations."""
    candidates = [
        os.path.join(os.getenv("VN_DATA_ROOT", ""), "kaggle", "seed", "erpx_seed_kaggle.json"),
        os.path.join(_REPO_ROOT, "data", "kaggle", "seed", "erpx_seed_kaggle.json"),
        "/data/kaggle/seed/erpx_seed_kaggle.json",
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


def seed_if_empty(conn: sqlite3.Connection, seed_path: str | None = None) -> None:
    if _count(conn, "invoices") > 0:
        return

    if seed_path and os.path.exists(seed_path):
        with open(seed_path, encoding="utf-8") as f:
            seed = json.load(f)
        _seed_from_json(conn, seed)
        return

    # R2: Default seed from Kaggle-derived data (no fabricated data).
    kaggle_seed = _find_kaggle_seed()
    if kaggle_seed:
        with open(kaggle_seed, encoding="utf-8") as f:
            seed = json.load(f)
        _seed_from_json(conn, seed)
        log.info("Seeded from Kaggle: %s", kaggle_seed)
        return

    # Fallback: minimal structural seed (no business values fabricated).
    # Only creates schema-required rows so API endpoints don't 500.
    log.warning("No Kaggle seed found — using minimal structural seed")
    _seed_from_json(
        conn,
        {
            "partners": [],
            "contracts": [],
            "payments": [],
            "invoices": [],
            "vouchers": [],
            "journals": [],
            "assets": [],
            "close_calendar": [],
            "bank_transactions": [],
        },
    )


def _seed_from_json(conn: sqlite3.Connection, seed: dict[str, Any]) -> None:
    c = conn.cursor()

    for p in seed.get("partners", []):
        c.execute(
            """
            INSERT INTO partners (partner_id, name, tax_id, email, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (p["partner_id"], p["name"], p.get("tax_id"), p.get("email"), p["updated_at"]),
        )

    for ct in seed.get("contracts", []):
        c.execute(
            """
            INSERT INTO contracts (contract_id, contract_code, partner_id, start_date, end_date, currency, total_amount, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ct["contract_id"],
                ct["contract_code"],
                ct["partner_id"],
                ct.get("start_date"),
                ct.get("end_date"),
                ct.get("currency", "VND"),
                ct.get("total_amount"),
                ct.get("status", "active"),
                ct["updated_at"],
            ),
        )

    for pay in seed.get("payments", []):
        c.execute(
            """
            INSERT INTO payments (payment_id, contract_id, date, amount, currency, method, note, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pay["payment_id"],
                pay["contract_id"],
                pay["date"],
                pay["amount"],
                pay.get("currency", "VND"),
                pay.get("method"),
                pay.get("note"),
                pay["updated_at"],
            ),
        )

    for inv in seed.get("invoices", []):
        c.execute(
            """
            INSERT INTO invoices (invoice_id, invoice_no, tax_id, date, amount, customer_id, due_date, status, email, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                inv["invoice_id"],
                inv["invoice_no"],
                inv["tax_id"],
                inv["date"],
                inv["amount"],
                inv["customer_id"],
                inv["due_date"],
                inv["status"],
                inv.get("email"),
                inv["updated_at"],
            ),
        )

    for v in seed.get("vouchers", []):
        c.execute(
            """
            INSERT INTO vouchers (voucher_id, voucher_no, voucher_type, date, amount, currency, partner_name, description, has_attachment, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                v["voucher_id"], v["voucher_no"], v.get("voucher_type", "other"),
                v["date"], v["amount"], v.get("currency", "VND"),
                v.get("partner_name"), v.get("description"),
                int(v["has_attachment"]), v["updated_at"],
            ),
        )

    for j in seed.get("journals", []):
        c.execute(
            """
            INSERT INTO journals (journal_id, journal_no, date, debit_total, credit_total, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (j["journal_id"], j["journal_no"], j["date"], j["debit_total"], j["credit_total"], j["updated_at"]),
        )

    for a in seed.get("assets", []):
        c.execute(
            """
            INSERT INTO assets (asset_id, asset_no, acquisition_date, cost, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (a["asset_id"], a["asset_no"], a["acquisition_date"], a["cost"], a["updated_at"]),
        )

    for t in seed.get("close_calendar", []):
        c.execute(
            """
            INSERT INTO close_calendar (id, period, task_name, owner_user_id, due_date, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (t["id"], t["period"], t["task_name"], t.get("owner_user_id"), t["due_date"], t["updated_at"]),
        )

    for btx in seed.get("bank_transactions", []):
        c.execute(
            """
            INSERT INTO bank_transactions (tx_id, tx_ref, bank_account, date, amount, currency, counterparty, memo, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                btx["tx_id"], btx["tx_ref"], btx.get("bank_account", "112-VCB-001"),
                btx["date"], btx["amount"], btx.get("currency", "VND"),
                btx.get("counterparty"), btx.get("memo"), btx["updated_at"],
            ),
        )

    conn.commit()


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
