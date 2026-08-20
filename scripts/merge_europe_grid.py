#!/usr/bin/env python3
"""Merge a complete model-year of Europe ERA5 grid files into R input CSV."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path


def merge(grid_root: Path, output: Path, *, start_day: str, end_day: str) -> dict[str, object]:
    expected = [
        date.fromisoformat(start_day) + timedelta(days=index)
        for index in range((date.fromisoformat(end_day) - date.fromisoformat(start_day)).days + 1)
    ]
    files = [grid_root / f"era5_grid_{day:%Y%m%d}.parquet" for day in expected]
    missing = [str(path) for path in files if not path.is_file() or path.stat().st_size == 0]
    if missing:
        raise ValueError(f"Europe grid is incomplete: {len(missing)} daily files are missing")
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("duckdb is required") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        source = "[" + ",".join(repr(str(path)) for path in files) + "]"
        con.execute(
            f"COPY (SELECT * FROM read_parquet({source}, union_by_name=true)) TO ? (HEADER, DELIMITER ',')",
            [str(output)],
        )
        rows = con.execute(
            f"SELECT count(*) FROM read_parquet({source}, union_by_name=true)"
        ).fetchone()[0]
    finally:
        con.close()
    return {
        "status": "passed",
        "day_count": len(files),
        "row_count": int(rows),
        "output": str(output),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--grid-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start-day", required=True)
    parser.add_argument("--end-day", required=True)
    args = parser.parse_args()
    print(
        merge(
            Path(args.grid_root), Path(args.output), start_day=args.start_day, end_day=args.end_day
        )
    )
