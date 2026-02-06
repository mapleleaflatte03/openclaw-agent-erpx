FROM python:3.10-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
COPY db /app/db
COPY config /app/config
COPY services/agent-service/entrypoint.sh /app/entrypoint.sh

RUN python -m pip install -U pip && \
    python -m pip install -e . && \
    chmod +x /app/entrypoint.sh

EXPOSE 8000

CMD ["/app/entrypoint.sh"]

