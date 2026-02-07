#!/usr/bin/env python3
"""Import Kaggle dataset(s) and transform into benchmark case format.

Requires: kaggle CLI or KAGGLE_USERNAME + KAGGLE_KEY env vars.

Usage:
  python import_kaggle_dataset.py --cases 50 --out-dir data/benchmark/cases
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _check_kaggle() -> bool:
    """Check if kaggle credentials are available."""
    if os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"):
        return True
    kaggle_json = Path.home() / ".kaggle" / "kaggle.json"
    return kaggle_json.exists()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Kaggle datasets for benchmarking")
    parser.add_argument("--cases", type=int, default=50)
    parser.add_argument("--out-dir", type=str, default="data/benchmark/cases")
    parser.parse_args()

    if not _check_kaggle():
        print("ERROR: No Kaggle credentials found.", file=sys.stderr)
        print("Set KAGGLE_USERNAME + KAGGLE_KEY or create ~/.kaggle/kaggle.json", file=sys.stderr)
        sys.exit(1)

    try:
        import kaggle  # noqa: F401
    except ImportError:
        print("ERROR: kaggle package not installed. Run: pip install kaggle", file=sys.stderr)
        sys.exit(1)

    # Placeholder: in a real scenario, download invoice/receipt/contract datasets.
    # For now, this is a stub that reports "not enough data" so the fallback to
    # synthetic generation kicks in.
    #
    # To extend: use kaggle.api to download a dataset, parse it, and create
    # case directories with truth.json files.
    print("Kaggle import: no suitable contract/invoice datasets configured yet.", file=sys.stderr)
    print("Falling back to synthetic generation.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
