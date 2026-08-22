#!/usr/bin/env python3
"""Generate the deterministic fictional restaurant dataset."""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.data_generator import generate_dataset


if __name__ == "__main__":
    output = PROJECT_ROOT / "data" / "generated"
    summary = generate_dataset(output)
    print(f"Generated fictional dataset in {output}")
    for name, rows in summary.items():
        print(f"  {name}: {rows:,} rows")
