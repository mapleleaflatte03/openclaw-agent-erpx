"""KPI gate test — runs full benchmark and asserts KPI thresholds.

Skipped unless 50+ cases exist in data/benchmark/cases/.
Requires a running agent-service (docker compose or staging).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def benchmark_results():
    results_path = REPO_ROOT / "reports" / "benchmark" / "latest.json"
    if not results_path.exists():
        pytest.skip("No benchmark results found (reports/benchmark/latest.json)")
    return json.loads(results_path.read_text())


@pytest.fixture(scope="module")
def kpi_scores(benchmark_results):
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "benchmark"))
    from score import score
    return score(benchmark_results)


def test_minimum_cases(benchmark_results):
    """At least 50 cases were run."""
    case_results = benchmark_results.get("case_results", [])
    effective = [r for r in case_results if r.get("status") != "skip"]
    if len(effective) < 50:
        pytest.skip(f"Only {len(effective)} cases — need 50 for KPI gate")


def test_accuracy_threshold(kpi_scores):
    """Accuracy must be >= 0.85."""
    assert kpi_scores["accuracy"] >= 0.85, (
        f"accuracy={kpi_scores['accuracy']:.4f} < 0.85"
    )


def test_fail_rate_threshold(kpi_scores):
    """Fail rate must be < 0.05."""
    assert kpi_scores["fail_rate"] < 0.05, (
        f"fail_rate={kpi_scores['fail_rate']:.4f} >= 0.05"
    )
