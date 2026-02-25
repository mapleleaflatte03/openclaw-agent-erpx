from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml


def _write_yaml(path: str, obj: dict) -> None:
    Path(os.path.dirname(path) or ".").mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(obj, f, sort_keys=False, allow_unicode=False)


def _normalize_binary_schema(node: object) -> None:
    if isinstance(node, dict):
        node_type = node.get("type")
        node_format = node.get("format")
        if node_type == "string" and node_format == "binary":
            node.pop("format", None)
            node.setdefault("contentMediaType", "application/octet-stream")
        for value in node.values():
            _normalize_binary_schema(value)
        return
    if isinstance(node, list):
        for item in node:
            _normalize_binary_schema(item)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root / "src"))

    # Import after adding src/ to sys.path (script can run without editable install).
    from accounting_agent.agent_service.main import app as agent_app
    from accounting_agent.erpx_mock.main import app as erpx_app

    agent_schema = agent_app.openapi()
    erpx_schema = erpx_app.openapi()
    _normalize_binary_schema(agent_schema)
    _normalize_binary_schema(erpx_schema)
    _write_yaml("openapi/agent-service.yaml", agent_schema)
    _write_yaml("openapi/erpx-mock.yaml", erpx_schema)


if __name__ == "__main__":
    main()
