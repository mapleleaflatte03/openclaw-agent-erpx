"""OpenClaw Kernel – lightweight skill/flow orchestration layer.

Provides:
  - SkillRegistry: register & lookup accounting skills (callable functions)
  - FlowRunner: execute a DAG of skills in sequence (future: LangGraph)
  - Hooks for LangGraph and Ray integration (TODO stubs)

Design:
  OpenClaw is READ-ONLY — flows produce *proposals* and *flags*, never mutate ERP.
"""

from openclaw_agent.kernel.registry import Skill, SkillRegistry
from openclaw_agent.kernel.runner import FlowRunner, FlowStep

__all__ = ["Skill", "SkillRegistry", "FlowRunner", "FlowStep"]
