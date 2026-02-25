"""Skill Registry – register accounting skills as named callables.

Future: skills will be LangGraph nodes or Ray remote functions.
For now they are plain Python callables.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Skill:
    """A registered skill (atomic unit of work)."""
    name: str
    fn: Callable[..., Any]
    description: str = ""
    tags: list[str] = field(default_factory=list)


class SkillRegistry:
    """In-memory registry of accounting skills."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(
        self,
        name: str,
        fn: Callable[..., Any],
        description: str = "",
        tags: list[str] | None = None,
    ) -> Skill:
        skill = Skill(name=name, fn=fn, description=description, tags=tags or [])
        self._skills[name] = skill
        return skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_skills(self, tag: str | None = None) -> list[Skill]:
        if tag is None:
            return list(self._skills.values())
        return [s for s in self._skills.values() if tag in s.tags]

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def __len__(self) -> int:
        return len(self._skills)


# Singleton registry used across the application
default_registry = SkillRegistry()
