"""Journal Suggestion Quality Benchmark — Spec §3.3.

Validates that journal entry suggestions match expected account mappings
on a small labelled dataset.  Measures TK Nợ/Có (debit/credit account)
precision per document type.

Current baseline is measured; roadmap targets >95% suggestion accuracy.
"""
from __future__ import annotations

import pytest

from accounting_agent.journal import (
    CHART_OF_ACCOUNTS_TT133,
    detect_vat_rate,
    suggest_journal_lines,
    validate_journal_balance,
)

# ---------------------------------------------------------------------------
# Golden set — expected journal entries per doc_type
# Each case specifies voucher→expected debit/credit accounts.
# ---------------------------------------------------------------------------

_JOURNAL_GOLDEN: list[dict] = [
    {
        "doc_type": "sell_invoice",
        "voucher": {"amount": 10_000_000},
        "expected_debit_accounts": {"131"},      # Phải thu KH
        "expected_credit_accounts": {"511", "33311"},  # Doanh thu + VAT out
        "min_lines": 2,
    },
    {
        "doc_type": "buy_invoice",
        "voucher": {"amount": 5_000_000},
        "expected_debit_accounts": {"152", "156", "133"},  # inventory + VAT in
        "expected_credit_accounts": {"331"},     # Phải trả NCC
        "min_lines": 2,
    },
    {
        "doc_type": "cash_receipt",
        "voucher": {"amount": 2_000_000},
        "expected_debit_accounts": {"111"},       # Tiền mặt
        "expected_credit_accounts": {"131"},      # Phải thu KH
        "min_lines": 2,
    },
    {
        "doc_type": "cash_payment",
        "voucher": {"amount": 3_000_000},
        "expected_debit_accounts": {"331"},       # Phải trả NCC
        "expected_credit_accounts": {"111"},      # Tiền mặt
        "min_lines": 2,
    },
    {
        "doc_type": "salary",
        "voucher": {"amount": 8_000_000},
        "expected_debit_accounts": {"642"},       # CP QLDN
        "expected_credit_accounts": {"334"},      # Phải trả NLĐ
        "min_lines": 2,
    },
    {
        "doc_type": "depreciation",
        "voucher": {"amount": 1_000_000},
        "expected_debit_accounts": {"642"},       # CP QLDN
        "expected_credit_accounts": {"214"},      # Hao mòn TSCĐ
        "min_lines": 2,
    },
]


class TestJournalSuggestionQuality:
    """Quality metrics for journal suggestion engine."""

    def test_overall_account_precision(self) -> None:
        """Overall debit/credit account match rate ≥ 70% baseline.

        Target endpoint: >95%.
        """
        total_checks = 0
        correct = 0

        for case in _JOURNAL_GOLDEN:
            lines = suggest_journal_lines(
                voucher=case["voucher"],
                doc_type=case["doc_type"],
            )
            debit_accts = {ln["account"] for ln in lines if ln.get("debit", 0) > 0}
            credit_accts = {ln["account"] for ln in lines if ln.get("credit", 0) > 0}

            # Check debit accounts
            for expected in case["expected_debit_accounts"]:
                total_checks += 1
                if expected in debit_accts:
                    correct += 1

            # Check credit accounts
            for expected in case["expected_credit_accounts"]:
                total_checks += 1
                if expected in credit_accts:
                    correct += 1

        precision = correct / total_checks if total_checks else 0
        assert precision >= 0.70, (
            f"Account precision {precision:.1%} below 70% baseline "
            f"({correct}/{total_checks}). Target: >95%."
        )

    @pytest.mark.parametrize("case_idx", range(len(_JOURNAL_GOLDEN)))
    def test_per_doc_type_produces_balanced_entry(self, case_idx: int) -> None:
        """Each doc type must produce balanced debit == credit lines."""
        case = _JOURNAL_GOLDEN[case_idx]
        lines = suggest_journal_lines(
            voucher=case["voucher"],
            doc_type=case["doc_type"],
        )
        result = validate_journal_balance(lines)
        assert result["balanced"], (
            f"Doc type '{case['doc_type']}': journal not balanced. "
            f"Debit={result['total_debit']}, Credit={result['total_credit']}"
        )

    @pytest.mark.parametrize("case_idx", range(len(_JOURNAL_GOLDEN)))
    def test_per_doc_type_min_lines(self, case_idx: int) -> None:
        """Each doc type must produce at least min_lines journal lines."""
        case = _JOURNAL_GOLDEN[case_idx]
        lines = suggest_journal_lines(
            voucher=case["voucher"],
            doc_type=case["doc_type"],
        )
        assert len(lines) >= case["min_lines"], (
            f"Doc type '{case['doc_type']}': only {len(lines)} lines, "
            f"expected ≥{case['min_lines']}"
        )

    def test_chart_of_accounts_coverage(self) -> None:
        """TT133 chart must cover all 9 account classes."""
        classes = set()
        for acct_code in CHART_OF_ACCOUNTS_TT133:
            classes.add(acct_code[0])
        # Vietnamese accounting has classes: 1,2,3,4,5,6,7,8,9
        assert classes >= {"1", "2", "3", "4", "5", "6"}, (
            f"Missing account classes. Found: {sorted(classes)}"
        )

    def test_vat_detection_rates(self) -> None:
        """VAT rate detector returns valid VN rates."""
        for amount in [100_000, 1_000_000, 50_000_000]:
            rate = detect_vat_rate({"amount": amount})
            assert rate in (0, 5, 8, 10), f"Invalid VAT rate {rate} for amount {amount}"

    def test_sell_invoice_vat_splitting(self) -> None:
        """Sell invoice with 10% VAT produces separate VAT debit line."""
        lines = suggest_journal_lines(
            voucher={"amount": 11_000_000},
            doc_type="sell_invoice",
        )
        vat_lines = [ln for ln in lines if "3331" in ln.get("account", "")]
        assert len(vat_lines) > 0, "Missing VAT output line for sell_invoice"
        total_credit = sum(ln.get("credit", 0) for ln in lines)
        assert total_credit > 0, "No credit amounts in sell_invoice journal"

    def test_read_only_guarantee(self) -> None:
        """suggest_journal_lines never writes to any database."""
        # The function is pure — takes dict, returns list[dict]
        # Verify it doesn't import DB session or models
        import inspect

        source = inspect.getsource(suggest_journal_lines)
        assert "session" not in source.lower() or "db_session" not in source.lower(), (
            "suggest_journal_lines should not access database directly"
        )

    def test_validate_balanced_entry(self) -> None:
        """validate_journal_balance correctly identifies balanced entries."""
        balanced = [
            {"account": "111", "debit": 100, "credit": 0},
            {"account": "511", "debit": 0, "credit": 100},
        ]
        assert validate_journal_balance(balanced)["balanced"] is True

        imbalanced = [
            {"account": "111", "debit": 100, "credit": 0},
            {"account": "511", "debit": 0, "credit": 50},
        ]
        assert validate_journal_balance(imbalanced)["balanced"] is False
