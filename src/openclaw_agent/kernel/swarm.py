"""Swarm integration – Ray-based distributed skill execution.

Provides:
  - RaySwarm: manages Ray lifecycle and remote execution
  - submit_skill_remote / get_remote_result: convenience wrappers
  - batch_map: apply a function to a list of items in parallel via Ray

Graceful degradation: if Ray is not installed or not running, all functions
raise ImportError or RuntimeError with helpful messages. The caller (worker)
can choose to fall back to sequential execution.

Set USE_RAY=1 to enable. By default Ray is off.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

log = logging.getLogger("openclaw.kernel.swarm")

# ---------------------------------------------------------------------------
# Guard – lazy import of ray
# ---------------------------------------------------------------------------


def _has_ray() -> bool:
    try:
        import ray  # noqa: F401
        return True
    except ImportError:
        return False


def _ray_enabled() -> bool:
    """True when Ray is installed AND USE_RAY=1."""
    return _has_ray() and os.getenv("USE_RAY", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Ray initialization
# ---------------------------------------------------------------------------


class RaySwarm:
    """Manages Ray runtime lifecycle and remote execution.

    Usage::

        swarm = RaySwarm()
        swarm.ensure_init()
        refs = swarm.batch_map(my_fn, items)
        results = swarm.gather(refs)
        swarm.shutdown()
    """

    def __init__(self) -> None:
        self._initialized = False

    def ensure_init(self, **ray_init_kwargs: Any) -> None:
        """Initialize Ray if not already running.

        Safe to call multiple times. Pass `address="auto"` to connect
        to an existing cluster.
        """
        if not _has_ray():
            raise ImportError(
                "Ray is not installed. "
                "Install with: pip install 'openclaw-agent-erpx[ray]'"
            )
        import ray

        if not ray.is_initialized():
            # Default: start local, ignore reinit
            ray_init_kwargs.setdefault("ignore_reinit_error", True)
            ray_init_kwargs.setdefault("log_to_driver", False)
            ray.init(**ray_init_kwargs)
            log.info("ray_initialized", extra={"resources": ray.available_resources()})
        self._initialized = True

    def shutdown(self) -> None:
        if self._initialized:
            import ray
            if ray.is_initialized():
                ray.shutdown()
            self._initialized = False

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Submit a function for remote execution, return an ObjectRef."""
        self.ensure_init()
        import ray
        remote_fn = ray.remote(fn)
        return remote_fn.remote(*args, **kwargs)

    def gather(self, refs: list[Any]) -> list[Any]:
        """Block until all ObjectRefs resolve, return results."""
        if not refs:
            return []
        import ray
        return ray.get(refs)

    def batch_map(
        self,
        fn: Callable[..., Any],
        items: list[Any],
        **extra_kwargs: Any,
    ) -> list[Any]:
        """Apply fn to each item in parallel via Ray, return results.

        Like [fn(item, **extra_kwargs) for item in items] but distributed.
        """
        self.ensure_init()
        import ray

        remote_fn = ray.remote(fn)
        refs = [remote_fn.remote(item, **extra_kwargs) for item in items]
        return ray.get(refs)

    @property
    def is_initialized(self) -> bool:
        if not _has_ray():
            return False
        import ray
        return ray.is_initialized()

    def cluster_resources(self) -> dict[str, Any]:
        """Return available cluster resources, or empty dict."""
        if not self.is_initialized:
            return {}
        import ray
        return dict(ray.available_resources())


# Module-level singleton
_swarm = RaySwarm()


# ---------------------------------------------------------------------------
# Convenience wrappers (match original stub API)
# ---------------------------------------------------------------------------


def submit_skill_remote(skill_name: str, context: dict[str, Any]) -> str:
    """Submit a skill for remote execution on a Ray cluster.

    Returns a task_id (string representation of the ObjectRef).
    """
    if not _ray_enabled():
        raise RuntimeError(
            "Ray is not enabled. Set USE_RAY=1 and ensure Ray is installed."
        )
    from openclaw_agent.kernel.registry import default_registry

    skill = default_registry.get(skill_name)
    if skill is None:
        raise ValueError(f"Skill '{skill_name}' not found in registry")

    ref = _swarm.submit(skill.fn, context)
    ref_id = str(ref)
    log.info("skill_submitted_remote", extra={"skill": skill_name, "ref": ref_id})
    return ref_id


def get_remote_result(task_id: str) -> dict[str, Any]:
    """Retrieve result of a remote skill execution.

    NOTE: task_id must be the string ObjectRef from submit_skill_remote.
    This is a simplified API — in production, use RaySwarm.gather() directly.
    """
    raise NotImplementedError(
        "get_remote_result by string task_id requires ObjectRef tracking. "
        "Use RaySwarm.submit() + RaySwarm.gather() directly instead."
    )


def is_available() -> bool:
    """Check if Ray is available and enabled."""
    return _ray_enabled()


def get_swarm() -> RaySwarm:
    """Get the module-level RaySwarm singleton."""
    return _swarm
