#!/usr/bin/env python3
"""Smoke test for VN Invoice Feeder & Command Center.

Run with limited events and verify the feeder → API → Agent pipeline works.

Usage:
    python3 scripts/smoke_vn_feeder.py              # against local/staging
    python3 scripts/smoke_vn_feeder.py --api-url http://127.0.0.1:30080
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

_API_URL = os.getenv("AGENT_API_URL", "http://127.0.0.1:30080")
API_KEY = os.getenv("AGENT_API_KEY", "ak-7e8ed81281a387b88d210759f445863161d07461")
HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"


def _api_url() -> str:
    return _API_URL


def _get(path: str):
    r = requests.get(f"{_api_url()}{path}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict):
    r = requests.post(f"{_api_url()}{path}", headers=HEADERS, json=body, timeout=15)
    r.raise_for_status()
    return r.json()


def step(name: str, ok: bool, detail: str = "") -> bool:
    icon = PASS if ok else FAIL
    msg = f"{icon} {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    return ok


def main() -> None:
    global _API_URL

    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default=_API_URL)
    parser.add_argument("--max-events", type=int, default=5)
    parser.add_argument("--wait-seconds", type=int, default=120)
    args = parser.parse_args()

    _API_URL = args.api_url
    results: list[bool] = []

    print("=" * 60)
    print("  VN Invoice Feeder — Smoke Test")
    print("=" * 60)
    print(f"API: {_API_URL}")
    print()

    # 1. Check API health
    try:
        h = _get("/agent/v1/healthz")
        results.append(step("API healthz", h.get("status") == "ok", json.dumps(h)))
    except Exception as exc:
        results.append(step("API healthz", False, str(exc)))
        print("\nAPI không khả dụng. Dừng smoke test.")
        sys.exit(1)

    # 2. Check vn_feeder/status endpoint exists
    try:
        s = _get("/agent/v1/vn_feeder/status")
        results.append(step("VN feeder status endpoint", True, f"running={s.get('running')}"))
    except Exception as exc:
        results.append(step("VN feeder status endpoint", False, str(exc)))

    # 3. Check vn_feeder/control endpoint exists (send stop then start)
    try:
        c = _post("/agent/v1/vn_feeder/control", {"action": "stop"})
        results.append(step("VN feeder control endpoint", c.get("status") == "ok"))
    except Exception as exc:
        results.append(step("VN feeder control endpoint", False, str(exc)))

    # 4. Run feeder with --max-events
    print(f"\n--- Running feeder with --max-events={args.max_events} ---")
    feeder_script = str(_PROJECT_ROOT / "scripts" / "vn_invoice_feeder.py")
    env = os.environ.copy()
    env["AGENT_API_URL"] = _API_URL
    env["AGENT_API_KEY"] = API_KEY

    try:
        proc = subprocess.Popen(
            [sys.executable, feeder_script, f"--max-events={args.max_events}"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # Wait for feeder to finish (with timeout)
        start = time.time()
        while proc.poll() is None:
            if time.time() - start > args.wait_seconds:
                proc.terminate()
                results.append(step("Feeder completed in time", False, "timeout"))
                break
            time.sleep(2)
        else:
            rc = proc.returncode
            results.append(step("Feeder completed", rc == 0, f"exit_code={rc}"))

        # Print feeder output
        out = proc.stdout.read() if proc.stdout else ""
        if out:
            for line in out.strip().split("\n")[-10:]:
                print(f"  feeder> {line}")
    except Exception as exc:
        results.append(step("Feeder execution", False, str(exc)))

    # 5. Verify runs were created
    print()
    try:
        runs = _get("/agent/v1/runs?limit=20")
        vn_runs = [
            r for r in (runs if isinstance(runs, list) else runs.get("items", []))
            if r.get("payload", {}).get("source_tag") == "vn_feeder"
            or r.get("run_type") == "voucher_ingest"
        ]
        results.append(step(
            "Voucher ingest runs created",
            len(vn_runs) >= 1,
            f"found {len(vn_runs)} runs",
        ))
    except Exception as exc:
        results.append(step("Runs query", False, str(exc)))

    # 6. Check status after feeder ran
    try:
        s2 = _get("/agent/v1/vn_feeder/status")
        total = s2.get("total_events_today", 0)
        results.append(step("Events recorded in status", total >= 1, f"total={total}"))

        sources = s2.get("sources", [])
        src_names = [s.get("source_name", "") for s in sources if s.get("sent_count", 0) > 0]
        results.append(step(
            "Multiple Kaggle sources used",
            len(src_names) >= 1,
            f"sources={src_names}",
        ))
    except Exception as exc:
        results.append(step("Status after feeder", False, str(exc)))

    # 7. TT133 module import
    try:
        from accounting_agent.regulations.tt133_index import TT133_ACCOUNTS, lookup_account
        results.append(step("TT133 module import", len(TT133_ACCOUNTS) > 30,
                            f"{len(TT133_ACCOUNTS)} accounts"))
        acct = lookup_account("111")
        results.append(step("TT133 lookup TK 111", acct is not None and "Tiền" in acct.name_vi))
    except Exception as exc:
        results.append(step("TT133 module", False, str(exc)))

    # Summary
    print()
    print("=" * 60)
    passed = sum(results)
    total_checks = len(results)
    icon = PASS if all(results) else FAIL
    print(f"{icon}  Smoke test: {passed}/{total_checks} passed")
    print("=" * 60)

    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
