#!/usr/bin/env python3
"""Evaluate classification quality on local real-world document bundles.

This benchmark uses practical ground-truth heuristics derived from document
names/paths (invoice, payment request, customs declaration, etc.).

Pipeline simulated:
  prepare_real_vn_data._convert_image_to_doc_json
    -> voucher_ingest._normalize_vn_fixture
    -> voucher_classify._classify_with_confidence
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from openclaw_agent.flows.voucher_classify import _classify_with_confidence  # noqa: E402
from openclaw_agent.flows.voucher_ingest import _normalize_vn_fixture  # noqa: E402
from scripts.prepare_real_vn_data import _ALL_EXTS, _convert_image_to_doc_json  # noqa: E402


def _norm(text: str) -> str:
    lowered = text.lower().replace("đ", "d")
    no_accent = "".join(
        ch for ch in unicodedata.normalize("NFD", lowered)
        if unicodedata.category(ch) != "Mn"
    )
    cleaned = re.sub(r"[^a-z0-9]+", " ", no_accent).strip()
    return f" {cleaned} " if cleaned else " "


def _expected_tag_from_path(path: str) -> str:
    text = _norm(path)
    lower_path = path.lower()

    if any(k in text for k in (" payment request ", " yctt ", " dntt ", " de nghi thanh toan ")):
        return "CASH_DISBURSEMENT"
    if any(k in text for k in (" phieu thu ", " bien lai thu ", " receipt ")):
        return "CASH_RECEIPT"
    if any(k in text for k in (" to khai ", " tokhai ", " tokhaihq7n ", " hq7n ", " hai quan ", " customs ")):
        return "TAX_DECLARATION"
    if any(
        k in text
        for k in (
            " invoice ",
            " hoa don ",
            " vat ",
            " inv ",
            " po ",
            " packing list ",
            " bill of lading ",
            " bill ",
            " c25t ",
        )
    ):
        return "PURCHASE_INVOICE"
    if re.search(r"\bva\d{2}\b", lower_path):
        return "PURCHASE_INVOICE"
    if re.search(r"\bva\d{2}[-_ ]?\d{5,}\b", lower_path):
        return "PURCHASE_INVOICE"
    return "OTHER"


def _collect_files(real_dir: Path) -> list[Path]:
    files = sorted(
        p for p in real_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in _ALL_EXTS
    )
    return files


def _metric_from_counts(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return precision, recall, f1


def run_eval(real_dir: Path) -> dict[str, Any]:
    files = _collect_files(real_dir)
    if not files:
        raise RuntimeError(f"No supported files found in: {real_dir}")

    rows: list[dict[str, Any]] = []
    truths: list[str] = []
    preds: list[str] = []

    for idx, path in enumerate(files):
        rel = str(path.relative_to(real_dir))
        expected = _expected_tag_from_path(rel)
        prepared = _convert_image_to_doc_json(path=path, source_type="local_real_eval", index=idx)
        normalized = _normalize_vn_fixture(prepared)
        pred, conf, reason = _classify_with_confidence(
            normalized.get("voucher_type", ""),
            normalized.get("type_hint", ""),
            normalized.get("description", "") or "",
        )
        truths.append(expected)
        preds.append(pred)
        rows.append(
            {
                "file": rel,
                "expected_tag": expected,
                "predicted_tag": pred,
                "confidence": conf,
                "reason": reason,
                "doc_type": prepared.get("doc_type"),
                "voucher_type": normalized.get("voucher_type"),
                "type_hint": normalized.get("type_hint"),
                "ok": pred == expected,
            }
        )

    labels = sorted(set(truths) | set(preds))
    total = len(rows)
    correct = sum(1 for r in rows if r["ok"])
    accuracy = correct / total if total else 0.0

    per_label: dict[str, dict[str, Any]] = {}
    for label in labels:
        tp = sum(1 for t, p in zip(truths, preds, strict=False) if t == label and p == label)
        fp = sum(1 for t, p in zip(truths, preds, strict=False) if t != label and p == label)
        fn = sum(1 for t, p in zip(truths, preds, strict=False) if t == label and p != label)
        precision, recall, f1 = _metric_from_counts(tp, fp, fn)
        per_label[label] = {
            "support": sum(1 for t in truths if t == label),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

    macro_f1 = sum(v["f1"] for v in per_label.values()) / len(per_label) if per_label else 0.0
    weighted_f1 = (
        sum(v["f1"] * v["support"] for v in per_label.values()) / total
        if total
        else 0.0
    )

    confusion: dict[str, dict[str, int]] = {t: {p: 0 for p in labels} for t in labels}
    for t, p in zip(truths, preds, strict=False):
        confusion[t][p] += 1

    wrong_rows = [r for r in rows if not r["ok"]]
    wrong_rows.sort(key=lambda r: (r["expected_tag"], r["predicted_tag"], r["file"]))

    return {
        "data_dir": str(real_dir),
        "total_files": total,
        "correct": correct,
        "accuracy": round(accuracy, 6),
        "macro_f1": round(macro_f1, 6),
        "weighted_f1": round(weighted_f1, 6),
        "label_distribution_truth": Counter(truths),
        "label_distribution_pred": Counter(preds),
        "per_label": per_label,
        "confusion_matrix": confusion,
        "top_errors": wrong_rows[:100],
        "all_rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate local real data accuracy")
    parser.add_argument("--data-dir", type=Path, required=True, help="Directory with extracted files")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("reports/benchmark/local_real_eval.json"),
        help="Output JSON report path",
    )
    args = parser.parse_args()

    report = run_eval(args.data_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Data dir:   {report['data_dir']}")
    print(f"Files:      {report['total_files']}")
    print(f"Accuracy:   {report['accuracy']:.4f}")
    print(f"Macro-F1:   {report['macro_f1']:.4f}")
    print(f"WeightedF1: {report['weighted_f1']:.4f}")
    print(f"Report:     {args.out}")


if __name__ == "__main__":
    main()
