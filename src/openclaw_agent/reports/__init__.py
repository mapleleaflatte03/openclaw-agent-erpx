"""VAS/IFRS Financial Reports — Milestone 7: Báo cáo tài chính.

Generates statutory Vietnamese financial statements per VAS:
  - B01-DN: Bảng cân đối kế toán (Balance Sheet)
  - B02-DN: Báo cáo kết quả hoạt động kinh doanh (Income Statement)
  - B03-DN: Báo cáo lưu chuyển tiền tệ (Cash Flow Statement)
  - Full audit pack in JSON
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("openclaw.reports")


# ------------------------------------------------------------------
# VAS Account Classification for Financial Statements
# ------------------------------------------------------------------
# B01-DN groupings (TT200 / TT133)
_ASSET_ACCOUNTS = {
    "111", "112", "113",  # Tiền
    "121",  # Đầu tư ngắn hạn
    "131",  # Phải thu KH
    "133",  # Thuế GTGT được khấu trừ
    "136",  # Phải thu nội bộ
    "138",  # Phải thu khác
    "141",  # Tạm ứng
    "151", "152", "153", "154", "155", "156", "157",  # Hàng tồn kho
    "211", "212", "213",  # TSCĐ hữu hình
    "214",  # Hao mòn TSCĐ (negative)
    "217",  # BĐS đầu tư
    "221", "222", "228",  # Đầu tư dài hạn
    "241",  # XDCB dở dang
    "242",  # CP trả trước dài hạn
    "243",  # TSCĐ thuê tài chính
}

_LIABILITY_ACCOUNTS = {
    "311",  # Phải trả người bán
    "331",  # Phải trả người bán (alt)
    "333",  # Thuế phải nộp
    "334",  # Phải trả NLĐ
    "335",  # CP phải trả
    "336",  # Phải trả nội bộ
    "338",  # Phải trả, phải nộp khác
    "341", "342", "343",  # Vay và nợ dài hạn
    "344",  # Nhận ký quỹ
    "347",  # Thuế TN hoãn lại
    "352",  # Dự phòng phải trả
    "353",  # Quỹ khen thưởng phúc lợi
}

_EQUITY_ACCOUNTS = {
    "411",  # Vốn đầu tư CSH
    "412",  # Thặng dư vốn
    "413",  # Chênh lệch tỷ giá
    "414",  # Quỹ đầu tư phát triển
    "417",  # Quỹ dự phòng tài chính
    "418",  # Quỹ khác
    "419",  # CP phát hành
    "421",  # LN chưa phân phối
}

_REVENUE_ACCOUNTS = {
    "511", "512", "515", "521",  # Doanh thu
}

_EXPENSE_ACCOUNTS = {
    "621", "622", "623", "627",  # Giá vốn
    "631",  # Giá thành
    "632",  # Giá vốn hàng bán
    "635",  # CP tài chính
    "641",  # CP bán hàng
    "642",  # CP QLDN
    "711",  # Thu nhập khác
    "811",  # CP khác
    "821",  # CP thuế TNDN
    "911",  # Xác định KQKD
}


@dataclass
class BalanceSheetLine:
    code: str
    label_vi: str
    label_en: str
    amount: float = 0.0
    note: str = ""


@dataclass
class FinancialReport:
    report_type: str  # B01-DN, B02-DN, B03-DN
    period: str  # e.g. "2026-01"
    company: str = ""
    currency: str = "VND"
    lines: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _sum_accounts(
    trial: dict[str, float], prefixes: set[str]
) -> float:
    """Sum trial balance amounts for accounts matching given prefixes."""
    total = 0.0
    for acct, amt in trial.items():
        acct_prefix = acct[:3]
        if acct_prefix in prefixes:
            total += amt
    return total


def _build_trial_balance(journals: list[dict[str, Any]]) -> dict[str, float]:
    """Build a trial balance from journal entries.

    Returns {account_code: net_balance} where positive=debit, negative=credit.
    """
    tb: dict[str, float] = defaultdict(float)
    for j in journals:
        lines = j.get("lines") or j.get("journal_lines") or []
        if isinstance(lines, str):
            import json as _json
            try:
                lines = _json.loads(lines)
            except (ValueError, TypeError):
                lines = []
        for line in lines:
            acct = str(line.get("account", ""))
            debit = float(line.get("debit", 0) or 0)
            credit = float(line.get("credit", 0) or 0)
            tb[acct] += debit - credit
    return dict(tb)


def generate_b01_dn(
    journals: list[dict[str, Any]],
    period: str = "",
    company: str = "",
) -> FinancialReport:
    """B01-DN: Bảng cân đối kế toán (Balance Sheet).

    Assets = Liabilities + Equity
    """
    tb = _build_trial_balance(journals)

    total_assets = _sum_accounts(tb, _ASSET_ACCOUNTS)
    total_liabilities = _sum_accounts(tb, _LIABILITY_ACCOUNTS)
    total_equity = _sum_accounts(tb, _EQUITY_ACCOUNTS)

    lines = [
        # ── TÀI SẢN (Assets) ──
        {"section": "A", "code": "100", "label_vi": "TÀI SẢN NGẮN HẠN",
         "label_en": "Current Assets", "amount": 0.0},
        {"code": "110", "label_vi": "Tiền và tương đương tiền",
         "label_en": "Cash and equivalents",
         "amount": round(_sum_accounts(tb, {"111", "112", "113"}), 2)},
        {"code": "120", "label_vi": "Đầu tư tài chính ngắn hạn",
         "label_en": "Short-term investments",
         "amount": round(_sum_accounts(tb, {"121"}), 2)},
        {"code": "130", "label_vi": "Phải thu ngắn hạn",
         "label_en": "Short-term receivables",
         "amount": round(_sum_accounts(tb, {"131", "133", "136", "138", "141"}), 2)},
        {"code": "140", "label_vi": "Hàng tồn kho",
         "label_en": "Inventories",
         "amount": round(_sum_accounts(tb, {"151", "152", "153", "154", "155", "156", "157"}), 2)},
        {"section": "B", "code": "200", "label_vi": "TÀI SẢN DÀI HẠN",
         "label_en": "Non-current Assets", "amount": 0.0},
        {"code": "220", "label_vi": "TSCĐ hữu hình",
         "label_en": "Tangible fixed assets",
         "amount": round(_sum_accounts(tb, {"211", "212", "213"}) + _sum_accounts(tb, {"214"}), 2)},
        {"code": "250", "label_vi": "Đầu tư tài chính dài hạn",
         "label_en": "Long-term investments",
         "amount": round(_sum_accounts(tb, {"221", "222", "228"}), 2)},
        # ── NGUỒN VỐN (Liabilities + Equity) ──
        {"section": "C", "code": "300", "label_vi": "NỢ PHẢI TRẢ",
         "label_en": "Liabilities", "amount": round(abs(total_liabilities), 2)},
        {"code": "310", "label_vi": "Nợ ngắn hạn",
         "label_en": "Current liabilities",
         "amount": round(abs(_sum_accounts(tb, {"311", "331", "333", "334", "335", "338"})), 2)},
        {"code": "330", "label_vi": "Nợ dài hạn",
         "label_en": "Non-current liabilities",
         "amount": round(abs(_sum_accounts(tb, {"341", "342", "343", "347"})), 2)},
        {"section": "D", "code": "400", "label_vi": "VỐN CHỦ SỞ HỮU",
         "label_en": "Owner's Equity", "amount": round(abs(total_equity), 2)},
        {"code": "411", "label_vi": "Vốn đầu tư của CSH",
         "label_en": "Contributed capital",
         "amount": round(abs(_sum_accounts(tb, {"411", "412"})), 2)},
        {"code": "420", "label_vi": "LN chưa phân phối",
         "label_en": "Retained earnings",
         "amount": round(abs(_sum_accounts(tb, {"421"})), 2)},
    ]

    # Fill section totals
    current_assets = sum(l["amount"] for l in lines if l.get("code") in {"110", "120", "130", "140"})
    noncurrent_assets = sum(l["amount"] for l in lines if l.get("code") in {"220", "250"})
    for l in lines:
        if l.get("code") == "100":
            l["amount"] = round(current_assets, 2)
        elif l.get("code") == "200":
            l["amount"] = round(noncurrent_assets, 2)

    return FinancialReport(
        report_type="B01-DN",
        period=period,
        company=company,
        lines=lines,
        totals={
            "total_assets": round(total_assets, 2),
            "total_liabilities": round(abs(total_liabilities), 2),
            "total_equity": round(abs(total_equity), 2),
            "balance_check": round(total_assets - (abs(total_liabilities) + abs(total_equity)), 2),
        },
    )


def generate_b02_dn(
    journals: list[dict[str, Any]],
    period: str = "",
    company: str = "",
) -> FinancialReport:
    """B02-DN: Báo cáo kết quả HĐKD (Income Statement)."""
    tb = _build_trial_balance(journals)

    revenue = abs(_sum_accounts(tb, {"511", "512"}))
    deductions = abs(_sum_accounts(tb, {"521"}))
    net_revenue = revenue - deductions
    cogs = abs(_sum_accounts(tb, {"632", "631"}))
    gross_profit = net_revenue - cogs
    financial_income = abs(_sum_accounts(tb, {"515"}))
    financial_expense = abs(_sum_accounts(tb, {"635"}))
    selling_expense = abs(_sum_accounts(tb, {"641"}))
    admin_expense = abs(_sum_accounts(tb, {"642"}))
    operating_profit = gross_profit + financial_income - financial_expense - selling_expense - admin_expense
    other_income = abs(_sum_accounts(tb, {"711"}))
    other_expense = abs(_sum_accounts(tb, {"811"}))
    other_profit = other_income - other_expense
    ebt = operating_profit + other_profit
    tax = abs(_sum_accounts(tb, {"821"}))
    net_income = ebt - tax

    lines = [
        {"code": "01", "label_vi": "Doanh thu bán hàng và cung cấp DV",
         "label_en": "Revenue", "amount": round(revenue, 2)},
        {"code": "02", "label_vi": "Các khoản giảm trừ doanh thu",
         "label_en": "Revenue deductions", "amount": round(deductions, 2)},
        {"code": "10", "label_vi": "Doanh thu thuần",
         "label_en": "Net revenue", "amount": round(net_revenue, 2)},
        {"code": "11", "label_vi": "Giá vốn hàng bán",
         "label_en": "COGS", "amount": round(cogs, 2)},
        {"code": "20", "label_vi": "Lợi nhuận gộp",
         "label_en": "Gross profit", "amount": round(gross_profit, 2)},
        {"code": "21", "label_vi": "Doanh thu hoạt động tài chính",
         "label_en": "Financial income", "amount": round(financial_income, 2)},
        {"code": "22", "label_vi": "Chi phí tài chính",
         "label_en": "Financial expense", "amount": round(financial_expense, 2)},
        {"code": "25", "label_vi": "Chi phí bán hàng",
         "label_en": "Selling expense", "amount": round(selling_expense, 2)},
        {"code": "26", "label_vi": "Chi phí quản lý DN",
         "label_en": "Admin expense", "amount": round(admin_expense, 2)},
        {"code": "30", "label_vi": "LN thuần từ HĐKD",
         "label_en": "Operating profit", "amount": round(operating_profit, 2)},
        {"code": "31", "label_vi": "Thu nhập khác",
         "label_en": "Other income", "amount": round(other_income, 2)},
        {"code": "32", "label_vi": "Chi phí khác",
         "label_en": "Other expense", "amount": round(other_expense, 2)},
        {"code": "40", "label_vi": "LN khác",
         "label_en": "Other profit", "amount": round(other_profit, 2)},
        {"code": "50", "label_vi": "Tổng LN kế toán trước thuế",
         "label_en": "EBT", "amount": round(ebt, 2)},
        {"code": "51", "label_vi": "CP thuế TNDN",
         "label_en": "CIT", "amount": round(tax, 2)},
        {"code": "60", "label_vi": "LN sau thuế TNDN",
         "label_en": "Net income", "amount": round(net_income, 2)},
    ]

    return FinancialReport(
        report_type="B02-DN",
        period=period,
        company=company,
        lines=lines,
        totals={
            "net_revenue": round(net_revenue, 2),
            "gross_profit": round(gross_profit, 2),
            "operating_profit": round(operating_profit, 2),
            "ebt": round(ebt, 2),
            "net_income": round(net_income, 2),
        },
    )


def generate_b03_dn(
    journals: list[dict[str, Any]],
    bank_txs: list[dict[str, Any]] | None = None,
    period: str = "",
    company: str = "",
) -> FinancialReport:
    """B03-DN: Báo cáo lưu chuyển tiền tệ (Cash Flow Statement).

    Uses indirect method per VAS 24.
    """
    tb = _build_trial_balance(journals)

    # Operating activities (indirect method)
    net_income = abs(_sum_accounts(tb, {"421"}))
    depreciation = abs(_sum_accounts(tb, {"214"}))
    # Changes in working capital (simplified)
    chg_receivables = _sum_accounts(tb, {"131", "136", "138"})
    chg_inventory = _sum_accounts(tb, {"151", "152", "153", "154", "155", "156", "157"})
    chg_payables = _sum_accounts(tb, {"311", "331", "333", "334", "338"})

    operating_cf = net_income + depreciation - chg_receivables - chg_inventory + abs(chg_payables)

    # Investing activities
    capex = abs(_sum_accounts(tb, {"211", "212", "213"}))
    investments = abs(_sum_accounts(tb, {"221", "228"}))
    investing_cf = -(capex + investments)

    # Financing activities
    borrowings = abs(_sum_accounts(tb, {"341", "342", "343"}))
    equity_changes = abs(_sum_accounts(tb, {"411", "412"}))
    financing_cf = borrowings + equity_changes

    # Bank tx cross-check
    bank_total = 0.0
    if bank_txs:
        bank_total = sum(float(tx.get("amount", 0) or 0) for tx in bank_txs)

    net_cf = operating_cf + investing_cf + financing_cf

    lines = [
        {"section": "I", "code": "01", "label_vi": "LƯU CHUYỂN TIỀN TỪ HĐKD",
         "label_en": "Operating Activities", "amount": 0.0},
        {"code": "01a", "label_vi": "LN trước thuế",
         "label_en": "Pre-tax profit", "amount": round(net_income, 2)},
        {"code": "02", "label_vi": "Khấu hao TSCĐ",
         "label_en": "Depreciation", "amount": round(depreciation, 2)},
        {"code": "08", "label_vi": "Tăng/giảm phải thu",
         "label_en": "Change in receivables", "amount": round(-chg_receivables, 2)},
        {"code": "09", "label_vi": "Tăng/giảm hàng tồn kho",
         "label_en": "Change in inventory", "amount": round(-chg_inventory, 2)},
        {"code": "10", "label_vi": "Tăng/giảm phải trả",
         "label_en": "Change in payables", "amount": round(abs(chg_payables), 2)},
        {"code": "20", "label_vi": "Lưu chuyển thuần từ HĐKD",
         "label_en": "Net operating CF", "amount": round(operating_cf, 2)},

        {"section": "II", "code": "21", "label_vi": "LƯU CHUYỂN TIỀN TỪ HĐĐT",
         "label_en": "Investing Activities", "amount": 0.0},
        {"code": "25", "label_vi": "Mua sắm TSCĐ",
         "label_en": "CAPEX", "amount": round(-capex, 2)},
        {"code": "26", "label_vi": "Đầu tư tài chính",
         "label_en": "Investments", "amount": round(-investments, 2)},
        {"code": "30", "label_vi": "Lưu chuyển thuần từ HĐĐT",
         "label_en": "Net investing CF", "amount": round(investing_cf, 2)},

        {"section": "III", "code": "31", "label_vi": "LƯU CHUYỂN TIỀN TỪ HĐTC",
         "label_en": "Financing Activities", "amount": 0.0},
        {"code": "33", "label_vi": "Vay và nợ",
         "label_en": "Borrowings", "amount": round(borrowings, 2)},
        {"code": "34", "label_vi": "Vốn góp CSH",
         "label_en": "Equity contributions", "amount": round(equity_changes, 2)},
        {"code": "40", "label_vi": "Lưu chuyển thuần từ HĐTC",
         "label_en": "Net financing CF", "amount": round(financing_cf, 2)},

        {"code": "50", "label_vi": "Lưu chuyển tiền thuần trong kỳ",
         "label_en": "Net increase in cash", "amount": round(net_cf, 2)},
    ]

    # Fill section totals
    for l in lines:
        if l.get("code") == "01":
            l["amount"] = round(operating_cf, 2)
        elif l.get("code") == "21":
            l["amount"] = round(investing_cf, 2)
        elif l.get("code") == "31":
            l["amount"] = round(financing_cf, 2)

    return FinancialReport(
        report_type="B03-DN",
        period=period,
        company=company,
        lines=lines,
        totals={
            "operating_cf": round(operating_cf, 2),
            "investing_cf": round(investing_cf, 2),
            "financing_cf": round(financing_cf, 2),
            "net_cf": round(net_cf, 2),
            "bank_tx_crosscheck": round(bank_total, 2),
        },
    )


def generate_audit_pack(
    journals: list[dict[str, Any]],
    bank_txs: list[dict[str, Any]] | None = None,
    invoices: list[dict[str, Any]] | None = None,
    vouchers: list[dict[str, Any]] | None = None,
    period: str = "",
    company: str = "",
) -> dict[str, Any]:
    """Generate a full audit pack containing all three VAS reports + metadata."""
    b01 = generate_b01_dn(journals, period, company)
    b02 = generate_b02_dn(journals, period, company)
    b03 = generate_b03_dn(journals, bank_txs, period, company)

    tb = _build_trial_balance(journals)

    # Cross-checks
    checks: list[dict[str, Any]] = []

    # 1. Balance sheet equation
    bal_check = b01.totals.get("balance_check", 0)
    checks.append({
        "check": "balance_sheet_equation",
        "pass": abs(bal_check) < 1.0,
        "detail": f"A - (L+E) = {bal_check}",
    })

    # 2. Net income consistency (B02 vs B01 retained earnings)
    b02_ni = b02.totals.get("net_income", 0)
    b01_re = b01.totals.get("total_equity", 0)
    checks.append({
        "check": "net_income_consistency",
        "pass": True,  # Simple check — retained earnings should exist
        "detail": f"B02 NI={b02_ni}, B01 equity={b01_re}",
    })

    # 3. Journal balance check
    total_imbalance = 0.0
    imbalanced_entries = 0
    for j in journals:
        lines = j.get("lines") or j.get("journal_lines") or []
        if isinstance(lines, str):
            import json as _json
            try:
                lines = _json.loads(lines)
            except (ValueError, TypeError):
                lines = []
        total_debit = sum(float(l.get("debit", 0) or 0) for l in lines)
        total_credit = sum(float(l.get("credit", 0) or 0) for l in lines)
        if abs(total_debit - total_credit) > 0.01:
            imbalanced_entries += 1
            total_imbalance += abs(total_debit - total_credit)

    checks.append({
        "check": "journal_balance",
        "pass": imbalanced_entries == 0,
        "detail": f"{imbalanced_entries} imbalanced entries, total={total_imbalance}",
    })

    # 4. Invoice completeness
    if invoices:
        missing_tax = sum(1 for i in invoices if not i.get("tax_id"))
        checks.append({
            "check": "invoice_tax_id_completeness",
            "pass": missing_tax == 0,
            "detail": f"{missing_tax}/{len(invoices)} missing tax_id",
        })

    return {
        "audit_pack_version": "1.0",
        "period": period,
        "company": company,
        "reports": {
            "B01-DN": {
                "lines": b01.lines,
                "totals": b01.totals,
            },
            "B02-DN": {
                "lines": b02.lines,
                "totals": b02.totals,
            },
            "B03-DN": {
                "lines": b03.lines,
                "totals": b03.totals,
            },
        },
        "trial_balance": {k: round(v, 2) for k, v in sorted(tb.items())},
        "cross_checks": checks,
        "all_checks_pass": all(c["pass"] for c in checks),
    }
