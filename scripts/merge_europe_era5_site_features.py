#!/usr/bin/env python3
"""Merge validated daily Europe ERA5 site features into one Parquet input."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path


def merge(feature_root: Path, output: Path, *, start_day: str, end_day: str) -> dict[str, object]:
    expected = _days(start_day, end_day)
    files = {path.stem.rsplit("_", 1)[-1]: path for path in feature_root.glob("era5_site_features_*.json")}
    missing = sorted(day.replace("-", "") for day in expected if day.replace("-", "") not in files)
    if missing:
        raise ValueError(f"missing Europe ERA5 site feature days: {len(missing)}")
    raw_rows = []
    for day in expected:
        stamp = day.replace("-", "")
        status = files[stamp].with_suffix(files[stamp].suffix + ".status.json")
        if not status.is_file() or not json.loads(status.read_text(encoding="utf-8")).get("ok"):
            raise ValueError(f"ERA5 site feature status failed for {day}")
        payload = json.loads(files[stamp].read_text(encoding="utf-8"))
        day_rows = payload.get("rows")
        if not isinstance(day_rows, list) or not day_rows:
            raise ValueError(f"ERA5 site features are empty for {day}")
        raw_rows.extend(day_rows)
    rows = _canonicalise(raw_rows)
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("pyarrow is required") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), output, compression="zstd")
    return {"status": "passed", "day_count": len(expected), "row_count": len(rows), "output": str(output)}


ALIASES = {
    "temperature_850_k": ("t_pressure_level_850", "t_pressure_level_850.0"),
    "relative_humidity_850_percent": ("r_pressure_level_850", "r_pressure_level_850.0"),
    "u_850_ms": ("u_pressure_level_850", "u_pressure_level_850.0"),
    "v_850_ms": ("v_pressure_level_850", "v_pressure_level_850.0"),
    "u_925_ms": ("u_pressure_level_925", "u_pressure_level_925.0"),
    "v_925_ms": ("v_pressure_level_925", "v_pressure_level_925.0"),
    "u_700_ms": ("u_pressure_level_700", "u_pressure_level_700.0"),
    "v_700_ms": ("v_pressure_level_700", "v_pressure_level_700.0"),
    "surface_pressure_pa": ("sp", "surface_pressure"),
    "mean_sea_level_pressure_pa": ("msl", "mean_sea_level_pressure"),
    "total_cloud_cover_fraction": ("tcc", "total_cloud_cover"),
    "boundary_layer_height_m": ("blh", "boundary_layer_height"),
    "hourly_precipitation_m": ("tp", "total_precipitation"),
}


def _canonicalise(raw_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: dict[tuple[str, str], dict[str, object]] = {}
    for row in raw_rows:
        radar, time_utc = str(row.get("radar") or ""), str(row.get("time_utc") or "")
        if not radar or not time_utc:
            raise ValueError("ERA5 site row has no radar or UTC time")
        target = merged.setdefault((radar.lower(), time_utc), {"radar": radar.lower(), "time_utc": time_utc})
        for key, value in row.items():
            if value is not None and key not in {"dataset_index", "radar_num"}:
                target[key] = value
    required = set(ALIASES)
    rows = []
    for key, row in sorted(merged.items()):
        for canonical, aliases in ALIASES.items():
            value = next((row.get(alias) for alias in aliases if row.get(alias) is not None), None)
            if value is not None:
                row[canonical] = value
        missing = sorted(name for name in required if row.get(name) is None)
        if missing:
            raise ValueError(f"Europe ERA5 site row {key[0]} {key[1]} lacks predictors: {', '.join(missing)}")
        rows.append(row)
    return rows


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
