from __future__ import annotations

import argparse
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _period_first_day(period: str) -> date:
    y, m = period.split("-")
    return date(int(y), int(m), 1)


def _rand_tax_id() -> str:
    return "03" + "".join(str(random.randint(0, 9)) for _ in range(8))


def _make_invoice_no(i: int) -> str:
    return f"AA/26E-{i:06d}"


def _iso(dt: date | datetime) -> str:
    if isinstance(dt, datetime):
        return dt.replace(microsecond=0).isoformat() + "Z"
    return dt.isoformat()


def _write_pdf(path: Path, lines: list[str]) -> None:
    # Minimal PDF generator (reportlab) to keep PDFs searchable (no OCR required).
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    x, y = 40, 800
    for line in lines:
        c.drawString(x, y, line)
        y -= 18
    c.showPage()
    c.save()


def _write_png(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (900, 1200), color=(255, 255, 255))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 20)
    except Exception:
        font = ImageFont.load_default()
    y = 40
    for line in lines:
        d.text((40, y), line, fill=(0, 0, 0), font=font)
        y += 32
    img.save(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="samples/demo_data")
    ap.add_argument("--period", default=date.today().strftime("%Y-%m"))
    ap.add_argument("--invoices", type=int, default=120)
    ap.add_argument("--vouchers", type=int, default=220)
    ap.add_argument("--journals", type=int, default=180)
    ap.add_argument("--drop-files", type=int, default=200)
    ap.add_argument("--kb-docs", type=int, default=20)
    args = ap.parse_args()

    random.seed(42)

    out_dir = Path(args.out_dir)
    period = args.period
    first = _period_first_day(period)
    updated_at = _iso(datetime.utcnow())

    invoices = []
    for i in range(1, args.invoices + 1):
        inv_date = first + timedelta(days=min(i % 28, 27))
        due = inv_date + timedelta(days=10)
        status = "unpaid" if i % 4 == 0 else "paid"
        invoices.append(
            {
                "invoice_id": f"INV-{i:04d}",
                "invoice_no": _make_invoice_no(i),
                "tax_id": _rand_tax_id(),
                "date": _iso(inv_date),
                "amount": float(1000000 + i * 10000),
                "customer_id": f"CUST-{(i % 30) + 1:03d}",
                "due_date": _iso(due),
                "status": status,
                "email": f"cust{(i % 30) + 1}@example.local",
                "updated_at": updated_at,
            }
        )

    vouchers = []
    for i in range(1, args.vouchers + 1):
        v_date = first + timedelta(days=min(i % 28, 27))
        vouchers.append(
            {
                "voucher_id": f"VCH-{i:04d}",
                "voucher_no": f"PT-{i:06d}",
                "date": _iso(v_date),
                "amount": float(500000 + i * 5000),
                "has_attachment": 0 if i % 12 == 0 else 1,
                "updated_at": updated_at,
            }
        )

    journals = []
    for i in range(1, args.journals + 1):
        j_date = first + timedelta(days=min(i % 28, 27))
        debit = float(100000 + i * 1000)
        credit = debit if i % 17 != 0 else debit + 123
        journals.append(
            {
                "journal_id": f"JRN-{i:04d}",
                "journal_no": f"GL-{i:06d}",
                "date": _iso(j_date),
                "debit_total": debit,
                "credit_total": credit,
                "updated_at": updated_at,
            }
        )

    assets = [
        {
            "asset_id": "AST-0001",
            "asset_no": "TSCD-0001",
            "acquisition_date": _iso(date.today() - timedelta(days=400)),
            "cost": 25000000.0,
            "updated_at": updated_at,
        }
    ]

    close_calendar = []
    for i, name in enumerate(
        [
            "Reconcile bank",
            "Review AP invoices",
            "Review AR aging",
            "Depreciation check",
            "Tax review",
            "Finalize management report",
            "Lock period preparation",
        ],
        start=1,
    ):
        close_calendar.append(
            {
                "id": f"CAL-{i:03d}",
                "period": period,
                "task_name": name,
                "owner_user_id": f"user-{i:03d}",
                "due_date": _iso(first + timedelta(days=20 + (i % 5))),
                "updated_at": updated_at,
            }
        )

    seed = {"invoices": invoices, "vouchers": vouchers, "journals": journals, "assets": assets, "close_calendar": close_calendar}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "erpx_seed.json").write_text(json.dumps(seed, indent=2, ensure_ascii=True), encoding="utf-8")

    # Drop files (attachments): mix PDF and PNG so OCR path is exercised for PNG.
    drop_dir = out_dir / "drop" / "attachments"
    for n in range(args.drop_files):
        inv = invoices[n % len(invoices)]
        lines = [
            "CHUNG TU / SUPPORTING DOCUMENT",
            f"Invoice No: {inv['invoice_no']}",
            f"Tax ID: {inv['tax_id']}",
            f"Date: {inv['date']}",
            f"Total: {int(inv['amount'])}",
            f"Customer Code: {inv['customer_id']}",
        ]
        if n % 3 == 0:
            _write_png(drop_dir / f"{inv['invoice_no']}_{n:03d}.png", lines)
        else:
            _write_pdf(drop_dir / f"{inv['invoice_no']}_{n:03d}.pdf", lines)

    kb_dir = out_dir / "drop" / "kb"
    for i in range(1, args.kb_docs + 1):
        lines = [
            "KE TOAN NOI BO / INTERNAL ACCOUNTING KB",
            f"Title: Process {i}",
            f"Effective Date: {_iso(date.today() - timedelta(days=30 * i))}",
            f"Version: v{i}",
            "This is a demo policy/process document for indexing.",
        ]
        _write_pdf(kb_dir / f"kb_doc_{i:03d}.pdf", lines)

    print(f"Wrote seed: {out_dir / 'erpx_seed.json'}")
    print(f"Wrote drop files: {drop_dir} ({args.drop_files})")
    print(f"Wrote kb docs: {kb_dir} ({args.kb_docs})")


if __name__ == "__main__":
    main()

