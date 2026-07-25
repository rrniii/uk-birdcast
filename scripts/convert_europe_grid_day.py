#!/usr/bin/env python3
"""Convert one audited Europe grid partition to a temporary R-readable CSV."""

from __future__ import annotations

import argparse
from pathlib import Path


def convert(source: Path, output: Path) -> int:
    try:
        import pyarrow.csv as csv
        import pyarrow.parquet as parquet
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("pyarrow is required for Europe grid conversion") from exc
    table = parquet.read_table(source)
    required = {"time_utc", "longitude", "latitude", "nearest_radar_km"}
    missing = required - set(table.column_names)
    if missing:
        raise ValueError(f"Europe grid day is missing columns: {', '.join(sorted(missing))}")
    if table.num_rows == 0:
        raise ValueError("Europe grid day has no rows")
    output.parent.mkdir(parents=True, exist_ok=True)
    csv.write_csv(table, output)
    return int(table.num_rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(convert(Path(args.source), Path(args.output)))
