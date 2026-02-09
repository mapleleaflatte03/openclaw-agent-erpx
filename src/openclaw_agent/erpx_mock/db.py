from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

from openclaw_agent.common.utils import new_uuid


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


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])


def seed_if_empty(conn: sqlite3.Connection, seed_path: str | None = None) -> None:
    if _count(conn, "invoices") > 0:
        return

    if seed_path and os.path.exists(seed_path):
        with open(seed_path, encoding="utf-8") as f:
            seed = json.load(f)
        _seed_from_json(conn, seed)
        return

    # Default lightweight seed (enough to demo end-to-end).
    today = date.today()
    period_first = today.replace(day=1)
    updated_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    partners = [
        {
            "partner_id": "PARTNER-0001",
            "name": "ACME Supplies LLC",
            "tax_id": "0312345678",
            "email": "ap@acme.example.local",
            "updated_at": updated_at,
        },
        {
            "partner_id": "PARTNER-0002",
            "name": "Sunrise Services Co",
            "tax_id": "0109876543",
            "email": "billing@sunrise.example.local",
            "updated_at": updated_at,
        },
    ]

    contracts = [
        {
            "contract_id": "CONTRACT-0001",
            "contract_code": "HD-ACME-2026-0001",
            "partner_id": "PARTNER-0001",
            "start_date": (today - timedelta(days=30)).isoformat(),
            "end_date": (today + timedelta(days=330)).isoformat(),
            "currency": "VND",
            "total_amount": 120_000_000.0,
            "status": "active",
            "updated_at": updated_at,
        }
    ]

    payments = [
        {
            "payment_id": "PAY-0001",
            "contract_id": "CONTRACT-0001",
            "date": (today - timedelta(days=5)).isoformat(),
            "amount": 20_000_000.0,
            "currency": "VND",
            "method": "bank_transfer",
            "note": "Advance payment",
            "updated_at": updated_at,
        }
    ]

    invoices = []
    for i in range(1, 21):
        inv_date = period_first + timedelta(days=min(i, 27))
        due = inv_date + timedelta(days=10)
        status = "unpaid" if i % 3 == 0 else "paid"
        invoices.append(
            {
                "invoice_id": f"INV-{i:04d}",
                "invoice_no": f"AA/26E-{i:06d}",
                "tax_id": "0312345678",
                "date": inv_date.isoformat(),
                "amount": float(1000000 + i * 10000),
                "customer_id": f"CUST-{(i%5)+1:03d}",
                "due_date": due.isoformat(),
                "status": status,
                "email": f"cust{(i%5)+1}@example.local",
                "updated_at": updated_at,
            }
        )

    v_types = ["sell_invoice", "buy_invoice", "receipt", "payment", "other"]
    v_partners = ["ACME Supplies LLC", "Sunrise Services Co", "Nguyễn Văn A", None, "TechViet JSC"]
    v_descs = [
        "Mua văn phòng phẩm", "Thu tiền bán hàng", "Thanh toán nhà cung cấp",
        "Hoàn ứng công tác phí", "Tạm ứng lương", "Thu tiền dịch vụ tư vấn",
        "Phí vận chuyển", "Tiền thuê văn phòng T1", "Mua thiết bị máy tính",
        "Phí bảo trì phần mềm",
    ]
    vouchers = []
    for i in range(1, 31):
        v_date = period_first + timedelta(days=min(i, 27))
        amt = float(500000 + i * 5000)
        vouchers.append(
            {
                "voucher_id": f"VCH-{i:04d}",
                "voucher_no": f"PT-{i:06d}",
                "voucher_type": v_types[i % len(v_types)],
                "date": v_date.isoformat(),
                "amount": amt,
                "currency": "VND",
                "partner_name": v_partners[i % len(v_partners)],
                "description": v_descs[i % len(v_descs)],
                "has_attachment": 0 if i % 10 == 0 else 1,
                "updated_at": updated_at,
            }
        )

    # Bank transactions — some match vouchers, some don't (for reconciliation demo)
    bank_txs = []
    for i in range(1, 26):
        tx_date = period_first + timedelta(days=min(i, 27))
        if i <= 20:
            # First 20 roughly correspond to vouchers (with slight variations)
            base_amt = float(500000 + i * 5000)
            # Introduce amount mismatch for items 5, 15; date gap for 10
            if i == 5:
                amt = base_amt * 1.02  # 2% mismatch → anomaly
            elif i == 15:
                amt = base_amt + 50000  # large mismatch → anomaly
            else:
                amt = base_amt
            if i == 10:
                tx_date = tx_date + timedelta(days=5)  # date gap > 3 days → anomaly
        else:
            # Items 21-25: no matching voucher → unmatched_tx anomaly
            amt = float(2000000 + i * 10000)
        bank_txs.append(
            {
                "tx_id": f"BTX-{i:04d}",
                "tx_ref": f"VCB-REF-{i:06d}",
                "bank_account": "112-VCB-001",
                "date": tx_date.isoformat(),
                "amount": amt,
                "currency": "VND",
                "counterparty": v_partners[i % len(v_partners)] if i <= 20 else f"Unknown Corp {i}",
                "memo": f"CK tham chiếu VCH-{i:04d}" if i <= 20 else "Giao dịch không rõ nguồn gốc",
                "updated_at": updated_at,
            }
        )

    journals = []
    for i in range(1, 21):
        j_date = period_first + timedelta(days=min(i, 27))
        debit = float(100000 + i * 1000)
        credit = debit if i % 7 != 0 else debit + 123  # introduce imbalance for soft_checks
        journals.append(
            {
                "journal_id": f"JRN-{i:04d}",
                "journal_no": f"GL-{i:06d}",
                "date": j_date.isoformat(),
                "debit_total": debit,
                "credit_total": credit,
                "updated_at": updated_at,
            }
        )

    assets = [
        {
            "asset_id": "AST-0001",
            "asset_no": "TSCD-0001",
            "acquisition_date": (today - timedelta(days=400)).isoformat(),
            "cost": 25000000.0,
            "updated_at": updated_at,
        }
    ]

    close_calendar = []
    period = f"{today.year:04d}-{today.month:02d}"
    for i, name in enumerate(
        [
            "Reconcile bank",
            "Review AP invoices",
            "Review AR aging",
            "Depreciation check",
            "Tax review",
        ],
        start=1,
    ):
        close_calendar.append(
            {
                "id": new_uuid(),
                "period": period,
                "task_name": name,
                "owner_user_id": f"user-{i:03d}",
                "due_date": (period_first + timedelta(days=25 + (i % 3))).isoformat(),
                "updated_at": updated_at,
            }
        )

    _seed_from_json(
        conn,
        {
            "partners": partners,
            "contracts": contracts,
            "payments": payments,
            "invoices": invoices,
            "vouchers": vouchers,
            "journals": journals,
            "assets": assets,
            "close_calendar": close_calendar,
            "bank_transactions": bank_txs,
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
