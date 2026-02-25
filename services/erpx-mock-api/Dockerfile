FROM python:3.10-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src

RUN python -m pip install -U pip && \
    python -m pip install -e .

EXPOSE 8001

CMD ["uvicorn", "accounting_agent.erpx_mock.main:app", "--host", "0.0.0.0", "--port", "8001"]

