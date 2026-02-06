#!/bin/sh
set -eu

if [ "${AUTO_MIGRATE:-1}" = "1" ]; then
  echo "[agent-service] running migrations..."
  alembic -c db/alembic.ini upgrade head
fi

echo "[agent-service] starting api..."
exec uvicorn openclaw_agent.agent_service.main:app --host 0.0.0.0 --port 8000

