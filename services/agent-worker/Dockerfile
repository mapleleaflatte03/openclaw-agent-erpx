FROM python:3.10-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr \
      tesseract-ocr-vie \
      poppler-utils \
      curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
COPY db /app/db
COPY config /app/config

RUN python -m pip install -U pip && \
    python -m pip install -e .

CMD ["celery", "-A", "accounting_agent.agent_worker.celery_app.celery_app", "worker", "-l", "INFO", "-Q", "default,ocr,export,io,index"]

