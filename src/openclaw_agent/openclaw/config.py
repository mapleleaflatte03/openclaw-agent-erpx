from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml


@dataclass(frozen=True)
class WorkflowStep:
    name: str
    skill: str
    queue: str
    soft_timeout_seconds: int | None = None


@dataclass(frozen=True)
class WorkflowDef:
    name: str
    run_type: str
    idempotency: dict[str, Any]
    steps: list[WorkflowStep]


def _default_workflows_path() -> str:
    # Allow overriding for tests/deployments.
    return os.getenv("WORKFLOWS_YAML", "config/workflows.yaml")


@lru_cache
def load_workflows(path: str | None = None) -> dict[str, WorkflowDef]:
    p = path or _default_workflows_path()
    with open(p, encoding="utf-8") as f:
        doc = yaml.safe_load(f)

    workflows: dict[str, WorkflowDef] = {}
    for name, wf in (doc.get("workflows") or {}).items():
        steps = [
            WorkflowStep(
                name=s["name"],
                skill=s["skill"],
                queue=s.get("queue", "default"),
                soft_timeout_seconds=s.get("soft_timeout_seconds"),
            )
            for s in (wf.get("steps") or [])
        ]
        workflows[name] = WorkflowDef(
            name=name,
            run_type=wf["run_type"],
            idempotency=wf.get("idempotency") or {},
            steps=steps,
        )
    return workflows

