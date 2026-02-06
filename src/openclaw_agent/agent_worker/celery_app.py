from __future__ import annotations

import os

from celery import Celery


def make_celery() -> Celery:
    celery = Celery(
        "openclaw_agent",
        broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
        backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
        include=["openclaw_agent.agent_worker.tasks"],
    )

    celery.conf.update(
        task_default_queue="default",
        task_queues={
            "default": {},
            "ocr": {},
            "export": {},
            "io": {},
            "index": {},
        },
        task_routes={
            "openclaw_agent.agent_worker.tasks.dispatch_run": {"queue": "default"},
        },
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        broker_connection_retry_on_startup=True,
    )
    return celery


celery_app = make_celery()
