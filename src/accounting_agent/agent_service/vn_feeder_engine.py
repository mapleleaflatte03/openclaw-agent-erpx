"""In-process VN Invoice Feeder engine — background thread within agent-service.

Loads the VN data catalog (3 Kaggle sources + GDT + synthetic), picks random
records, and creates ``voucher_ingest`` runs via internal API calls.  Writes
a ``feeder_status.json`` file that the ``/agent/v1/vn_feeder/status`` endpoint
reads.

Thread-safe: controlled via start/stop/inject_now signals.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import random
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("accounting_agent.vn_feeder_engine")

# ---------------------------------------------------------------------------
# Data catalog import (lazy to avoid import-time side effects)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"

_VN_FEEDER_CACHE = os.getenv("VN_FEEDER_CACHE_DIR", "/data/vn_feeder_cache")
_STATUS_FILE = os.path.join(_VN_FEEDER_CACHE, "feeder_status.json")
_CONTROL_FILE = os.path.join(_VN_FEEDER_CACHE, "feeder_control.json")

# Feeder thread globals
_thread: threading.Thread | None = None
_lock = threading.Lock()
_stop_event = threading.Event()
_inject_event = threading.Event()
_target_epm: int = 3  # default events per minute


def _ensure_dirs() -> None:
    os.makedirs(_VN_FEEDER_CACHE, exist_ok=True)


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
    try:
        Path(_STATUS_FILE).write_text(json.dumps(status, ensure_ascii=False, indent=2))
    except Exception:
        log.exception("Failed to write feeder status")


def _load_catalog():
    """Load VN data catalog records (lazy import from scripts/)."""
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    try:
        from vn_data_catalog import (
            VnInvoiceRecord,
            load_all_records,
            source_stats,
        )
        return load_all_records, source_stats, VnInvoiceRecord
    except ImportError:
        log.warning("Cannot import vn_data_catalog — feeder will use synthetic data only")
        return None, None, None


def _generate_synthetic_records(count: int = 500) -> list[dict]:
    """Generate synthetic VN invoice records when no Kaggle data is on disk."""
    _sources = ["MC_OCR_2021", "RECEIPT_OCR", "APPEN_VN_OCR"]
    _companies = [
        ("CÔNG TY TNHH ABC", "0100123456"),
        ("CÔNG TY CP XYZ", "0200987654"),
        ("DOANH NGHIỆP TƯ NHÂN DEF", "0300111222"),
        ("CTY TNHH SẢN XUẤT GHI", "0400333444"),
        ("CÔNG TY CP THƯƠNG MẠI JKL", "0500555666"),
        ("CTY TNHH DỊCH VỤ MNO", "0600777888"),
    ]
    _items_desc = [
        "Tiền thuê văn phòng", "Mua văn phòng phẩm", "Phí vận chuyển",
        "Mua nguyên vật liệu", "Chi phí bảo trì", "Tiền điện nước",
        "Phí tư vấn", "Chi phí quảng cáo", "Mua thiết bị", "Tiền lương thời vụ",
    ]
    records = []
    for i in range(count):
        src = random.choice(_sources)
        seller = random.choice(_companies)
        buyer = random.choice([c for c in _companies if c != seller])
        amount = random.randint(50, 5000) * 1000
        vat = round(amount * 0.1)
        day = random.randint(1, 28)
        month = random.randint(1, 12)
        records.append({
            "source_name": src,
            "external_id": f"SYN-{src[:4]}-{i:06d}",
            "issue_date": f"2026-{month:02d}-{day:02d}",
            "seller_name": seller[0],
            "seller_tax_code": seller[1],
            "buyer_name": buyer[0],
            "buyer_tax_code": buyer[1],
            "total_amount": amount + vat,
            "vat_amount": vat,
            "currency": "VND",
            "line_items": [{"description": random.choice(_items_desc), "amount": amount}],
            "regulation_hint": "TT133/2016/TT-BTC",
        })
    return records


def _feeder_loop() -> None:
    """Main feeder loop — runs in a background thread."""
    global _target_epm

    log.info("VN Feeder engine starting...")
    _ensure_dirs()

    # Try loading real catalog
    load_fn, stats_fn, RecordCls = _load_catalog()
    all_records_raw: list[dict] = []
    _stats: dict[str, int] = {}

    if load_fn is not None:
        try:
            recs = load_fn()
            if recs:
                all_records_raw = [r.to_dict() for r in recs]
                _stats = stats_fn(recs)
                log.info("Loaded %d records from catalog: %s", len(all_records_raw), _stats)
        except Exception:
            log.exception("Error loading VN data catalog")

    # If no real data, generate synthetic
    if not all_records_raw:
        all_records_raw = _generate_synthetic_records(500)
        for rec in all_records_raw:
            src = rec["source_name"]
            _stats[src] = _stats.get(src, 0) + 1
        log.info("Using %d synthetic records: %s", len(all_records_raw), _stats)

    # Sent tracking (in-memory — resets on process restart)
    sent_ids: set[str] = set()
    total_today = 0
    last_event_at = ""
    start_ts = time.monotonic()

    # API config for creating runs — inside the same container, use port 8000
    api_url = os.getenv("AGENT_API_URL", "http://127.0.0.1:8000")
    api_key = os.getenv("AGENT_API_KEY", "ak-7e8ed81281a387b88d210759f445863161d07461")
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    def _build_source_stats() -> list[dict]:
        result = []
        for src, total in _stats.items():
            sent_count = sum(1 for r in all_records_raw
                             if r["source_name"] == src and r["external_id"] in sent_ids)
            result.append({
                "source_name": src,
                "total": total,
                "sent_count": sent_count,
                "pct_consumed": round(sent_count / max(total, 1) * 100, 1),
            })
        return result

    def _create_voucher(rec_dict: dict, period: str) -> str | None:
        """Create a voucher_ingest run via internal API."""
        import requests
        body = {
            "run_type": "voucher_ingest",
            "trigger_type": "event",
            "payload": {
                "period": period,
                "source_tag": "vn_feeder",
                "source_name": rec_dict.get("source_name", ""),
                "invoice_data": {
                    "seller_name": rec_dict.get("seller_name", ""),
                    "buyer_name": rec_dict.get("buyer_name", ""),
                    "total_amount": rec_dict.get("total_amount", 0),
                    "vat_amount": rec_dict.get("vat_amount", 0),
                    "currency": rec_dict.get("currency", "VND"),
                },
            },
        }
        try:
            resp = requests.post(
                f"{api_url}/agent/v1/runs",
                headers=headers,
                json=body,
                timeout=15,
            )
            if resp.status_code in (200, 201):
                return resp.json().get("run_id", "")
            log.warning("Feeder run API returned %d: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            log.warning("Feeder run API error: %s", exc)
        return None

    log.info("VN Feeder engine running — %d records, target=%d epm", len(all_records_raw), _target_epm)

    while not _stop_event.is_set():
        # Check for inject_now signal
        injecting = _inject_event.is_set()
        if injecting:
            _inject_event.clear()

        # Determine batch size
        epm = _target_epm
        k = random.randint(max(1, epm - 1), epm + 1) if not injecting else max(3, epm)

        # Check for reset threshold (90% consumed)
        available = [r for r in all_records_raw if r["external_id"] not in sent_ids]
        if not available or (len(sent_ids) / max(len(all_records_raw), 1)) >= 0.90:
            sent_ids.clear()
            available = list(all_records_raw)
            log.info("Feeder reset — cycling back to beginning (%d records)", len(available))

        for i in range(min(k, len(available))):
            if _stop_event.is_set():
                break

            idx = random.randrange(len(available))
            rec = available.pop(idx)
            ext_id = rec["external_id"]
            period = rec.get("issue_date", "")[:7] or _dt.date.today().strftime("%Y-%m")

            run_id = _create_voucher(rec, period)
            if run_id:
                sent_ids.add(ext_id)
                total_today += 1
                last_event_at = _dt.datetime.utcnow().isoformat()
                log.info(
                    "Feeder event #%d: src=%s ext=%s period=%s",
                    total_today, rec["source_name"], ext_id[:15], period,
                )

            # Delay between events within the batch
            if i < k - 1 and not _stop_event.is_set():
                delay = random.uniform(3.0, max(5.0, 60.0 / max(epm, 1)))
                _stop_event.wait(timeout=delay)

        # Update status file
        elapsed_min = max((time.monotonic() - start_ts) / 60.0, 0.1)
        avg_epm = total_today / elapsed_min
        _write_status(True, total_today, last_event_at, avg_epm, _build_source_stats())

        # Sleep until next batch (aim for ~1 batch per minute)
        sleep_sec = max(5, 60 - k * 5)
        _stop_event.wait(timeout=sleep_sec)

    # Thread is stopping
    elapsed_min = max((time.monotonic() - start_ts) / 60.0, 0.1)
    avg_epm = total_today / elapsed_min if total_today else 0
    _write_status(False, total_today, last_event_at, avg_epm, _build_source_stats())
    log.info("VN Feeder engine stopped. Total events today: %d", total_today)


# ---------------------------------------------------------------------------
# Public API — called by the FastAPI endpoints
# ---------------------------------------------------------------------------

def start_feeder(target_epm: int | None = None) -> bool:
    """Start the feeder background thread. Returns True if started."""
    global _thread, _target_epm
    with _lock:
        if target_epm is not None:
            _target_epm = max(1, min(10, target_epm))
        if _thread is not None and _thread.is_alive():
            log.info("Feeder already running")
            return True
        _stop_event.clear()
        _thread = threading.Thread(target=_feeder_loop, daemon=True, name="vn-feeder")
        _thread.start()
        log.info("Feeder thread started (epm=%d)", _target_epm)
        return True


def stop_feeder() -> bool:
    """Stop the feeder background thread. Returns True if stopped."""
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            # Already stopped — update status file
            _write_status(False, 0, "", 0, [])
            return True
        _stop_event.set()
        _thread.join(timeout=10)
        _thread = None
        log.info("Feeder thread stopped")
        return True


def inject_now(target_epm: int | None = None) -> bool:
    """Trigger an immediate injection batch. Starts feeder if not running."""
    global _target_epm
    if target_epm is not None:
        _target_epm = max(1, min(10, target_epm))
    _inject_event.set()
    if _thread is None or not _thread.is_alive():
        return start_feeder(target_epm)
    return True


def is_running() -> bool:
    """Check if feeder thread is alive."""
    return _thread is not None and _thread.is_alive()


def set_target_events_per_min(target_epm: int) -> int:
    """Update feeder target events/minute without forcing restart."""
    global _target_epm
    _target_epm = max(1, min(10, int(target_epm)))
    return _target_epm


def get_target_events_per_min() -> int:
    """Expose current configured feeder events/minute."""
    return int(_target_epm)
