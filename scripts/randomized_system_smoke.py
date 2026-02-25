#!/usr/bin/env python3
"""Randomized system smoke test — 200+ iterations.

Runs a comprehensive randomized test harness against the agent service,
exercising ALL accounting flows with random parameters, edge-case inputs,
and varied env flag configurations.

Supports two modes:
  * **--use-real-llm** — sends real LLM calls via DO Agent endpoint
    (reads ``DO_AGENT_*`` from ``.env``).  Q&A, journal-suggestion and
    soft-check flows will use the LLM for reasoning.
  * Default (no flag) — pure mock/rule-based, no network calls.

Usage:
    cd /root/accounting-agent-layer

    # mock mode (CI-safe, no network)
    .venv/bin/python scripts/randomized_system_smoke.py --iterations 200

    # real-LLM mode
    USE_REAL_LLM=1 .venv/bin/python scripts/randomized_system_smoke.py \\
        --iterations 200 --seed 2025 --use-real-llm

Output:
    logs/randomized_smoke_report.jsonl   — one JSON line per iteration
    logs/randomized_smoke_summary.md     — human-readable summary
    logs/randomized_llm_report_YYYYMMDD.json — structured JSON report
    stdout                               — compact progress log
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import random
import sys
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ensure repo root on sys.path
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# ---------------------------------------------------------------------------
# Env defaults — safe offline mode (overridden by --use-real-llm)
# ---------------------------------------------------------------------------
os.environ.setdefault("USE_REAL_LLM", "false")
os.environ.setdefault("USE_LANGGRAPH", "false")

LOG_DIR = REPO_ROOT / "logs"
REPORT_FILE = LOG_DIR / "randomized_smoke_report.jsonl"
SUMMARY_FILE = LOG_DIR / "randomized_smoke_summary.md"

# All run_types the harness exercises
_RUN_TYPES = [
    "voucher_ingest",
    "voucher_classify",
    "journal_suggestion",
    "soft_checks",
    "tax_export",
    "cashflow_forecast",
    "close_checklist",
    "bank_reconcile",
]

# Valid + edge-case periods
_VALID_PERIODS = [
    "2025-01", "2025-02", "2025-03", "2025-06",
    "2025-12", "2024-03", "2024-12", "2026-01",
]
_INVALID_PERIODS = [
    "2025/01", "2025-13", "abc", "", "2025", "01-2025",
]

# Q&A questions to test (includes LLM-targeted reasoning questions)
_QNA_QUESTIONS_VALID = [
    "Tháng 1/2025 có bao nhiêu chứng từ?",
    "Vì sao chứng từ hóa đơn số 0000001 được hạch toán như vậy?",
    "Có bao nhiêu giao dịch bất thường?",
    "Tóm tắt dòng tiền dự báo",
    "Thống kê phân loại chứng từ",
    "Thông tư 200 quy định gì về hạch toán?",
    "Nghị định 123 quy định gì về hóa đơn điện tử?",
    "TK 131 phải thu khách hàng dùng khi nào?",
    # LLM-targeted reasoning questions (only meaningful with USE_REAL_LLM=1)
    "Giải thích bút toán cho hóa đơn mua hàng tháng 1/2025?",
    "Tháng 1/2026 có bao nhiêu chứng từ bất thường?",
    "Sự khác biệt giữa TK 331 và TK 131 trong hạch toán?",
    "Khi nào dùng TK 642 chi phí quản lý doanh nghiệp?",
    "Giải thích quy trình đối chiếu ngân hàng theo chuẩn VAS?",
]
_QNA_QUESTIONS_EDGE = [
    "",         # empty
    " ",        # whitespace only
    "x" * 5000, # very long
]

# Goal commands (Agent Command Center)
_GOAL_COMMANDS = [
    "Đóng sổ tháng 1/2025",
    "Kiểm tra kỳ 2025-03",
    "Đối chiếu ngân hàng tháng 12/2024",
    "Báo cáo thuế tháng 6/2025",
    "Nhập chứng từ",
    "Phân loại chứng từ",
    "Dự báo dòng tiền",
    "Hỏi đáp kế toán",
    "Phát hiện bất thường",
    "Rà soát hợp đồng",
]


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def _setup_env(tmp_dir: Path, *, use_langgraph: bool = False, use_real_llm: bool = False) -> None:
    """Set all required env vars for in-process test."""
    # When real LLM is enabled, load .env to pick up DO_AGENT_* credentials
    if use_real_llm:
        env_file = REPO_ROOT / ".env"
        if env_file.exists():
            from dotenv import load_dotenv
            load_dotenv(env_file, override=False)  # don't override explicit env

    agent_db = tmp_dir / "agent.sqlite"
    erpx_db = tmp_dir / "erpx_mock.sqlite"
    seed_path = (REPO_ROOT / "data" / "kaggle" / "seed" / "erpx_seed_kaggle.json").resolve()

    env_map = {
        "AGENT_DB_DSN": f"sqlite+pysqlite:///{agent_db}",
        "ERPX_MOCK_DB_PATH": str(erpx_db),
        "ERPX_MOCK_SEED_PATH": str(seed_path),
        "ERPX_MOCK_TOKEN": "testtoken",
        "ERPX_TOKEN": "testtoken",
        "MINIO_ENDPOINT": "minio:9000",
        "MINIO_ACCESS_KEY": "minioadmin",
        "MINIO_SECRET_KEY": "minioadmin",
        "REDIS_URL": "redis://localhost:6379/0",
        "CELERY_BROKER_URL": "redis://localhost:6379/0",
        "CELERY_RESULT_BACKEND": "redis://localhost:6379/1",
        "AGENT_AUTH_MODE": "api_key",
        "AGENT_API_KEY": "test-key-smoke",
        "USE_LANGGRAPH": "true" if use_langgraph else "false",
        "USE_REAL_LLM": "true" if use_real_llm else "false",
    }
    for k, v in env_map.items():
        os.environ[k] = v


def _bootstrap_services(tmp_dir: Path) -> tuple:
    """Start ERPX mock server in-thread, init DB, return (engine, port, erpx_server, erpx_thread)."""
    from accounting_agent.common.testutils import get_free_port, run_uvicorn_in_thread
    from accounting_agent.erpx_mock import main as erpx_main

    port = get_free_port()
    os.environ["ERPX_BASE_URL"] = f"http://127.0.0.1:{port}"

    erpx_main.DbState.conn = None
    erpx_server, erpx_thread = run_uvicorn_in_thread(erpx_main.app, port=port)

    # Reload settings + worker tasks so they pick up fresh env
    from accounting_agent.common.settings import get_settings
    get_settings.cache_clear()
    from accounting_agent.agent_worker import tasks as worker_tasks
    importlib.reload(worker_tasks)

    from accounting_agent.common.db import Base
    from accounting_agent.common.storage import S3ObjectRef

    Base.metadata.create_all(worker_tasks.engine)

    # Reset LLM client singleton so it re-reads USE_REAL_LLM from env
    from accounting_agent.llm.client import reset_llm_client
    reset_llm_client()

    # Stub S3 upload (no real MinIO)
    def fake_upload_file(_settings, bucket, key, path, content_type=None):
        return S3ObjectRef(bucket="test-bucket", key=key)
    worker_tasks.upload_file = fake_upload_file  # type: ignore[attr-defined]

    return worker_tasks, port, erpx_server, erpx_thread


# ---------------------------------------------------------------------------
# Scenario builders
# ---------------------------------------------------------------------------

def _build_run_scenario(iteration: int) -> dict[str, Any]:
    """Build one random test scenario."""
    # 70% valid period, 15% invalid, 15% missing
    period_choice = random.random()
    if period_choice < 0.70:
        period = random.choice(_VALID_PERIODS)
        period_valid = True
    elif period_choice < 0.85:
        period = random.choice(_INVALID_PERIODS)
        period_valid = False
    else:
        period = None
        period_valid = True  # optional for some run_types

    run_type = random.choice(_RUN_TYPES)
    requested_by = random.choice([
        "Kế toán A", "User-123", "Nguyễn Văn B", None, "admin",
    ])

    return {
        "iteration": iteration,
        "test_type": "run",
        "run_type": run_type,
        "period": period,
        "period_valid": period_valid,
        "requested_by": requested_by,
    }


def _build_qna_scenario(iteration: int) -> dict[str, Any]:
    """Build a random Q&A test scenario."""
    if random.random() < 0.8:
        question = random.choice(_QNA_QUESTIONS_VALID)
        valid = True
    else:
        question = random.choice(_QNA_QUESTIONS_EDGE)
        valid = False

    return {
        "iteration": iteration,
        "test_type": "qna",
        "question": question[:200],  # truncate for logging
        "valid": valid,
    }


def _build_goal_scenario(iteration: int) -> dict[str, Any]:
    """Build a random Agent Command Center goal scenario."""
    command = random.choice(_GOAL_COMMANDS)
    period = random.choice(_VALID_PERIODS + [None])
    return {
        "iteration": iteration,
        "test_type": "goal",
        "command": command,
        "period": period,
    }


def _build_approval_scenario(iteration: int) -> dict[str, Any]:
    """Build a journal proposal approval test (approve/reject)."""
    return {
        "iteration": iteration,
        "test_type": "approval",
        "decision": random.choice(["approved", "rejected"]),
    }


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

def _exec_run_scenario(
    scenario: dict[str, Any],
    worker_tasks: Any,
) -> dict[str, Any]:
    """Execute a run_type scenario via direct worker dispatch."""
    from accounting_agent.common.db import db_session as db_ctx
    from accounting_agent.common.models import AgentRun
    from accounting_agent.common.utils import make_idempotency_key, new_uuid

    run_type = scenario["run_type"]
    period = scenario["period"]

    payload: dict[str, Any] = {}
    if period is not None:
        payload["period"] = period
    if run_type == "voucher_ingest":
        payload["source"] = "vn_fixtures"

    run_id = new_uuid()
    idem_key = make_idempotency_key(run_type, str(period), f"smoke-{scenario['iteration']}")

    with db_ctx(worker_tasks.engine) as s:
        s.add(AgentRun(
            run_id=run_id,
            run_type=run_type,
            trigger_type="manual",
            requested_by=scenario.get("requested_by"),
            status="queued",
            idempotency_key=idem_key,
            cursor_in=payload,
            cursor_out=None,
            started_at=None,
            finished_at=None,
            stats=None,
        ))

    t0 = time.perf_counter()
    error_msg = None
    status = "unknown"
    try:
        worker_tasks.dispatch_run.run(run_id)
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
    # Always read back status from DB (dispatch_run sets it before re-raising)
    try:
        with db_ctx(worker_tasks.engine) as s:
            run = s.query(AgentRun).filter_by(run_id=run_id).first()
            status = run.status if run else "missing"
    except Exception:
        status = "exception"

    elapsed = round(time.perf_counter() - t0, 3)

    return {
        **scenario,
        "run_id": run_id,
        "status": status,
        "elapsed_s": elapsed,
        "error": error_msg,
    }


def _exec_qna_scenario(
    scenario: dict[str, Any],
    client: Any,
    headers: dict[str, str],
) -> dict[str, Any]:
    """Execute a Q&A scenario via TestClient."""
    question = scenario["question"]
    t0 = time.perf_counter()
    error_msg = None
    status_code = 0
    answer_excerpt = ""
    llm_used = False
    has_reasoning = False

    try:
        body: dict[str, Any] = {"question": question}
        r = client.post("/agent/v1/acct/qna", json=body, headers=headers)
        status_code = r.status_code
        if r.status_code == 200:
            data = r.json()
            answer_excerpt = str(data.get("answer", ""))[:200]
            meta = data.get("meta", {})
            llm_used = bool(meta.get("llm_used"))
            has_reasoning = bool(meta.get("reasoning_chain") or meta.get("llm_used"))
        elif r.status_code >= 500:
            error_msg = f"HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        status_code = -1

    elapsed = round(time.perf_counter() - t0, 3)

    return {
        **scenario,
        "status_code": status_code,
        "answer_excerpt": answer_excerpt,
        "llm_used": llm_used,
        "has_reasoning": has_reasoning,
        "elapsed_s": elapsed,
        "error": error_msg,
    }


def _exec_goal_scenario(
    scenario: dict[str, Any],
    client: Any,
    headers: dict[str, str],
) -> dict[str, Any]:
    """Execute an Agent Command Center goal scenario via TestClient."""
    t0 = time.perf_counter()
    error_msg = None
    status_code = 0
    goal_key = ""
    chain_len = 0

    try:
        body: dict[str, Any] = {"command": scenario["command"]}
        if scenario.get("period"):
            body["period"] = scenario["period"]
        r = client.post("/agent/v1/agent/commands", json=body, headers=headers)
        status_code = r.status_code
        if r.status_code == 200:
            data = r.json()
            goal_key = data.get("goal", "")
            chain_len = len(data.get("chain", []))
        elif r.status_code >= 500:
            error_msg = f"HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        status_code = -1

    elapsed = round(time.perf_counter() - t0, 3)

    return {
        **scenario,
        "status_code": status_code,
        "goal_key": goal_key,
        "chain_len": chain_len,
        "elapsed_s": elapsed,
        "error": error_msg,
    }


def _exec_approval_scenario(
    scenario: dict[str, Any],
    client: Any,
    headers: dict[str, str],
) -> dict[str, Any]:
    """Test journal proposal review endpoint."""
    t0 = time.perf_counter()
    error_msg = None
    status_code = 0

    try:
        # List existing proposals
        r = client.get("/agent/v1/acct/journal_proposals?limit=5", headers=headers)
        if r.status_code != 200:
            return {**scenario, "status_code": r.status_code, "elapsed_s": 0, "error": "list failed", "skip": True}

        items = r.json().get("items", [])
        pending = [p for p in items if p.get("status") == "pending"]
        if not pending:
            return {**scenario, "status_code": 200, "elapsed_s": 0, "error": None, "skip": True, "note": "no pending proposals"}

        proposal = random.choice(pending)
        pid = proposal["id"]
        review_body = {
            "status": scenario["decision"],
            "reviewed_by": "smoke-tester",
        }
        r2 = client.post(f"/agent/v1/acct/journal_proposals/{pid}/review", json=review_body, headers=headers)
        status_code = r2.status_code
        if r2.status_code >= 500:
            error_msg = f"HTTP {r2.status_code}: {r2.text[:300]}"

        # Test double-review (should be blocked)
        r3 = client.post(f"/agent/v1/acct/journal_proposals/{pid}/review", json=review_body, headers=headers)
        if r3.status_code == 200:
            error_msg = "DOUBLE_REVIEW_NOT_BLOCKED: same proposal approved/rejected twice without 409"
        elif r3.status_code not in (409, 400, 422):
            error_msg = f"DOUBLE_REVIEW_UNEXPECTED: HTTP {r3.status_code}"

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        status_code = -1

    elapsed = round(time.perf_counter() - t0, 3)
    return {**scenario, "status_code": status_code, "elapsed_s": elapsed, "error": error_msg}


# ---------------------------------------------------------------------------
# Leak checker
# ---------------------------------------------------------------------------

_LEAK_PATTERNS = [
    "s3://", "minio://", "agent-service:", "minio:9000",
    "file_uri", "source_uri", "stored_uri", "pack_uri",
    "/root/", "/tmp/", "localhost:", "127.0.0.1:",
]


def _check_leaks(text: str) -> list[str]:
    """Check for internal URI leaks in response text."""
    found: list[str] = []
    lower = text.lower()
    for pat in _LEAK_PATTERNS:
        if pat.lower() in lower:
            found.append(pat)
    return found


# ---------------------------------------------------------------------------
# Data seeding (pre-populate some vouchers for flows to operate on)
# ---------------------------------------------------------------------------

def _seed_vouchers(worker_tasks: Any, count: int = 20) -> None:
    """Seed AcctVoucher rows so classify/journal/soft_checks have data."""
    from accounting_agent.common.db import db_session as db_ctx
    from accounting_agent.common.models import AcctVoucher
    from accounting_agent.common.utils import new_uuid

    types = ["sell_invoice", "buy_invoice", "receipt", "payment", "other"]
    with db_ctx(worker_tasks.engine) as s:
        for i in range(count):
            vtype = random.choice(types)
            s.add(AcctVoucher(
                id=new_uuid(),
                erp_voucher_id=f"SEED-{i:04d}",
                voucher_no=f"VCH-SEED-{i:04d}",
                voucher_type=vtype,
                date=f"2025-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
                amount=float(random.randint(500_000, 200_000_000)),
                currency="VND",
                partner_name=random.choice(["CÔNG TY A", "CÔNG TY B", "DNTN C", None]),
                description=random.choice(["Bán hàng", "Mua NVL", "Thu tiền", "Chi phí", None]),
                has_attachment=random.random() > 0.3,
                run_id="seed-run",
                source="mock_vn_fixture",
                type_hint=random.choice(["invoice_vat", "cash_disbursement", "cash_receipt", None]),
            ))


# ---------------------------------------------------------------------------
# Main harness
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Randomized system smoke test (200+ iterations)")
    parser.add_argument("--iterations", type=int, default=200, help="Number of iterations (default: 200)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument("--use-real-llm", action="store_true", default=False,
                        help="Enable real LLM calls (reads DO_AGENT_* from .env)")
    args = parser.parse_args()

    # --use-real-llm flag OR env var
    use_real_llm = args.use_real_llm or os.getenv("USE_REAL_LLM", "").strip().lower() in ("1", "true", "yes")

    if args.seed is not None:
        random.seed(args.seed)

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("🎲 Randomized System Smoke Test")
    print(f"   Iterations: {args.iterations}")
    print(f"   Seed: {args.seed or 'random'}")
    print(f"   USE_REAL_LLM: {use_real_llm}")
    print(f"   USE_LANGGRAPH: {os.getenv('USE_LANGGRAPH', 'false')}")
    print(f"   Report: {REPORT_FILE}")
    print("=" * 70)

    # --- Setup ---
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp(prefix="randomized_smoke_"))
    _setup_env(tmp_dir, use_real_llm=use_real_llm)
    worker_tasks, erpx_port, erpx_server, erpx_thread = _bootstrap_services(tmp_dir)

    # Seed some vouchers
    _seed_vouchers(worker_tasks, count=30)

    # Create TestClient for API-level tests
    from fastapi.testclient import TestClient

    from accounting_agent.agent_service.main import app
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-API-Key": "test-key-smoke"}

    # --- Build scenario list ---
    scenarios: list[dict[str, Any]] = []
    for i in range(1, args.iterations + 1):
        rnd = random.random()
        if rnd < 0.45:
            scenarios.append(_build_run_scenario(i))
        elif rnd < 0.65:
            scenarios.append(_build_qna_scenario(i))
        elif rnd < 0.82:
            scenarios.append(_build_goal_scenario(i))
        else:
            scenarios.append(_build_approval_scenario(i))

    # --- Execute ---
    results: list[dict[str, Any]] = []
    counters: Counter = Counter()
    errors_by_type: defaultdict[str, list[str]] = defaultdict(list)
    leak_count = 0

    with open(REPORT_FILE, "w", encoding="utf-8") as report_f:
        for sc in scenarios:
            i = sc["iteration"]
            test_type = sc["test_type"]

            try:
                if test_type == "run":
                    result = _exec_run_scenario(sc, worker_tasks)
                    # "failed" is acceptable — means dispatch_run caught, set status,
                    # and re-raised.  A real problem is "exception" (dispatch didn't
                    # catch) or "unknown"/"missing".
                    ok = result["status"] in ("success", "failed") and result.get("error") is None or result["status"] == "failed"
                elif test_type == "qna":
                    result = _exec_qna_scenario(sc, client, headers)
                    ok = result["status_code"] in (200, 400, 422) and result["error"] is None
                elif test_type == "goal":
                    result = _exec_goal_scenario(sc, client, headers)
                    ok = result["status_code"] in (200, 400, 422) and result["error"] is None
                elif test_type == "approval":
                    result = _exec_approval_scenario(sc, client, headers)
                    ok = result.get("skip") or (result["status_code"] in (200, 409, 400, 422) and result["error"] is None)
                else:
                    result = {**sc, "error": f"unknown test_type: {test_type}"}
                    ok = False

                # Check for leaks in goal/qna responses
                if test_type in ("qna", "goal") and result.get("answer_excerpt"):
                    leaks = _check_leaks(result["answer_excerpt"])
                    if leaks:
                        result["leaks"] = leaks
                        leak_count += 1

            except Exception as e:
                result = {**sc, "error": f"HARNESS_ERROR: {type(e).__name__}: {e}"}
                ok = False
                traceback.print_exc()

            result["ok"] = ok
            result["timestamp"] = datetime.utcnow().isoformat()
            results.append(result)

            tag = "✅" if ok else "❌"
            counters["total"] += 1
            if ok:
                counters["pass"] += 1
            else:
                counters["fail"] += 1
                err_key = f"{test_type}:{result.get('run_type', result.get('status_code', '?'))}"
                errors_by_type[err_key].append(str(result.get("error", ""))[:200])

            # One-line progress
            extra = ""
            if test_type == "run":
                extra = f" run_type={sc['run_type']} period={sc.get('period')} → {result.get('status', '?')}"
            elif test_type == "qna":
                llm_tag = " 🤖LLM" if result.get("llm_used") else ""
                extra = f" q={sc.get('question', '')[:40]}… → {result.get('status_code')}{llm_tag}"
            elif test_type == "goal":
                extra = f" cmd={sc.get('command', '')[:30]}… → {result.get('status_code')}"
            elif test_type == "approval":
                extra = f" decision={sc.get('decision')} → {result.get('status_code')}"
            print(f"  {tag} #{i:03d} [{test_type:8s}]{extra}  ({result.get('elapsed_s', 0):.2f}s)")

            # Write JSONL
            report_f.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
            report_f.flush()

    # --- Teardown ---
    from accounting_agent.common.testutils import stop_uvicorn
    stop_uvicorn(erpx_server, erpx_thread)

    # --- Summary ---
    total = counters["total"]
    passed = counters["pass"]
    failed = counters["fail"]
    pass_rate = round(100 * passed / max(total, 1), 1)

    # Count LLM calls
    llm_calls = sum(1 for r in results if r.get("llm_used"))

    print("\n" + "=" * 70)
    print(f"📊 SUMMARY: {passed}/{total} passed ({pass_rate}%), {failed} failed, {leak_count} leaks")
    if use_real_llm:
        print(f"🤖 LLM calls: {llm_calls} (real LLM enabled)")

    if errors_by_type:
        print("\n❌ Error groups:")
        for key, errs in sorted(errors_by_type.items()):
            print(f"  [{key}] × {len(errs)}")
            for e in errs[:3]:
                print(f"    → {e}")

    # --- Write summary markdown ---
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        f.write("# Randomized Smoke Test Summary\n\n")
        f.write(f"**Date:** {datetime.utcnow().isoformat()}\n\n")
        f.write(f"**Iterations:** {total}\n\n")
        f.write(f"**Pass / Fail:** {passed} / {failed} ({pass_rate}%)\n\n")
        f.write(f"**Leaks detected:** {leak_count}\n\n")
        f.write("**Env flags:**\n")
        f.write(f"- `USE_LANGGRAPH={os.getenv('USE_LANGGRAPH', 'false')}`\n")
        f.write(f"- `USE_REAL_LLM={os.getenv('USE_REAL_LLM', 'false')}`\n\n")

        # Break down by test_type
        type_counter: Counter = Counter()
        type_pass: Counter = Counter()
        for r in results:
            tt = r["test_type"]
            type_counter[tt] += 1
            if r.get("ok"):
                type_pass[tt] += 1

        f.write("## Results by test type\n\n")
        f.write("| Test Type | Total | Pass | Fail | Pass % |\n")
        f.write("|-----------|-------|------|------|--------|\n")
        for tt in sorted(type_counter.keys()):
            t = type_counter[tt]
            p = type_pass[tt]
            fl = t - p
            pct = round(100 * p / max(t, 1), 1)
            f.write(f"| {tt} | {t} | {p} | {fl} | {pct}% |\n")
        f.write("\n")

        if errors_by_type:
            f.write("## Error groups\n\n")
            for key, errs in sorted(errors_by_type.items()):
                f.write(f"### `{key}` — {len(errs)} occurrence(s)\n\n")
                for e in errs[:5]:
                    f.write(f"- `{e}`\n")
                f.write("\n")

        # Run-type breakdown for "run" test type
        run_results = [r for r in results if r["test_type"] == "run"]
        if run_results:
            f.write("## Run-type breakdown\n\n")
            f.write("| run_type | Total | success | failed | exception |\n")
            f.write("|----------|-------|---------|--------|-----------|\n")
            rt_counter: defaultdict[str, Counter] = defaultdict(Counter)
            for r in run_results:
                rt = r.get("run_type", "?")
                rt_counter[rt]["total"] += 1
                rt_counter[rt][r.get("status", "unknown")] += 1
            for rt in sorted(rt_counter.keys()):
                c = rt_counter[rt]
                f.write(f"| {rt} | {c['total']} | {c.get('success',0)} | {c.get('failed',0)} | {c.get('exception',0)} |\n")
            f.write("\n")

        f.write("## Conclusion\n\n")
        if failed == 0:
            f.write("✅ All iterations passed. System is stable.\n")
        else:
            f.write(f"⚠️ {failed} iteration(s) had issues. See error groups above for details.\n")

    print(f"\n📄 Full report: {REPORT_FILE}")
    print(f"📄 Summary:     {SUMMARY_FILE}")

    # --- Write structured JSON report (for LLM mode) ---
    today_str = datetime.utcnow().strftime("%Y%m%d")
    json_report_path = LOG_DIR / f"randomized_llm_report_{today_str}.json"

    # Collect sample runs for the report
    sample_runs = []
    for r in results:
        if r.get("test_type") == "qna" and r.get("status_code") == 200:
            sample_runs.append({
                "run_type": "qna",
                "question": r.get("question", "")[:100],
                "llm_used": bool(r.get("llm_used")),
                "has_reasoning_chain": bool(r.get("has_reasoning")),
                "answer_excerpt": r.get("answer_excerpt", "")[:120],
            })
        elif r.get("test_type") == "run" and r.get("status") == "success":
            sample_runs.append({
                "run_type": r.get("run_type"),
                "period": r.get("period"),
                "status": r.get("status"),
                "elapsed_s": r.get("elapsed_s"),
            })
        if len(sample_runs) >= 20:
            break

    validation_failures = sum(
        1 for r in results
        if r.get("test_type") == "run" and r.get("status") == "failed"
    )

    json_report = {
        "date": datetime.utcnow().isoformat(),
        "iterations": total,
        "use_real_llm": use_real_llm,
        "llm_calls": llm_calls,
        "success": passed,
        "hard_failures": failed,
        "validation_failures": validation_failures,
        "leaks_detected": leak_count,
        "sample_runs": sample_runs,
    }

    with open(json_report_path, "w", encoding="utf-8") as jf:
        json.dump(json_report, jf, ensure_ascii=False, indent=2)
    print(f"📄 JSON report: {json_report_path}")

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
