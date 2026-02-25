#!/usr/bin/env python3
"""Probe OCR voucher preview URLs and report availability.

Example:
  python scripts/benchmark/probe_preview_urls.py \
    --api-base https://app.welliam.codes/agent/v1 \
    --out reports/benchmark/prod_preview_probe_latest.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
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


def _target_root_from_api_base(api_base: str) -> str:
    if api_base.endswith("/agent/v1"):
        return api_base[: -len("/agent/v1")]
    return api_base.rsplit("/agent", 1)[0]


def _make_session(api_key: str | None) -> requests.Session:
    session = requests.Session()
    if api_key:
        session.headers["X-API-Key"] = api_key
    return session


def _get_vouchers(
    session: requests.Session,
    api_base: str,
    *,
    max_candidates: int,
    source: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    limit = 500
    offset = 0
    while len(out) < max_candidates:
        resp = session.get(
            f"{api_base}/acct/vouchers",
            params={"source": source, "limit": limit, "offset": offset},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        items = list(body.get("items") or [])
        if not items:
            break
        for item in items:
            if not item.get("attachment_id"):
                continue
            out.append(item)
            if len(out) >= max_candidates:
                break
        if len(items) < limit:
            break
        offset += limit
    return out


def _probe_preview(
    session: requests.Session,
    target_root: str,
    item: dict[str, Any],
) -> tuple[int | str, float, str]:
    preview_url = str(item.get("preview_url") or "").strip()
    attachment_id = str(item.get("attachment_id") or "").strip()
    if not preview_url and attachment_id:
        preview_url = f"{target_root}/agent/v1/attachments/{attachment_id}/content?inline=1"
    if not preview_url.startswith("http"):
        preview_url = target_root.rstrip("/") + "/" + preview_url.lstrip("/")

    t0 = time.perf_counter()
    response = session.get(preview_url, timeout=20)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    status = int(response.status_code)
    error = ""
    if status >= 400:
        error = (response.text or "")[:500]
    return status, elapsed_ms, error


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    api_base = _normalize_api_base(args.api_base)
    target_root = _target_root_from_api_base(api_base)
    api_key = (args.api_key or os.getenv("AGENT_API_KEY", "")).strip() or None
    session = _make_session(api_key)

    vouchers = _get_vouchers(
        session,
        api_base,
        max_candidates=max(1, args.max_candidates),
        source=args.source,
    )

    status_counts: dict[str, int] = {}
    latencies: list[float] = []
    failure_samples: list[dict[str, Any]] = []
    ok_200 = 0

    for item in vouchers:
        status, latency_ms, error = _probe_preview(session, target_root, item)
        latencies.append(latency_ms)
        status_key = str(status)
        status_counts[status_key] = status_counts.get(status_key, 0) + 1
        if status == 200:
            ok_200 += 1
            continue
        if len(failure_samples) < args.failure_samples:
            preview_url = str(item.get("preview_url") or "").strip()
            if not preview_url:
                preview_url = f"{target_root}/agent/v1/attachments/{item.get('attachment_id')}/content?inline=1"
            elif not preview_url.startswith("http"):
                preview_url = target_root.rstrip("/") + "/" + preview_url.lstrip("/")
            failure_samples.append(
                {
                    "voucher_id": item.get("id"),
                    "attachment_id": item.get("attachment_id"),
                    "status": item.get("status"),
                    "preview_url": preview_url,
                    "http_status": status_key,
                    "error": error,
                }
            )

    def _pct(values: list[float], p: float) -> float:
        if not values:
            return 0.0
        arr = sorted(values)
        idx = int((len(arr) - 1) * p)
        return round(arr[idx], 2)

    latency = {
        "min": round(min(latencies), 2) if latencies else 0.0,
        "p50": _pct(latencies, 0.50),
        "p95": _pct(latencies, 0.95),
        "max": round(max(latencies), 2) if latencies else 0.0,
    }
    if latencies:
        latency["avg"] = round(statistics.mean(latencies), 2)

    total = len(vouchers)
    report = {
        "generated_at": _now_iso(),
        "target": target_root,
        "api_base": api_base,
        "total_vouchers": total,
        "probe_candidates": total,
        "preview_http_200": ok_200,
        "preview_http_200_rate": (ok_200 / total) if total else 0.0,
        "status_counts": status_counts,
        "latency_ms": latency,
        "failure_samples": failure_samples,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe OCR preview URL availability")
    parser.add_argument("--api-base", required=True, help="API base URL (https://host/agent/v1 or https://host)")
    parser.add_argument("--api-key", default="", help="Optional API key (or use AGENT_API_KEY env)")
    parser.add_argument("--source", default="ocr_upload", help="Voucher source filter")
    parser.add_argument("--max-candidates", type=int, default=50, help="Maximum vouchers to probe")
    parser.add_argument("--failure-samples", type=int, default=25, help="Max failed samples in output")
    parser.add_argument("--out", default="", help="Optional output JSON path")
    args = parser.parse_args()

    report = run_probe(args)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(str(out_path))
    else:
        print(text)


if __name__ == "__main__":
    main()
