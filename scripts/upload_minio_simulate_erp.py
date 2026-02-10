#!/usr/bin/env python3
"""Upload VN documents to MinIO to simulate ERP event triggers.

This script generates synthetic VN accounting documents and uploads them
to MinIO's `agent-drop` bucket under `drop/attachments/`, triggering
the `attachments_drop` poller defined in config/schedules.yaml.

The poller detects new files and auto-runs: ingest → classify → suggest chain.

Usage:
    # Synthetic mode (default — faker JSON documents)
    python scripts/upload_minio_simulate_erp.py [--interval SEC] [--cycles N]

    # Real mode (pre-converted JSON from prepare_real_vn_data.py)
    python scripts/upload_minio_simulate_erp.py \\
        --mode real --real-data-dir data/real_vn/prepared/ --cycles 5

    # Mix mode (randomly interleave synthetic + real)
    python scripts/upload_minio_simulate_erp.py \\
        --mode mix --real-data-dir data/real_vn/prepared/

Example:
    # Upload 1-5 docs every 20 seconds, 10 cycles
    python scripts/upload_minio_simulate_erp.py --interval 20 --cycles 10

    # Continuous mode (Ctrl+C to stop)
    python scripts/upload_minio_simulate_erp.py --interval 30 --cycles 0

Environment variables (from .env):
    MINIO_ENDPOINT   — MinIO endpoint URL (default http://minio:9000)
    MINIO_ACCESS_KEY — MinIO access key
    MINIO_SECRET_KEY — MinIO secret key
    MINIO_BUCKET_DROP — Bucket for file drops (default agent-drop)
    MINIO_REGION     — Region (default sgp1)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from datetime import date, timedelta
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# MinIO config from environment
# ---------------------------------------------------------------------------

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "admin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "admin123456")
MINIO_BUCKET = os.getenv("MINIO_BUCKET_DROP", "agent-drop")
MINIO_REGION = os.getenv("MINIO_REGION", "sgp1")

# Drop prefix — matches schedules.yaml `attachments_drop.prefix`
DROP_PREFIX = "drop/attachments/"


# ---------------------------------------------------------------------------
# VN document generators (reuses patterns from generate_vn_synthetic_data.py)
# ---------------------------------------------------------------------------

_COMPANIES = [
    "CÔNG TY TNHH ABC",
    "CÔNG TY CP TÂN BÌNH",
    "CÔNG TY TNHH ĐẠI NAM",
    "CÔNG TY CP SƠN HÀ",
    "CÔNG TY TNHH HOÀNG GIA",
    "DOANH NGHIỆP TƯ NHÂN MINH TÂM",
    "CÔNG TY CP THẾ GIỚI DI ĐỘNG",
    "CÔNG TY TNHH DỊCH VỤ LẠC VIỆT",
]

_DESCRIPTIONS = [
    "Bán hàng hóa theo hợp đồng",
    "Mua nguyên vật liệu sản xuất",
    "Chi tiền tiếp khách",
    "Thu tiền thanh toán hóa đơn",
    "Thanh toán lương tháng",
    "Mua tài sản cố định - máy in",
    "Thanh toán tiền điện nước tháng",
    "Phí vận chuyển hàng hóa",
    "Thu tiền dịch vụ tư vấn",
    "Tạm ứng công tác phí",
]


def _gen_mst() -> str:
    """Generate fake MST (mã số thuế) — 10 or 13 digits."""
    base = f"0{random.randint(100000000, 999999999)}"
    if random.random() < 0.3:
        base += f"{random.randint(100, 999)}"
    return base


def _gen_invoice_no() -> str:
    seq = random.randint(1, 9999999)
    symbol = f"1C{random.randint(20, 26)}T{chr(random.randint(65, 90))*2}"
    return f"{symbol} {seq:07d}"


def _gen_doc() -> dict:
    """Generate a single random VN accounting document (synthetic)."""
    doc_type = random.choice([
        "invoice_vat", "invoice_vat", "invoice_vat",
        "cash_disbursement", "cash_receipt",
    ])

    base_date = date.today() - timedelta(days=random.randint(0, 60))
    amount = random.randint(500_000, 500_000_000)

    doc: dict = {
        "issue_date": base_date.isoformat(),
        "currency": "VND",
        "doc_type": doc_type,
        "description": random.choice(_DESCRIPTIONS),
    }

    if doc_type == "invoice_vat":
        vat_rate = random.choice([8, 10])
        vat_amount = int(amount * vat_rate / 100)
        doc.update({
            "invoice_no": _gen_invoice_no(),
            "seller_name": random.choice(_COMPANIES),
            "seller_tax_code": _gen_mst(),
            "buyer_name": random.choice(_COMPANIES),
            "buyer_tax_code": _gen_mst(),
            "subtotal": amount,
            "vat_rate": vat_rate,
            "vat_amount": vat_amount,
            "total_amount": amount + vat_amount,
        })
    elif doc_type == "cash_disbursement":
        doc.update({
            "doc_no": f"PC{random.randint(1, 9999):04d}",
            "payer": random.choice(_COMPANIES),
            "payee": f"Nguyễn Văn {chr(random.randint(65, 90))}",
            "amount": amount,
        })
    elif doc_type == "cash_receipt":
        doc.update({
            "doc_no": f"PT{random.randint(1, 9999):04d}",
            "payer": f"Trần Thị {chr(random.randint(65, 90))}",
            "payee": random.choice(_COMPANIES),
            "amount": amount,
        })

    return doc


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name=MINIO_REGION,
    )


def _ensure_bucket(s3) -> None:
    try:
        s3.head_bucket(Bucket=MINIO_BUCKET)
    except ClientError:
        try:
            s3.create_bucket(
                Bucket=MINIO_BUCKET,
                CreateBucketConfiguration={"LocationConstraint": MINIO_REGION},
            )
            print(f"  ✅ Đã tạo bucket '{MINIO_BUCKET}'")
        except Exception as e:
            print(f"  ⚠️ Không tạo được bucket: {e}")


def run_cycle(s3, cycle_num: int, doc_count: int, *, real_docs: list[dict] | None = None, mode: str = "synthetic") -> int:
    """Upload `doc_count` random docs. Returns actual upload count.

    ``mode`` controls document source:
      * ``synthetic`` — generate fake VN docs (default)
      * ``real``      — pick from ``real_docs`` list
      * ``mix``       — random 50/50 between synthetic and real
    """
    uploaded = 0
    for i in range(doc_count):
        # Pick document source
        use_real = False
        if mode == "real" and real_docs:
            use_real = True
        elif mode == "mix" and real_docs:
            use_real = random.random() < 0.5

        if use_real and real_docs:
            doc = random.choice(real_docs).copy()
            tag = "real"
        else:
            doc = _gen_doc()
            tag = "synth"

        key = (
            f"{DROP_PREFIX}"
            f"{date.today().isoformat()}/"
            f"cycle{cycle_num:04d}_{i:02d}_{int(time.time())}_{doc.get('doc_type', 'doc')}_{tag}.json"
        )
        body = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
        s3.put_object(Bucket=MINIO_BUCKET, Key=key, Body=body, ContentType="application/json")
        amt = doc.get("total_amount") or doc.get("amount", 0)
        src_label = "[📊 real]" if (use_real and real_docs) else "[🎲 synth]"
        print(
            f"  📄 [{i+1}/{doc_count}] {src_label} {doc.get('doc_type', '?'):<20s} "
            f"{'VND':>4s} {amt:>15,}  → {key}"
        )
        uploaded += 1
    return uploaded


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload VN docs to MinIO (simulate ERP)")
    parser.add_argument("--interval", type=int, default=30, help="Giây giữa các đợt (mặc định: 30)")
    parser.add_argument("--cycles", type=int, default=5, help="Số đợt (0 = chạy liên tục)")
    parser.add_argument("--min-docs", type=int, default=1, help="Số chứng từ tối thiểu mỗi đợt")
    parser.add_argument("--max-docs", type=int, default=5, help="Số chứng từ tối đa mỗi đợt")
    parser.add_argument(
        "--mode",
        choices=["synthetic", "real", "mix"],
        default="synthetic",
        help="Nguồn chứng từ: synthetic (mặc định), real (đữ liệu thật), mix (trộn)",
    )
    parser.add_argument(
        "--real-data-dir",
        type=Path,
        default=None,
        help="Thư mục chứa JSON đã chuẩn bị bởi prepare_real_vn_data.py",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("🏭 MinIO Upload Simulator — Giả lập ERP gửi chứng từ")
    print(f"   Endpoint: {MINIO_ENDPOINT}")
    print(f"   Bucket:   {MINIO_BUCKET}")
    print(f"   Prefix:   {DROP_PREFIX}")
    print(f"   Interval: {args.interval}s  |  Docs/cycle: {args.min_docs}-{args.max_docs}")
    print(f"   Cycles:   {'∞ (liên tục)' if args.cycles == 0 else args.cycles}")
    print(f"   Mode:     {args.mode}")
    print("=" * 60)

    # --- Load real data JSON files (if applicable) ---
    real_docs: list[dict] | None = None
    if args.mode in ("real", "mix"):
        if not args.real_data_dir or not args.real_data_dir.is_dir():
            print(
                f"  \u274c --mode={args.mode} y\u00eau c\u1ea7u --real-data-dir ch\u1ec9 \u0111\u1ebfn th\u01b0 m\u1ee5c ch\u1ee9a JSON.\n"
                f"  Ch\u1ea1y prepare_real_vn_data.py tr\u01b0\u1edbc \u0111\u1ec3 t\u1ea1o d\u1eef li\u1ec7u."
            )
            return
        json_files = sorted(args.real_data_dir.glob("*.json"))
        if not json_files:
            print(f"  \u26a0\ufe0f Kh\u00f4ng t\u00ecm th\u1ea5y file JSON trong {args.real_data_dir}")
            return
        real_docs = []
        for jf in json_files:
            try:
                real_docs.append(json.loads(jf.read_text(encoding="utf-8")))
            except Exception as e:
                print(f"  \u26a0\ufe0f B\u1ecf qua {jf.name}: {e}")
        print(f"  \ud83d\udcc2 \u0110\u00e3 t\u1ea3i {len(real_docs)} file th\u1eadt t\u1eeb {args.real_data_dir}")

    s3 = _s3_client()
    _ensure_bucket(s3)

    total_uploaded = 0
    cycle = 0
    continuous = args.cycles == 0

    try:
        while continuous or cycle < args.cycles:
            cycle += 1
            n = random.randint(args.min_docs, args.max_docs)
            print(f"\n📦 Đợt {cycle}: tải {n} chứng từ lên MinIO…")
            uploaded = run_cycle(s3, cycle, n, real_docs=real_docs, mode=args.mode)
            total_uploaded += uploaded
            print(f"  ✅ Đã tải {uploaded} chứng từ (tổng: {total_uploaded})")

            if continuous or cycle < args.cycles:
                print(f"  ⏳ Chờ {args.interval}s trước đợt tiếp theo…")
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n\n⏹️  Dừng bởi người dùng.")

    print(f"\n📊 Hoàn tất: {total_uploaded} chứng từ đã tải qua {cycle} đợt.")


if __name__ == "__main__":
    main()
