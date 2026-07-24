#!/usr/bin/env python3
"""Merge validated daily Europe ERA5 site features into one Parquet input."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import glob
import json
from pathlib import Path


def merge(feature_root: Path, output: Path, *, start_day: str, end_day: str) -> dict[str, object]:
    expected = _days(start_day, end_day)
    files = {path.stem.rsplit("_", 1)[-1]: path for path in feature_root.glob("era5_site_features_*.json")}
    missing = sorted(day.replace("-", "") for day in expected if day.replace("-", "") not in files)
    if missing:
        raise ValueError(f"missing Europe ERA5 site feature days: {len(missing)}")
    rows = []
    for day in expected:
        stamp = day.replace("-", "")
        status = files[stamp].with_suffix(files[stamp].suffix + ".status.json")
        if not status.is_file() or not json.loads(status.read_text(encoding="utf-8")).get("ok"):
            raise ValueError(f"ERA5 site feature status failed for {day}")
        payload = json.loads(files[stamp].read_text(encoding="utf-8"))
        day_rows = payload.get("rows")
        if not isinstance(day_rows, list) or not day_rows:
            raise ValueError(f"ERA5 site features are empty for {day}")
        rows.extend(day_rows)
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("pyarrow is required") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), output, compression="zstd")
    return {"status": "passed", "day_count": len(expected), "row_count": len(rows), "output": str(output)}


def _days(start_day: str, end_day: str) -> list[str]:
    start, end = date.fromisoformat(start_day), date.fromisoformat(end_day)
    if end < start:
        raise ValueError("end day precedes start day")
    return [(start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start-day", required=True)
    parser.add_argument("--end-day", required=True)
    args = parser.parse_args()
    print(json.dumps(merge(Path(args.feature_root), Path(args.output), start_day=args.start_day, end_day=args.end_day), indent=2))
