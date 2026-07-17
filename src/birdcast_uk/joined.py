"""Join hourly VPTS observations to the independent ERA5 feature flow."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from .config import PROCESSING_VERSION
from .static_artifacts import utc_now, write_json


def join_observed_to_era5(
    *,
    observed_hourly: Path,
    era5_dir: Path,
    output: Path,
) -> dict[str, object]:
    observed_payload = _read_payload(observed_hourly)
    observed_rows = observed_payload.get("rows")
    if not isinstance(observed_rows, list) or not observed_rows:
        raise ValueError("observed hourly artifact has no rows")

    era5_index: dict[tuple[str, str], dict[str, Any]] = {}
    source_files = []
    for path in sorted(era5_dir.glob("era5_site_features_[0-9]*.json")):
        payload = _read_payload(path)
        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            continue
        source_files.append(str(path))
        for row in rows:
            if not isinstance(row, dict):
                continue
            radar = str(row.get("radar") or "")
            hour = _hour_key(row.get("time_utc"))
            if not radar or hour is None:
                continue
            joined = era5_index.setdefault(
                (radar, hour),
                {
                    "radar": radar,
                    "time_utc": hour,
                    "era5_single_levels_available": False,
                    "era5_pressure_levels_available": False,
                },
            )
            dataset_index = int(row.get("dataset_index") or 0)
            if dataset_index == 0:
                joined["era5_single_levels_available"] = True
            elif dataset_index == 1:
                joined["era5_pressure_levels_available"] = True
            for key, value in row.items():
                if key in {"radar", "time_utc", "dataset_index"}:
                    continue
                joined[key] = value

    matched_rows = []
    unmatched_observed = []
    for observed in observed_rows:
        if not isinstance(observed, dict):
            continue
        radar = str(observed.get("radar") or "")
        hour = _hour_key(observed.get("time_utc"))
        era5 = era5_index.get((radar, hour or ""))
        if era5 is None:
            unmatched_observed.append({"radar": radar, "time_utc": hour})
            continue
        row = dict(era5)
        for key, value in observed.items():
            if key in {"radar", "time_utc"}:
                continue
            row[f"observed_{key}"] = value
        matched_rows.append(row)

    matched_keys = {(str(row["radar"]), str(row["time_utc"])) for row in matched_rows}
    unmatched_era5_count = len(set(era5_index) - matched_keys)
    radar_count = len({str(row["radar"]) for row in matched_rows})
    ok = bool(matched_rows)
    result = {
        "ok": ok,
        "generated_at_utc": utc_now(),
        "processing_version": PROCESSING_VERSION,
        "observed_source": str(observed_hourly),
        "era5_source_dir": str(era5_dir),
        "era5_source_files": source_files,
        "row_count": len(matched_rows),
        "radar_count": radar_count,
        "first_time_utc": min((str(row["time_utc"]) for row in matched_rows), default=None),
        "last_time_utc": max((str(row["time_utc"]) for row in matched_rows), default=None),
        "unmatched_observed_count": len(unmatched_observed),
        "unmatched_observed_sample": unmatched_observed[:25],
        "unmatched_era5_count": unmatched_era5_count,
        "rows": matched_rows,
    }
    write_json(output, result)
    return {
        key: value
        for key, value in result.items()
        if key not in {"rows", "unmatched_observed_sample"}
    }


def _hour_key(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).replace("Z", "+00:00")
    # Earthkit/xarray serializes nanosecond timestamps. Python 3.10 accepts at
    # most microseconds in datetime.fromisoformat().
    text = re.sub(r"(\.[0-9]{6})[0-9]+", r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return parsed.isoformat().replace("+00:00", "Z")


def _read_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return payload
