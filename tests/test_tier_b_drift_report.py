"""Tests for tier_b_drift_report script."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

# Make scripts/ importable
_scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

tier_b_drift_report = importlib.import_module("tier_b_drift_report")
run = tier_b_drift_report.run


def test_drift_report_importable():
    """Script can be imported without side effects."""
    assert callable(run)


def test_drift_report_no_dsn():
    """Returns exit-code 2 when no DSN provided."""
    import os

    old = os.environ.pop("AGENT_DB_DSN", None)
    try:
        assert run(["--days", "7"]) == 2
    finally:
        if old is not None:
            os.environ["AGENT_DB_DSN"] = old


def test_drift_report_not_enough_data(tmp_path):
    """With empty DB, returns exit-code 2 (not enough data)."""
    db_file = tmp_path / "test.sqlite"
    dsn = f"sqlite+pysqlite:///{db_file}"

    from accounting_agent.common.db import Base, make_engine

    engine = make_engine(dsn)
    Base.metadata.create_all(engine)

    assert run(["--dsn", dsn, "--days", "7"]) == 2


def test_drift_report_ok(tmp_path):
    """With mostly positive feedback, returns 0 (OK)."""
    db_file = tmp_path / "test.sqlite"
    dsn = f"sqlite+pysqlite:///{db_file}"

    from accounting_agent.common.db import Base, db_session, make_engine
    from accounting_agent.common.models import TierBFeedback
    from accounting_agent.common.utils import new_uuid

    engine = make_engine(dsn)
    Base.metadata.create_all(engine)

    with db_session(engine) as s:
        for i in range(15):
            s.add(
                TierBFeedback(
                    id=new_uuid(),
                    obligation_id=f"obl-{i}",
                    feedback_type="explicit_yes" if i < 14 else "explicit_no",
                )
            )

    # 1/15 = 6.7% < 20% threshold → OK
    assert run(["--dsn", dsn, "--days", "7"]) == 0


def test_drift_report_drift(tmp_path):
    """With many rejects, returns 1 (DRIFT)."""
    db_file = tmp_path / "test.sqlite"
    dsn = f"sqlite+pysqlite:///{db_file}"

    from accounting_agent.common.db import Base, db_session, make_engine
    from accounting_agent.common.models import TierBFeedback
    from accounting_agent.common.utils import new_uuid

    engine = make_engine(dsn)
    Base.metadata.create_all(engine)

    with db_session(engine) as s:
        for i in range(10):
            s.add(
                TierBFeedback(
                    id=new_uuid(),
                    obligation_id=f"obl-{i}",
                    feedback_type="explicit_no" if i < 5 else "explicit_yes",
                )
            )

    # 5/10 = 50% > 20% threshold → DRIFT
    assert run(["--dsn", dsn, "--days", "7"]) == 1
