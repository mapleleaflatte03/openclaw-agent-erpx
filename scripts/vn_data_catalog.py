"""VN Invoice Data Catalog — source definitions, schema, and mapping.

Each Kaggle dataset (MC-OCR 2021, Receipt OCR, Appen VN OCR) and synthetic
data are mapped to a unified internal schema for the VN Invoice Feeder.
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import json
import os
import random
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Source enum
# ---------------------------------------------------------------------------

class VnSource(str, Enum):
    VN_SOURCE_MC_OCR_2021 = "MC_OCR_2021"
    VN_SOURCE_RECEIPT_OCR = "RECEIPT_OCR"
    VN_SOURCE_APPEN_VN_OCR = "APPEN_VN_OCR"
    VN_SOURCE_GDT_SAMPLE = "GDT_SAMPLE"
    VN_SOURCE_SYNTHETIC = "SYNTHETIC"


# ---------------------------------------------------------------------------
# Unified internal schema
# ---------------------------------------------------------------------------

@dataclass
class VnLineItem:
    description: str = ""
    quantity: float = 1.0
    unit_price: float = 0.0
    amount: float = 0.0
    vat_rate: float = 0.0


@dataclass
class VnInvoiceRecord:
    source_name: str = ""
    external_id: str = ""
    issue_date: str = ""            # YYYY-MM-DD
    seller_name: str = ""
    seller_tax_code: str = ""
    buyer_name: str = ""
    buyer_tax_code: str = ""
    total_amount: float = 0.0
    vat_amount: float = 0.0
    currency: str = "VND"
    line_items: list[dict[str, Any]] = field(default_factory=list)
    file_paths: dict[str, str] = field(default_factory=dict)
    regulation_hint: str = "TT133/2016/TT-BTC"
    raw_texts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Default data directories
# ---------------------------------------------------------------------------

DATA_ROOT = os.getenv("VN_DATA_ROOT", "/data")

KAGGLE_MC_OCR_DIR = os.path.join(DATA_ROOT, "kaggle", "mc_ocr_2021")
KAGGLE_RECEIPT_OCR_DIR = os.path.join(DATA_ROOT, "kaggle", "receipt_ocr")
KAGGLE_APPEN_VN_DIR = os.path.join(DATA_ROOT, "kaggle", "appen_vn_ocr")
GDT_SAMPLES_DIR = os.path.join(DATA_ROOT, "gdt_samples")
VN_FEEDER_CACHE_DIR = os.path.join(DATA_ROOT, "vn_feeder_cache")


# ---------------------------------------------------------------------------
# Helper: stable external_id from file path
# ---------------------------------------------------------------------------

def _make_ext_id(source: str, path: str) -> str:
    return hashlib.md5(f"{source}:{path}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# VN seller/buyer & amount generators (for enrichment when raw data lacks it)
# ---------------------------------------------------------------------------

_VN_SELLERS = [
    ("Công ty TNHH ABC Việt Nam", "0101234567"),
    ("Công ty CP XYZ Thương mại", "0309876543"),
    ("Cửa hàng Bách Hoá Xanh", "0312345678"),
    ("Siêu thị Co.opmart", "0301234589"),
    ("Nhà hàng Hương Việt", "0106789012"),
    ("Công ty TNHH Sản xuất Minh Đức", "3602345678"),
    ("Doanh nghiệp TN Phát Tài", "0107890123"),
    ("Công ty CP Đầu tư Sài Gòn Xanh", "0313456789"),
]

_VN_BUYERS = [
    ("Công ty TNHH Kế toán Accounting Agent Layer", "0109999888"),
    ("Doanh nghiệp TN Demo ERP", "0301112233"),
    ("Công ty CP Công nghệ AI Việt", "0108765432"),
]

_VN_ITEMS = [
    "Văn phòng phẩm",
    "Mực in HP LaserJet",
    "Giấy A4 Double A",
    "Nước uống đóng chai Aquafina",
    "Cơm trưa văn phòng",
    "Dịch vụ vận chuyển hàng hóa",
    "Sửa chữa máy vi tính",
    "Phí thuê văn phòng tháng",
    "Dịch vụ kế toán thuế",
    "Bảo trì phần mềm ERP",
]


def _random_date(year: int = 2026) -> str:
    m = random.randint(1, 12)
    d = random.randint(1, 28)
    return f"{year}-{m:02d}-{d:02d}"


def _enrich_blank(rec: VnInvoiceRecord) -> VnInvoiceRecord:
    """Fill in missing financial fields with plausible VN data."""
    if not rec.seller_name:
        s = random.choice(_VN_SELLERS)
        rec.seller_name, rec.seller_tax_code = s
    if not rec.buyer_name:
        b = random.choice(_VN_BUYERS)
        rec.buyer_name, rec.buyer_tax_code = b
    if not rec.issue_date:
        rec.issue_date = _random_date()
    if rec.total_amount == 0:
        n_items = random.randint(1, 4)
        items: list[dict[str, Any]] = []
        subtotal = 0.0
        for _ in range(n_items):
            desc = random.choice(_VN_ITEMS)
            qty = random.randint(1, 10)
            price = random.choice([15000, 25000, 50000, 100000, 250000, 500000])
            amt = qty * price
            subtotal += amt
            items.append({
                "description": desc,
                "quantity": qty,
                "unit_price": price,
                "amount": amt,
                "vat_rate": 10.0,
            })
        rec.line_items = items
        rec.vat_amount = round(subtotal * 0.10, 0)
        rec.total_amount = subtotal + rec.vat_amount
    rec.currency = "VND"
    return rec


# ---------------------------------------------------------------------------
# MC-OCR 2021 mapping
# ---------------------------------------------------------------------------

_LABEL_MAP_MCOCR = {
    "SELLER": 1,
    "ADDRESS": 2,
    "TIMESTAMP": 3,
    "TOTAL_COST": 4,
}


def load_mc_ocr_records(limit: int = 0) -> list[VnInvoiceRecord]:
    """Parse MC-OCR 2021 KIE TSV data into VnInvoiceRecord list."""
    kie_dir = os.path.join(KAGGLE_MC_OCR_DIR, "kie_data", "kie_data",
                           "boxes_and_transcripts")
    img_list = os.path.join(KAGGLE_MC_OCR_DIR, "kie_data", "kie_data",
                            "image_list.csv")
    if not os.path.isdir(kie_dir):
        return []

    # Build image id → filename map
    id_to_img: dict[str, str] = {}
    if os.path.isfile(img_list):
        with open(img_list, encoding="utf-8-sig") as f:
            for row in csv.reader(f):
                if len(row) >= 3:
                    id_to_img[row[0].strip()] = row[2].strip()

    records: list[VnInvoiceRecord] = []
    tsv_files = sorted(Path(kie_dir).glob("*.tsv"))
    if limit > 0:
        tsv_files = tsv_files[:limit]

    for tsv_path in tsv_files:
        stem = tsv_path.stem  # e.g. mcocr_public_145014rxymg
        ext_id = _make_ext_id("MC_OCR", stem)
        texts: list[str] = []
        seller = ""
        total_str = ""
        date_str = ""

        with open(tsv_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.strip().split(",")
                if len(parts) < 11:
                    continue
                label = parts[10].strip() if len(parts) > 10 else ""
                text_col = parts[9].strip() if len(parts) > 9 else ""
                texts.append(text_col)
                if label == "SELLER" and text_col:
                    seller = text_col
                elif label == "TOTAL_COST" and text_col:
                    total_str = text_col
                elif label == "TIMESTAMP" and text_col:
                    date_str = text_col

        # Parse total
        total = 0.0
        if total_str:
            cleaned = re.sub(r"[^\d.]", "", total_str.replace(",", ""))
            with contextlib.suppress(ValueError):
                total = float(cleaned)

        # Parse date
        issue_date = ""
        if date_str:
            # Common VN format: DD/MM/YYYY or DD-MM-YYYY
            m = re.search(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", date_str)
            if m:
                with contextlib.suppress(ValueError):
                    issue_date = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"

        rec = VnInvoiceRecord(
            source_name=VnSource.VN_SOURCE_MC_OCR_2021.value,
            external_id=ext_id,
            issue_date=issue_date,
            seller_name=seller,
            total_amount=total,
            raw_texts=texts,
            file_paths={"tsv": str(tsv_path)},
        )
        rec = _enrich_blank(rec)
        records.append(rec)

    return records


# ---------------------------------------------------------------------------
# Appen VN OCR mapping (BILLS, TRADE DOCUMENTS, FORMS)
# ---------------------------------------------------------------------------

_APPEN_SUBDIRS = ["BILLS", "TRADE DOCUMENTS", "FORMS"]


def load_appen_records(limit: int = 0) -> list[VnInvoiceRecord]:
    """Parse Appen VN OCR JSON labels into VnInvoiceRecord list."""
    base = os.path.join(KAGGLE_APPEN_VN_DIR, "IMG_OCR_VIE_CN")
    if not os.path.isdir(base):
        return []

    records: list[VnInvoiceRecord] = []
    for subdir in _APPEN_SUBDIRS:
        sd = os.path.join(base, subdir)
        if not os.path.isdir(sd):
            continue
        json_files = sorted(Path(sd).glob("*.json"))
        if limit > 0 and len(records) + len(json_files) > limit:
            json_files = json_files[:max(0, limit - len(records))]

        for jf in json_files:
            ext_id = _make_ext_id("APPEN", jf.stem)
            texts: list[str] = []
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                for shape in data.get("shapes", []):
                    lbl = shape.get("label", "")
                    if lbl:
                        texts.append(lbl)
            except (json.JSONDecodeError, OSError):
                continue

            # Try to extract seller from first text
            seller = texts[0] if texts else ""
            img_name = jf.stem + ".jpg"
            img_path = os.path.join(sd, img_name)

            rec = VnInvoiceRecord(
                source_name=VnSource.VN_SOURCE_APPEN_VN_OCR.value,
                external_id=ext_id,
                seller_name=seller,
                raw_texts=texts,
                file_paths={
                    "ocr_json": str(jf),
                    "image": img_path if os.path.isfile(img_path) else "",
                },
            )
            rec = _enrich_blank(rec)
            records.append(rec)

    return records


# ---------------------------------------------------------------------------
# Receipt OCR mapping (line annotations → grouped by image)
# ---------------------------------------------------------------------------

def load_receipt_ocr_records(limit: int = 0) -> list[VnInvoiceRecord]:
    """Parse Receipt OCR line annotations into VnInvoiceRecord list."""
    ann_file = os.path.join(KAGGLE_RECEIPT_OCR_DIR, "data_line",
                            "line_annotation.txt")
    if not os.path.isfile(ann_file):
        return []

    # Group lines by image prefix
    groups: dict[str, list[str]] = {}
    with open(ann_file, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Format: image_path\ttext
            parts = line.split("\t", 1)
            if len(parts) < 2:
                continue
            img_path = parts[0]
            text = parts[1]
            # Group by image base name
            base = os.path.basename(img_path).rsplit("_", 1)[0]
            groups.setdefault(base, []).append(text)

    records: list[VnInvoiceRecord] = []
    for base_name, texts in list(groups.items()):
        if limit > 0 and len(records) >= limit:
            break
        ext_id = _make_ext_id("RECEIPT_OCR", base_name)
        rec = VnInvoiceRecord(
            source_name=VnSource.VN_SOURCE_RECEIPT_OCR.value,
            external_id=ext_id,
            raw_texts=texts,
            file_paths={"group_key": base_name},
        )
        rec = _enrich_blank(rec)
        records.append(rec)

    return records


# ---------------------------------------------------------------------------
# GDT sample (placeholder — hook for future data from hoadondientu.gdt.gov.vn)
# ---------------------------------------------------------------------------

def load_gdt_records(limit: int = 0) -> list[VnInvoiceRecord]:
    """Load GDT sample XML/PDF invoices if available."""
    if not os.path.isdir(GDT_SAMPLES_DIR):
        return []
    records: list[VnInvoiceRecord] = []
    for ext in ("*.xml", "*.pdf", "*.json"):
        for fp in sorted(Path(GDT_SAMPLES_DIR).glob(ext)):
            if limit > 0 and len(records) >= limit:
                break
            ext_id = _make_ext_id("GDT", fp.stem)
            rec = VnInvoiceRecord(
                source_name=VnSource.VN_SOURCE_GDT_SAMPLE.value,
                external_id=ext_id,
                file_paths={"file": str(fp)},
            )
            rec = _enrich_blank(rec)
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Aggregate loader
# ---------------------------------------------------------------------------

def load_all_records(limit_per_source: int = 0) -> list[VnInvoiceRecord]:
    """Load records from all available VN sources."""
    all_recs: list[VnInvoiceRecord] = []
    all_recs.extend(load_mc_ocr_records(limit_per_source))
    all_recs.extend(load_appen_records(limit_per_source))
    all_recs.extend(load_receipt_ocr_records(limit_per_source))
    all_recs.extend(load_gdt_records(limit_per_source))
    return all_recs


def source_stats(records: list[VnInvoiceRecord]) -> dict[str, int]:
    """Count records by source."""
    stats: dict[str, int] = {}
    for r in records:
        stats[r.source_name] = stats.get(r.source_name, 0) + 1
    return stats


if __name__ == "__main__":
    recs = load_all_records()
    print(f"Total records: {len(recs)}")
    for src, cnt in source_stats(recs).items():
        print(f"  {src}: {cnt}")
    if recs:
        print(f"\nSample record:\n{json.dumps(recs[0].to_dict(), indent=2, ensure_ascii=False)}")
