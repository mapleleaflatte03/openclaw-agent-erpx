FROM python:3.10-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src

RUN python -m pip install -U pip && \
    python -m pip install -e '.[ui]'

EXPOSE 8501

CMD ["streamlit", "run", "src/openclaw_agent/ui/app.py", "--server.port", "8501", "--server.address", "0.0.0.0"]

