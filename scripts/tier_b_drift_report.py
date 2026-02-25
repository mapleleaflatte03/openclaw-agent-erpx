#!/usr/bin/env python3
"""Tier B drift report — detect reject-rate drift from feedback logs.

Usage:
    python scripts/tier_b_drift_report.py --days 30 --threshold 0.2

Exit codes:
    0  no drift detected (reject rate ≤ threshold)
    1  drift detected (reject rate > threshold)
    2  not enough data (< 10 feedback records in window)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tier B drift report")
    p.add_argument("--days", type=int, default=30, help="Lookback window in days")
    p.add_argument("--threshold", type=float, default=0.20, help="Reject-rate alert threshold")
    p.add_argument("--dsn", type=str, default=None, help="Database DSN (default: $AGENT_DB_DSN)")
    p.add_argument("--min-records", type=int, default=10, help="Minimum records to evaluate")
    return p.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    """Execute drift report. Returns exit code."""
    import os

    from sqlalchemy import func, select

    from accounting_agent.common.db import db_session, make_engine
    from accounting_agent.common.models import TierBFeedback

    args = _parse_args(argv)
    dsn = args.dsn or os.environ.get("AGENT_DB_DSN", "")
    if not dsn:
        print("ERROR: --dsn or AGENT_DB_DSN required", file=sys.stderr)
        return 2

    engine = make_engine(dsn)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    with db_session(engine) as session:
        total = session.execute(
            select(func.count(TierBFeedback.id)).where(
                TierBFeedback.created_at >= cutoff,
            )
        ).scalar() or 0

        rejects = session.execute(
            select(func.count(TierBFeedback.id)).where(
                TierBFeedback.created_at >= cutoff,
                TierBFeedback.feedback_type.in_(["explicit_no", "implicit_reject"]),
            )
        ).scalar() or 0

    if total < args.min_records:
        print(f"NOT_ENOUGH_DATA  total={total} min_required={args.min_records}")
        return 2

    reject_rate = rejects / total
    status = "DRIFT" if reject_rate > args.threshold else "OK"
    print(
        f"{status}  reject_rate={reject_rate:.2%} "
        f"({rejects}/{total})  threshold={args.threshold:.0%}  "
        f"window={args.days}d"
    )
    return 1 if status == "DRIFT" else 0


if __name__ == "__main__":
    sys.exit(run())
