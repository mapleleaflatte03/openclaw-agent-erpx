"""Accounting Agent Layer Kernel – lightweight skill/flow orchestration layer.

Provides:
  - SkillRegistry: register & lookup accounting skills (callable functions)
  - FlowRunner: execute a DAG of skills in sequence (future: LangGraph)
  - RaySwarm: distributed execution via Ray (optional, USE_RAY=1)

Design:
  Accounting Agent Layer is READ-ONLY — flows produce *proposals* and *flags*, never mutate ERP.
"""

from accounting_agent.kernel.registry import Skill, SkillRegistry
from accounting_agent.kernel.runner import FlowRunner, FlowStep
from accounting_agent.kernel.swarm import RaySwarm, get_swarm
from accounting_agent.kernel.swarm import is_available as ray_available

__all__ = [
    "Skill", "SkillRegistry",
    "FlowRunner", "FlowStep",
    "RaySwarm", "get_swarm", "ray_available",
]
