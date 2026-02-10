#!/usr/bin/env python3
"""Observe Agent — simulate random ERP uploads and auto-trigger agent chains.

This script simulates a "file watcher" that:
  1. Periodically generates VN accounting documents (1-5 per cycle)
  2. Sends them to the agent API as voucher_ingest payload
  3. Optionally chains: ingest → classify → journal → soft_check
  4. Logs activity for timeline observation

Usage:
    python scripts/observe_agent_chain.py [--api URL] [--interval SEC] [--cycles N]

Example:
    # Against local dev
    python scripts/observe_agent_chain.py --api http://localhost:8000 --cycles 3

    # Against k3s staging
    python scripts/observe_agent_chain.py --api http://159.223.83.184:30080 --cycles 5
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import date

import requests

# Import our VN data generator
sys.path.insert(0, ".")
from scripts.generate_vn_synthetic_data import (
    gen_bank_transfer,
    gen_cash_disbursement,
    gen_cash_receipt,
    gen_payroll_record,
    gen_vat_invoice,
)


def _random_docs(n: int) -> list[dict]:
    """Generate n random VN documents."""
    generators = [gen_vat_invoice, gen_cash_disbursement, gen_cash_receipt,
                  gen_bank_transfer, gen_payroll_record]
    docs = []
    for _ in range(n):
        gen = random.choice(generators)
        doc = gen(date.today())
        # Convert date objects to strings for JSON serialization
        for k, v in doc.items():
            if isinstance(v, date):
                doc[k] = v.isoformat()
        docs.append(doc)
    return docs


def trigger_run(api_base: str, run_type: str, payload: dict | None = None) -> dict | None:
    """Trigger a run via the agent API."""
    url = f"{api_base}/agent/v1/runs"
    body = {"run_type": run_type}
    if payload:
        body["payload"] = payload
    try:
        resp = requests.post(url, json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"  ⚠️  Lỗi khi gọi {run_type}: {e}")
        return None


def trigger_agent_command(api_base: str, command: str, period: str | None = None) -> dict | None:
    """Trigger a goal-centric agent command."""
    url = f"{api_base}/agent/v1/agent/commands"
    body = {"command": command}
    if period:
        body["period"] = period
    try:
        resp = requests.post(url, json=body, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"  ⚠️  Lỗi khi gửi lệnh '{command}': {e}")
        return None


def observe_cycle(api_base: str, cycle: int, chain: bool = True) -> dict:
    """Run one observe cycle: generate docs → ingest → (optional chain)."""
    n_docs = random.randint(1, 5)
    docs = _random_docs(n_docs)

    doc_types = {}
    for d in docs:
        dt = d.get("doc_type", "other")
        doc_types[dt] = doc_types.get(dt, 0) + 1

    print(f"\n{'='*60}")
    print(f"🔄 Chu kỳ {cycle}: tạo {n_docs} chứng từ VN ngẫu nhiên")
    for dt, cnt in doc_types.items():
        print(f"   • {dt}: {cnt}")

    # Step 1: Ingest
    print("  📥 Bước 1: Ingest chứng từ...")
    result = trigger_run(api_base, "voucher_ingest", {"source": "payload", "documents": docs})
    if result:
        run_id = result.get("id", "?")
        print(f"     Run ID: {run_id} | Status: {result.get('status', '?')}")

    if not chain:
        return {"cycle": cycle, "docs": n_docs, "chain": False}

    # Small delay between chain steps
    time.sleep(1)

    # Step 2: Classify
    print("  🏷️  Bước 2: Phân loại chứng từ...")
    result = trigger_run(api_base, "voucher_classify", {})
    if result:
        print(f"     Run ID: {result.get('id', '?')} | Status: {result.get('status', '?')}")
    time.sleep(1)

    # Step 3: Journal
    print("  📝 Bước 3: Đề xuất bút toán...")
    result = trigger_run(api_base, "journal_proposal", {})
    if result:
        print(f"     Run ID: {result.get('id', '?')} | Status: {result.get('status', '?')}")
    time.sleep(1)

    # Step 4: Soft check
    print("  ✅ Bước 4: Kiểm tra mềm...")
    result = trigger_run(api_base, "soft_check", {})
    if result:
        print(f"     Run ID: {result.get('id', '?')} | Status: {result.get('status', '?')}")

    return {"cycle": cycle, "docs": n_docs, "chain": True}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Observe Agent — auto-trigger agent chain with VN data",
    )
    parser.add_argument("--api", type=str, default="http://localhost:8000",
                        help="Agent API base URL")
    parser.add_argument("--interval", type=int, default=10,
                        help="Seconds between cycles")
    parser.add_argument("--cycles", type=int, default=3,
                        help="Number of observe cycles (0 = infinite)")
    parser.add_argument("--no-chain", action="store_true",
                        help="Only ingest, don't chain classify→journal→soft_check")
    parser.add_argument("--command", type=str, default=None,
                        help="Use agent command instead of individual runs (e.g. 'kiểm tra chứng từ')")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    print(f"🔍 Observe Agent — {args.api}")
    print(f"   Chu kỳ: {args.cycles or '∞'} | Khoảng cách: {args.interval}s")
    print(f"   Chain: {'❌ Chỉ ingest' if args.no_chain else '✅ Ingest → Classify → Journal → Soft Check'}")

    # Verify API is reachable
    try:
        resp = requests.get(f"{args.api}/healthz", timeout=5)
        print(f"   API status: {'✅' if resp.ok else '⚠️'} ({resp.status_code})")
    except requests.RequestException:
        print("   API status: ❌ Không kết nối được — tiếp tục anyway")

    cycle = 0
    results = []
    try:
        while True:
            cycle += 1
            if args.command:
                # Use agent command mode
                print(f"\n{'='*60}")
                print(f"🤖 Chu kỳ {cycle}: gửi lệnh '{args.command}'")
                n_docs = random.randint(1, 5)
                docs = _random_docs(n_docs)
                # Ingest first
                trigger_run(args.api, "voucher_ingest", {"source": "payload", "documents": docs})
                time.sleep(1)
                # Then send command
                result = trigger_agent_command(args.api, args.command)
                if result:
                    print(f"   Kết quả: {json.dumps(result, ensure_ascii=False)[:200]}")
                results.append({"cycle": cycle, "docs": n_docs, "command": args.command})
            else:
                r = observe_cycle(args.api, cycle, chain=not args.no_chain)
                results.append(r)

            if args.cycles and cycle >= args.cycles:
                break

            print(f"\n⏳ Chờ {args.interval}s trước chu kỳ tiếp...")
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n\n🛑 Dừng observe.")

    # Summary
    print(f"\n{'='*60}")
    print(f"📊 Tổng kết observe: {len(results)} chu kỳ")
    total_docs = sum(r.get("docs", 0) for r in results)
    print(f"   Tổng chứng từ đã tạo: {total_docs}")
    print("✅ Observe hoàn tất.")


if __name__ == "__main__":
    main()
