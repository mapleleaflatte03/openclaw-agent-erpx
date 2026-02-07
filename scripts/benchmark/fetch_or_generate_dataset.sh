#!/usr/bin/env bash
# fetch_or_generate_dataset.sh — Populate benchmark dataset (fallback: real → Kaggle → synthetic)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DATA_DIR="$REPO_ROOT/data/benchmark"
CASES_DIR="$DATA_DIR/cases"
MANIFEST="$DATA_DIR/manifests/cases.jsonl"

CASES=${CASES:-50}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cases) CASES="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

mkdir -p "$CASES_DIR" "$DATA_DIR/manifests"

# -------------------------------------------------------------------
# Source A: real anonymized data
# -------------------------------------------------------------------
REAL_DIR="$REPO_ROOT/data/real_anonymized"
if [[ -d "$REAL_DIR" ]] && [[ "$(find "$REAL_DIR" -maxdepth 1 -type d | wc -l)" -gt 1 ]]; then
  echo "[A] Found real anonymized data in $REAL_DIR — linking..."
  for d in "$REAL_DIR"/*/; do
    cid="$(basename "$d")"
    if [[ ! -d "$CASES_DIR/$cid" ]]; then
      ln -sfn "$d" "$CASES_DIR/$cid"
    fi
  done
  existing=$(find "$CASES_DIR" -maxdepth 1 -type d -o -type l | grep -v "^$CASES_DIR$" | wc -l)
  if [[ "$existing" -ge "$CASES" ]]; then
    echo "[A] Real data has $existing cases (>=$CASES). Done."
    python "$SCRIPT_DIR/generate_synthetic_cases.py" --manifest-only --dir "$CASES_DIR" --out "$MANIFEST"
    exit 0
  fi
  echo "[A] Real data has $existing cases, need $CASES. Supplementing..."
  CASES=$((CASES - existing))
fi

# -------------------------------------------------------------------
# Source B: Kaggle datasets
# -------------------------------------------------------------------
KAGGLE_CREDS="${HOME}/.kaggle/kaggle.json"
if [[ -n "${KAGGLE_USERNAME:-}" && -n "${KAGGLE_KEY:-}" ]] || [[ -f "$KAGGLE_CREDS" ]]; then
  echo "[B] Kaggle credentials found. Attempting Kaggle import..."
  if python "$SCRIPT_DIR/import_kaggle_dataset.py" --cases "$CASES" --out-dir "$CASES_DIR" 2>&1; then
    existing=$(find "$CASES_DIR" -maxdepth 1 -type d | grep -v "^$CASES_DIR$" | wc -l)
    if [[ "$existing" -ge "$CASES" ]]; then
      echo "[B] Kaggle import produced $existing cases. Done."
      python "$SCRIPT_DIR/generate_synthetic_cases.py" --manifest-only --dir "$CASES_DIR" --out "$MANIFEST"
      exit 0
    fi
    echo "[B] Kaggle import got $existing cases, supplementing with synthetic..."
    CASES=$((CASES - existing))
  else
    echo "[B] Kaggle import failed, falling back to synthetic."
  fi
else
  echo "[B] No Kaggle credentials. Skipping."
fi

# -------------------------------------------------------------------
# Source C: Synthetic generator
# -------------------------------------------------------------------
echo "[C] Generating $CASES synthetic cases..."
python "$SCRIPT_DIR/generate_synthetic_cases.py" \
  --cases "$CASES" \
  --out-dir "$CASES_DIR" \
  --manifest "$MANIFEST"

TOTAL=$(find "$CASES_DIR" -maxdepth 1 -type d | grep -v "^$CASES_DIR$" | wc -l)
echo "=== Dataset ready: $TOTAL cases in $CASES_DIR ==="
echo "Manifest: $MANIFEST"
