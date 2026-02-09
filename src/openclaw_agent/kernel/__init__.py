"""OpenClaw Kernel – lightweight skill/flow orchestration layer.

Provides:
  - SkillRegistry: register & lookup accounting skills (callable functions)
  - FlowRunner: execute a DAG of skills in sequence (future: LangGraph)
  - RaySwarm: distributed execution via Ray (optional, USE_RAY=1)

Design:
  OpenClaw is READ-ONLY — flows produce *proposals* and *flags*, never mutate ERP.
"""

from openclaw_agent.kernel.registry import Skill, SkillRegistry
from openclaw_agent.kernel.runner import FlowRunner, FlowStep
from openclaw_agent.kernel.swarm import RaySwarm, get_swarm, is_available as ray_available

__all__ = [
    "Skill", "SkillRegistry",
    "FlowRunner", "FlowStep",
    "RaySwarm", "get_swarm", "ray_available",
]
