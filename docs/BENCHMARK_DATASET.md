# Benchmark Dataset — Accounting Agent Layer ERPX

## Purpose

Validate all 9 workflows (8 standard + `contract_obligation`) against realistic data,
measuring accuracy, fail rate, and latency. Gate CI merges on KPI thresholds.

## Dataset Sources (priority order)

| Priority | Source                    | How                                                        |
|----------|---------------------------|------------------------------------------------------------|
| A        | Real anonymized data      | Place in `data/real_anonymized/` — used directly           |
| B        | Kaggle public datasets    | `scripts/benchmark/import_kaggle_dataset.py` — requires `KAGGLE_USERNAME` + `KAGGLE_KEY` |
| C        | Synthetic generator       | `scripts/benchmark/generate_synthetic_cases.py` — always available |

The fetch script `scripts/benchmark/fetch_or_generate_dataset.sh` tries A → B → C automatically.

## Case Schema

Each case lives in `data/benchmark/cases/{case_id}/`:

```
cases/
  case_001/
    sources/
      contract.pdf          # Vietnamese contract (reportlab-generated or real)
      amendment.eml          # Email thread with addendum
      recording.wav          # (optional, feature-flagged) audio reading of terms
    truth.json               # Ground-truth obligations + expected outputs
    meta.json                # License, source, timestamps
```

### truth.json schema

```json
{
  "case_id": "case_001",
  "obligations": [
    {
      "type": "payment",
      "amount": 150000000,
      "currency": "VND",
      "due_date": "2026-03-15",
      "milestone": "delivery_phase_1",
      "conditions": ["early_discount_2pct_if_before_20260301"]
    }
  ],
  "evidence_anchors": [
    {"source": "contract.pdf", "page": 3, "line": 12},
    {"source": "amendment.eml", "message_id": "<abc@example.com>"}
  ],
  "expected_gating_tier": 1,
  "expected_risk": "low",
  "expected_approvals_required": 1
}
```

### meta.json schema

```json
{
  "source": "synthetic",
  "license": "CC0-1.0",
  "generated_at": "2026-02-07T00:00:00Z",
  "generator_version": "1.0.0"
}
```

### Manifest

`data/benchmark/manifests/cases.jsonl` — one JSON object per line:
```json
{"case_id": "case_001", "has_pdf": true, "has_eml": true, "has_audio": false, "obligation_count": 3}
```

## Synthetic Generator Details

File: `scripts/benchmark/generate_synthetic_cases.py`

Generates Vietnamese-language contracts with:
- 1–3 payment tranches (amounts 50M–5B VND or $10K–$1M USD)
- Early payment discounts (1–3%)
- Late payment penalties (0.03–0.1%/day)
- Warranty retention (5–10%)
- Foreign currency clauses (USD, EUR, JPY)
- Milestone-based and calendar-based due dates

EML generation:
- Amendment emails referencing the contract
- Confirmation/acknowledgment chains
- Valid RFC 2822 headers

Audio (optional, feature-flagged via `BENCHMARK_AUDIO=1`):
- Uses `espeak-ng` + `ffmpeg` to generate WAV from obligation text
- Auto-disabled if dependencies missing — does not fail the benchmark

## KPI Definitions

| Metric          | Formula                                            | Threshold |
| --------------- | -------------------------------------------------- | --------- |
| `accuracy`      | (correct obligations) / (total ground-truth)       | ≥ 0.85    |
| `fail_rate`     | (failed runs) / (total runs)                       | < 0.05    |
| `avg_latency`   | mean(run_duration_seconds)                         | report    |
| `p95_latency`   | p95(run_duration_seconds)                          | report    |

An obligation is "correct" if `type`, `amount`, `currency`, `due_date`, and `gating_tier` all match ground-truth.

## Running the Benchmark

```bash
# 1. Generate or fetch dataset (50 cases)
./scripts/benchmark/fetch_or_generate_dataset.sh --cases 50

# 2. Run benchmark against local docker compose
python scripts/benchmark/run_benchmark.py --cases 50 --target docker \
  --out reports/benchmark/latest.json

# 3. Generate report
python scripts/benchmark/report_benchmark.py \
  --in reports/benchmark/latest.json \
  --out-md reports/benchmark/latest.md

# 4. Quick smoke test (5 cases, pytest)
python -m pytest tests/benchmark/test_benchmark_smoke.py -q
```

## CI Integration

- **PR gate**: benchmark smoke (5 cases) runs on every PR — must pass.
- **Main/nightly**: full 50–100 case benchmark with KPI gates.
- Reports uploaded as GitHub Actions artifacts.

## License & Compliance

- Synthetic data: CC0-1.0 (public domain).
- Kaggle data: license noted in `data/third_party/<dataset>/LICENSE.md`.
- **No PII** in any dataset — all names/addresses are fictional.
- Source and license always recorded in `meta.json`.
