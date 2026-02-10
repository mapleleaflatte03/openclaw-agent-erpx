#!/usr/bin/env python3
"""VN Invoice Feeder — simulates 1–5 invoice upload events per minute.

Reads VN invoice records from Kaggle datasets + synthetic data and
feeds them into the Agent pipeline via ``voucher_ingest`` runs.

Usage:
    python3 scripts/vn_invoice_feeder.py                    # run forever
    python3 scripts/vn_invoice_feeder.py --max-events 10    # limited run
    python3 scripts/vn_invoice_feeder.py --inject-once      # single batch

Environment:
    AGENT_API_URL       — Agent API base (default http://127.0.0.1:30080)
    AGENT_API_KEY       — API key
    VN_FEEDER_MIN_EPM   — min events/minute (default 1)
    VN_FEEDER_MAX_EPM   — max events/minute (default 5)
    VN_DATA_ROOT        — data root (default /data)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import random
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import requests

# Add project root to path for sibling imports
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

from vn_data_catalog import (  # noqa: E402
    VN_FEEDER_CACHE_DIR,
    VnInvoiceRecord,
    load_all_records,
    source_stats,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = _PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

_today = _dt.date.today().isoformat()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / f"vn_feeder_{_today}.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("vn_feeder")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_URL = os.getenv("AGENT_API_URL", "http://127.0.0.1:30080")
API_KEY = os.getenv("AGENT_API_KEY", "ak-7e8ed81281a387b88d210759f445863161d07461")
HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

MIN_EPM = int(os.getenv("VN_FEEDER_MIN_EPM", "1"))
MAX_EPM = int(os.getenv("VN_FEEDER_MAX_EPM", "5"))

RESET_THRESHOLD = float(os.getenv("VN_FEEDER_RESET_THRESHOLD", "0.90"))
MAX_CONSECUTIVE_ERRORS = 10
BACKOFF_SECONDS = 30

# ---------------------------------------------------------------------------
# State DB (SQLite) — track which records have been sent
# ---------------------------------------------------------------------------

STATE_DB = os.path.join(VN_FEEDER_CACHE_DIR, "feeder_state.db")
CONTROL_FILE = os.path.join(VN_FEEDER_CACHE_DIR, "feeder_control.json")
STATUS_FILE = os.path.join(VN_FEEDER_CACHE_DIR, "feeder_status.json")


def _ensure_dirs() -> None:
    os.makedirs(VN_FEEDER_CACHE_DIR, exist_ok=True)


def _get_db() -> sqlite3.Connection:
    _ensure_dirs()
    conn = sqlite3.connect(STATE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent_records (
            external_id TEXT PRIMARY KEY,
            source_name TEXT,
            sent_at     TEXT,
            run_id      TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feeder_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT,
            source_name TEXT,
            external_id TEXT,
            run_id      TEXT,
            status      TEXT,
            period      TEXT
        )
    """)
    conn.commit()
    return conn


def _mark_sent(conn: sqlite3.Connection, rec: VnInvoiceRecord, run_id: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sent_records (external_id, source_name, sent_at, run_id) "
        "VALUES (?, ?, ?, ?)",
        (rec.external_id, rec.source_name, _dt.datetime.utcnow().isoformat(), run_id),
    )
    conn.commit()


def _get_sent_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT external_id FROM sent_records").fetchall()
    return {r[0] for r in rows}


def _record_event(
    conn: sqlite3.Connection,
    rec: VnInvoiceRecord,
    run_id: str,
    status: str,
    period: str,
) -> None:
    conn.execute(
        "INSERT INTO feeder_events (timestamp, source_name, external_id, run_id, status, period) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (_dt.datetime.utcnow().isoformat(), rec.source_name, rec.external_id, run_id, status, period),
    )
    conn.commit()


def _reset_state(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM sent_records")
    conn.commit()
    log.info("Reset VN feeder state — consumed %.0f%% records, restarting from beginning",
             RESET_THRESHOLD * 100)


# ---------------------------------------------------------------------------
# Control file (read by feeder, written by API)
# ---------------------------------------------------------------------------

def _read_control() -> dict:
    if os.path.isfile(CONTROL_FILE):
        try:
            return json.loads(Path(CONTROL_FILE).read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"running": True, "target_events_per_min": None}


def _is_running() -> bool:
    return _read_control().get("running", True)


def _get_target_epm() -> tuple[int, int]:
    ctrl = _read_control()
    target = ctrl.get("target_events_per_min")
    if target and isinstance(target, (int, float)):
        t = max(1, min(10, int(target)))
        return t, t
    return MIN_EPM, MAX_EPM


def _write_status(
    running: bool,
    total_today: int,
    last_event_at: str,
    avg_epm: float,
    sources: list[dict],
) -> None:
    _ensure_dirs()
    status = {
        "running": running,
        "total_events_today": total_today,
        "last_event_at": last_event_at,
        "avg_events_per_min": round(avg_epm, 2),
        "sources": sources,
        "updated_at": _dt.datetime.utcnow().isoformat(),
    }
    Path(STATUS_FILE).write_text(json.dumps(status, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# API interaction
# ---------------------------------------------------------------------------

def _create_run(payload_file: str, period: str) -> dict | None:
    body = {
        "run_type": "voucher_ingest",
        "trigger_type": "manual",
        "payload": {
            "period": period,
            "source_path": payload_file,
            "source_tag": "vn_feeder",
        },
    }
    try:
        resp = requests.post(
            f"{API_URL}/agent/v1/runs",
            headers=HEADERS,
            json=body,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            return resp.json()
        log.warning("API returned %d: %s", resp.status_code, resp.text[:200])
    except requests.RequestException as exc:
        log.error("API error: %s", exc)
    return None


def _create_journal_run(period: str) -> dict | None:
    body = {
        "run_type": "journal_suggestion",
        "trigger_type": "manual",
        "payload": {
            "period": period,
            "source": "vn_feeder",
        },
    }
    try:
        resp = requests.post(
            f"{API_URL}/agent/v1/runs",
            headers=HEADERS,
            json=body,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            return resp.json()
    except requests.RequestException:
        pass
    return None


# ---------------------------------------------------------------------------
# Main feeder loop
# ---------------------------------------------------------------------------

def run_feeder(
    max_events: int = 0,
    inject_once: bool = False,
) -> None:
    log.info("VN Invoice Feeder starting — loading data catalog…")
    all_records = load_all_records()
    if not all_records:
        log.error("No VN data records found. Check /data/kaggle/ directories.")
        return

    stats = source_stats(all_records)
    log.info("Loaded %d records: %s", len(all_records), stats)

    conn = _get_db()
    total_today = 0
    consecutive_errors = 0
    start_ts = time.monotonic()
    last_event_at = ""

    # Build source stats for status
    def _build_source_stats() -> list[dict]:
        sent = _get_sent_ids(conn)
        result = []
        for src, total in stats.items():
            sent_count = sum(
                1 for r in all_records
                if r.source_name == src and r.external_id in sent
            )
            result.append({
                "source_name": src,
                "total": total,
                "sent_count": sent_count,
                "pct_consumed": round(sent_count / max(total, 1) * 100, 1),
            })
        return result

    while True:
        # Check control
        if not _is_running() and not inject_once:
            _write_status(False, total_today, last_event_at, 0, _build_source_stats())
            log.info("Feeder paused by control. Sleeping 5s…")
            time.sleep(5)
            continue

        # Check max events
        if max_events > 0 and total_today >= max_events:
            log.info("Reached max-events=%d, stopping.", max_events)
            _write_status(False, total_today, last_event_at, 0, _build_source_stats())
            break

        # Check reset threshold
        sent_ids = _get_sent_ids(conn)
        available = [r for r in all_records if r.external_id not in sent_ids]
        if len(available) == 0 or (len(sent_ids) / len(all_records)) >= RESET_THRESHOLD:
            _reset_state(conn)
            sent_ids = set()
            available = list(all_records)

        # Determine how many events this minute
        epm_min, epm_max = _get_target_epm()
        k = random.randint(epm_min, epm_max)

        for i in range(k):
            if max_events > 0 and total_today >= max_events:
                break
            if not _is_running() and not inject_once:
                break

            # Pick random record
            if not available:
                break
            idx = random.randrange(len(available))
            rec = available.pop(idx)

            # Write temp batch file
            period = rec.issue_date[:7] if rec.issue_date else _dt.date.today().strftime("%Y-%m")
            batch = [rec.to_dict()]
            tmp_path = os.path.join(
                tempfile.gettempdir(),
                f"vn_feeder_batch_{_dt.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{rec.external_id}.json",
            )
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(batch, f, ensure_ascii=False)

            # Send to API
            result = _create_run(tmp_path, period)
            if result:
                run_id = result.get("run_id", "")
                _mark_sent(conn, rec, run_id)
                _record_event(conn, rec, run_id, "OK", period)
                total_today += 1
                consecutive_errors = 0
                last_event_at = _dt.datetime.utcnow().isoformat()
                log.info(
                    "Event #%d: src=%s ext_id=%s period=%s run_id=%s",
                    total_today, rec.source_name, rec.external_id[:12], period, run_id[:12],
                )

                # Optional: trigger journal suggestion every 3rd event
                if total_today % 3 == 0:
                    jr = _create_journal_run(period)
                    if jr:
                        log.info("Journal suggestion run: %s", jr.get("run_id", "")[:12])
            else:
                consecutive_errors += 1
                _record_event(conn, rec, "", "ERROR", period)
                log.warning("Failed to create run, consecutive errors: %d", consecutive_errors)
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    log.error("Too many errors, backing off %ds", BACKOFF_SECONDS)
                    time.sleep(BACKOFF_SECONDS)
                    consecutive_errors = 0

            # Sleep between events within the minute
            if i < k - 1:
                delay = random.uniform(60.0 / max(epm_max, 1), 60.0 / max(epm_min, 1))
                delay = min(delay, 30)  # cap at 30s
                time.sleep(delay)

        # Update status
        elapsed_min = max((time.monotonic() - start_ts) / 60.0, 0.1)
        avg_epm = total_today / elapsed_min
        _write_status(True, total_today, last_event_at, avg_epm, _build_source_stats())

        if inject_once:
            log.info("Inject-once complete. %d events sent.", total_today)
            break

        # Sleep remainder of the minute
        remaining = max(5, 60 - k * (60.0 / max(epm_max, 1)))
        time.sleep(remaining)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="VN Invoice Data Feeder")
    parser.add_argument("--max-events", type=int, default=0,
                        help="Stop after N events (0=unlimited)")
    parser.add_argument("--inject-once", action="store_true",
                        help="Inject 1 batch then exit")
    args = parser.parse_args()
    run_feeder(max_events=args.max_events, inject_once=args.inject_once)


if __name__ == "__main__":
    main()
