"""Flow Runner – execute a sequence of skills.

Current: simple sequential execution.
TODO: Replace with LangGraph StateGraph for branching/parallel nodes.
TODO: Add Ray remote execution for heavy workloads.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from openclaw_agent.kernel.registry import SkillRegistry, default_registry

log = logging.getLogger("openclaw.kernel.runner")


@dataclass
class FlowStep:
    """One step in a flow – references a skill by name."""
    skill_name: str
    kwargs: dict[str, Any] = field(default_factory=dict)


class FlowRunner:
    """Run a list of FlowSteps sequentially, threading context dict through."""

    def __init__(self, registry: SkillRegistry | None = None) -> None:
        self.registry = registry or default_registry

    def run(self, steps: list[FlowStep], context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute steps in order. Each skill receives and returns context dict."""
        ctx = dict(context or {})
        for i, step in enumerate(steps):
            skill = self.registry.get(step.skill_name)
            if skill is None:
                raise ValueError(f"Skill '{step.skill_name}' not found in registry")
            log.info("flow_step", extra={"step": i, "skill": step.skill_name})
            # TODO: wrap in LangGraph node / Ray remote
            result = skill.fn(ctx, **step.kwargs)
            if isinstance(result, dict):
                ctx.update(result)
            else:
                ctx[f"_step_{i}_result"] = result
        return ctx
