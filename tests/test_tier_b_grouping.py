"""Tests for Tier B obligation grouping logic (high-confidence vs candidate)."""

from __future__ import annotations

CONFIDENCE_THRESHOLD = 0.75
CANDIDATE_LIMIT = 5

_TYPE_PRIORITY = {"payment": 0, "penalty": 1, "discount": 2}


def split_obligations(
    obligations: list[dict],
    threshold: float = CONFIDENCE_THRESHOLD,
    candidate_limit: int = CANDIDATE_LIMIT,
) -> tuple[list[dict], list[dict], int]:
    """Split obligations into high-confidence and candidate groups.

    Returns (high_conf, visible_candidates, hidden_count).
    """
    high_conf = [o for o in obligations if o.get("confidence", 0) >= threshold]
    candidates = [o for o in obligations if o.get("confidence", 0) < threshold]
    candidates.sort(
        key=lambda o: (
            _TYPE_PRIORITY.get(o.get("obligation_type", ""), 99),
            -(o.get("confidence", 0)),
        )
    )
    visible = candidates[:candidate_limit]
    hidden = max(0, len(candidates) - candidate_limit)
    return high_conf, visible, hidden


def test_empty_obligations():
    high, vis, hidden = split_obligations([])
    assert high == []
    assert vis == []
    assert hidden == 0


def test_all_high_confidence():
    obs = [
        {"obligation_type": "payment", "confidence": 0.9},
        {"obligation_type": "penalty", "confidence": 0.8},
    ]
    high, vis, hidden = split_obligations(obs)
    assert len(high) == 2
    assert vis == []
    assert hidden == 0


def test_all_candidates():
    obs = [{"obligation_type": "payment", "confidence": 0.3 + i * 0.05} for i in range(8)]
    high, vis, hidden = split_obligations(obs)
    assert len(high) == 0
    assert len(vis) == CANDIDATE_LIMIT
    assert hidden == 3


def test_mixed_split():
    obs = [
        {"obligation_type": "payment", "confidence": 0.95},
        {"obligation_type": "penalty", "confidence": 0.85},
        {"obligation_type": "discount", "confidence": 0.5},
        {"obligation_type": "payment", "confidence": 0.6},
        {"obligation_type": "penalty", "confidence": 0.4},
    ]
    high, vis, hidden = split_obligations(obs)
    assert len(high) == 2
    assert high[0]["confidence"] == 0.95
    assert len(vis) == 3
    assert hidden == 0


def test_candidate_sort_order():
    """Candidates: payment > penalty > discount > other, then by confidence desc."""
    obs = [
        {"obligation_type": "other", "confidence": 0.7},
        {"obligation_type": "payment", "confidence": 0.5},
        {"obligation_type": "penalty", "confidence": 0.6},
        {"obligation_type": "discount", "confidence": 0.4},
        {"obligation_type": "payment", "confidence": 0.3},
    ]
    _, vis, _ = split_obligations(obs)
    types = [o["obligation_type"] for o in vis]
    assert types == ["payment", "payment", "penalty", "discount", "other"]


def test_hidden_count_with_limit():
    # All below threshold (0.01..0.10) -> 10 candidates
    obs = [{"obligation_type": "payment", "confidence": 0.01 * (i + 1)} for i in range(10)]
    high, vis, hidden = split_obligations(obs, candidate_limit=3)
    assert len(high) == 0
    assert len(vis) == 3
    assert hidden == 7


def test_threshold_boundary():
    """Obligation at exactly the threshold is high-confidence."""
    obs = [
        {"obligation_type": "payment", "confidence": 0.75},
        {"obligation_type": "penalty", "confidence": 0.749},
    ]
    high, vis, hidden = split_obligations(obs)
    assert len(high) == 1
    assert high[0]["confidence"] == 0.75
    assert len(vis) == 1
    assert vis[0]["confidence"] == 0.749
