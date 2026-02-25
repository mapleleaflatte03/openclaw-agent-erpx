#!/usr/bin/env python3
"""Generate synthetic benchmark cases for Accounting Agent Layer ERPX.

Creates Vietnamese-language contract PDFs, amendment EML files, and
ground-truth JSON for each case.  Output is compatible with the benchmark
runner and scoring pipeline.

Usage:
  python generate_synthetic_cases.py --cases 50 --out-dir data/benchmark/cases --manifest data/benchmark/manifests/cases.jsonl
  python generate_synthetic_cases.py --manifest-only --dir data/benchmark/cases --out data/benchmark/manifests/cases.jsonl
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import string
import subprocess
import sys
import textwrap
import uuid
from email.message import EmailMessage
from pathlib import Path

# ---------------------------------------------------------------------------
# PDF generation (reportlab)
# ---------------------------------------------------------------------------
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

# ---------------------------------------------------------------------------
# Audio generation (optional — espeak-ng + ffmpeg)
# ---------------------------------------------------------------------------
AUDIO_ENABLED = os.environ.get("BENCHMARK_AUDIO", "0") == "1"
HAS_ESPEAK = False
if AUDIO_ENABLED:
    try:
        subprocess.run(["espeak-ng", "--version"], capture_output=True, check=True)
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        HAS_ESPEAK = True
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("WARN: espeak-ng/ffmpeg not found — audio generation disabled.", file=sys.stderr)
        HAS_ESPEAK = False

# ---------------------------------------------------------------------------
# Constants for realistic VN contract data
# ---------------------------------------------------------------------------
VN_NAMES = [
    "Công ty TNHH Minh Phát",
    "Công ty CP Xây dựng Đại Thành",
    "Công ty TNHH TM DV Hoàng Gia",
    "Công ty CP Vật liệu Thăng Long",
    "Công ty TNHH Công nghệ Green Star",
    "Công ty CP Thương mại Sài Gòn",
    "Công ty TNHH Sản xuất Đông Á",
    "Công ty CP Logistics Biển Đông",
    "Công ty TNHH Dược phẩm Hà Nội",
    "Công ty CP Bất động sản Vạn Phúc",
    "Công ty TNHH Cơ khí Phương Nam",
    "Công ty CP Nông nghiệp Xanh",
    "Tập đoàn Vinatex",
    "Công ty TNHH Điện tử FPT",
    "Công ty CP Năng lượng SolarVN",
]

OBLIGATION_TYPES = ["payment", "delivery", "warranty_retention", "penalty", "early_discount"]
CURRENCIES = ["VND", "USD", "EUR"]
CURRENCY_RANGES = {
    "VND": (50_000_000, 5_000_000_000),
    "USD": (10_000, 1_000_000),
    "EUR": (10_000, 800_000),
}
RISK_LEVELS = ["low", "medium", "high"]
RISK_WEIGHTS = [0.5, 0.35, 0.15]
GATING_TIERS = [1, 2, 3]
GATING_TIER_WEIGHTS = [0.5, 0.35, 0.15]

BASE_DATE = datetime.date(2026, 3, 1)


def _rand_tax_id() -> str:
    return "".join(random.choices(string.digits, k=10))


def _rand_date_offset(min_days: int = 15, max_days: int = 365) -> datetime.date:
    return BASE_DATE + datetime.timedelta(days=random.randint(min_days, max_days))


def _rand_amount(currency: str) -> float:
    lo, hi = CURRENCY_RANGES[currency]
    raw = random.uniform(lo, hi)
    if currency == "VND":
        return round(raw / 1_000_000) * 1_000_000
    return round(raw, 2)


# ---------------------------------------------------------------------------
# Obligation generation
# ---------------------------------------------------------------------------

def _generate_obligations(rng: random.Random) -> list[dict]:
    """Generate 1-5 obligations for a case."""
    n = rng.randint(1, 5)
    currency = rng.choices(CURRENCIES, weights=[0.7, 0.2, 0.1])[0]
    obligations = []

    for _i in range(n):
        otype = rng.choice(OBLIGATION_TYPES)
        obl: dict = {
            "type": otype,
            "currency": currency,
            "due_date": str(_rand_date_offset()),
        }

        if otype == "payment":
            obl["amount"] = _rand_amount(currency)
            obl["milestone"] = rng.choice([
                "delivery_phase_1", "delivery_phase_2", "final_acceptance",
                "signing", "upon_invoice", "30_days_net",
            ])
            obl["conditions"] = []
        elif otype == "early_discount":
            obl["amount"] = None
            obl["amount_percent"] = round(rng.uniform(1.0, 3.0), 1)
            obl["milestone"] = "early_payment"
            obl["conditions"] = [f"early_discount_{obl['amount_percent']}pct_if_before_{obl['due_date'].replace('-', '')}"]
        elif otype == "penalty":
            obl["amount"] = None
            obl["amount_percent"] = round(rng.uniform(0.03, 0.1), 3)
            obl["milestone"] = "late_payment_penalty"
            obl["conditions"] = [f"penalty_{obl['amount_percent']}pct_per_day"]
        elif otype == "warranty_retention":
            obl["amount"] = None
            obl["amount_percent"] = round(rng.uniform(5.0, 10.0), 1)
            obl["milestone"] = "warranty_end"
            obl["conditions"] = [f"retain_{obl['amount_percent']}pct_until_warranty"]
        elif otype == "delivery":
            obl["amount"] = _rand_amount(currency)
            obl["milestone"] = rng.choice(["delivery_goods", "installation_complete", "commissioning"])
            obl["conditions"] = []

        obligations.append(obl)

    return obligations


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

def _generate_pdf(case_dir: Path, partner: str, tax_id: str, contract_code: str,
                  obligations: list[dict], rng: random.Random) -> Path:
    """Generate a Vietnamese contract PDF using reportlab."""
    pdf_path = case_dir / "sources" / "contract.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    if not HAS_REPORTLAB:
        # Fallback: simple text file with .pdf extension (for CI without reportlab)
        lines = [f"HỢP ĐỒNG: {contract_code}", f"Đối tác: {partner}", f"MST: {tax_id}", ""]
        for i, obl in enumerate(obligations, 1):
            lines.append(f"Điều {i}: {_obligation_to_vn(obl)}")
        pdf_path.write_text("\n".join(lines), encoding="utf-8")
        return pdf_path

    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    w, h = A4
    y = h - 30 * mm

    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(w / 2, y, "HOP DONG KINH TE")
    y -= 10 * mm
    c.setFont("Helvetica", 11)
    c.drawCentredString(w / 2, y, f"So: {contract_code}")
    y -= 15 * mm

    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, y, f"Doi tac: {partner}")
    y -= 6 * mm
    c.drawString(20 * mm, y, f"Ma so thue: {tax_id}")
    y -= 6 * mm
    c.drawString(20 * mm, y, f"Ngay ky: {BASE_DATE.isoformat()}")
    y -= 12 * mm

    for i, obl in enumerate(obligations, 1):
        if y < 40 * mm:
            c.showPage()
            y = h - 30 * mm
            c.setFont("Helvetica", 10)

        c.setFont("Helvetica-Bold", 10)
        c.drawString(20 * mm, y, f"Dieu {i}. {obl['type'].upper()}")
        y -= 6 * mm
        c.setFont("Helvetica", 9)

        desc = _obligation_to_vn(obl)
        for line in textwrap.wrap(desc, width=90):
            c.drawString(25 * mm, y, line)
            y -= 5 * mm
        y -= 4 * mm

    c.save()
    return pdf_path


def _obligation_to_vn(obl: dict) -> str:
    """Render obligation as Vietnamese text."""
    parts = []
    otype = obl["type"]
    if otype == "payment":
        parts.append(f"Thanh toan {_fmt_amount(obl)} {obl['currency']}")
        parts.append(f"han {obl['due_date']}")
        parts.append(f"dot: {obl.get('milestone', 'N/A')}")
    elif otype == "early_discount":
        parts.append(f"Chiet khau thanh toan som {obl.get('amount_percent', 0)}%")
        parts.append(f"truoc ngay {obl['due_date']}")
    elif otype == "penalty":
        parts.append(f"Phat cham thanh toan {obl.get('amount_percent', 0)}% moi ngay")
    elif otype == "warranty_retention":
        parts.append(f"Giu lai bao hanh {obl.get('amount_percent', 0)}%")
        parts.append(f"den het han bao hanh {obl['due_date']}")
    elif otype == "delivery":
        parts.append(f"Giao hang tri gia {_fmt_amount(obl)} {obl['currency']}")
        parts.append(f"han {obl['due_date']}")
        parts.append(f"dot: {obl.get('milestone', 'N/A')}")
    for cond in obl.get("conditions", []):
        parts.append(f"dk: {cond}")
    return "; ".join(parts)


def _fmt_amount(obl: dict) -> str:
    amt = obl.get("amount")
    if amt is None:
        return f"{obl.get('amount_percent', '?')}%"
    if obl.get("currency") == "VND":
        return f"{amt:,.0f}"
    return f"{amt:,.2f}"


# ---------------------------------------------------------------------------
# EML generation
# ---------------------------------------------------------------------------

def _generate_eml(case_dir: Path, partner: str, contract_code: str,
                  obligations: list[dict], rng: random.Random) -> Path:
    eml_path = case_dir / "sources" / "amendment.eml"
    eml_path.parent.mkdir(parents=True, exist_ok=True)

    msg = EmailMessage()
    msg["Subject"] = f"Bo sung dieu khoan - {contract_code}"
    msg["From"] = f"kd@{partner.lower().replace(' ', '').replace(',', '')[:15]}.vn"
    msg["To"] = "ke.toan@accounting-agent.vn"
    msg["Date"] = (datetime.datetime.now(tz=datetime.timezone.utc)).strftime("%a, %d %b %Y %H:%M:%S %z")
    msg["Message-ID"] = f"<{uuid.uuid4()}@accounting-agent.vn>"

    body_lines = [
        "Kinh gui Phong Ke toan,",
        "",
        f"Lien quan den hop dong {contract_code}, chung toi xac nhan cac dieu khoan bo sung:",
        "",
    ]
    for i, obl in enumerate(obligations, 1):
        body_lines.append(f"{i}. {_obligation_to_vn(obl)}")
    body_lines += ["", "Tran trong,", partner]
    msg.set_content("\n".join(body_lines))

    eml_path.write_text(msg.as_string(), encoding="utf-8")
    return eml_path


# ---------------------------------------------------------------------------
# Audio generation (optional)
# ---------------------------------------------------------------------------

def _generate_audio(case_dir: Path, obligations: list[dict]) -> Path | None:
    if not AUDIO_ENABLED or not HAS_ESPEAK:
        return None
    wav_path = case_dir / "sources" / "recording.wav"
    wav_path.parent.mkdir(parents=True, exist_ok=True)

    text = ". ".join(_obligation_to_vn(o) for o in obligations)
    try:
        subprocess.run(
            ["espeak-ng", "-v", "vi", "-w", str(wav_path), text],
            capture_output=True, check=True, timeout=30,
        )
        return wav_path
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Ground-truth + meta
# ---------------------------------------------------------------------------

def _build_truth(case_id: str, obligations: list[dict], rng: random.Random) -> dict:
    risk = rng.choices(RISK_LEVELS, weights=RISK_WEIGHTS)[0]
    gating_tier = rng.choices(GATING_TIERS, weights=GATING_TIER_WEIGHTS)[0]
    # High-risk => tier >=2
    if risk == "high" and gating_tier < 2:
        gating_tier = 2

    truth_obls = []
    for obl in obligations:
        entry = {
            "type": obl["type"],
            "amount": obl.get("amount"),
            "amount_percent": obl.get("amount_percent"),
            "currency": obl["currency"],
            "due_date": obl["due_date"],
            "milestone": obl.get("milestone"),
            "conditions": obl.get("conditions", []),
        }
        truth_obls.append(entry)

    evidence_anchors = [
        {"source": "contract.pdf", "page": 1, "line": 1},
    ]
    approvals_required = 1
    if risk == "high" or gating_tier >= 3:
        approvals_required = 2

    return {
        "case_id": case_id,
        "obligations": truth_obls,
        "evidence_anchors": evidence_anchors,
        "expected_gating_tier": gating_tier,
        "expected_risk": risk,
        "expected_approvals_required": approvals_required,
    }


def _build_meta(case_id: str, source: str = "synthetic") -> dict:
    return {
        "source": source,
        "license": "CC0-1.0",
        "generated_at": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
        "generator_version": "1.0.0",
    }


# ---------------------------------------------------------------------------
# Main generation logic
# ---------------------------------------------------------------------------

def generate_cases(n: int, out_dir: Path, manifest_path: Path | None = None) -> list[dict]:
    """Generate n synthetic cases. Returns manifest entries."""
    rng = random.Random(42)  # reproducible
    out_dir.mkdir(parents=True, exist_ok=True)

    # Count existing cases to offset IDs
    existing = sorted(
        [d.name for d in out_dir.iterdir() if d.is_dir() and d.name.startswith("case_")]
    )
    offset = len(existing)

    manifest_entries: list[dict] = []

    # Re-index existing for manifest
    for cid in existing:
        cdir = out_dir / cid
        truth_path = cdir / "truth.json"
        has_pdf = (cdir / "sources" / "contract.pdf").exists()
        has_eml = (cdir / "sources" / "amendment.eml").exists()
        has_audio = (cdir / "sources" / "recording.wav").exists()
        obl_count = 0
        if truth_path.exists():
            truth = json.loads(truth_path.read_text())
            obl_count = len(truth.get("obligations", []))
        manifest_entries.append({
            "case_id": cid,
            "has_pdf": has_pdf,
            "has_eml": has_eml,
            "has_audio": has_audio,
            "obligation_count": obl_count,
        })

    for i in range(n):
        case_num = offset + i + 1
        case_id = f"case_{case_num:04d}"
        case_dir = out_dir / case_id

        if case_dir.exists():
            continue

        case_dir.mkdir(parents=True, exist_ok=True)

        partner = rng.choice(VN_NAMES)
        tax_id = _rand_tax_id()
        contract_code = f"HD-{rng.randint(2024, 2026)}-{rng.randint(1000, 9999)}"
        obligations = _generate_obligations(rng)

        _generate_pdf(case_dir, partner, tax_id, contract_code, obligations, rng)
        _generate_eml(case_dir, partner, contract_code, obligations, rng)
        audio_path = _generate_audio(case_dir, obligations)

        truth = _build_truth(case_id, obligations, rng)
        (case_dir / "truth.json").write_text(json.dumps(truth, indent=2, ensure_ascii=False), encoding="utf-8")

        meta = _build_meta(case_id)
        (case_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        manifest_entries.append({
            "case_id": case_id,
            "has_pdf": True,
            "has_eml": True,
            "has_audio": audio_path is not None,
            "obligation_count": len(obligations),
        })

    if manifest_path:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            for entry in manifest_entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return manifest_entries


def write_manifest_only(cases_dir: Path, manifest_path: Path) -> None:
    """Re-generate the manifest from existing cases on disk."""
    entries = []
    for cdir in sorted(cases_dir.iterdir()):
        if not cdir.is_dir():
            continue
        cid = cdir.name
        truth_path = cdir / "truth.json"
        obl_count = 0
        if truth_path.exists():
            truth = json.loads(truth_path.read_text())
            obl_count = len(truth.get("obligations", []))
        entries.append({
            "case_id": cid,
            "has_pdf": (cdir / "sources" / "contract.pdf").exists(),
            "has_eml": (cdir / "sources" / "amendment.eml").exists(),
            "has_audio": (cdir / "sources" / "recording.wav").exists(),
            "obligation_count": obl_count,
        })
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"Manifest written: {manifest_path} ({len(entries)} entries)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic benchmark cases")
    parser.add_argument("--cases", type=int, default=50)
    parser.add_argument("--out-dir", type=str, default="data/benchmark/cases")
    parser.add_argument("--manifest", type=str, default="data/benchmark/manifests/cases.jsonl")
    parser.add_argument("--manifest-only", action="store_true")
    parser.add_argument("--dir", type=str, help="Alias for --out-dir (used with --manifest-only)")
    parser.add_argument("--out", type=str, help="Alias for --manifest (used with --manifest-only)")
    args = parser.parse_args()

    if args.manifest_only:
        d = Path(args.dir or args.out_dir)
        m = Path(args.out or args.manifest)
        write_manifest_only(d, m)
        return

    out_dir = Path(args.out_dir)
    manifest = Path(args.manifest)
    entries = generate_cases(args.cases, out_dir, manifest)
    print(f"Generated {len(entries)} total cases in {out_dir}")


if __name__ == "__main__":
    main()
