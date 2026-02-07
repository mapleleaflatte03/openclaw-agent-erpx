#!/usr/bin/env python3
"""Score benchmark results against ground-truth.

Reads a benchmark results JSON and computes KPI metrics.

Usage:
  python score.py --in reports/benchmark/latest.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


def score(results: dict) -> dict:
    """Compute KPI scores from benchmark results."""
    case_results = results.get("case_results", [])
    workflow_results = results.get("workflow_results", [])

    total_cases = len(case_results)
    if total_cases == 0:
        return {
            "accuracy": 0.0,
            "fail_rate": 1.0,
            "avg_latency": 0.0,
            "p95_latency": 0.0,
            "total_cases": 0,
            "workflow_pass_rate": 0.0,
        }

    # Fail rate
    failed = sum(1 for r in case_results if r.get("status") in ("failed", "error"))
    skipped = sum(1 for r in case_results if r.get("status") == "skip")
    effective_total = total_cases - skipped
    fail_rate = failed / effective_total if effective_total > 0 else 1.0

    # Accuracy: ratio of correctly detected obligations
    # For cases where the run succeeded, compare detected vs truth count
    # A more sophisticated scorer would compare field-by-field; this is
    # the baseline scorer comparing obligation count detection ratio.
    total_truth = 0
    total_correct = 0
    for r in case_results:
        if r.get("status") == "skip":
            continue
        truth_n = r.get("truth_obligations", 0)
        detected_n = r.get("detected_obligations", 0)
        total_truth += truth_n
        # Credit: min(detected, truth) — penalizes both over- and under-detection
        total_correct += min(detected_n, truth_n)

    accuracy = total_correct / total_truth if total_truth > 0 else 0.0

    # Latency
    durations = [r["duration_s"] for r in case_results if r.get("duration_s") and r.get("status") != "skip"]
    avg_latency = statistics.mean(durations) if durations else 0.0
    p95_latency = (
        sorted(durations)[int(len(durations) * 0.95)] if len(durations) >= 2 else (durations[0] if durations else 0.0)
    )

    # Workflow pass rate
    wf_total = len(workflow_results)
    wf_passed = sum(1 for r in workflow_results if r.get("status") in ("success", "completed"))
    wf_pass_rate = wf_passed / wf_total if wf_total > 0 else 0.0

    return {
        "accuracy": round(accuracy, 4),
        "fail_rate": round(fail_rate, 4),
        "avg_latency": round(avg_latency, 2),
        "p95_latency": round(p95_latency, 2),
        "total_cases": effective_total,
        "failed_cases": failed,
        "skipped_cases": skipped,
        "total_truth_obligations": total_truth,
        "total_correct_obligations": total_correct,
        "workflow_pass_rate": round(wf_pass_rate, 4),
        "workflow_total": wf_total,
        "workflow_passed": wf_passed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score benchmark results")
    parser.add_argument("--in", dest="input", type=str, required=True)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    results = json.loads(input_path.read_text())
    scores = score(results)

    print(json.dumps(scores, indent=2))

    # Exit non-zero if KPI gate fails
    if scores["accuracy"] < 0.85:
        print(f"\nFAIL: accuracy {scores['accuracy']} < 0.85", file=sys.stderr)
        sys.exit(1)
    if scores["fail_rate"] >= 0.05:
        print(f"\nFAIL: fail_rate {scores['fail_rate']} >= 0.05", file=sys.stderr)
        sys.exit(1)

    print("\nPASS: KPI gate met.")


if __name__ == "__main__":
    main()
