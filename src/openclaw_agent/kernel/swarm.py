"""Swarm integration stubs – Ray-based distributed skill execution.

TODO: Implement actual Ray remote functions when scaling beyond single-node.
For now these are no-op stubs that document the intended API.
"""
from __future__ import annotations

from typing import Any


def submit_skill_remote(skill_name: str, context: dict[str, Any]) -> str:
    """Submit a skill for remote execution on a Ray cluster.

    TODO: Use ``ray.remote`` decorator and ``ray.get()`` to distribute.
    Returns a task_id for tracking.
    """
    raise NotImplementedError(
        "Ray remote execution not yet implemented. "
        "Use FlowRunner.run() for single-node execution."
    )


def get_remote_result(task_id: str) -> dict[str, Any]:
    """Retrieve result of a remote skill execution.

    TODO: Implement with ``ray.get(ObjectRef)``.
    """
    raise NotImplementedError("Ray remote execution not yet implemented.")
