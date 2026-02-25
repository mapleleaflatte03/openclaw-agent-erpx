#!/usr/bin/env python3
"""Field-level OCR benchmark evaluator.

Input:
  - Gold labels (JSON) with expected OCR fields.
  - Predictions (JSON) from either:
    1) a flat list/dict of rows, or
    2) prod_e2e_benchmark style report (runs.ingest_response... + ocr_gate.cases).

Output:
  - JSON report with per-field precision/recall/F1 and gate results.
  - Optional HTML summary report.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FIELDS = (
    "doc_type",
    "invoice_no",
    "partner_name",
    "partner_tax_code",
    "total_amount",
    "vat_amount",
    "line_items_count",
)

GATE_THRESHOLDS = {
    "total_amount": 0.90,
    "vat_amount": 0.85,
    "invoice_no": 0.90,
    "partner_tax_code": 0.85,
    "partner_name": 0.80,  # token-level F1
    "line_items_count": 0.80,
    "doc_type_macro_f1": 0.88,
    "false_quarantine_max": 0.10,
}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        num = float(value)
    except Exception:
        return default
    if not math.isfinite(num):
        return default
    return num


def _normalize_key(value: str) -> str:
    text = str(value or "").strip().lower().replace("\\", "/")
    return text


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower().replace("đ", "d")
    text = "".join(ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return text


def _norm_doc_type(value: Any) -> str:
    doc = _normalize_text(value)
    mapping = {
        "invoice": "invoice",
        "invoice vat": "invoice",
        "invoice_vat": "invoice",
        "hoa don": "invoice",
        "other": "other",
        "non invoice": "non_invoice",
        "non_invoice": "non_invoice",
        "report": "non_invoice",
    }
    return mapping.get(doc, doc or "other")


def _parse_amount(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    if isinstance(value, (int, float)):
        amount = _safe_float(value)
        return amount if amount >= 0 else None
    text = str(value).strip()
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.\-]", "", text)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif cleaned.count(",") >= 1 and cleaned.count(".") == 0:
        cleaned = cleaned.replace(",", "")
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    amount = _safe_float(cleaned, default=-1.0)
    if amount < 0:
        return None
    return amount


def _parse_int(value: Any) -> int | None:
    if value in (None, "", "null"):
        return None
    try:
        iv = int(float(value))
    except Exception:
        return None
    return iv if iv >= 0 else None


def _token_f1(expected: str, predicted: str) -> float:
    exp_tokens = Counter(_normalize_text(expected).split())
    pred_tokens = Counter(_normalize_text(predicted).split())
    if not exp_tokens and not pred_tokens:
        return 1.0
    if not exp_tokens or not pred_tokens:
        return 0.0
    overlap = sum((exp_tokens & pred_tokens).values())
    precision = overlap / max(sum(pred_tokens.values()), 1)
    recall = overlap / max(sum(exp_tokens.values()), 1)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _field_match(field_name: str, expected: Any, predicted: Any) -> tuple[bool, float | None]:
    if expected is None:
        return False, None
    if field_name == "doc_type":
        return _norm_doc_type(expected) == _norm_doc_type(predicted), None
    if field_name == "invoice_no":
        a = re.sub(r"[^a-z0-9]", "", _normalize_text(expected))
        b = re.sub(r"[^a-z0-9]", "", _normalize_text(predicted))
        return bool(a) and a == b, None
    if field_name == "partner_tax_code":
        a = re.sub(r"[^0-9a-z]", "", str(expected).lower())
        b = re.sub(r"[^0-9a-z]", "", str(predicted).lower())
        return bool(a) and a == b, None
    if field_name == "partner_name":
        score = _token_f1(str(expected or ""), str(predicted or ""))
        return score >= GATE_THRESHOLDS["partner_name"], score
    if field_name in {"total_amount", "vat_amount"}:
        exp = _parse_amount(expected)
        pred = _parse_amount(predicted)
        if exp is None or pred is None:
            return False, None
        if exp == 0:
            return pred == 0, 1.0 if pred == 0 else 0.0
        rel_err = abs(pred - exp) / max(abs(exp), 1.0)
        return rel_err <= 0.02, 1.0 - min(rel_err, 1.0)
    if field_name == "line_items_count":
        exp = _parse_int(expected)
        pred = _parse_int(predicted)
        if exp is None or pred is None:
            return False, None
        return abs(exp - pred) <= 1, None
    return str(expected).strip() == str(predicted).strip(), None


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_gold_rows(path: Path) -> list[dict[str, Any]]:
    data = _load_json(path)
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("items") or data.get("rows") or data.get("gold") or []
    else:
        rows = []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        file_name = (
            row.get("file")
            or row.get("source_file")
            or row.get("filename")
            or row.get("name")
        )
        if not file_name:
            continue
        item = {field: row.get(field) for field in FIELDS}
        item["status"] = row.get("status")
        item["file"] = str(file_name)
        out.append(item)
    return out


def _flatten_pred_rows_from_report(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def _field_from_ocr_fields(ocr_fields: Any, field_name: str) -> Any:
        if not isinstance(ocr_fields, dict):
            return None
        raw = ocr_fields.get(field_name)
        if isinstance(raw, dict):
            return raw.get("value")
        return raw

    # Source 1: uploaded OCR hard-check cases
    for case in (report.get("ocr_gate", {}) or {}).get("cases", []) or []:
        if not isinstance(case, dict):
            continue
        response = case.get("response") if isinstance(case.get("response"), dict) else {}
        response_ocr_fields = response.get("ocr_fields") if isinstance(response.get("ocr_fields"), dict) else {}
        file_name = case.get("file") or response.get("filename")
        if not file_name:
            continue
        rows.append(
            {
                "file": str(file_name),
                "status": case.get("actual_status") or response.get("status"),
                "doc_type": response.get("doc_type") or _field_from_ocr_fields(response_ocr_fields, "doc_type"),
                "invoice_no": response.get("invoice_no") or _field_from_ocr_fields(response_ocr_fields, "invoice_no"),
                "partner_name": response.get("partner_name") or _field_from_ocr_fields(response_ocr_fields, "partner_name"),
                "partner_tax_code": response.get("partner_tax_code") or _field_from_ocr_fields(response_ocr_fields, "partner_tax_code"),
                "total_amount": response.get("total_amount") or _field_from_ocr_fields(response_ocr_fields, "total_amount"),
                "vat_amount": response.get("vat_amount") or _field_from_ocr_fields(response_ocr_fields, "vat_amount"),
                "line_items_count": response.get("line_items_count") or _field_from_ocr_fields(response_ocr_fields, "line_items_count"),
            }
        )

    # Source 2: ingest payload documents (classification/export runs)
    docs = (
        (report.get("runs", {}) or {})
        .get("ingest_response", {})
        .get("cursor_in", {})
        .get("documents", [])
    )
    for doc in docs or []:
        if not isinstance(doc, dict):
            continue
        doc_ocr_fields = doc.get("ocr_fields") if isinstance(doc.get("ocr_fields"), dict) else {}
        file_name = doc.get("source_file") or doc.get("file")
        if not file_name:
            continue
        rows.append(
            {
                "file": str(file_name),
                "status": doc.get("ocr_status") or doc.get("status"),
                "doc_type": (
                    doc.get("inferred_doc_type")
                    or doc.get("doc_type")
                    or _field_from_ocr_fields(doc_ocr_fields, "doc_type")
                ),
                "invoice_no": doc.get("invoice_no") or _field_from_ocr_fields(doc_ocr_fields, "invoice_no"),
                "partner_name": (
                    doc.get("seller_name")
                    or doc.get("partner_name")
                    or _field_from_ocr_fields(doc_ocr_fields, "partner_name")
                ),
                "partner_tax_code": (
                    doc.get("seller_tax_code")
                    or doc.get("partner_tax_code")
                    or _field_from_ocr_fields(doc_ocr_fields, "partner_tax_code")
                ),
                "total_amount": doc.get("total_amount") or _field_from_ocr_fields(doc_ocr_fields, "total_amount"),
                "vat_amount": doc.get("vat_amount") or _field_from_ocr_fields(doc_ocr_fields, "vat_amount"),
                "line_items_count": doc.get("line_items_count") or _field_from_ocr_fields(doc_ocr_fields, "line_items_count"),
            }
        )
    return rows


def _load_prediction_rows(path: Path) -> list[dict[str, Any]]:
    data = _load_json(path)
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("items") or data.get("rows") or data.get("predictions")
        if not rows:
            rows = _flatten_pred_rows_from_report(data)
    else:
        rows = []

    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        file_name = (
            row.get("file")
            or row.get("source_file")
            or row.get("filename")
            or row.get("name")
        )
        if not file_name:
            continue
        item = {field: row.get(field) for field in FIELDS}
        item["status"] = row.get("status")
        item["file"] = str(file_name)
        out.append(item)
    return out


def _pick_prediction(pred_rows: list[dict[str, Any]], file_name: str) -> dict[str, Any] | None:
    key = _normalize_key(file_name)
    candidates = [row for row in pred_rows if _normalize_key(row.get("file", "")) == key]
    if not candidates:
        # fuzzy by basename
        name = Path(file_name).name.lower()
        candidates = [row for row in pred_rows if Path(str(row.get("file", ""))).name.lower() == name]
    if not candidates:
        return None
    return candidates[-1]


def _doc_type_macro_f1(rows: list[dict[str, Any]]) -> tuple[float, dict[str, dict[str, float]]]:
    labels = sorted(
        set(
            _norm_doc_type(row["gold"].get("doc_type"))
            for row in rows
            if row["gold"].get("doc_type") is not None
        )
        | set(
            _norm_doc_type(row["pred"].get("doc_type"))
            for row in rows
            if row["pred"] is not None and row["pred"].get("doc_type") is not None
        )
    )
    if not labels:
        return 0.0, {}
    metrics: dict[str, dict[str, float]] = {}
    for label in labels:
        tp = fp = fn = 0
        for row in rows:
            exp = _norm_doc_type(row["gold"].get("doc_type"))
            pred = _norm_doc_type((row["pred"] or {}).get("doc_type"))
            if exp == label and pred == label:
                tp += 1
            elif exp != label and pred == label:
                fp += 1
            elif exp == label and pred != label:
                fn += 1
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        metrics[label] = {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
            "support": sum(1 for row in rows if _norm_doc_type(row["gold"].get("doc_type")) == label),
        }
    macro_f1 = sum(item["f1"] for item in metrics.values()) / len(metrics)
    return round(macro_f1, 6), metrics


def evaluate(gold_rows: list[dict[str, Any]], pred_rows: list[dict[str, Any]]) -> dict[str, Any]:
    aligned: list[dict[str, Any]] = []
    for gold in gold_rows:
        pred = _pick_prediction(pred_rows, str(gold["file"]))
        aligned.append({"file": gold["file"], "gold": gold, "pred": pred})

    field_metrics: dict[str, dict[str, Any]] = {}
    for field in FIELDS:
        tp = fp = fn = 0
        support = 0
        token_scores: list[float] = []
        for row in aligned:
            expected = row["gold"].get(field)
            predicted = (row["pred"] or {}).get(field)
            pred_present = predicted not in {None, "", "null"}
            exp_present = expected not in {None, "", "null"}
            if exp_present:
                support += 1
                ok, score = _field_match(field, expected, predicted)
                if ok:
                    tp += 1
                else:
                    fn += 1
                if field == "partner_name" and score is not None:
                    token_scores.append(score)
            elif pred_present:
                fp += 1
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        field_metrics[field] = {
            "support": support,
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
            "token_f1_avg": round(sum(token_scores) / len(token_scores), 6) if token_scores else None,
        }

    macro_f1, doc_type_confusion = _doc_type_macro_f1(aligned)

    invoice_truth = 0
    false_quarantine = 0
    for row in aligned:
        truth_doc_type = _norm_doc_type(row["gold"].get("doc_type"))
        if truth_doc_type != "invoice":
            continue
        invoice_truth += 1
        status = _normalize_text((row["pred"] or {}).get("status"))
        if status in {"review", "quarantined", "low quality", "low_quality", "non invoice", "non_invoice"}:
            false_quarantine += 1
    false_quarantine_rate = false_quarantine / invoice_truth if invoice_truth else 0.0

    gates = {
        "total_amount": field_metrics["total_amount"]["f1"] >= GATE_THRESHOLDS["total_amount"],
        "vat_amount": field_metrics["vat_amount"]["f1"] >= GATE_THRESHOLDS["vat_amount"],
        "invoice_no": field_metrics["invoice_no"]["f1"] >= GATE_THRESHOLDS["invoice_no"],
        "partner_tax_code": field_metrics["partner_tax_code"]["f1"] >= GATE_THRESHOLDS["partner_tax_code"],
        "partner_name": (
            _safe_float(field_metrics["partner_name"].get("token_f1_avg"), 0.0)
            >= GATE_THRESHOLDS["partner_name"]
        ),
        "line_items_count": field_metrics["line_items_count"]["f1"] >= GATE_THRESHOLDS["line_items_count"],
        "doc_type_macro_f1": macro_f1 >= GATE_THRESHOLDS["doc_type_macro_f1"],
        "false_quarantine": false_quarantine_rate <= GATE_THRESHOLDS["false_quarantine_max"],
    }

    result = {
        "generated_at": _now_iso(),
        "thresholds": GATE_THRESHOLDS,
        "counts": {
            "gold_rows": len(gold_rows),
            "prediction_rows": len(pred_rows),
            "aligned_rows": len(aligned),
            "invoice_truth_rows": invoice_truth,
        },
        "field_metrics": field_metrics,
        "doc_type": {
            "macro_f1": macro_f1,
            "confusion": doc_type_confusion,
        },
        "false_quarantine": {
            "count": false_quarantine,
            "total_invoice_truth": invoice_truth,
            "rate": round(false_quarantine_rate, 6),
        },
        "gates": gates,
        "pass": all(gates.values()),
    }
    return result


def _to_html(report: dict[str, Any]) -> str:
    gate_rows = "".join(
        f"<tr><td>{name}</td><td>{'PASS' if ok else 'FAIL'}</td></tr>"
        for name, ok in report["gates"].items()
    )
    field_rows = []
    for field, metric in report["field_metrics"].items():
        f1 = metric.get("f1")
        token_f1 = metric.get("token_f1_avg")
        score = token_f1 if field == "partner_name" and token_f1 is not None else f1
        field_rows.append(
            "<tr>"
            f"<td>{field}</td>"
            f"<td>{metric.get('support')}</td>"
            f"<td>{metric.get('precision')}</td>"
            f"<td>{metric.get('recall')}</td>"
            f"<td>{metric.get('f1')}</td>"
            f"<td>{token_f1 if token_f1 is not None else '-'}</td>"
            f"<td>{score}</td>"
            "</tr>"
        )
    field_table = "".join(field_rows)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>OCR Field-Level Benchmark</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 24px; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
    th {{ background: #f3f4f6; }}
    .pass {{ color: #0a7f2e; font-weight: bold; }}
    .fail {{ color: #b91c1c; font-weight: bold; }}
  </style>
</head>
<body>
  <h1>OCR Field-Level Benchmark</h1>
  <p>Generated at: {report['generated_at']}</p>
  <p>Overall: <span class="{'pass' if report['pass'] else 'fail'}">{'PASS' if report['pass'] else 'FAIL'}</span></p>
  <h2>Field Metrics</h2>
  <table>
    <thead>
      <tr><th>Field</th><th>Support</th><th>Precision</th><th>Recall</th><th>F1</th><th>Token-F1(avg)</th><th>Gate Score</th></tr>
    </thead>
    <tbody>{field_table}</tbody>
  </table>
  <h2>Gate Checks</h2>
  <table>
    <thead><tr><th>Gate</th><th>Result</th></tr></thead>
    <tbody>{gate_rows}</tbody>
  </table>
  <h2>Doc Type</h2>
  <p>Macro-F1: {report['doc_type']['macro_f1']}</p>
  <h2>False Quarantine</h2>
  <p>Rate: {report['false_quarantine']['rate']} ({report['false_quarantine']['count']}/{report['false_quarantine']['total_invoice_truth']})</p>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate OCR field-level benchmark against a gold set.")
    parser.add_argument("--gold", required=True, type=Path, help="Gold labels JSON path.")
    parser.add_argument("--pred", required=True, type=Path, help="Predictions/report JSON path.")
    parser.add_argument("--out", type=Path, default=Path("reports/benchmark/ocr_field_level_latest.json"))
    parser.add_argument("--html", type=Path, default=Path("reports/benchmark/ocr_field_level_latest.html"))
    parser.add_argument("--fail-on-gate", action="store_true", help="Exit non-zero when gate fails.")
    args = parser.parse_args()

    gold_rows = _load_gold_rows(args.gold)
    pred_rows = _load_prediction_rows(args.pred)
    report = evaluate(gold_rows, pred_rows)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.html.parent.mkdir(parents=True, exist_ok=True)
    args.html.write_text(_to_html(report), encoding="utf-8")

    print(f"Gold rows:       {report['counts']['gold_rows']}")
    print(f"Prediction rows: {report['counts']['prediction_rows']}")
    print(f"Aligned rows:    {report['counts']['aligned_rows']}")
    print(f"Pass:            {report['pass']}")
    print(f"JSON report:     {args.out}")
    print(f"HTML report:     {args.html}")

    if args.fail_on_gate and not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
