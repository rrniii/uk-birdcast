#!/usr/bin/env python3
"""Merge and validate daily component-selected GAMM prediction CSVs."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path


REQUIRED_COLUMNS = {
    "time_utc",
    "longitude",
    "latitude",
    "support",
    "mtr_birds_km_h",
    "vid_birds_per_km2",
    "bird_u_ms",
    "bird_v_ms",
}


def _daily_files(root: Path, pulse: str) -> list[Path]:
    files = sorted(root.glob(f"*/predictions_wide_{pulse}.csv"))
    if not files:
        raise ValueError(f"no {pulse} prediction files under {root}")
    return files


def merge(root: Path, pulse: str, output: Path, expected_days: int) -> dict[str, int]:
    files = _daily_files(root, pulse)
    if len(files) != expected_days:
        raise ValueError(f"expected {expected_days} {pulse} days, found {len(files)}")

    output.parent.mkdir(parents=True, exist_ok=True)
    reference_cells: int | None = None
    row_count = 0
    with output.open("w", encoding="utf-8", newline="") as target:
        writer: csv.DictWriter[str] | None = None
        for source in files:
            with source.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                fields = set(reader.fieldnames or [])
                missing = REQUIRED_COLUMNS - fields
                if missing:
                    raise ValueError(f"{source} missing columns: {', '.join(sorted(missing))}")
                if writer is None:
                    writer = csv.DictWriter(target, fieldnames=reader.fieldnames or [])
                    writer.writeheader()
                elif reader.fieldnames != writer.fieldnames:
                    raise ValueError(f"{source} has a different column layout")
                rows = list(reader)
            hours = Counter(row["time_utc"] for row in rows)
            if len(hours) != 24:
                raise ValueError(f"{source} has {len(hours)} hours, expected 24")
            cell_counts = set(hours.values())
            if len(cell_counts) != 1:
                raise ValueError(f"{source} has inconsistent per-hour grid sizes")
            cells = next(iter(cell_counts))
            if reference_cells is None:
                reference_cells = cells
            elif cells != reference_cells:
                raise ValueError(f"{source} grid has {cells} cells, expected {reference_cells}")
            assert writer is not None
            writer.writerows(rows)
            row_count += len(rows)
    return {"days": len(files), "rows": row_count, "cells_per_hour": reference_cells or 0}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--pulse", choices=("lp", "sp"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-days", type=int, default=365)
    args = parser.parse_args()
    print(merge(args.input_dir, args.pulse, args.output, args.expected_days))


if __name__ == "__main__":
    main()
