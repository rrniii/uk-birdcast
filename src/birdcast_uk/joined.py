"""Join hourly VPTS observations to the independent ERA5 feature flow."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PROCESSING_VERSION
from .era5 import SITE_PRESSURE_LEVEL_KEYS, SITE_SINGLE_LEVEL_KEYS
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
    seen_sources: set[tuple[str, str, str]] = set()
    integrity_errors: list[str] = []
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
            source_kind = _source_kind(row)
            if source_kind is None:
                integrity_errors.append(f"{radar} {hour}: unknown ERA5 source kind")
                continue
            source_key = (radar, hour, source_kind)
            if source_key in seen_sources:
                integrity_errors.append(f"{radar} {hour}: duplicate {source_kind} feature row")
                continue
            seen_sources.add(source_key)
            joined = era5_index.setdefault(
                (radar, hour),
                {
                    "radar": radar,
                    "time_utc": hour,
                    "era5_single_levels_available": False,
                    "era5_pressure_levels_available": False,
                },
            )
            if source_kind == "single_levels":
                joined["era5_single_levels_available"] = True
            else:
                joined["era5_pressure_levels_available"] = True
            for key, value in row.items():
                if key in {"radar", "time_utc", "dataset_index", "source_kind"}:
                    continue
                if key in joined and joined[key] != value:
                    integrity_errors.append(f"{radar} {hour}: conflicting ERA5 value for {key}")
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
        missing_predictors = _missing_predictors(era5) if era5 is not None else []
        if era5 is None or missing_predictors:
            unmatched_observed.append(
                {
                    "radar": radar,
                    "time_utc": hour,
                    "reason": "missing ERA5 radar-hour"
                    if era5 is None
                    else "incomplete ERA5 predictor set",
                    "missing_predictors": missing_predictors,
                }
            )
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
    ok = bool(matched_rows) and not unmatched_observed and not integrity_errors
    result = {
        "ok": ok,
        "data_available": ok,
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
        "integrity_error_count": len(integrity_errors),
        "integrity_error_sample": integrity_errors[:25],
        "rows": matched_rows,
    }
    write_json(output, result)
    if ok:
        _update_latest_status(
            output.parent / "status.json",
            latest_era5_time=str(result["last_time_utc"]),
            row_count=len(matched_rows),
            radar_count=radar_count,
        )
    return {
        key: value
        for key, value in result.items()
        if key not in {"rows", "unmatched_observed_sample", "integrity_error_sample"}
    }


def _source_kind(row: dict[str, Any]) -> str | None:
    source_kind = str(row.get("source_kind") or "")
    if source_kind in {"single_levels", "pressure_levels"}:
        return source_kind
    try:
        dataset_index = int(row["dataset_index"])
    except (KeyError, TypeError, ValueError):
        return None
    if dataset_index == 0:
        return "single_levels"
    if dataset_index == 1:
        return "pressure_levels"
    return None


def _missing_predictors(row: dict[str, Any]) -> list[str]:
    missing = []
    if not row.get("era5_single_levels_available"):
        missing.append("single_levels")
    if not row.get("era5_pressure_levels_available"):
        missing.append("pressure_levels")
    for key in sorted(SITE_SINGLE_LEVEL_KEYS | SITE_PRESSURE_LEVEL_KEYS):
        value = row.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            missing.append(key)
    return missing


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


def _update_latest_status(
    path: Path,
    *,
    latest_era5_time: str,
    row_count: int,
    radar_count: int,
) -> None:
    if not path.exists():
        return
    payload = _read_payload(path)
    payload["generated_at_utc"] = utc_now()
    payload["latest_era5_date"] = latest_era5_time[:10].replace("-", "")
    payload["latest_model_feature_time_utc"] = latest_era5_time
    payload["model_feature_summary"] = {
        "status": "ok",
        "row_count": row_count,
        "radar_count": radar_count,
    }
    write_json(path, payload)
