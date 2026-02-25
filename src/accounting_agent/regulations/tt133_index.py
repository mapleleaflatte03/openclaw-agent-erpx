"""TT133/2016/TT-BTC — Regulation index for Vietnamese SME accounting.

Thông tư 133/2016/TT-BTC hướng dẫn chế độ kế toán doanh nghiệp nhỏ và vừa.
Provides a lookup table of standardized accounts and rules usable by QnA
and journal-suggestion flows for contextual enrichment.

Usage:
    from accounting_agent.regulations.tt133_index import (
        TT133_ACCOUNTS,
        lookup_account,
        suggest_journal_entry,
        get_regulation_context,
    )
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TT133Account:
    """A single account entry from the TT133 chart of accounts."""
    code: str
    name_vi: str
    name_en: str
    group: str
    debit_nature: bool  # True = debit-normal, False = credit-normal
    level: int  # 1 = top-level, 2 = sub-account
    parent_code: str | None
    note: str


# ---------------------------------------------------------------------------
# Chart of Accounts — TT133 (abridged, most-used accounts for SME)
# ---------------------------------------------------------------------------

TT133_ACCOUNTS: dict[str, TT133Account] = {}

_RAW = [
    # Loại 1 – Tài sản ngắn hạn
    ("111", "Tiền mặt", "Cash on hand", "Tài sản ngắn hạn", True, 1, None, ""),
    ("1111", "Tiền Việt Nam", "VND cash", "Tài sản ngắn hạn", True, 2, "111", ""),
    ("1112", "Ngoại tệ", "Foreign currency cash", "Tài sản ngắn hạn", True, 2, "111", ""),
    ("112", "Tiền gửi ngân hàng", "Bank deposits", "Tài sản ngắn hạn", True, 1, None, ""),
    ("1121", "Tiền Việt Nam", "VND deposits", "Tài sản ngắn hạn", True, 2, "112", ""),
    ("1122", "Ngoại tệ", "FC deposits", "Tài sản ngắn hạn", True, 2, "112", ""),
    ("131", "Phải thu khách hàng", "Accounts receivable", "Tài sản ngắn hạn", True, 1, None, ""),
    ("133", "Thuế GTGT được khấu trừ", "VAT deductible", "Tài sản ngắn hạn", True, 1, None, ""),
    ("1331", "Thuế GTGT hàng hoá dịch vụ", "VAT on goods/services", "Tài sản ngắn hạn", True, 2, "133", ""),
    ("1332", "Thuế GTGT TSCĐ", "VAT on fixed assets", "Tài sản ngắn hạn", True, 2, "133", ""),
    ("138", "Phải thu khác", "Other receivables", "Tài sản ngắn hạn", True, 1, None, ""),
    ("141", "Tạm ứng", "Advances", "Tài sản ngắn hạn", True, 1, None, ""),
    ("152", "Nguyên liệu, vật liệu", "Raw materials", "Tài sản ngắn hạn", True, 1, None, ""),
    ("153", "Công cụ, dụng cụ", "Tools & supplies", "Tài sản ngắn hạn", True, 1, None, ""),
    ("154", "Chi phí SXKD dở dang", "WIP", "Tài sản ngắn hạn", True, 1, None, ""),
    ("155", "Thành phẩm", "Finished goods", "Tài sản ngắn hạn", True, 1, None, ""),
    ("156", "Hàng hóa", "Merchandise", "Tài sản ngắn hạn", True, 1, None, ""),
    ("157", "Hàng gửi đi bán", "Goods in transit", "Tài sản ngắn hạn", True, 1, None, ""),

    # Loại 2 – Tài sản dài hạn
    ("211", "Tài sản cố định hữu hình", "Tangible fixed assets", "Tài sản dài hạn", True, 1, None, ""),
    ("214", "Hao mòn TSCĐ", "Accumulated depreciation", "Tài sản dài hạn", False, 1, None, "Contra-asset"),
    ("217", "Bất động sản đầu tư", "Investment properties", "Tài sản dài hạn", True, 1, None, ""),
    ("241", "XDCB dở dang", "Construction in progress", "Tài sản dài hạn", True, 1, None, ""),
    ("242", "Chi phí trả trước", "Prepaid expenses", "Tài sản dài hạn", True, 1, None, ""),

    # Loại 3 – Nợ phải trả
    ("331", "Phải trả người bán", "Accounts payable", "Nợ phải trả", False, 1, None, ""),
    ("333", "Thuế và các khoản phải nộp NN", "Taxes payable", "Nợ phải trả", False, 1, None, ""),
    ("3331", "Thuế GTGT phải nộp", "VAT payable", "Nợ phải trả", False, 2, "333", ""),
    ("33311", "Thuế GTGT đầu ra", "Output VAT", "Nợ phải trả", False, 2, "3331", ""),
    ("3332", "Thuế TTĐB", "Special consumption tax", "Nợ phải trả", False, 2, "333", ""),
    ("3334", "Thuế TNDN", "Corporate income tax", "Nợ phải trả", False, 2, "333", ""),
    ("3335", "Thuế TNCN", "Personal income tax", "Nợ phải trả", False, 2, "333", ""),
    ("334", "Phải trả người lao động", "Payroll payable", "Nợ phải trả", False, 1, None, ""),
    ("335", "Chi phí phải trả", "Accrued expenses", "Nợ phải trả", False, 1, None, ""),
    ("338", "Phải trả, phải nộp khác", "Other payables", "Nợ phải trả", False, 1, None, ""),
    ("341", "Vay và nợ thuê tài chính", "Borrowings & fin leases", "Nợ phải trả", False, 1, None, ""),

    # Loại 4 – Vốn chủ sở hữu
    ("411", "Vốn đầu tư của CSH", "Owner's capital", "Vốn CSH", False, 1, None, ""),
    ("418", "Các quỹ thuộc VCSH", "Equity reserves", "Vốn CSH", False, 1, None, ""),
    ("421", "Lợi nhuận sau thuế chưa PP", "Retained earnings", "Vốn CSH", False, 1, None, ""),
    ("4211", "LNST chưa PP năm trước", "Prior year RE", "Vốn CSH", False, 2, "421", ""),
    ("4212", "LNST chưa PP năm nay", "Current year RE", "Vốn CSH", False, 2, "421", ""),

    # Loại 5 – Doanh thu
    ("511", "Doanh thu bán hàng & CCDV", "Revenue", "Doanh thu", False, 1, None, ""),
    ("515", "Doanh thu hoạt động tài chính", "Financial income", "Doanh thu", False, 1, None, ""),
    ("521", "Các khoản giảm trừ DT", "Revenue deductions", "Doanh thu", True, 1, None, "Contra-revenue"),

    # Loại 6 – Chi phí SXKD
    ("611", "Mua hàng", "Purchases", "Chi phí SXKD", True, 1, None, "Periodic inventory"),
    ("621", "CPNVLTT", "Direct materials", "Chi phí SXKD", True, 1, None, ""),
    ("622", "CPNCTT", "Direct labor", "Chi phí SXKD", True, 1, None, ""),
    ("623", "CP máy thi công", "Machinery expenses", "Chi phí SXKD", True, 1, None, "Construction"),
    ("627", "CP sản xuất chung", "Manufacturing overhead", "Chi phí SXKD", True, 1, None, ""),
    ("632", "Giá vốn hàng bán", "COGS", "Chi phí SXKD", True, 1, None, ""),
    ("635", "CP tài chính", "Financial expenses", "Chi phí SXKD", True, 1, None, ""),
    ("641", "CP bán hàng", "Selling expenses", "Chi phí SXKD", True, 1, None, "TT133 merged from 641+642"),
    ("642", "CP quản lý doanh nghiệp", "G&A expenses", "Chi phí SXKD", True, 1, None, "TT133 merged from 641+642"),

    # Loại 7 – Thu nhập khác
    ("711", "Thu nhập khác", "Other income", "Thu nhập khác", False, 1, None, ""),

    # Loại 8 – Chi phí khác
    ("811", "Chi phí khác", "Other expenses", "Chi phí khác", True, 1, None, ""),
    ("821", "Chi phí thuế TNDN", "CIT expense", "Chi phí khác", True, 1, None, ""),

    # Loại 9 – Xác định KQKD
    ("911", "Xác định KQKD", "P&L summary", "Xác định KQKD", True, 1, None, "Closing account"),
]

for _row in _RAW:
    _acct = TT133Account(
        code=_row[0],
        name_vi=_row[1],
        name_en=_row[2],
        group=_row[3],
        debit_nature=_row[4],
        level=_row[5],
        parent_code=_row[6],
        note=_row[7],
    )
    TT133_ACCOUNTS[_acct.code] = _acct


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def lookup_account(code: str) -> TT133Account | None:
    """Return the TT133 account entry for a given code, or None."""
    return TT133_ACCOUNTS.get(code.strip())


def search_accounts(query: str) -> list[TT133Account]:
    """Search accounts by Vietnamese name, English name, or code prefix."""
    q = query.lower()
    results = []
    for acct in TT133_ACCOUNTS.values():
        if (
            q in acct.code
            or q in acct.name_vi.lower()
            or q in acct.name_en.lower()
            or q in acct.group.lower()
        ):
            results.append(acct)
    return results


# ---------------------------------------------------------------------------
# Journal-entry suggestion rules (simplified)
# ---------------------------------------------------------------------------

_JOURNAL_RULES: list[dict] = [
    {
        "scenario_vi": "Mua hàng hoá thanh toán tiền mặt",
        "scenario_en": "Purchase goods, pay cash",
        "debit": ["156", "1331"],
        "credit": ["111"],
        "note": "Nợ TK 156 (giá mua), Nợ TK 1331 (VAT); Có TK 111",
    },
    {
        "scenario_vi": "Mua hàng hoá thanh toán chuyển khoản",
        "scenario_en": "Purchase goods, pay bank",
        "debit": ["156", "1331"],
        "credit": ["112"],
        "note": "Nợ TK 156, 1331; Có TK 112",
    },
    {
        "scenario_vi": "Mua hàng hoá chưa thanh toán",
        "scenario_en": "Purchase goods, on credit",
        "debit": ["156", "1331"],
        "credit": ["331"],
        "note": "Nợ TK 156, 1331; Có TK 331",
    },
    {
        "scenario_vi": "Bán hàng thu tiền mặt",
        "scenario_en": "Sell goods, receive cash",
        "debit": ["111"],
        "credit": ["511", "33311"],
        "note": "Nợ TK 111; Có TK 511 (doanh thu), Có TK 33311 (VAT đầu ra)",
    },
    {
        "scenario_vi": "Bán hàng thu tiền chuyển khoản",
        "scenario_en": "Sell goods, receive bank",
        "debit": ["112"],
        "credit": ["511", "33311"],
        "note": "Nợ TK 112; Có TK 511, 33311",
    },
    {
        "scenario_vi": "Bán hàng chưa thu tiền",
        "scenario_en": "Sell goods, on credit",
        "debit": ["131"],
        "credit": ["511", "33311"],
        "note": "Nợ TK 131; Có TK 511, 33311",
    },
    {
        "scenario_vi": "Xuất kho bán hàng ghi nhận giá vốn",
        "scenario_en": "Record COGS on goods sold",
        "debit": ["632"],
        "credit": ["156"],
        "note": "Nợ TK 632; Có TK 156",
    },
    {
        "scenario_vi": "Chi lương nhân viên",
        "scenario_en": "Pay employee salary",
        "debit": ["334"],
        "credit": ["111", "112"],
        "note": "Nợ TK 334; Có TK 111/112",
    },
    {
        "scenario_vi": "Tính lương phải trả",
        "scenario_en": "Accrue salary payable",
        "debit": ["641", "642"],
        "credit": ["334"],
        "note": "Nợ TK 641/642; Có TK 334",
    },
    {
        "scenario_vi": "Khấu hao TSCĐ",
        "scenario_en": "Depreciation of fixed assets",
        "debit": ["641", "642", "627"],
        "credit": ["214"],
        "note": "Nợ TK 641/642/627; Có TK 214",
    },
    {
        "scenario_vi": "Nộp thuế GTGT",
        "scenario_en": "Pay VAT to state",
        "debit": ["33311"],
        "credit": ["111", "112"],
        "note": "Nợ TK 33311; Có TK 111/112",
    },
    {
        "scenario_vi": "Thanh toán cho nhà cung cấp",
        "scenario_en": "Pay supplier",
        "debit": ["331"],
        "credit": ["111", "112"],
        "note": "Nợ TK 331; Có TK 111/112",
    },
    {
        "scenario_vi": "Thu nợ khách hàng",
        "scenario_en": "Collect receivable from customer",
        "debit": ["111", "112"],
        "credit": ["131"],
        "note": "Nợ TK 111/112; Có TK 131",
    },
]


def suggest_journal_entry(scenario: str) -> list[dict]:
    """Return matching journal-entry rules for a given scenario description."""
    q = scenario.lower()
    matches = []
    for rule in _JOURNAL_RULES:
        if (
            q in rule["scenario_vi"].lower()
            or q in rule["scenario_en"].lower()
            or any(q in code for code in rule["debit"] + rule["credit"])
        ):
            matches.append(rule)
    return matches


def get_regulation_context(topic: str | None = None) -> str:
    """Return a formatted regulation context string for LLM prompt enrichment.

    If *topic* is provided, filter to matching accounts/rules;
    otherwise return a brief summary of TT133.
    """
    header = (
        "Thông tư 133/2016/TT-BTC — Chế độ kế toán doanh nghiệp nhỏ và vừa.\n"
        "Hệ thống tài khoản gồm 9 loại (Loại 1–9).\n\n"
    )
    if topic:
        accts = search_accounts(topic)
        rules = suggest_journal_entry(topic)
        lines = [header, f"Kết quả tra cứu cho: «{topic}»\n"]
        if accts:
            lines.append("Tài khoản liên quan:")
            for a in accts[:10]:
                lines.append(f"  TK {a.code} – {a.name_vi} ({a.name_en})")
        if rules:
            lines.append("\nBút toán mẫu:")
            for r in rules[:5]:
                lines.append(f"  • {r['scenario_vi']}: {r['note']}")
        return "\n".join(lines)

    # General summary
    groups: dict[str, int] = {}
    for a in TT133_ACCOUNTS.values():
        groups[a.group] = groups.get(a.group, 0) + 1
    summary_lines = [header, "Tổng hợp hệ thống tài khoản:"]
    for g, cnt in groups.items():
        summary_lines.append(f"  {g}: {cnt} tài khoản")
    return "\n".join(summary_lines)


# ---------------------------------------------------------------------------
# Module-level self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"TT133 chart: {len(TT133_ACCOUNTS)} accounts loaded")
    print()
    print("Search 'tiền mặt':")
    for a in search_accounts("tiền mặt"):
        print(f"  TK {a.code} – {a.name_vi} ({a.name_en})")
    print()
    print("Journal suggestions for 'mua hàng':")
    for r in suggest_journal_entry("mua hàng"):
        print(f"  {r['scenario_vi']}: {r['note']}")
    print()
    print("Regulation context for 'thuế':")
    print(get_regulation_context("thuế"))
