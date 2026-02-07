"""Benchmark smoke test — validates dataset generation and scoring pipeline.

Runs quickly (5 cases) and does NOT require a running service.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts" / "benchmark"
DATA_DIR = REPO_ROOT / "data" / "benchmark"
CASES_DIR = DATA_DIR / "cases"
MANIFEST_PATH = DATA_DIR / "manifests" / "cases.jsonl"


@pytest.fixture(scope="module", autouse=True)
def generate_5_cases():
    """Generate 5 synthetic cases for smoke testing."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "generate_synthetic_cases.py"),
         "--cases", "5",
         "--out-dir", str(CASES_DIR),
         "--manifest", str(MANIFEST_PATH)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"Generator failed: {result.stderr}"


def test_cases_generated():
    """At least 5 case directories exist."""
    dirs = [d for d in CASES_DIR.iterdir() if d.is_dir() and d.name.startswith("case_")]
    assert len(dirs) >= 5, f"Expected >=5 cases, got {len(dirs)}"


def test_each_case_has_truth():
    """Every case has a truth.json with obligations."""
    dirs = sorted([d for d in CASES_DIR.iterdir() if d.is_dir() and d.name.startswith("case_")])[:5]
    for d in dirs:
        truth = d / "truth.json"
        assert truth.exists(), f"Missing truth.json in {d}"
        data = json.loads(truth.read_text())
        assert "obligations" in data, f"No obligations in {truth}"
        assert len(data["obligations"]) >= 1, f"Empty obligations in {truth}"
        assert "expected_gating_tier" in data
        assert "expected_risk" in data


def test_each_case_has_sources():
    """Every case has at least a PDF and EML in sources/."""
    dirs = sorted([d for d in CASES_DIR.iterdir() if d.is_dir() and d.name.startswith("case_")])[:5]
    for d in dirs:
        sources = d / "sources"
        assert sources.exists(), f"Missing sources/ in {d}"
        assert (sources / "contract.pdf").exists(), f"Missing PDF in {d}"
        assert (sources / "amendment.eml").exists(), f"Missing EML in {d}"


def test_manifest_valid():
    """Manifest is valid JSONL with correct fields."""
    assert MANIFEST_PATH.exists(), f"Manifest not found: {MANIFEST_PATH}"
    lines = MANIFEST_PATH.read_text().strip().split("\n")
    assert len(lines) >= 5
    for line in lines[:5]:
        entry = json.loads(line)
        assert "case_id" in entry
        assert "has_pdf" in entry
        assert "obligation_count" in entry
        assert entry["obligation_count"] >= 1


def test_score_module_importable():
    """The score module can be imported and score() works."""
    sys.path.insert(0, str(SCRIPTS_DIR))
    from score import score  # noqa: E402

    # Fake results
    fake_results = {
        "case_results": [
            {"case_id": "case_0001", "status": "success", "duration_s": 1.0,
             "truth_obligations": 3, "detected_obligations": 3},
            {"case_id": "case_0002", "status": "success", "duration_s": 2.0,
             "truth_obligations": 2, "detected_obligations": 2},
        ],
        "workflow_results": [
            {"workflow": "attachment", "status": "success", "duration_s": 1.0},
        ],
    }
    scores = score(fake_results)
    assert scores["accuracy"] == 1.0
    assert scores["fail_rate"] == 0.0
    assert scores["avg_latency"] == 1.5


def test_report_module_importable():
    """The report module can be imported."""
    sys.path.insert(0, str(SCRIPTS_DIR))
    from report_benchmark import _render_md  # noqa: E402, F401
