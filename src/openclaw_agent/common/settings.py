from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    agent_env: Literal["dev", "staging", "prod"] = Field(default="dev", alias="AGENT_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    agent_db_dsn: str = Field(alias="AGENT_DB_DSN")

    redis_url: str = Field(alias="REDIS_URL")
    celery_broker_url: str = Field(alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field(alias="CELERY_RESULT_BACKEND")

    erpx_base_url: str = Field(alias="ERPX_BASE_URL")
    erpx_token: str = Field(default="", alias="ERPX_TOKEN")
    erpx_rate_limit_qps: float = Field(default=10.0, alias="ERPX_RATE_LIMIT_QPS")
    erpx_timeout_seconds: float = Field(default=15.0, alias="ERPX_TIMEOUT_SECONDS")
    erpx_retry_max_attempts: int = Field(default=3, alias="ERPX_RETRY_MAX_ATTEMPTS")
    erpx_retry_base_seconds: float = Field(default=0.5, alias="ERPX_RETRY_BASE_SECONDS")
    erpx_retry_max_seconds: float = Field(default=10.0, alias="ERPX_RETRY_MAX_SECONDS")

    task_retry_max_attempts: int = Field(default=3, alias="TASK_RETRY_MAX_ATTEMPTS")
    task_retry_backoff_seconds: int = Field(default=2, alias="TASK_RETRY_BACKOFF_SECONDS")

    agent_auth_mode: Literal["none", "api_key"] = Field(default="none", alias="AGENT_AUTH_MODE")
    agent_api_key: str = Field(default="", alias="AGENT_API_KEY")

    minio_endpoint: str = Field(alias="MINIO_ENDPOINT")
    minio_region: str = Field(default="sgp1", alias="MINIO_REGION")
    minio_access_key: str = Field(alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(alias="MINIO_SECRET_KEY")
    minio_secure: bool = Field(default=False, alias="MINIO_SECURE")
    minio_bucket_attachments: str = Field(default="agent-attachments", alias="MINIO_BUCKET_ATTACHMENTS")
    minio_bucket_exports: str = Field(default="agent-exports", alias="MINIO_BUCKET_EXPORTS")
    minio_bucket_evidence: str = Field(default="agent-evidence", alias="MINIO_BUCKET_EVIDENCE")
    minio_bucket_kb: str = Field(default="agent-kb", alias="MINIO_BUCKET_KB")
    minio_bucket_drop: str = Field(default="agent-drop", alias="MINIO_BUCKET_DROP")

    match_confidence_threshold: float = Field(default=0.85, alias="MATCH_CONFIDENCE_THRESHOLD")
    ocr_timeout_seconds: int = Field(default=40, alias="OCR_TIMEOUT_SECONDS")
    ocr_pdf_max_pages: int = Field(default=3, alias="OCR_PDF_MAX_PAGES")

    obligation_confidence_threshold: float = Field(default=0.8, alias="OBLIGATION_CONFIDENCE_THRESHOLD")
    obligation_required_fields: Literal["strict", "relaxed"] = Field(
        default="strict", alias="OBLIGATION_REQUIRED_FIELDS"
    )
    obligation_conflict_policy: Literal["drop_to_tier2"] = Field(
        default="drop_to_tier2", alias="OBLIGATION_CONFLICT_POLICY"
    )
    obligation_primary_source_weight: float = Field(default=1.0, alias="OBLIGATION_PRIMARY_SOURCE_WEIGHT")

    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="accounting@example.local", alias="SMTP_FROM")
    smtp_tls: bool = Field(default=True, alias="SMTP_TLS")

    # --- LLM ---
    use_real_llm: bool = Field(default=False, alias="USE_REAL_LLM")
    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    llm_api_key: str = Field(default="", alias="DO_AGENT_API_KEY")
    llm_base_url: str = Field(default="", alias="DO_AGENT_BASE_URL")
    llm_model: str = Field(default="gpt-4.1-mini", alias="DO_AGENT_MODEL")
    llm_timeout: float = Field(default=25.0, alias="LLM_TIMEOUT")
    llm_max_tokens: int = Field(default=512, alias="LLM_MAX_TOKENS")
    llm_temperature: float = Field(default=0.1, alias="LLM_TEMPERATURE")

    @field_validator("erpx_retry_max_attempts", mode="before")
    @classmethod
    def _clamp_erpx_retry_max_attempts(cls, value: object) -> int:
        """Keep ERPX retries within hardening gate (1..3) even if env is higher."""
        try:
            attempts = int(value)
        except Exception:
            return 3
        if attempts < 1:
            return 1
        return min(attempts, 3)

    @field_validator("erpx_rate_limit_qps", mode="before")
    @classmethod
    def _enforce_erpx_rate_limit_qps(cls, _value: object) -> float:
        """Lock ERPX request rate at hardening baseline (10 qps)."""
        return 10.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
