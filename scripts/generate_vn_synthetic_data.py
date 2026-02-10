#!/usr/bin/env python3
"""Generate Vietnamese accounting synthetic data for testing & demo.

Creates realistic VN invoices, payment vouchers, and receipts using
patterns from Nghị định 123/2020/NĐ-CP and Thông tư 200/2014/TT-BTC.

Usage:
    python scripts/generate_vn_synthetic_data.py [--count N] [--output DIR] [--format json|csv]

Output:
    - Hóa đơn GTGT (VAT invoices)
    - Phiếu chi (Cash disbursements)
    - Phiếu thu (Cash receipts)
    - Ủy nhiệm chi (Bank transfers)
    - Bảng lương (Payroll records)

The data follows VN tax patterns:
    - MST (Mã số thuế): 10 or 13 digits
    - Invoice number: ký hiệu + số (e.g. 1C25TAA 0000123)
    - VND amounts with proper Vietnamese number formatting
    - Realistic company names (CÔNG TY TNHH/CP/TN...)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
from datetime import date, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# VN-specific data pools
# ---------------------------------------------------------------------------

_VN_COMPANY_PREFIXES = [
    "CÔNG TY TNHH",
    "CÔNG TY CỔ PHẦN",
    "DOANH NGHIỆP TƯ NHÂN",
    "CÔNG TY TNHH MTV",
    "CÔNG TY TNHH SX-TM",
    "CÔNG TY CP ĐẦU TƯ",
    "CÔNG TY TNHH TM-DV",
]

_VN_COMPANY_NAMES = [
    "MINH PHÁT", "TÂN HOÀNG", "AN KHANG", "PHÚC LỘC",
    "VĨNH THUẬN", "HƯNG THỊNH", "ĐẠI PHONG", "NAM VIỆT",
    "THÀNH ĐẠT", "QUỐC CƯỜNG", "HOÀNG ANH", "KIM LONG",
    "BẢO MINH", "TÍN PHÁT", "TRƯỜNG PHÁT", "PHÚ QUÝ",
    "THIÊN AN", "SĐ TOÀN CẦU", "HÙNG VƯƠNG", "LONG THÀNH",
]

_VN_INDUSTRIES = [
    "Xây dựng", "Thương mại", "Dịch vụ", "Sản xuất",
    "Vận tải", "Công nghệ", "Nông nghiệp", "Chế biến",
]

_VN_PERSON_FIRST = [
    "Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh",
    "Phan", "Vũ", "Võ", "Đặng", "Bùi", "Đỗ",
]

_VN_PERSON_MIDDLE = ["Văn", "Thị", "Hữu", "Minh", "Quốc", "Thanh"]

_VN_PERSON_LAST = [
    "An", "Bình", "Chi", "Dũng", "Hà", "Hùng",
    "Linh", "Mai", "Nam", "Phúc", "Quân", "Tâm",
    "Tuấn", "Uyên", "Vinh", "Xuân", "Yến",
]

_VN_BANKS = [
    "Vietcombank", "VietinBank", "BIDV", "Agribank",
    "Techcombank", "MB Bank", "ACB", "VPBank",
    "SHB", "HDBank", "TPBank", "Sacombank",
]

_PRODUCT_DESCRIPTIONS = [
    "Bán hàng hóa theo hợp đồng",
    "Cung cấp dịch vụ tư vấn",
    "Mua nguyên vật liệu sản xuất",
    "Chi phí vận chuyển hàng hóa",
    "Thanh toán tiền thuê văn phòng",
    "Mua thiết bị văn phòng",
    "Dịch vụ bảo trì hệ thống",
    "Cung cấp phần mềm quản lý",
    "Mua sắm công cụ dụng cụ",
    "Chi phí quảng cáo marketing",
    "Thanh toán tiền điện, nước",
    "Dịch vụ kiểm toán báo cáo tài chính",
    "Mua hàng nhập kho",
    "Chi trả hoa hồng đại lý",
    "Dịch vụ vệ sinh công nghiệp",
]

_PAYROLL_POSITIONS = [
    "Kế toán trưởng", "Nhân viên kế toán", "Giám đốc",
    "Phó giám đốc", "Trưởng phòng kinh doanh", "Nhân viên bán hàng",
    "Kỹ sư phần mềm", "Nhân viên hành chính", "Thủ kho",
    "Nhân viên vận chuyển",
]


# ---------------------------------------------------------------------------
# Generator helpers
# ---------------------------------------------------------------------------

def _gen_mst(digits: int = 10) -> str:
    """Generate a Vietnamese MST (tax code)."""
    # First 2 digits: province code (01-99)
    province = random.randint(1, 96)
    remaining = digits - 2
    suffix = "".join(str(random.randint(0, 9)) for _ in range(remaining))
    return f"{province:02d}{suffix}"


def _gen_company() -> dict[str, str]:
    prefix = random.choice(_VN_COMPANY_PREFIXES)
    name = random.choice(_VN_COMPANY_NAMES)
    industry = random.choice(_VN_INDUSTRIES)
    mst = _gen_mst(random.choice([10, 13]))
    return {
        "name": f"{prefix} {name}",
        "tax_code": mst,
        "industry": industry,
    }


def _gen_person() -> str:
    return f"{random.choice(_VN_PERSON_FIRST)} {random.choice(_VN_PERSON_MIDDLE)} {random.choice(_VN_PERSON_LAST)}"


def _gen_date(start: date, end: date) -> str:
    delta = (end - start).days
    d = start + timedelta(days=random.randint(0, max(delta, 1)))
    return d.isoformat()


def _gen_amount(low: int = 500_000, high: int = 500_000_000) -> int:
    """Generate VND amount (rounded to 1000)."""
    return random.randint(low // 1000, high // 1000) * 1000


def _gen_invoice_no(year: int = 2025) -> tuple[str, str]:
    """Generate NĐ123-style invoice number.

    Returns (ký_hiệu, số_hóa_đơn) e.g. ("1C25TAA", "0000123")
    """
    form_type = random.choice(["1C", "2C"])
    suffix = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=3))
    symbol = f"{form_type}{year % 100}{suffix}"
    number = f"{random.randint(1, 9999999):07d}"
    return symbol, number


# ---------------------------------------------------------------------------
# Document generators
# ---------------------------------------------------------------------------

def gen_vat_invoice(d: date | None = None) -> dict[str, Any]:
    """Generate a VAT invoice (Hóa đơn GTGT)."""
    seller = _gen_company()
    buyer = _gen_company()
    year = (d or date(2025, 1, 1)).year
    symbol, inv_no = _gen_invoice_no(year)
    subtotal = _gen_amount(1_000_000, 200_000_000)
    vat_rate = random.choice([0, 5, 8, 10])
    vat_amount = int(subtotal * vat_rate / 100)

    return {
        "doc_type": "invoice_vat",
        "invoice_symbol": symbol,
        "invoice_no": inv_no,
        "issue_date": _gen_date(
            d or date(2025, 1, 1),
            d or date(2025, 12, 31),
        ),
        "seller_name": seller["name"],
        "seller_tax_code": seller["tax_code"],
        "buyer_name": buyer["name"],
        "buyer_tax_code": buyer["tax_code"],
        "description": random.choice(_PRODUCT_DESCRIPTIONS),
        "subtotal": subtotal,
        "vat_rate": vat_rate,
        "vat_amount": vat_amount,
        "total_amount": subtotal + vat_amount,
        "currency": "VND",
    }


def gen_cash_disbursement(d: date | None = None) -> dict[str, Any]:
    """Generate a cash disbursement voucher (Phiếu chi)."""
    company = _gen_company()
    payee = random.choice([_gen_person(), _gen_company()["name"]])
    return {
        "doc_type": "cash_disbursement",
        "doc_no": f"PC{random.randint(1, 9999):04d}",
        "issue_date": _gen_date(
            d or date(2025, 1, 1),
            d or date(2025, 12, 31),
        ),
        "payer": company["name"],
        "payee": payee,
        "description": random.choice(_PRODUCT_DESCRIPTIONS),
        "amount": _gen_amount(100_000, 50_000_000),
        "currency": "VND",
    }


def gen_cash_receipt(d: date | None = None) -> dict[str, Any]:
    """Generate a cash receipt (Phiếu thu)."""
    company = _gen_company()
    payer = random.choice([_gen_person(), _gen_company()["name"]])
    return {
        "doc_type": "cash_receipt",
        "doc_no": f"PT{random.randint(1, 9999):04d}",
        "issue_date": _gen_date(
            d or date(2025, 1, 1),
            d or date(2025, 12, 31),
        ),
        "payer": payer,
        "payee": company["name"],
        "description": random.choice([
            "Thu tiền thanh toán hóa đơn",
            "Thu tiền công nợ khách hàng",
            "Thu tiền đặt cọc hợp đồng",
            "Thu tiền bán hàng",
            "Thu tiền phạt vi phạm hợp đồng",
        ]),
        "amount": _gen_amount(200_000, 100_000_000),
        "currency": "VND",
    }


def gen_bank_transfer(d: date | None = None) -> dict[str, Any]:
    """Generate a bank transfer (Ủy nhiệm chi)."""
    from_co = _gen_company()
    to_co = _gen_company()
    return {
        "doc_type": "bank_transfer",
        "doc_no": f"UNC{random.randint(1, 99999):05d}",
        "issue_date": _gen_date(
            d or date(2025, 1, 1),
            d or date(2025, 12, 31),
        ),
        "from_company": from_co["name"],
        "from_tax_code": from_co["tax_code"],
        "from_bank": random.choice(_VN_BANKS),
        "from_account": f"{random.randint(10**9, 10**13 - 1)}",
        "to_company": to_co["name"],
        "to_tax_code": to_co["tax_code"],
        "to_bank": random.choice(_VN_BANKS),
        "to_account": f"{random.randint(10**9, 10**13 - 1)}",
        "description": random.choice(_PRODUCT_DESCRIPTIONS),
        "amount": _gen_amount(1_000_000, 500_000_000),
        "currency": "VND",
    }


def gen_payroll_record(d: date | None = None) -> dict[str, Any]:
    """Generate a payroll record (Bảng lương)."""
    gross = _gen_amount(8_000_000, 50_000_000)
    bhxh = int(gross * 0.08)
    bhyt = int(gross * 0.015)
    bhtn = int(gross * 0.01)
    tncn = max(0, int((gross - bhxh - bhyt - bhtn - 11_000_000) * 0.05))
    net = gross - bhxh - bhyt - bhtn - tncn

    issue = d or date(2025, random.randint(1, 12), 28)

    return {
        "doc_type": "payroll",
        "doc_no": f"BL{issue.strftime('%Y%m')}",
        "issue_date": issue.isoformat(),
        "employee_name": _gen_person(),
        "position": random.choice(_PAYROLL_POSITIONS),
        "department": random.choice(["Kế toán", "Kinh doanh", "IT", "HC-NS", "Sản xuất"]),
        "gross_salary": gross,
        "bhxh": bhxh,
        "bhyt": bhyt,
        "bhtn": bhtn,
        "tncn": tncn,
        "net_salary": net,
        "currency": "VND",
    }


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------

def generate_dataset(
    count: int = 50,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, Any]]:
    """Generate a mixed dataset of VN accounting documents.

    Distribution: ~40% invoices, ~20% cash disb, ~15% receipts,
                  ~15% bank transfers, ~10% payroll
    """
    start = start_date or date(2025, 1, 1)
    end = end_date or date(2025, 12, 31)

    generators = [
        (gen_vat_invoice, 0.40),
        (gen_cash_disbursement, 0.20),
        (gen_cash_receipt, 0.15),
        (gen_bank_transfer, 0.15),
        (gen_payroll_record, 0.10),
    ]

    docs: list[dict[str, Any]] = []
    for gen_fn, ratio in generators:
        n = max(1, int(count * ratio))
        for _ in range(n):
            # Generate with random date between start and end
            rand_day = start + timedelta(days=random.randint(0, max((end - start).days, 1)))
            docs.append(gen_fn(rand_day))

    # Shuffle for realistic ordering
    random.shuffle(docs)
    return docs[:count]


def write_json(docs: list[dict[str, Any]], output_dir: str) -> str:
    path = os.path.join(output_dir, "vn_synthetic_data.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=2, default=str)
    return path


def write_csv(docs: list[dict[str, Any]], output_dir: str) -> str:
    path = os.path.join(output_dir, "vn_synthetic_data.csv")
    if not docs:
        return path
    # Collect all keys
    all_keys: list[str] = []
    for d in docs:
        for k in d:
            if k not in all_keys:
                all_keys.append(k)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_keys)
        writer.writeheader()
        for d in docs:
            writer.writerow(d)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Vietnamese accounting synthetic data",
    )
    parser.add_argument("--count", type=int, default=50, help="Number of documents")
    parser.add_argument("--output", type=str, default="samples/seed", help="Output directory")
    parser.add_argument("--format", type=str, default="json", choices=["json", "csv", "both"])
    parser.add_argument("--start-date", type=str, default="2025-01-01")
    parser.add_argument("--end-date", type=str, default="2025-12-31")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)

    os.makedirs(args.output, exist_ok=True)
    docs = generate_dataset(args.count, start, end)

    paths = []
    if args.format in ("json", "both"):
        paths.append(write_json(docs, args.output))
    if args.format in ("csv", "both"):
        paths.append(write_csv(docs, args.output))

    # Print summary
    type_counts: dict[str, int] = {}
    total_amount = 0
    for d in docs:
        dt = d.get("doc_type", "other")
        type_counts[dt] = type_counts.get(dt, 0) + 1
        total_amount += d.get("total_amount", 0) or d.get("amount", 0) or d.get("gross_salary", 0)

    print(f"✅ Generated {len(docs)} VN synthetic documents")
    print(f"   Tổng giá trị: {total_amount:,.0f} VND")
    for dt, cnt in sorted(type_counts.items()):
        label_map = {
            "invoice_vat": "🧾 Hóa đơn GTGT",
            "cash_disbursement": "📤 Phiếu chi",
            "cash_receipt": "📥 Phiếu thu",
            "bank_transfer": "🏦 Ủy nhiệm chi",
            "payroll": "💰 Bảng lương",
        }
        print(f"   {label_map.get(dt, dt)}: {cnt}")
    for p in paths:
        print(f"   📁 {p}")


if __name__ == "__main__":
    main()
