from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from accounting_agent.common.settings import Settings


class ErpXError(RuntimeError):
    pass


@dataclass
class _RateLimiter:
    qps: float
    _lock: threading.Lock
    _next_allowed: float

    @classmethod
    def create(cls, qps: float) -> _RateLimiter:
        return cls(qps=qps, _lock=threading.Lock(), _next_allowed=0.0)

    def acquire(self) -> None:
        if self.qps <= 0:
            return
        min_interval = 1.0 / self.qps
        with self._lock:
            now = time.time()
            if now < self._next_allowed:
                time.sleep(self._next_allowed - now)
                now = time.time()
            self._next_allowed = now + min_interval


class ErpXClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self._settings = settings
        self._limiter = _RateLimiter.create(settings.erpx_rate_limit_qps)
        self._client = client or httpx.Client(timeout=settings.erpx_timeout_seconds)
        self._retrying = Retrying(
            reraise=True,
            retry=retry_if_exception_type((httpx.TimeoutException, httpx.TransportError, ErpXError)),
            stop=stop_after_attempt(settings.erpx_retry_max_attempts),
            wait=wait_exponential_jitter(
                initial=settings.erpx_retry_base_seconds, max=settings.erpx_retry_max_seconds
            ),
        )

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self._settings.erpx_token:
            h["Authorization"] = f"Bearer {self._settings.erpx_token}"
        return h

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in self._retrying:
            with attempt:
                self._limiter.acquire()
                url = self._settings.erpx_base_url.rstrip("/") + path
                r = self._client.get(url, params=params, headers=self._headers())
                if r.status_code >= 500:
                    raise ErpXError(f"ERPX server error {r.status_code}")
                if r.status_code >= 400:
                    raise ErpXError(f"ERPX client error {r.status_code}: {r.text}")
                return r.json()

    def get_journals(self, updated_after: str | None = None) -> list[dict]:
        return list(self._get("/erp/v1/journals", params={"updated_after": updated_after} if updated_after else None))

    def get_partners(self, updated_after: str | None = None) -> list[dict]:
        return list(self._get("/erp/v1/partners", params={"updated_after": updated_after} if updated_after else None))

    def get_contracts(self, updated_after: str | None = None, partner_id: str | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if updated_after:
            params["updated_after"] = updated_after
        if partner_id:
            params["partner_id"] = partner_id
        return list(self._get("/erp/v1/contracts", params=params or None))

    def get_payments(self, contract_id: str | None = None, updated_after: str | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if contract_id:
            params["contract_id"] = contract_id
        if updated_after:
            params["updated_after"] = updated_after
        return list(self._get("/erp/v1/payments", params=params or None))

    def get_vouchers(self, updated_after: str | None = None) -> list[dict]:
        return list(self._get("/erp/v1/vouchers", params={"updated_after": updated_after} if updated_after else None))

    def get_invoices(self, period: str) -> list[dict]:
        return list(self._get("/erp/v1/invoices", params={"period": period}))

    def get_ar_aging(self, as_of: str) -> list[dict]:
        return list(self._get("/erp/v1/ar/aging", params={"as_of": as_of}))

    def get_assets(self, updated_after: str | None = None) -> list[dict]:
        return list(self._get("/erp/v1/assets", params={"updated_after": updated_after} if updated_after else None))

    def get_close_calendar(self, period: str) -> list[dict]:
        return list(self._get("/erp/v1/close/calendar", params={"period": period}))

    def get_bank_transactions(self, updated_after: str | None = None) -> list[dict]:
        return list(self._get("/erp/v1/bank_transactions", params={"updated_after": updated_after} if updated_after else None))

    def close(self) -> None:
        self._client.close()
