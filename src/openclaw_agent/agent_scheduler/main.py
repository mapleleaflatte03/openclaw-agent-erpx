from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
import yaml

from openclaw_agent.common.logging import configure_logging, get_logger
from openclaw_agent.common.settings import get_settings
from openclaw_agent.common.storage import list_objects
from openclaw_agent.common.utils import make_idempotency_key

settings = get_settings()
configure_logging(settings.log_level)
log = get_logger("agent-scheduler")


def _expand_env(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(v) for v in obj]
    if isinstance(obj, str):
        def repl(m: re.Match[str]) -> str:
            return os.getenv(m.group(1), "")

        return re.sub(r"\\$\\{([A-Z0-9_]+)\\}", repl, obj)
    return obj


def _load_yaml(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return _expand_env(yaml.safe_load(f) or {})


def _month_yyyy_mm(dt: date) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def _prev_month_period(today: date) -> str:
    first = today.replace(day=1)
    prev_last = first - timedelta(days=1)
    return _month_yyyy_mm(prev_last)


def _this_month_period(today: date) -> str:
    return _month_yyyy_mm(today)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class AgentClient:
    def __init__(self, base_url: str):
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=10.0)

    def create_run(self, run_type: str, trigger_type: str, payload: dict[str, Any], idem_key: str) -> dict[str, Any]:
        headers = {"Content-Type": "application/json", "Idempotency-Key": idem_key}
        if settings.agent_auth_mode != "none" and settings.agent_api_key:
            headers["X-API-Key"] = settings.agent_api_key
        r = self._client.post(
            "/agent/v1/runs",
            headers=headers,
            json={"run_type": run_type, "trigger_type": trigger_type, "payload": payload},
        )
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        self._client.close()


@dataclass
class CronJob:
    name: str
    cron: str
    run_type: str
    payload_template: dict[str, Any]
    next_ts: float


def _cron_next(cron_expr: str, base: datetime) -> datetime:
    from croniter import croniter

    it = croniter(cron_expr, base)
    return it.get_next(datetime)


def _materialize_payload(template: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    today = date.today()
    out: dict[str, Any] = {}
    for k, v in template.items():
        if k == "updated_after_hours":
            hours = int(v)
            out["updated_after"] = _iso(now - timedelta(hours=hours))
        elif k == "as_of" and v == "today":
            out["as_of"] = today.isoformat()
        elif k == "period" and v == "prev_month":
            out["period"] = _prev_month_period(today)
        elif k == "period" and v == "this_month":
            out["period"] = _this_month_period(today)
        else:
            out[k] = v
    return out


def _poll_drop_bucket(agent: AgentClient, poller: dict[str, Any], seen: set[str]) -> None:
    bucket = poller["bucket"]
    prefix = poller["prefix"]
    run_type = poller["run_type"]
    for obj in list_objects(settings, bucket=bucket, prefix=prefix):
        if obj.key in seen:
            continue
        seen.add(obj.key)
        payload = {"file_uri": obj.uri()}
        idem = f"{run_type}:{bucket}:{obj.key}"
        try:
            agent.create_run(run_type=run_type, trigger_type="event", payload=payload, idem_key=idem)
            log.info("event_run_created", run_type=run_type, key=obj.key)
        except Exception as e:
            log.error("event_run_failed", run_type=run_type, key=obj.key, error=str(e))


def main() -> None:
    cfg = _load_yaml(os.getenv("SCHEDULES_YAML", "config/schedules.yaml"))
    base_url = cfg.get("agent_base_url") or os.getenv("AGENT_BASE_URL", "http://agent-service:8000")
    pollers = cfg.get("pollers") or {}
    schedules = cfg.get("schedules") or {}

    agent = AgentClient(base_url=base_url)

    cron_jobs: list[CronJob] = []
    now = datetime.now(timezone.utc)
    for name, job in schedules.items():
        if not job.get("enabled", True):
            continue
        cron = job["cron"]
        next_dt = _cron_next(cron, now)
        cron_jobs.append(
            CronJob(
                name=name,
                cron=cron,
                run_type=job["run_type"],
                payload_template=job.get("payload") or {},
                next_ts=next_dt.timestamp(),
            )
        )

    seen: dict[str, set[str]] = {name: set() for name in pollers}
    next_poll_ts: dict[str, float] = {name: 0.0 for name in pollers}

    log.info("scheduler_started", base_url=base_url, cron_jobs=[j.name for j in cron_jobs], pollers=list(pollers))
    try:
        while True:
            now = datetime.now(timezone.utc)
            now_ts = now.timestamp()

            # Pollers
            for name, p in pollers.items():
                if not p.get("enabled", True):
                    continue
                interval = int(p.get("interval_seconds", 30))
                if now_ts < next_poll_ts[name]:
                    continue
                next_poll_ts[name] = now_ts + max(interval, 1)
                _poll_drop_bucket(agent, p, seen[name])

            # Cron jobs
            for j in cron_jobs:
                if now_ts >= j.next_ts:
                    payload = _materialize_payload(j.payload_template)
                    idem = make_idempotency_key("schedule", j.name, payload, _month_yyyy_mm(date.today()))
                    try:
                        agent.create_run(run_type=j.run_type, trigger_type="schedule", payload=payload, idem_key=idem)
                        log.info("schedule_run_created", job=j.name, run_type=j.run_type, payload=payload)
                    except Exception as e:
                        log.error("schedule_run_failed", job=j.name, run_type=j.run_type, error=str(e))
                    # schedule next
                    next_dt = _cron_next(j.cron, now)
                    j.next_ts = next_dt.timestamp()

            time.sleep(10)
    finally:
        agent.close()


if __name__ == "__main__":
    main()
