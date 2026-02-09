"""Ray batch processing for accounting workflows.

Provides parallelized batch operations:
  - batch_classify_vouchers: classify N vouchers in parallel
  - batch_reconcile_segments: split bank txs into segments, reconcile in parallel
  - batch_anomaly_scan: scan data for anomalies in parallel

All functions gracefully degrade to sequential execution when Ray is
not available or USE_RAY is not set.
"""
from __future__ import annotations

import logging
import math
from typing import Any

log = logging.getLogger("openclaw.kernel.batch")


def _classify_single_voucher(voucher: dict[str, Any]) -> dict[str, Any]:
    """Classify a single voucher — pure function suitable for Ray remote."""
    from openclaw_agent.flows.journal_suggestion import _classify_voucher
    result = _classify_voucher(voucher)
    result["voucher_id"] = voucher.get("voucher_id") or voucher.get("erp_voucher_id", "")
    return result


def _scan_anomalies_chunk(items: list[dict[str, Any]], rules: list[str]) -> list[dict[str, Any]]:
    """Check a chunk of items against anomaly rules — pure function for Ray."""
    issues: list[dict[str, Any]] = []
    for item in items:
        amount = float(item.get("amount", 0) or 0)
        if "LARGE_AMOUNT" in rules and amount >= 500_000_000 and not item.get("approved_by"):
            issues.append({
                "rule": "LARGE_AMOUNT_NO_APPROVAL",
                "ref": item.get("voucher_id") or item.get("voucher_no", "?"),
                "amount": amount,
            })
        if "MISSING_ATTACHMENT" in rules and not item.get("has_attachment", True):
            issues.append({
                "rule": "MISSING_ATTACHMENT",
                "ref": item.get("voucher_id") or item.get("voucher_no", "?"),
            })
    return issues


def _chunk_list(items: list, chunk_size: int) -> list[list]:
    """Split a list into chunks."""
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def batch_classify_vouchers(
    vouchers: list[dict[str, Any]],
    use_ray: bool = False,
) -> list[dict[str, Any]]:
    """Classify vouchers — Ray-parallel if enabled, else sequential.

    Returns list of classification dicts.
    """
    if not vouchers:
        return []

    if use_ray:
        try:
            from openclaw_agent.kernel.swarm import get_swarm
            swarm = get_swarm()
            results = swarm.batch_map(_classify_single_voucher, vouchers)
            log.info("batch_classify via Ray: %d items", len(results))
            return results
        except Exception as e:
            log.warning("Ray batch_classify failed, falling back: %s", e)

    # Sequential fallback
    return [_classify_single_voucher(v) for v in vouchers]


def batch_anomaly_scan(
    items: list[dict[str, Any]],
    rules: list[str] | None = None,
    chunk_size: int = 100,
    use_ray: bool = False,
) -> list[dict[str, Any]]:
    """Scan items for anomalies — Ray-parallel if enabled.

    Returns flat list of issue dicts.
    """
    if not items:
        return []

    rules = rules or ["LARGE_AMOUNT", "MISSING_ATTACHMENT"]
    chunks = _chunk_list(items, chunk_size)

    if use_ray and len(chunks) > 1:
        try:
            from openclaw_agent.kernel.swarm import get_swarm
            swarm = get_swarm()
            swarm.ensure_init()

            import ray
            remote_scan = ray.remote(_scan_anomalies_chunk)
            refs = [remote_scan.remote(chunk, rules) for chunk in chunks]
            chunk_results = ray.get(refs)
            flat = [issue for chunk in chunk_results for issue in chunk]
            log.info("batch_anomaly_scan via Ray: %d chunks, %d issues", len(chunks), len(flat))
            return flat
        except Exception as e:
            log.warning("Ray batch_anomaly_scan failed, falling back: %s", e)

    # Sequential fallback
    flat = []
    for chunk in chunks:
        flat.extend(_scan_anomalies_chunk(chunk, rules))
    return flat


def parallel_map(
    fn: Any,
    items: list[Any],
    use_ray: bool = False,
) -> list[Any]:
    """Generic parallel map — tries Ray, falls back to sequential.

    fn must be a picklable top-level function.
    """
    if not items:
        return []

    if use_ray:
        try:
            from openclaw_agent.kernel.swarm import get_swarm
            swarm = get_swarm()
            return swarm.batch_map(fn, items)
        except Exception as e:
            log.warning("parallel_map via Ray failed: %s", e)

    return [fn(item) for item in items]
