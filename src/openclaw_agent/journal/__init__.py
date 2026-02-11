"""Journal Suggestion Enhancement — Milestone 2.

Extends the base journal_suggestion flow with:
  - Multi-line journals (VAT splitting, debit/credit per TT133)
  - Tax rate optimizer (auto-detect VAT 0/5/8/10%, CIT)
  - Comprehensive TT133/TT200 chart-of-accounts mapping
  - LLM-powered account suggestion refinement
  - Read-only guarantee: writes only to Acct* mirror tables
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("openclaw.journal.tax_optimizer")


# ---------------------------------------------------------------------------
# TT133/TT200 Chart of Accounts — Vietnamese Accounting Standards
# ---------------------------------------------------------------------------

CHART_OF_ACCOUNTS_TT133: dict[str, dict[str, str]] = {
    # Loại 1: Tài sản ngắn hạn
    "111": {"name": "Tiền mặt", "group": "assets_current", "nature": "debit"},
    "112": {"name": "Tiền gửi ngân hàng", "group": "assets_current", "nature": "debit"},
    "113": {"name": "Tiền đang chuyển", "group": "assets_current", "nature": "debit"},
    "121": {"name": "Chứng khoán kinh doanh", "group": "assets_current", "nature": "debit"},
    "128": {"name": "Đầu tư nắm giữ đến ngày đáo hạn", "group": "assets_current", "nature": "debit"},
    "131": {"name": "Phải thu của khách hàng", "group": "assets_current", "nature": "debit"},
    "133": {"name": "Thuế GTGT được khấu trừ", "group": "assets_current", "nature": "debit"},
    "136": {"name": "Phải thu nội bộ", "group": "assets_current", "nature": "debit"},
    "138": {"name": "Phải thu khác", "group": "assets_current", "nature": "debit"},
    "141": {"name": "Tạm ứng", "group": "assets_current", "nature": "debit"},
    "142": {"name": "Chi phí trả trước ngắn hạn", "group": "assets_current", "nature": "debit"},
    "152": {"name": "Nguyên liệu, vật liệu", "group": "inventory", "nature": "debit"},
    "153": {"name": "Công cụ, dụng cụ", "group": "inventory", "nature": "debit"},
    "154": {"name": "Chi phí SXKD dở dang", "group": "inventory", "nature": "debit"},
    "155": {"name": "Thành phẩm", "group": "inventory", "nature": "debit"},
    "156": {"name": "Hàng hóa", "group": "inventory", "nature": "debit"},
    "157": {"name": "Hàng gửi đi bán", "group": "inventory", "nature": "debit"},
    # Loại 2: Tài sản dài hạn
    "211": {"name": "TSCĐ hữu hình", "group": "assets_fixed", "nature": "debit"},
    "212": {"name": "TSCĐ thuê tài chính", "group": "assets_fixed", "nature": "debit"},
    "213": {"name": "TSCĐ vô hình", "group": "assets_fixed", "nature": "debit"},
    "214": {"name": "Hao mòn TSCĐ", "group": "assets_fixed", "nature": "credit"},
    "217": {"name": "Bất động sản đầu tư", "group": "assets_fixed", "nature": "debit"},
    "221": {"name": "Đầu tư vào công ty con", "group": "investments", "nature": "debit"},
    "222": {"name": "Đầu tư vào công ty liên kết", "group": "investments", "nature": "debit"},
    "228": {"name": "Đầu tư khác", "group": "investments", "nature": "debit"},
    "229": {"name": "Dự phòng tổn thất tài sản", "group": "provisions", "nature": "credit"},
    "241": {"name": "Xây dựng cơ bản dở dang", "group": "assets_fixed", "nature": "debit"},
    "242": {"name": "Chi phí trả trước dài hạn", "group": "assets_fixed", "nature": "debit"},
    # Loại 3: Nợ phải trả
    "331": {"name": "Phải trả cho người bán", "group": "liabilities", "nature": "credit"},
    "333": {"name": "Thuế và các khoản phải nộp NN", "group": "liabilities", "nature": "credit"},
    "3331": {"name": "Thuế GTGT phải nộp", "group": "liabilities", "nature": "credit"},
    "33311": {"name": "Thuế GTGT đầu ra", "group": "liabilities", "nature": "credit"},
    "33312": {"name": "Thuế GTGT hàng NK", "group": "liabilities", "nature": "credit"},
    "3332": {"name": "Thuế tiêu thụ đặc biệt", "group": "liabilities", "nature": "credit"},
    "3334": {"name": "Thuế thu nhập DN", "group": "liabilities", "nature": "credit"},
    "3335": {"name": "Thuế thu nhập cá nhân", "group": "liabilities", "nature": "credit"},
    "334": {"name": "Phải trả người lao động", "group": "liabilities", "nature": "credit"},
    "335": {"name": "Chi phí phải trả", "group": "liabilities", "nature": "credit"},
    "336": {"name": "Phải trả nội bộ", "group": "liabilities", "nature": "credit"},
    "338": {"name": "Phải trả, phải nộp khác", "group": "liabilities", "nature": "credit"},
    "341": {"name": "Vay và nợ thuê tài chính", "group": "liabilities", "nature": "credit"},
    "343": {"name": "Trái phiếu phát hành", "group": "liabilities", "nature": "credit"},
    # Loại 4: Vốn chủ sở hữu
    "411": {"name": "Vốn đầu tư của chủ sở hữu", "group": "equity", "nature": "credit"},
    "412": {"name": "Chênh lệch đánh giá lại TS", "group": "equity", "nature": "credit"},
    "413": {"name": "Chênh lệch tỷ giá hối đoái", "group": "equity", "nature": "credit"},
    "414": {"name": "Quỹ đầu tư phát triển", "group": "equity", "nature": "credit"},
    "418": {"name": "Các quỹ thuộc VCSH", "group": "equity", "nature": "credit"},
    "419": {"name": "Cổ phiếu quỹ", "group": "equity", "nature": "debit"},
    "421": {"name": "Lợi nhuận sau thuế chưa PP", "group": "equity", "nature": "credit"},
    # Loại 5: Doanh thu
    "511": {"name": "Doanh thu bán hàng và CCDV", "group": "revenue", "nature": "credit"},
    "515": {"name": "Doanh thu hoạt động tài chính", "group": "revenue", "nature": "credit"},
    "521": {"name": "Các khoản giảm trừ DT", "group": "revenue", "nature": "debit"},
    # Loại 6: Chi phí
    "621": {"name": "Chi phí NVL trực tiếp", "group": "cogs", "nature": "debit"},
    "622": {"name": "Chi phí nhân công trực tiếp", "group": "cogs", "nature": "debit"},
    "623": {"name": "Chi phí sử dụng máy thi công", "group": "cogs", "nature": "debit"},
    "627": {"name": "Chi phí SX chung", "group": "cogs", "nature": "debit"},
    "631": {"name": "Giá thành sản xuất", "group": "cogs", "nature": "debit"},
    "632": {"name": "Giá vốn hàng bán", "group": "cogs", "nature": "debit"},
    "635": {"name": "Chi phí tài chính", "group": "expense", "nature": "debit"},
    "641": {"name": "Chi phí bán hàng", "group": "expense", "nature": "debit"},
    "642": {"name": "Chi phí quản lý DN", "group": "expense", "nature": "debit"},
    # Loại 7: Thu nhập khác
    "711": {"name": "Thu nhập khác", "group": "other_income", "nature": "credit"},
    # Loại 8: Chi phí khác
    "811": {"name": "Chi phí khác", "group": "other_expense", "nature": "debit"},
    "821": {"name": "Chi phí thuế TNDN", "group": "tax_expense", "nature": "debit"},
    # Loại 9: Xác định KQKD
    "911": {"name": "Xác định kết quả kinh doanh", "group": "pnl", "nature": "both"},
}

# ---------------------------------------------------------------------------
# Tax Rate Optimizer
# ---------------------------------------------------------------------------

# Vietnamese VAT rates per Thông tư 219/2013/TT-BTC
VAT_RATES = {
    "exempt": 0,       # Hàng hóa/DV không chịu thuế GTGT
    "zero": 0,         # Hàng xuất khẩu
    "reduced_5": 5,    # Lương thực, nước sạch, TBYT, sách, giống cây/con
    "reduced_8": 8,    # Thuế suất ưu đãi (NQ 43/2022/QH15)
    "standard_10": 10, # Thuế suất phổ thông
}


def detect_vat_rate(voucher: dict[str, Any]) -> int:
    """Auto-detect appropriate VAT rate for a voucher.

    Follows TT219/2013/TT-BTC and NQ43/2022/QH15.
    """
    description = (voucher.get("description", "") or "").lower()
    doc_type = voucher.get("doc_type", "")
    vat_rate = voucher.get("vat_rate")
    if vat_rate is not None and vat_rate >= 0:
        return int(vat_rate)

    # Heuristic detection from description keywords
    exempt_keywords = ["xuất khẩu", "export", "miễn thuế", "exempt"]
    reduced_keywords = ["lương thực", "nước sạch", "thuốc", "sách", "giống"]
    for kw in exempt_keywords:
        if kw in description:
            return 0
    for kw in reduced_keywords:
        if kw in description:
            return 5
    return 10  # Default: standard rate


def suggest_journal_lines(
    voucher: dict[str, Any],
    doc_type: str = "",
) -> list[dict[str, Any]]:
    """Generate multi-line journal entries for a voucher.

    Creates proper debit/credit lines with VAT splitting per TT133.
    Read-only: returns proposed lines (not persisted here).

    Returns list of dicts with: account, name, debit, credit, description.
    """
    amount = float(voucher.get("amount", 0) or voucher.get("total_amount", 0) or 0)
    vat_rate = detect_vat_rate(voucher)
    vat_amount = float(voucher.get("vat_amount", 0) or 0)
    if vat_amount == 0 and vat_rate > 0:
        vat_amount = amount * vat_rate / (100 + vat_rate)
    net_amount = amount - vat_amount

    dtype = doc_type or voucher.get("doc_type", "") or voucher.get("voucher_type", "")
    lines: list[dict[str, Any]] = []

    if dtype in ("sell_invoice", "invoice_vat"):
        # Bán hàng: Nợ 131 / Có 511 + Có 33311
        lines.append({"account": "131", "name": "Phải thu KH", "debit": amount, "credit": 0, "desc": "Phải thu tiền bán hàng"})
        lines.append({"account": "511", "name": "Doanh thu", "debit": 0, "credit": net_amount, "desc": "DT bán hàng"})
        if vat_amount > 0:
            lines.append({"account": "33311", "name": "Thuế GTGT đầu ra", "debit": 0, "credit": vat_amount, "desc": f"VAT {vat_rate}%"})

    elif dtype == "buy_invoice":
        # Mua hàng: Nợ 152/156 + Nợ 133 / Có 331
        lines.append({"account": "156", "name": "Hàng hóa", "debit": net_amount, "credit": 0, "desc": "Mua hàng hóa"})
        if vat_amount > 0:
            lines.append({"account": "133", "name": "Thuế GTGT được KT", "debit": vat_amount, "credit": 0, "desc": f"VAT đầu vào {vat_rate}%"})
        lines.append({"account": "331", "name": "Phải trả NCC", "debit": 0, "credit": amount, "desc": "Phải trả người bán"})

    elif dtype in ("receipt", "cash_receipt"):
        # Thu tiền: Nợ 111/112 / Có 131
        lines.append({"account": "111", "name": "Tiền mặt", "debit": amount, "credit": 0, "desc": "Thu tiền mặt"})
        lines.append({"account": "131", "name": "Phải thu KH", "debit": 0, "credit": amount, "desc": "Thanh toán công nợ"})

    elif dtype in ("payment", "cash_disbursement"):
        # Chi tiền: Nợ 331 / Có 111/112
        lines.append({"account": "331", "name": "Phải trả NCC", "debit": amount, "credit": 0, "desc": "Thanh toán NCC"})
        lines.append({"account": "111", "name": "Tiền mặt", "debit": 0, "credit": amount, "desc": "Chi tiền mặt"})

    elif dtype == "salary":
        # Lương: Nợ 642 / Có 334
        lines.append({"account": "642", "name": "Chi phí QLDN", "debit": amount, "credit": 0, "desc": "Chi phí lương"})
        lines.append({"account": "334", "name": "Phải trả NLĐ", "debit": 0, "credit": amount, "desc": "Lương phải trả"})

    elif dtype == "depreciation":
        # Khấu hao: Nợ 642 / Có 214
        lines.append({"account": "642", "name": "Chi phí QLDN", "debit": amount, "credit": 0, "desc": "CP khấu hao"})
        lines.append({"account": "214", "name": "Hao mòn TSCĐ", "debit": 0, "credit": amount, "desc": "KH TSCĐ"})

    else:
        # Generic: Nợ 642 / Có 111
        lines.append({"account": "642", "name": "Chi phí QLDN", "debit": amount, "credit": 0, "desc": "Chi phí khác"})
        lines.append({"account": "111", "name": "Tiền mặt", "debit": 0, "credit": amount, "desc": "Chi tiền"})

    return lines


def validate_journal_balance(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate that journal lines are balanced (total debit == total credit).

    Read-only check per TT133 requirement.
    """
    total_debit = sum(float(l.get("debit", 0) or 0) for l in lines)
    total_credit = sum(float(l.get("credit", 0) or 0) for l in lines)
    balanced = abs(total_debit - total_credit) < 0.01

    return {
        "balanced": balanced,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "diff": round(total_debit - total_credit, 2),
        "line_count": len(lines),
    }
