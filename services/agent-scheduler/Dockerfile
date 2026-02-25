FROM python:3.10-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
COPY config /app/config

RUN python -m pip install -U pip && \
    python -m pip install -e .

CMD ["python", "-m", "accounting_agent.agent_scheduler.main"]

