#!/usr/bin/env python3
"""Prepare real Vietnamese accounting data for the agent pipeline.

This script converts a **small subset** of real VN accounting documents
(images, PDFs, XML e-invoices) into the JSON format expected by
``upload_minio_simulate_erp.py`` and ``voucher_ingest``.

⚠️  **Data is NOT committed to the repo.**  You must download it locally.

Supported sources (URLs provided by the project maintainer):

  1. Kaggle – MC_OCR 2021 (Vietnamese receipts, ~2.3 GB)
     https://www.kaggle.com/datasets/domixi1989/vietnamese-receipts-mc-ocr-2021

  2. Kaggle – Receipt OCR VN (line-level receipt OCR, ~76 MB)
     https://www.kaggle.com/datasets/blyatfk/receipt-ocr

  3. Kaggle – Appen VN OCR documents (11 categories, ~17 MB, CC BY-SA 4.0)
     https://www.kaggle.com/datasets/appenlimited/ocr-image-data-of-vietnamese-language-documents
     Categories: RECEIPT, BILLS, CONTRACTS, FORMS, IDCARD, TRADE,
                 TABLE, WHITEBOARD, NEWSPAPER, NOTES, BOOKCONTENT

  4. GDT e-invoice portal (NĐ 123/2020 format)
     https://hoadondientu.gdt.gov.vn/

  5. TT133/2016 full-text PDF (accounting standard for SMEs)
     https://vbpl.vn/FileData/TW/Lists/vbpq/Attachments/113560/VanBanGoc_133_2016_TT_BTC.pdf

How to use:

  1. Download one or more datasets locally (DO NOT commit):
       kaggle datasets download -d domixi1989/vietnamese-receipts-mc-ocr-2021
       unzip *.zip -d data/real_vn/mc_ocr/

  2. Run this script to convert a small subset to JSON:
       python scripts/prepare_real_vn_data.py \\
         --source-dir data/real_vn/mc_ocr/ \\
         --output-dir data/real_vn/prepared/ \\
         --max-files 20 \\
         --source-type kaggle_receipt

  3. Feed the prepared data into the MinIO pipeline:
       python scripts/upload_minio_simulate_erp.py \\
         --mode real \\
         --real-data-dir data/real_vn/prepared/ \\
         --interval 30 --cycles 5

  Alternatively, use ``--mode mix`` to randomly interleave real
  and synthetic documents in the same pipeline run.

Security notice:
  Real data may contain PII (tax codes, names, amounts).
  The developer is responsible for handling it securely.
  NEVER commit real data files to the repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Source-type converters
# ---------------------------------------------------------------------------

# Supported extensions for image/document files
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"}
_DOC_EXTS = {".pdf", ".xml", ".json", ".xls", ".xlsx", ".htm", ".html"}
_ALL_EXTS = _IMAGE_EXTS | _DOC_EXTS

# Category → doc_type mapping (for Appen dataset categories)
_APPEN_CATEGORY_MAP: dict[str, str] = {
    "RECEIPT": "invoice_vat",
    "BILLS": "invoice_vat",
    "CONTRACTS": "contract",
    "FORMS": "form",
    "IDCARD": "id_card",
    "TRADE": "trade_doc",
    "TABLE": "table",
    "WHITEBOARD": "other",
    "NEWSPAPER": "other",
    "NOTES": "other",
    "BOOKCONTENT": "other",
}


def _file_hash(path: Path) -> str:
    """Short SHA-256 prefix for dedup / reference."""
    h = hashlib.sha256()
    h.update(path.read_bytes()[:8192])  # first 8 KB for speed
    return h.hexdigest()[:12]


def _detect_category_from_path(path: Path) -> str:
    """Try to detect Appen category from parent directory name."""
    for part in path.parts:
        upper = part.upper()
        if upper in _APPEN_CATEGORY_MAP:
            return upper
    return "OTHER"


def _normalize_for_match(text: str) -> str:
    lowered = text.lower().replace("đ", "d")
    no_accent = "".join(
        ch for ch in unicodedata.normalize("NFD", lowered)
        if unicodedata.category(ch) != "Mn"
    )
    cleaned = re.sub(r"[^a-z0-9]+", " ", no_accent).strip()
    return f" {cleaned} " if cleaned else " "


def _infer_doc_type_from_filename(path: Path) -> str:
    """Infer doc_type from real-world filename/path conventions."""
    text = _normalize_for_match(f"{path.parent.name} {path.name}")

    if any(k in text for k in (" payment request ", " yctt ", " dntt ", " de nghi thanh toan ")):
        return "cash_disbursement"
    if any(k in text for k in (" phieu thu ", " bien lai thu ", " receipt ")):
        return "cash_receipt"
    if any(k in text for k in (" invoice ", " hoa don ", " vat ", " inv ", " bill ", " c25t ")):
        return "invoice_vat"
    if re.search(r"\bva\d{2}[-_ ]?\d{5,}\b", path.name.lower()):
        return "invoice_vat"
    return "other"


def _infer_invoice_direction(path: Path) -> str:
    """Infer whether invoice is likely purchase (AP) or sales (AR)."""
    text = _normalize_for_match(f"{path.parent} {path.name}")
    if any(k in text for k in (" ban hang ", " xuat hoa don ", " dau ra ", " doanh thu ")):
        return "sales"
    return "purchase"


def _convert_image_to_doc_json(
    path: Path,
    source_type: str,
    index: int,
) -> dict[str, Any]:
    """Convert one image/PDF/XML file into a pipeline-compatible JSON dict."""
    category = _detect_category_from_path(path)
    inferred_doc_type = _infer_doc_type_from_filename(path)
    doc_type = _APPEN_CATEGORY_MAP.get(category, "other")
    if doc_type == "other" and inferred_doc_type != "other":
        doc_type = inferred_doc_type
    file_hash = _file_hash(path)

    doc: dict[str, Any] = {
        "doc_type": doc_type,
        "source": source_type,
        "source_file": path.name,
        "source_hash": file_hash,
        "file_ext": path.suffix.lower(),
        "file_size_bytes": path.stat().st_size,
        "category": category.lower(),
        "issue_date": date.today().isoformat(),
        "currency": "VND",
        "description": (
            f"Real VN document ({category.lower()}) — "
            f"{path.parent.name} / {path.name}"
        ),
        "index": index,
        "source_path": str(path),
        "inferred_doc_type": inferred_doc_type,
    }

    if doc_type == "invoice_vat":
        doc["invoice_direction"] = _infer_invoice_direction(path)

    # For images: mark as needing OCR
    if path.suffix.lower() in _IMAGE_EXTS:
        doc["requires_ocr"] = True
        doc["ocr_status"] = "pending"

    # For XML: try to extract invoice fields
    if path.suffix.lower() == ".xml":
        doc["requires_ocr"] = False
        doc["format"] = "xml_einvoice"

    # For JSON: try to read metadata
    if path.suffix.lower() == ".json":
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                # Merge known fields
                for k in ("invoice_no", "seller_name", "buyer_name",
                           "total_amount", "vat_amount", "vat_rate"):
                    if k in raw:
                        doc[k] = raw[k]
        except Exception:
            pass

    return doc


def _collect_files(source_dir: Path, max_files: int) -> list[Path]:
    """Recursively collect eligible files, capped at max_files."""
    all_files = sorted(
        p for p in source_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in _ALL_EXTS
    )
    if not all_files:
        return []
    if len(all_files) > max_files:
        all_files = random.sample(all_files, max_files)
    return all_files


# ---------------------------------------------------------------------------
# GDT e-invoice template generator
# ---------------------------------------------------------------------------

def _generate_gdt_sample(output_dir: Path, count: int) -> list[Path]:
    """Generate sample GDT-format e-invoice JSON fixtures.

    These are realistic **mock** e-invoices following NĐ 123/2020 schema.
    To use real e-invoices, export XML from https://hoadondientu.gdt.gov.vn/
    and place them in your source directory.
    """
    created: list[Path] = []
    for i in range(count):
        inv = {
            "format": "gdt_einvoice_nd123",
            "doc_type": "invoice_vat",
            "invoice_no": f"1C25TAA {random.randint(1, 9999999):07d}",
            "issue_date": date.today().isoformat(),
            "seller_name": f"CÔNG TY TNHH MẪU {i+1:03d}",
            "seller_tax_code": f"0{random.randint(100000000, 999999999)}",
            "buyer_name": f"CÔNG TY CP TEST {i+1:03d}",
            "buyer_tax_code": f"0{random.randint(100000000, 999999999)}",
            "currency": "VND",
            "subtotal": (amt := random.randint(1_000_000, 200_000_000)),
            "vat_rate": (vr := random.choice([8, 10])),
            "vat_amount": int(amt * vr / 100),
            "total_amount": amt + int(amt * vr / 100),
            "description": "Hóa đơn mẫu theo NĐ 123/2020/NĐ-CP",
            "source": "gdt_sample",
        }
        p = output_dir / f"gdt_einvoice_sample_{i+1:04d}.json"
        p.write_text(json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
        created.append(p)
    return created


# ---------------------------------------------------------------------------
# TT133 regulation excerpt generator
# ---------------------------------------------------------------------------

def _generate_tt133_excerpt(output_dir: Path) -> Path:
    """Create a small TT133 regulation excerpt fixture.

    Full text: https://vbpl.vn/FileData/TW/Lists/vbpq/Attachments/113560/
               VanBanGoc_133_2016_TT_BTC.pdf

    This fixture contains key article summaries only (no copyrighted full text).
    """
    excerpt = {
        "regulation": "Thông tư 133/2016/TT-BTC",
        "subject": "Chế độ kế toán doanh nghiệp nhỏ và vừa",
        "effective_date": "2017-01-01",
        "key_articles": [
            {
                "article": "Điều 9",
                "title": "Hệ thống tài khoản kế toán",
                "summary": "DN nhỏ và vừa sử dụng hệ thống TK đơn giản hóa từ TT200.",
            },
            {
                "article": "Điều 10",
                "title": "Sổ kế toán",
                "summary": "Gồm sổ nhật ký chung, sổ cái, sổ chi tiết theo TK.",
            },
            {
                "article": "Điều 11",
                "title": "Báo cáo tài chính",
                "summary": "Gồm: Bảng CĐKT (B01-DNN), Báo cáo KQHĐKD (B02-DNN), "
                           "Thuyết minh BCTC (B09-DNN).",
            },
            {
                "article": "Phụ lục 1",
                "title": "Hệ thống tài khoản kế toán DN nhỏ và vừa",
                "summary": "Loại 1: Tài sản ngắn hạn (111-Tiền mặt, 112-TGNH, 131-Phải thu KH, ...); "
                           "Loại 2: Tài sản dài hạn (211-TSCĐ hữu hình, ...); "
                           "Loại 3: Nợ phải trả (331-Phải trả NB, 334-Phải trả CNV, ...); "
                           "Loại 5: Doanh thu (511-DTBH, 515-DTTC, ...); "
                           "Loại 6: Chi phí (621-CPNVLTT, 622-CPNCTT, 627-CPSXC, 641-CPBH, 642-CPQLDN, ...).",
            },
        ],
        "source_url": "https://vbpl.vn/FileData/TW/Lists/vbpq/Attachments/113560/"
                      "VanBanGoc_133_2016_TT_BTC.pdf",
    }
    p = output_dir / "tt133_2016_excerpt.json"
    p.write_text(json.dumps(excerpt, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare real VN data for the accounting agent pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Supported --source-type values:\n"
            "  kaggle_receipt   — MC_OCR 2021 / Receipt OCR datasets (images)\n"
            "  kaggle_appen     — Appen VN OCR (11 categories)\n"
            "  gdt_einvoice     — Generate GDT NĐ123 sample e-invoices (mock)\n"
            "  tt133_excerpt    — Generate TT133/2016 regulation excerpt fixture\n"
            "  auto             — Auto-detect from directory contents\n"
        ),
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="Directory containing downloaded real data (images/PDFs/XML).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/real_vn/prepared"),
        help="Where to write converted JSON files (default: data/real_vn/prepared/).",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=20,
        help="Max files to convert from source (default: 20).",
    )
    parser.add_argument(
        "--source-type",
        choices=["kaggle_receipt", "kaggle_appen", "gdt_einvoice", "tt133_excerpt", "auto"],
        default="auto",
        help="Type of source data (default: auto-detect).",
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("📋 Chuẩn bị dữ liệu VN thật cho pipeline agent")
    print(f"   Output: {args.output_dir}")
    print(f"   Source type: {args.source_type}")
    print("=" * 60)

    converted = 0

    # --- GDT sample e-invoices (no source-dir needed) ---
    if args.source_type in ("gdt_einvoice", "auto"):
        n = min(args.max_files, 10)
        created = _generate_gdt_sample(args.output_dir, n)
        print(f"  ✅ Tạo {len(created)} hóa đơn mẫu GDT (NĐ 123)")
        converted += len(created)

    # --- TT133 excerpt (no source-dir needed) ---
    if args.source_type in ("tt133_excerpt", "auto"):
        p = _generate_tt133_excerpt(args.output_dir)
        print(f"  ✅ Tạo trích dẫn TT133/2016 → {p.name}")
        converted += 1

    # --- Image / PDF / XML from downloaded datasets ---
    if args.source_dir and args.source_type in ("kaggle_receipt", "kaggle_appen", "auto"):
        if not args.source_dir.is_dir():
            print(f"  ❌ Thư mục nguồn không tồn tại: {args.source_dir}", file=sys.stderr)
            sys.exit(1)

        files = _collect_files(args.source_dir, args.max_files)
        if not files:
            print(f"  ⚠️  Không tìm thấy file phù hợp trong {args.source_dir}")
        else:
            for i, fp in enumerate(files):
                doc = _convert_image_to_doc_json(fp, args.source_type, i)
                out_path = args.output_dir / f"real_{args.source_type}_{i+1:04d}.json"
                out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
                converted += 1
                print(
                    f"  📄 [{i+1}/{len(files)}] {fp.name:<40s} "
                    f"→ {out_path.name}  ({doc['doc_type']})"
                )

    if args.source_type == "auto" and not args.source_dir:
        print(
            "\n  ℹ️  Chạy ở chế độ auto không có --source-dir → chỉ tạo fixtures mẫu.\n"
            "  Để dùng dữ liệu thật, tải từ Kaggle/GDT và chỉ định --source-dir."
        )

    print(f"\n📊 Hoàn tất: {converted} file đã chuẩn bị tại {args.output_dir}/")
    print(
        "\n⏭️  Bước tiếp: chạy pipeline:\n"
        f"  python scripts/upload_minio_simulate_erp.py \\\n"
        f"    --mode real --real-data-dir {args.output_dir}/ \\\n"
        f"    --interval 30 --cycles 5"
    )


if __name__ == "__main__":
    main()
