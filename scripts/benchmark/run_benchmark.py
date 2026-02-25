#!/usr/bin/env python3
"""Benchmark runner — executes workflows against benchmark cases and records results.

Supports two targets:
  - docker: uses docker compose locally (default)
  - staging: uses a remote staging URL (set BENCHMARK_TARGET_URL)

Usage:
  python run_benchmark.py --cases 50 --target docker --out reports/benchmark/latest.json
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

try:
    import httpx
except ImportError:
    print("WARN: httpx not installed, using urllib", file=sys.stderr)
    httpx = None  # type: ignore

WORKFLOWS = [
    "attachment",
    "tax_export",
    "working_papers",
    "soft_checks",
    "ar_dunning",
    "close_checklist",
    "evidence_pack",
    "kb_index",
    "contract_obligation",
]

DEFAULT_BASE_URL = "http://localhost:8000"
PERIOD_REQUIRED_RUN_TYPES = {
    "voucher_ingest",
    "soft_checks",
    "journal_suggestion",
    "cashflow_forecast",
    "tax_export",
    "bank_reconcile",
}


def _get_base_url(target: str) -> str:
    if target == "staging":
        url = os.environ.get("BENCHMARK_TARGET_URL", "")
        if not url:
            print("ERROR: BENCHMARK_TARGET_URL required for staging target", file=sys.stderr)
            sys.exit(1)
        return url.rstrip("/")
    return DEFAULT_BASE_URL


def _http_post(base_url: str, path: str, body: dict, timeout: float = 30.0) -> dict:
    """POST JSON and return response body."""
    import urllib.error
    import urllib.request

    url = f"{base_url}{path}"
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}

    if httpx:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=body)
            r.raise_for_status()
            return r.json()

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": str(e), "status": e.code, "body": e.read().decode()}


def _http_get(base_url: str, path: str, timeout: float = 10.0) -> dict:
    import urllib.error
    import urllib.request

    url = f"{base_url}{path}"
    if httpx:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.json()

    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": str(e), "status": e.code}


def _wait_for_service(base_url: str, max_wait: int = 60) -> bool:
    """Wait for the agent-service to become ready."""
    import urllib.error
    import urllib.request

    for _ in range(max_wait):
        try:
            url = f"{base_url}/healthz"
            if httpx:
                with httpx.Client(timeout=3) as client:
                    r = client.get(url)
                    if r.status_code == 200:
                        return True
            else:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=3):
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _run_case(base_url: str, case_dir: Path, case_id: str) -> dict:
    """Run contract_obligation workflow for a single case and return results."""
    truth_path = case_dir / "truth.json"
    if not truth_path.exists():
        return {"case_id": case_id, "status": "skip", "reason": "no truth.json"}

    truth = json.loads(truth_path.read_text())
    start_ts = time.time()

    # Create a run for contract_obligation
    run_payload = {
        "run_type": "contract_obligation",
        "trigger_type": "manual",
        "requested_by": "benchmark-runner",
        "payload": {
            "contract_files": [],
            "email_files": [],
            "contract_files_inline": [],
            "email_files_inline": [],
            "partner_name": f"Benchmark Partner {case_id}",
            "partner_tax_id": f"BM{case_id[-4:]}",
            "contract_code": f"BM-{case_id}",
            "case_key": f"benchmark-{case_id}",
        },
    }

    def _to_inline(path: Path) -> dict[str, str]:
        return {
            "filename": path.name,
            "content_b64": base64.b64encode(path.read_bytes()).decode("ascii"),
        }

    # Check for source files
    sources_dir = case_dir / "sources"
    if sources_dir.exists():
        for f in sources_dir.iterdir():
            if f.suffix.lower() == ".pdf":
                run_payload["payload"]["contract_files_inline"].append(_to_inline(f))
            elif f.suffix.lower() == ".eml":
                run_payload["payload"]["email_files_inline"].append(_to_inline(f))

    try:
        resp = _http_post(base_url, "/agent/v1/runs", run_payload)
    except Exception as e:
        return {
            "case_id": case_id,
            "status": "error",
            "error": str(e),
            "duration_s": time.time() - start_ts,
        }

    run_id = resp.get("run_id") or resp.get("id", "unknown")
    status = resp.get("status", "unknown")

    # Poll for completion (max 120s)
    for _ in range(120):
        try:
            run_resp = _http_get(base_url, f"/agent/v1/runs/{run_id}")
            status = run_resp.get("status", "unknown")
            if status in ("success", "completed", "failed", "error"):
                break
        except Exception:
            pass
        time.sleep(1)

    duration = time.time() - start_ts

    # Collect obligations from the API
    detected_obligations: list[dict] = []
    try:
        contract_resp = _http_get(base_url, "/agent/v1/contract/cases?limit=200")
        items = contract_resp.get("items", []) if isinstance(contract_resp, dict) else []
        benchmark_key = f"benchmark-{case_id}"
        case_match = next((x for x in items if str(x.get("case_key") or "") == benchmark_key), None)
        if case_match:
            agent_case_id = str(case_match.get("case_id") or "").strip()
            if agent_case_id:
                obls_resp = _http_get(base_url, f"/agent/v1/contract/cases/{agent_case_id}/obligations")
                if isinstance(obls_resp, dict):
                    detected_obligations = list(obls_resp.get("items", []) or [])
    except Exception:
        pass

    return {
        "case_id": case_id,
        "run_id": run_id,
        "status": status,
        "duration_s": round(duration, 2),
        "truth_obligations": len(truth.get("obligations", [])),
        "detected_obligations": len(detected_obligations),
        "expected_risk": truth.get("expected_risk"),
        "expected_gating_tier": truth.get("expected_gating_tier"),
        "expected_approvals": truth.get("expected_approvals_required"),
    }


def _run_workflow_basic(base_url: str, workflow: str) -> dict:
    """Run a basic (non-contract) workflow and check it doesn't error."""
    start_ts = time.time()
    if workflow == "attachment":
        # Attachment workflow requires payload.file_uri that is accessible from
        # the worker runtime, so skip it in generic benchmark mode.
        return {
            "workflow": workflow,
            "status": "skip",
            "reason": "requires_payload_file_uri",
            "duration_s": round(time.time() - start_ts, 2),
        }

    payload: dict[str, object] = {}
    if workflow in PERIOD_REQUIRED_RUN_TYPES:
        payload["period"] = datetime.now(tz=timezone.utc).strftime("%Y-%m")
    run_payload = {
        "run_type": workflow,
        "trigger_type": "manual",
        "requested_by": "benchmark-runner",
        "payload": payload,
    }

    try:
        resp = _http_post(base_url, "/agent/v1/runs", run_payload)
        run_id = resp.get("run_id") or resp.get("id", "unknown")
        status = resp.get("status", "unknown")

        for _ in range(60):
            try:
                run_resp = _http_get(base_url, f"/agent/v1/runs/{run_id}")
                status = run_resp.get("status", "unknown")
                if status in ("success", "completed", "failed", "error"):
                    break
            except Exception:
                pass
            time.sleep(1)

        return {
            "workflow": workflow,
            "run_id": run_id,
            "status": status,
            "duration_s": round(time.time() - start_ts, 2),
        }
    except Exception as e:
        return {
            "workflow": workflow,
            "status": "error",
            "error": str(e),
            "duration_s": round(time.time() - start_ts, 2),
        }


def run_benchmark(
    cases_dir: Path,
    max_cases: int,
    base_url: str,
    output_path: Path,
) -> dict:
    """Run the full benchmark suite."""
    results: dict = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target": base_url,
        "max_cases": max_cases,
        "workflow_results": [],
        "case_results": [],
    }

    # 1. Run basic workflows (non-contract)
    print("=== Running basic workflow checks ===")
    for wf in WORKFLOWS:
        if wf == "contract_obligation":
            continue
        print(f"  {wf}...", end=" ", flush=True)
        r = _run_workflow_basic(base_url, wf)
        results["workflow_results"].append(r)
        print(r["status"])

    # 2. Run contract_obligation for each benchmark case
    print("\n=== Running contract_obligation on benchmark cases ===")
    case_dirs = sorted([
        d for d in cases_dir.iterdir()
        if d.is_dir() and d.name.startswith("case_")
    ])[:max_cases]

    for case_dir in case_dirs:
        case_id = case_dir.name
        print(f"  {case_id}...", end=" ", flush=True)
        r = _run_case(base_url, case_dir, case_id)
        results["case_results"].append(r)
        print(r["status"])

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nResults written to {output_path}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run benchmark against Accounting Agent Layer ERPX")
    parser.add_argument("--cases", type=int, default=50)
    parser.add_argument("--target", choices=["docker", "staging"], default="docker")
    parser.add_argument("--out", type=str, default="reports/benchmark/latest.json")
    parser.add_argument("--cases-dir", type=str, default="data/benchmark/cases")
    parser.add_argument("--no-wait", action="store_true", help="Skip waiting for service")
    args = parser.parse_args()

    base_url = _get_base_url(args.target)
    cases_dir = Path(args.cases_dir)

    if not cases_dir.exists() or not list(cases_dir.iterdir()):
        print(f"ERROR: No cases found in {cases_dir}. Run fetch_or_generate_dataset.sh first.", file=sys.stderr)
        sys.exit(1)

    if not args.no_wait:
        print(f"Waiting for service at {base_url}...", end=" ", flush=True)
        if _wait_for_service(base_url):
            print("OK")
        else:
            print("TIMEOUT — service not reachable. Use --no-wait to skip.")
            sys.exit(1)

    run_benchmark(cases_dir, args.cases, base_url, Path(args.out))


if __name__ == "__main__":
    main()
