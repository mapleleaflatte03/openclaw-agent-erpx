#!/usr/bin/env python3
"""Bulk reprocess OCR vouchers with wrong/old statuses.

Typical usage:
  python scripts/maintenance/reprocess_ocr_vouchers.py \
    --api-base https://app.welliam.codes/agent/v1 \
    --max-items 200 \
    --include-non-invoice \
    --wait
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_api_base(value: str) -> str:
    base = (value or "").strip().rstrip("/")
    if not base:
        raise ValueError("api-base is required")
    if base.endswith("/agent/v1"):
        return base
    if base.endswith("/agent"):
        return base + "/v1"
    return base + "/agent/v1"


def _make_session(api_key: str | None) -> requests.Session:
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    if api_key:
        session.headers["X-API-Key"] = api_key
    return session


def _api_get(session: requests.Session, url: str, *, params: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {"items": payload}


def _api_post(
    session: requests.Session,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout: int = 45,
) -> tuple[int, dict[str, Any]]:
    response = session.post(url, json=json_body or {}, timeout=timeout)
    status = int(response.status_code)
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            payload = {"data": payload}
    except Exception:
        payload = {"raw": response.text[:500]}
    return status, payload


def _trigger_reprocess(
    session: requests.Session,
    api_base: str,
    *,
    voucher_id: str,
    attachment_id: str,
    reason: str,
    requested_by: str,
) -> tuple[int, dict[str, Any], str]:
    status_code, payload = _api_post(
        session,
        f"{api_base}/acct/vouchers/{voucher_id}/reprocess",
        json_body={
            "reason": reason,
            "requested_by": requested_by,
            "attachment_id": attachment_id,
        },
    )
    if status_code != 404:
        return status_code, payload, "voucher_endpoint"

    # Backward compatibility: older deployments may not expose the wrapper
    # endpoint yet but still support generic /runs with voucher_reprocess.
    status_code, payload = _api_post(
        session,
        f"{api_base}/runs",
        json_body={
            "run_type": "voucher_reprocess",
            "trigger_type": "manual",
            "requested_by": requested_by,
            "payload": {
                "voucher_id": voucher_id,
                "attachment_id": attachment_id,
                "reason": reason,
            },
        },
    )
    return status_code, payload, "runs_fallback"


def _fetch_candidate_vouchers(
    session: requests.Session,
    api_base: str,
    *,
    source_prefix: str,
    max_items: int,
    include_non_invoice: bool,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    offset = 0
    page_size = 500

    while len(selected) < max_items:
        data = _api_get(
            session,
            f"{api_base}/acct/vouchers",
            params={
                "quality_scope": "review",
                "limit": page_size,
                "offset": offset,
            },
        )
        items = data.get("items") or []
        if not items:
            break

        for row in items:
            status = str(row.get("status") or "").strip().lower()
            source_tag = str(row.get("source_tag") or row.get("source") or "").strip().lower()
            if not source_tag.startswith(source_prefix):
                continue
            if status == "non_invoice" and not include_non_invoice:
                continue

            attachment_id = str(row.get("attachment_id") or row.get("source_ref") or "").strip()
            if not attachment_id:
                continue

            selected.append(
                {
                    "voucher_id": row.get("id"),
                    "attachment_id": attachment_id,
                    "status": status,
                    "quality_reasons": row.get("quality_reasons") or [],
                    "source_tag": source_tag,
                    "filename": row.get("original_filename") or row.get("voucher_no"),
                }
            )
            if len(selected) >= max_items:
                break

        if len(items) < page_size:
            break
        offset += page_size

    return selected


def _wait_for_run(
    session: requests.Session,
    api_base: str,
    run_id: str,
    *,
    timeout_seconds: int,
    poll_seconds: float = 1.5,
) -> tuple[str, dict[str, Any] | None]:
    deadline = time.time() + max(timeout_seconds, 1)
    while time.time() < deadline:
        try:
            run_data = _api_get(session, f"{api_base}/runs/{run_id}")
        except Exception:
            time.sleep(poll_seconds)
            continue
        status = str(run_data.get("status") or "").strip().lower()
        if status in {"success", "failed", "canceled"}:
            return status, run_data
        time.sleep(poll_seconds)
    return "timeout", None


def run_cleanup(args: argparse.Namespace) -> dict[str, Any]:
    api_base = _normalize_api_base(args.api_base)
    api_key = (args.api_key or os.getenv("AGENT_API_KEY", "")).strip() or None
    session = _make_session(api_key)

    # health check
    _api_get(session, f"{api_base}/healthz")

    candidates = _fetch_candidate_vouchers(
        session,
        api_base,
        source_prefix=args.source_prefix.lower().strip(),
        max_items=args.max_items,
        include_non_invoice=args.include_non_invoice,
    )

    report: dict[str, Any] = {
        "generated_at": _now_iso(),
        "api_base": api_base,
        "dry_run": bool(args.dry_run),
        "wait_for_completion": bool(args.wait),
        "max_items": int(args.max_items),
        "source_prefix": args.source_prefix,
        "include_non_invoice": bool(args.include_non_invoice),
        "candidate_count": len(candidates),
        "processed": 0,
        "success": 0,
        "failed": 0,
        "running_or_queued": 0,
        "timeouts": 0,
        "details": [],
    }

    if args.dry_run:
        report["details"] = candidates
        return report

    for row in candidates:
        voucher_id = str(row["voucher_id"])
        status_code, response, request_mode = _trigger_reprocess(
            session,
            api_base,
            voucher_id=voucher_id,
            attachment_id=str(row["attachment_id"]),
            reason=args.reason,
            requested_by=args.requested_by,
        )

        detail = {
            **row,
            "request_mode": request_mode,
            "http_status": status_code,
            "response": response,
            "result": "unknown",
        }
        report["processed"] += 1

        if status_code >= 400:
            detail["result"] = "failed"
            report["failed"] += 1
            report["details"].append(detail)
            continue

        run_id = str(response.get("run_id") or "")
        detail["run_id"] = run_id
        if not run_id or not args.wait:
            detail["result"] = "queued"
            report["running_or_queued"] += 1
            report["details"].append(detail)
            continue

        final_status, run_data = _wait_for_run(
            session,
            api_base,
            run_id,
            timeout_seconds=args.wait_timeout,
        )
        detail["run_final_status"] = final_status
        detail["run_data"] = run_data
        if final_status == "success":
            detail["result"] = "success"
            report["success"] += 1
        elif final_status == "timeout":
            detail["result"] = "timeout"
            report["timeouts"] += 1
        else:
            detail["result"] = "failed"
            report["failed"] += 1
        report["details"].append(detail)

    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk reprocess OCR vouchers in review/non_invoice states.")
    parser.add_argument("--api-base", default=os.getenv("AGENT_BASE_URL", "http://localhost:8000/agent/v1"))
    parser.add_argument("--api-key", default="")
    parser.add_argument("--source-prefix", default="ocr_upload")
    parser.add_argument("--max-items", type=int, default=200)
    parser.add_argument("--include-non-invoice", action="store_true")
    parser.add_argument("--reason", default="bulk_cleanup_reprocess")
    parser.add_argument("--requested-by", default="ops-batch-cleanup")
    parser.add_argument("--wait", action="store_true", help="Wait for each run to finish.")
    parser.add_argument("--wait-timeout", type=int, default=120)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("reports/benchmark/ocr_reprocess_cleanup_latest.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run_cleanup(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"API base:         {report['api_base']}")
    print(f"Dry run:          {report['dry_run']}")
    print(f"Candidates:       {report['candidate_count']}")
    print(f"Processed:        {report['processed']}")
    print(f"Success:          {report['success']}")
    print(f"Failed:           {report['failed']}")
    print(f"Queued/Running:   {report['running_or_queued']}")
    print(f"Timeouts:         {report['timeouts']}")
    print(f"Report:           {args.out}")


if __name__ == "__main__":
    main()
