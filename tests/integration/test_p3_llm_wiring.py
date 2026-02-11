"""Tests for LLM client module and flow wiring.

All tests run with ``USE_REAL_LLM=false`` (default) — no HTTP calls.
Tests that verify the LLM *branch* use a **stub** client (patched).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# LLMClientConfig
# ---------------------------------------------------------------------------

class TestLLMClientConfig:
    """Env-driven config construction."""

    def test_default_disabled(self, monkeypatch):
        """Without any env → enabled=False."""
        monkeypatch.delenv("USE_REAL_LLM", raising=False)
        monkeypatch.delenv("DO_AGENT_BASE_URL", raising=False)
        monkeypatch.delenv("DO_AGENT_API_KEY", raising=False)

        from openclaw_agent.llm.client import LLMClientConfig
        cfg = LLMClientConfig.from_env()
        assert cfg.enabled is False

    def test_enabled_when_all_vars_present(self, monkeypatch):
        """With USE_REAL_LLM=true + both DO_AGENT vars → enabled."""
        monkeypatch.setenv("USE_REAL_LLM", "true")
        monkeypatch.setenv("DO_AGENT_BASE_URL", "https://example.com")
        monkeypatch.setenv("DO_AGENT_API_KEY", "secret-key")
        monkeypatch.setenv("DO_AGENT_MODEL", "test-model")

        from openclaw_agent.llm.client import LLMClientConfig
        cfg = LLMClientConfig.from_env()
        assert cfg.enabled is True
        assert cfg.base_url == "https://example.com"
        assert cfg.model_label == "test-model"

    def test_disabled_when_key_missing(self, monkeypatch):
        """USE_REAL_LLM=true but no key → disabled (soft fail)."""
        monkeypatch.setenv("USE_REAL_LLM", "true")
        monkeypatch.setenv("DO_AGENT_BASE_URL", "https://example.com")
        monkeypatch.delenv("DO_AGENT_API_KEY", raising=False)

        from openclaw_agent.llm.client import LLMClientConfig
        cfg = LLMClientConfig.from_env()
        assert cfg.enabled is False

    def test_disabled_when_url_missing(self, monkeypatch):
        """USE_REAL_LLM=true but no URL → disabled."""
        monkeypatch.setenv("USE_REAL_LLM", "true")
        monkeypatch.delenv("DO_AGENT_BASE_URL", raising=False)
        monkeypatch.setenv("DO_AGENT_API_KEY", "secret-key")

        from openclaw_agent.llm.client import LLMClientConfig
        cfg = LLMClientConfig.from_env()
        assert cfg.enabled is False

    def test_custom_timeout_and_tokens(self, monkeypatch):
        """Custom LLM_TIMEOUT and LLM_MAX_TOKENS are parsed."""
        monkeypatch.setenv("USE_REAL_LLM", "true")
        monkeypatch.setenv("DO_AGENT_BASE_URL", "https://example.com")
        monkeypatch.setenv("DO_AGENT_API_KEY", "k")
        monkeypatch.setenv("LLM_TIMEOUT", "42")
        monkeypatch.setenv("LLM_MAX_TOKENS", "1024")
        monkeypatch.setenv("LLM_TEMPERATURE", "0.5")

        from openclaw_agent.llm.client import LLMClientConfig
        cfg = LLMClientConfig.from_env()
        assert cfg.timeout == 42.0
        assert cfg.max_tokens == 1024
        assert cfg.temperature == 0.5


# ---------------------------------------------------------------------------
# LLMClient stub tests (no HTTP)
# ---------------------------------------------------------------------------

class TestLLMClientMethods:
    """Verify public methods return expected shapes via stub."""

    @pytest.fixture
    def _stub_client(self):
        """Return an LLMClient whose _chat is patched."""
        from openclaw_agent.llm.client import LLMClient, LLMClientConfig

        cfg = LLMClientConfig(
            enabled=True,
            base_url="http://stub",
            api_key="stub-key",
            model_label="stub-model",
        )
        client = LLMClient(config=cfg)
        return client

    def test_generate_qna_answer_with_stub(self, _stub_client):
        """generate_qna_answer returns dict with llm_used=True."""
        with patch.object(_stub_client, "_chat", return_value="Đây là câu trả lời mẫu."):
            result = _stub_client.generate_qna_answer(
                question="Tại sao TK 131 được dùng?",
                context_summary="Chứng từ bán hàng",
                regulation_refs=["TT200"],
            )
        assert result is not None
        assert result["llm_used"] is True
        assert "câu trả lời" in result["answer"]

    def test_generate_qna_answer_disabled(self, monkeypatch):
        """When config.enabled=False → returns None."""
        from openclaw_agent.llm.client import LLMClient, LLMClientConfig

        cfg = LLMClientConfig(enabled=False)
        client = LLMClient(config=cfg)
        result = client.generate_qna_answer("test", "ctx")
        assert result is None

    def test_refine_journal_suggestion_valid_json(self, _stub_client):
        """refine_journal_suggestion parses LLM JSON response."""
        llm_response = json.dumps({
            "debit_account": "131",
            "debit_name": "Phải thu KH",
            "credit_account": "511",
            "credit_name": "Doanh thu",
            "confidence": 0.95,
            "reasoning": "TT200 điều X",
        })
        with patch.object(_stub_client, "_chat", return_value=llm_response):
            result = _stub_client.refine_journal_suggestion(
                {"voucher_type": "sell_invoice", "amount": 10_000_000},
                {"debit_account": "131", "credit_account": "511", "confidence": 0.92, "reasoning": "rb"},
            )
        assert result is not None
        assert result["llm_used"] is True
        assert result["confidence"] == 0.95

    def test_refine_journal_suggestion_garbage(self, _stub_client):
        """Non-JSON response → returns None (fallback)."""
        with patch.object(_stub_client, "_chat", return_value="Xin lỗi, tôi không hiểu"):
            result = _stub_client.refine_journal_suggestion(
                {"voucher_type": "other", "amount": 100},
                {"debit_account": "642", "credit_account": "111", "confidence": 0.55, "reasoning": "rb"},
            )
        assert result is None  # fallback

    def test_explain_soft_check_issues(self, _stub_client):
        """explain_soft_check_issues returns explanations list."""
        with patch.object(_stub_client, "_chat", return_value="- Thiếu file đính kèm\n- Mất cân đối"):
            result = _stub_client.explain_soft_check_issues([
                {"code": "MISSING_ATTACHMENT", "message": "Chứng từ X thiếu file"},
                {"code": "JOURNAL_IMBALANCED", "message": "Bút toán Y mất cân đối"},
            ])
        assert result is not None
        assert result["llm_used"] is True
        assert len(result["explanations"]) == 2

    def test_explain_soft_check_empty_list(self, _stub_client):
        """Empty issues → None."""
        result = _stub_client.explain_soft_check_issues([])
        assert result is None


# ---------------------------------------------------------------------------
# Flow wiring tests (USE_REAL_LLM patched, no HTTP)
# ---------------------------------------------------------------------------

class TestQnaLLMWiring:
    """Verify qna_accounting uses LLM path when _USE_REAL_LLM=True."""

    def test_qna_llm_fallback_path(self, monkeypatch):
        """When USE_REAL_LLM=false, default fallback is used (no LLM).

        Fallback now provides TT133 context when available, or 'Xin lỗi' otherwise.
        """
        monkeypatch.setenv("USE_REAL_LLM", "false")
        import openclaw_agent.flows.qna_accounting as qna_mod

        mock_session = MagicMock()
        result = qna_mod.answer_question(mock_session, "Câu hỏi ngẫu nhiên không khớp handler nào?")
        assert "llm_used" not in result or result.get("llm_used") is not True
        # Fallback provides TT133 context or "Xin lỗi" guidance
        assert "TT133" in result["answer"] or "Xin lỗi" in result["answer"]

    def test_qna_llm_active_path(self, monkeypatch):
        """When LLM is active and no handler matches → LLM branch fires."""
        monkeypatch.setenv("USE_REAL_LLM", "true")
        import openclaw_agent.flows.qna_accounting as qna_mod

        fake_llm_result = {
            "answer": "Theo TT200 điều 42...",
            "llm_used": True,
            "model": "test",
        }
        with patch.object(qna_mod, "_try_llm_answer", return_value=fake_llm_result):
            mock_session = MagicMock()
            result = qna_mod.answer_question(mock_session, "Random question not matching any handler")

        assert result["llm_used"] is True
        assert "TT200" in result["answer"]

    def test_qna_llm_error_falls_through(self, monkeypatch):
        """LLM error → falls through to default help text (TT133 context or Xin lỗi)."""
        monkeypatch.setenv("USE_REAL_LLM", "true")
        import openclaw_agent.flows.qna_accounting as qna_mod

        with patch.object(qna_mod, "_try_llm_answer", return_value=None):
            mock_session = MagicMock()
            result = qna_mod.answer_question(mock_session, "Unknown question that triggers nothing")

        assert "TT133" in result["answer"] or "Xin lỗi" in result["answer"]

    def test_qna_no_reasoning_chain_in_result(self, monkeypatch):
        """Handler results must NOT contain reasoning_chain (stripped for UI/API)."""
        monkeypatch.setenv("USE_REAL_LLM", "false")
        import openclaw_agent.flows.qna_accounting as qna_mod

        mock_session = MagicMock()
        result = qna_mod.answer_question(mock_session, "Bất kỳ câu hỏi nào")
        assert "reasoning_chain" not in result

    def test_po_benchmark_matcher_q1(self, monkeypatch):
        """PO benchmark Q1 (131 vs 331) matches template."""
        monkeypatch.setenv("USE_REAL_LLM", "false")
        import openclaw_agent.flows.qna_accounting as qna_mod

        mock_session = MagicMock()
        result = qna_mod.answer_question(mock_session, "So sánh TK 131 vs 331")
        assert "131" in result["answer"]
        assert "331" in result["answer"]
        assert "Nợ" in result["answer"]
        assert "Có" in result["answer"]

    def test_po_benchmark_matcher_q2(self, monkeypatch):
        """PO benchmark Q2 (642 vs 641) matches template."""
        monkeypatch.setenv("USE_REAL_LLM", "false")
        import openclaw_agent.flows.qna_accounting as qna_mod

        mock_session = MagicMock()
        result = qna_mod.answer_question(mock_session, "Khi nào dùng TK 642 thay vì TK 641")
        assert "641" in result["answer"]
        assert "642" in result["answer"]
        assert "VND" in result["answer"]

    def test_po_benchmark_matcher_q3(self, monkeypatch):
        """PO benchmark Q3 (khấu hao TSCĐ) matches template."""
        monkeypatch.setenv("USE_REAL_LLM", "false")
        import openclaw_agent.flows.qna_accounting as qna_mod

        mock_session = MagicMock()
        result = qna_mod.answer_question(mock_session, "Khấu hao TSCĐ hữu hình 30 triệu VND trong 3 năm theo phương pháp đường thẳng")
        assert "214" in result["answer"]
        assert "VND" in result["answer"]
        assert "10.000.000" in result["answer"]

    def test_quality_guardrail_rejects_inner_monologue(self):
        """Quality guardrail rejects answers containing inner monologue."""
        from openclaw_agent.flows.qna_accounting import _passes_quality_guardrail

        assert _passes_quality_guardrail("Better: Use TK 214 for depreciation") is False
        assert _passes_quality_guardrail("I think we should use account 131") is False
        assert _passes_quality_guardrail("Let's recall the accounting rules") is False
        assert _passes_quality_guardrail("Hmm, not sure about this") is False

    def test_quality_guardrail_accepts_good_answer(self):
        """Quality guardrail accepts well-formed Vietnamese answers."""
        from openclaw_agent.flows.qna_accounting import _passes_quality_guardrail

        good = (
            "TK 131 – Phải thu của khách hàng: phản ánh số tiền khách hàng còn nợ DN. "
            "Bút toán mẫu: Nợ TK 131 / Có TK 511 – 50.000.000 VND."
        )
        assert _passes_quality_guardrail(good) is True

    def test_quality_guardrail_rejects_generic_fallback(self):
        """Quality guardrail rejects generic apology responses."""
        from openclaw_agent.flows.qna_accounting import _passes_quality_guardrail

        assert _passes_quality_guardrail("Xin lỗi, tôi cần thêm thông tin ngữ cảnh") is False
        assert _passes_quality_guardrail("Xin lỗi, hệ thống chưa thể đưa ra câu trả lời") is False


# ---------------------------------------------------------------------------
# Golden tests – 3 benchmark Q&A questions (stub LLM)
# ---------------------------------------------------------------------------

class TestQnaGoldenBenchmark:
    """Golden answers for the 3 PO benchmark questions (using stub LLM)."""

    @pytest.fixture(autouse=True)
    def _enable_llm(self, monkeypatch):
        monkeypatch.setenv("USE_REAL_LLM", "true")

    def _run_with_stub(self, question: str, stub_answer: str) -> dict:
        import openclaw_agent.flows.qna_accounting as qna_mod

        fake_result = {
            "answer": stub_answer,
            "llm_used": True,
            "model": "golden-stub",
        }
        with patch.object(qna_mod, "_try_llm_answer", return_value=fake_result):
            mock_session = MagicMock()
            return qna_mod.answer_question(mock_session, question)

    def test_golden_q1_tk131_vs_tk331(self):
        """PO Q1: So sánh TK 131 vs TK 331 — must mention both accounts, Nợ/Có."""
        answer_text = (
            "TK 131 – Phải thu của khách hàng: phản ánh khoản phải thu, số dư bên Nợ.\n"
            "Bút toán mẫu: Nợ TK 131 / Có TK 511 — ghi nhận doanh thu 50.000.000 VND.\n\n"
            "TK 331 – Phải trả cho người bán: phản ánh khoản phải trả, số dư bên Có.\n"
            "Bút toán mẫu: Nợ TK 152 / Có TK 331 — mua NVL 30.000.000 VND.\n\n"
            "Căn cứ: TT200/2014/TT-BTC."
        )
        result = self._run_with_stub("So sánh TK 131 vs 331", answer_text)
        assert result["llm_used"] is True
        assert "131" in result["answer"]
        assert "331" in result["answer"]
        assert "Nợ" in result["answer"]
        assert "Có" in result["answer"]
        assert "VND" in result["answer"]
        assert "reasoning_chain" not in result

    def test_golden_q2_tk642_vs_tk641(self):
        """PO Q2: Khi nào dùng TK 642 thay vì 641 — must distinguish selling vs admin."""
        answer_text = (
            "TK 641 – Chi phí bán hàng: dùng cho các chi phí phát sinh trong quá trình "
            "tiêu thụ sản phẩm, hàng hoá, dịch vụ (lương nhân viên bán hàng, vận chuyển, "
            "hoa hồng…).\n\n"
            "TK 642 – Chi phí quản lý doanh nghiệp: dùng cho các chi phí quản lý chung "
            "của doanh nghiệp (lương ban giám đốc, văn phòng phẩm, khấu hao TSCĐ dùng "
            "chung…).\n\n"
            "Ví dụ: Chi lương nhân viên kinh doanh → Nợ TK 641 / Có TK 334: 15.000.000 VND.\n"
            "Chi lương kế toán trưởng → Nợ TK 642 / Có TK 334: 20.000.000 VND.\n\n"
            "Căn cứ: TT200/2014/TT-BTC."
        )
        result = self._run_with_stub("Khi nào dùng TK 642 thay vì 641", answer_text)
        assert result["llm_used"] is True
        assert "641" in result["answer"]
        assert "642" in result["answer"]
        assert "VND" in result["answer"]
        assert "reasoning_chain" not in result

    def test_golden_q3_depreciation(self):
        """PO Q3: Khấu hao TSCĐ 30 triệu / 3 năm — must show TK 211/214, calculation."""
        answer_text = (
            "Tài sản cố định nguyên giá 30.000.000 VND, thời gian sử dụng 3 năm.\n"
            "Phương pháp khấu hao đường thẳng: mức khấu hao hàng năm = "
            "30.000.000 / 3 = 10.000.000 VND/năm, tương đương khoảng "
            "833.333 VND/tháng.\n\n"
            "Bút toán hạch toán hàng tháng:\n"
            "Nợ TK 642 (hoặc 627/641 tuỳ bộ phận sử dụng): 833.333 VND\n"
            "Có TK 214 – Hao mòn TSCĐ: 833.333 VND\n\n"
            "TK 211 – Tài sản cố định hữu hình: ghi nhận nguyên giá ban đầu.\n"
            "TK 214 – Hao mòn TSCĐ: lũy kế giá trị khấu hao.\n\n"
            "Căn cứ: TT200/2014/TT-BTC, Điều 35-38."
        )
        result = self._run_with_stub("Khấu hao TSCĐ 30 triệu/3 năm", answer_text)
        assert result["llm_used"] is True
        assert "211" in result["answer"] or "214" in result["answer"]
        assert "VND" in result["answer"]
        assert "khấu hao" in result["answer"].lower() or "833" in result["answer"]
        assert "reasoning_chain" not in result


class TestJournalLLMWiring:
    """Verify journal_suggestion LLM refinement path."""

    def test_classify_without_llm(self, monkeypatch):
        """Default (USE_REAL_LLM=false) → rule-based, llm_used=False."""
        monkeypatch.setenv("USE_REAL_LLM", "false")
        import openclaw_agent.flows.journal_suggestion as js_mod

        result = js_mod._classify_voucher({
            "voucher_type": "sell_invoice",
            "amount": 10_000_000,
            "has_attachment": True,
        })
        assert result["llm_used"] is False
        assert result["debit_account"] == "131"

    def test_classify_with_llm_refinement(self, monkeypatch):
        """When LLM is enabled + returns valid JSON → merged."""
        monkeypatch.setenv("USE_REAL_LLM", "true")
        import openclaw_agent.flows.journal_suggestion as js_mod

        refined = {
            "debit_account": "131",
            "debit_name": "Phải thu (LLM)",
            "credit_account": "511",
            "credit_name": "Doanh thu (LLM)",
            "confidence": 0.97,
            "reasoning": "LLM refined",
            "llm_used": True,
            "model": "stub",
        }
        fake_client = MagicMock()
        fake_client.refine_journal_suggestion.return_value = refined

        with patch("openclaw_agent.llm.client.get_llm_client", return_value=fake_client):
            result = js_mod._classify_voucher({
                "voucher_type": "sell_invoice",
                "amount": 10_000_000,
                "has_attachment": True,
            })
        assert result["llm_used"] is True
        assert result["confidence"] == 0.97
        assert "LLM" in result["reasoning"]
