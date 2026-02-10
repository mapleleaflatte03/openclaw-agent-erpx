"""Env-driven LLM client with fallback to rule-based logic.

Configuration is read entirely from environment variables (or ``.env``).
When ``USE_REAL_LLM=true`` **and** the required ``DO_AGENT_*`` vars are
present the client will issue real HTTP calls to the DigitalOcean
(OpenAI-compatible) agent endpoint.  Otherwise it degrades gracefully to
``enabled=False`` so that every consumer can keep using rule-based logic
without any change in behavior.

Env vars consumed (all optional — missing ⇒ disabled):
    USE_REAL_LLM       — master toggle (``true`` / ``1`` / ``yes``)
    DO_AGENT_BASE_URL  — DigitalOcean agent base URL
    DO_AGENT_API_KEY   — bearer token
    DO_AGENT_MODEL     — informational model label (logged, not sent)
    LLM_TIMEOUT        — HTTP timeout in seconds (default 25)
    LLM_MAX_TOKENS     — max completion tokens  (default 512)
    LLM_TEMPERATURE    — sampling temperature    (default 0.1)

Design principles:
  * NO hard-coded API keys / endpoints.
  * NO secrets logged / printed / returned to UI.
  * NO external HTTP in test suite — tests set ``USE_REAL_LLM=false``.
  * Thread-safe: one ``httpx.Client`` per ``LLMClient`` instance.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("openclaw.llm.client")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LLMClientConfig:
    """Immutable configuration resolved from env at construction time."""

    enabled: bool = False
    base_url: str = ""
    api_key: str = ""           # never logged / surfaced
    model_label: str = ""       # informational only
    timeout: float = 25.0
    max_tokens: int = 512
    temperature: float = 0.1

    @classmethod
    def from_env(cls) -> LLMClientConfig:
        """Build config from current environment.

        Returns an *always-valid* config — if anything is missing the
        ``enabled`` flag is simply ``False``.
        """
        use_real = os.getenv("USE_REAL_LLM", "").strip().lower() in ("1", "true", "yes")
        base_url = (os.getenv("DO_AGENT_BASE_URL") or "").strip().rstrip("/")
        api_key = (os.getenv("DO_AGENT_API_KEY") or "").strip()
        model = (os.getenv("DO_AGENT_MODEL") or "").strip()
        timeout = float(os.getenv("LLM_TIMEOUT", "25"))
        max_tokens = int(os.getenv("LLM_MAX_TOKENS", "512"))
        temperature = float(os.getenv("LLM_TEMPERATURE", "0.1"))

        enabled = use_real and bool(base_url) and bool(api_key)
        if use_real and not enabled:
            log.warning(
                "USE_REAL_LLM=true nhưng thiếu DO_AGENT_BASE_URL hoặc DO_AGENT_API_KEY "
                "→ fallback sang logic rule-based"
            )

        return cls(
            enabled=enabled,
            base_url=base_url,
            api_key=api_key,
            model_label=model,
            timeout=timeout,
            max_tokens=max_tokens,
            temperature=temperature,
        )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

@dataclass
class LLMClient:
    """Thin wrapper around an OpenAI-compatible chat-completions endpoint.

    All public methods return a ``dict`` with at least ``llm_used: bool``.
    When ``config.enabled is False`` (or on provider error) the methods
    return a *fallback* dict so callers never need to handle exceptions.
    """

    config: LLMClientConfig = field(default_factory=LLMClientConfig.from_env)

    # -- low-level ----------------------------------------------------------

    def _chat(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str | None:
        """Send one chat-completion request.  Returns assistant text or ``None``."""
        if not self.config.enabled:
            return None
        payload: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_completion_tokens": max_tokens or self.config.max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(
                base_url=self.config.base_url,
                timeout=self.config.timeout,
            ) as client:
                r = client.post(
                    "/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )
                r.raise_for_status()
                data = r.json()
                choices = data.get("choices") or []
                if choices:
                    msg = choices[0].get("message") or {}
                    # Reasoning models (e.g. GPT-oss-120b / DeepSeek-R1) may
                    # put output in ``reasoning_content`` and leave ``content``
                    # empty.  Prefer ``content``; fall back to ``reasoning_content``.
                    text = (msg.get("content") or "").strip()
                    if not text:
                        text = (msg.get("reasoning_content") or "").strip()
                    return text or None
                return None
        except Exception:
            # Log without leaking secrets (no headers / key)
            log.exception("LLM request failed — fallback to rule-based")
            return None

    # -- public high-level methods ------------------------------------------

    def generate_qna_answer(
        self,
        question: str,
        context_summary: str,
        regulation_refs: list[str] | None = None,
        existing_reasoning: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Answer a free-form VN accounting question via LLM.

        Returns ``{"answer": str, "llm_used": True, ...}``
        or ``None`` when LLM is disabled / errors out.
        """
        regs_text = "\n".join(f"- {r}" for r in regulation_refs) if regulation_refs else "(không)"
        existing_steps = ""
        if existing_reasoning and existing_reasoning.get("steps"):
            existing_steps = "\nCác bước suy luận hiện có:\n" + "\n".join(
                f"  {i+1}. {s}" for i, s in enumerate(existing_reasoning["steps"])
            )

        system = (
            "Bạn là trợ lý kế toán Việt Nam chuyên nghiệp.  Trả lời bằng tiếng Việt, "
            "ngắn gọn, chính xác.  Luôn dẫn chiếu văn bản pháp quy nếu liên quan.  "
            "Không bịa số liệu — nếu không biết, nói rõ."
        )
        user_prompt = (
            f"Câu hỏi: {question}\n\n"
            f"Dữ liệu ngữ cảnh:\n{context_summary}\n\n"
            f"Văn bản pháp quy liên quan:\n{regs_text}"
            f"{existing_steps}"
        )

        answer = self._chat(system=system, user=user_prompt)
        if answer is None:
            return None

        return {
            "answer": answer,
            "llm_used": True,
            "model": self.config.model_label or "unknown",
            "used_models": [self.config.model_label or "unknown"],
        }

    def refine_journal_suggestion(
        self,
        voucher: dict[str, Any],
        rule_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Ask LLM to review / refine a rule-based journal suggestion.

        Returns refined dict with ``llm_used=True`` or ``None``.
        """
        system = (
            "Bạn là trợ lý kế toán VN.  Cho một chứng từ (voucher) và kết quả phân loại "
            "rule-based, hãy xác nhận hoặc đề xuất sửa tài khoản Nợ/Có.  "
            "Trả lời JSON thuần: "
            '{"debit_account":"...", "debit_name":"...", "credit_account":"...", '
            '"credit_name":"...", "confidence":0.xx, "reasoning":"..."}. '
            "Luôn tuân thủ TT200/TT133.  Không bịa tài khoản."
        )
        user_prompt = (
            f"Chứng từ:\n  loại: {voucher.get('voucher_type')}\n"
            f"  số tiền: {voucher.get('amount')} {voucher.get('currency', 'VND')}\n"
            f"  mô tả: {voucher.get('description')}\n\n"
            f"Kết quả rule-based hiện tại:\n"
            f"  Nợ TK {rule_result.get('debit_account')} ({rule_result.get('debit_name')})\n"
            f"  Có TK {rule_result.get('credit_account')} ({rule_result.get('credit_name')})\n"
            f"  confidence: {rule_result.get('confidence')}\n"
            f"  reasoning: {rule_result.get('reasoning')}"
        )

        raw = self._chat(system=system, user=user_prompt, max_tokens=256)
        if raw is None:
            return None

        # Try to parse JSON from LLM response
        import json as _json

        try:
            parsed = _json.loads(raw)
            parsed["llm_used"] = True
            parsed["model"] = self.config.model_label or "unknown"
            # Ensure required keys
            for k in ("debit_account", "credit_account", "confidence", "reasoning"):
                if k not in parsed:
                    log.warning("LLM journal response thiếu key '%s' → fallback", k)
                    return None
            return parsed
        except _json.JSONDecodeError:
            log.warning("LLM trả về không phải JSON → fallback rule-based")
            return None

    def explain_soft_check_issues(
        self,
        issues_summary: list[dict[str, str]],
    ) -> dict[str, list[str]] | None:
        """Generate user-friendly VN explanations for soft-check issues.

        ``issues_summary`` — list of ``{"code": ..., "message": ...}``.
        Returns ``{"explanations": [...], "llm_used": True}`` or ``None``.
        """
        if not issues_summary:
            return None

        bullet_list = "\n".join(
            f"- [{iss['code']}] {iss['message']}" for iss in issues_summary
        )
        system = (
            "Bạn là trợ lý kế toán VN.  Với danh sách lỗi / cảnh báo từ kiểm tra mềm, "
            "hãy giải thích ngắn gọn từng lỗi bằng tiếng Việt cho người dùng cuối "
            "(không phải lập trình viên).  Trả về dạng dấu gạch đầu dòng."
        )
        user_prompt = f"Danh sách lỗi kiểm tra mềm:\n{bullet_list}"

        raw = self._chat(system=system, user=user_prompt, max_tokens=1024)
        if raw is None:
            return None

        lines = [ln.lstrip("- ").strip() for ln in raw.split("\n") if ln.strip()]
        return {
            "explanations": lines,
            "llm_used": True,
            "model": self.config.model_label or "unknown",
        }


# ---------------------------------------------------------------------------
# Module-level singleton (lazy)
# ---------------------------------------------------------------------------

_DEFAULT_CLIENT: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Return the module-level LLM client singleton (lazy init)."""
    global _DEFAULT_CLIENT  # noqa: PLW0603
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = LLMClient()
    return _DEFAULT_CLIENT


def reset_llm_client() -> None:
    """Force re-creation of the singleton on next access.

    Call this after changing env vars (e.g. in test harnesses) so the
    client re-reads ``USE_REAL_LLM`` / ``DO_AGENT_*`` from the new env.
    """
    global _DEFAULT_CLIENT  # noqa: PLW0603
    _DEFAULT_CLIENT = None
