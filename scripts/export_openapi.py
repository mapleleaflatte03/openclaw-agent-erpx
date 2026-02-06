from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml


def _write_yaml(path: str, obj: dict) -> None:
    Path(os.path.dirname(path) or ".").mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(obj, f, sort_keys=False, allow_unicode=False)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root / "src"))

    # Import after adding src/ to sys.path (script can run without editable install).
    from openclaw_agent.agent_service.main import app as agent_app
    from openclaw_agent.erpx_mock.main import app as erpx_app

    _write_yaml("openapi/agent-service.yaml", agent_app.openapi())
    _write_yaml("openapi/erpx-mock.yaml", erpx_app.openapi())


if __name__ == "__main__":
    main()
