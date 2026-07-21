"""Historical ERA5-driven UK BirdCast reanalysis contracts.

This module deliberately keeps model execution on JASMIN batch compute.  It
prepares pulse-separated, all-hour input tables; records model-selection
evidence; and publishes small daily browser assets.  No calendar, season,
sunrise, sunset, or clock-time variable is emitted for model fitting.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import random
import shutil
from tempfile import mkdtemp
from typing import Any

from .config import PROCESSING_VERSION
from .scales import log_colour_scale
from .static_artifacts import utc_now, write_json


REANALYSIS_SCHEMA_VERSION = "live-uk-bird-maps-gam-era5-1.1"
MODEL_FAMILIES = ("gamm", "xgboost")
PULSES = ("lp", "sp")
INTENSITY_TARGETS = ("mtr_birds_km_h", "vid_birds_per_km2")
VECTOR_TARGETS = ("bird_u_ms", "bird_v_ms")
ERA5_FEATURES = (
    "temperature_850_k",
    "relative_humidity_850_percent",
    "u_850_ms",
    "v_850_ms",
    "surface_pressure_pa",
    "mean_sea_level_pressure_pa",
    "total_cloud_cover_fraction",
    "boundary_layer_height_m",
    "hourly_precipitation_m",
)
OPTIONAL_ERA5_FEATURES = (
    "u_925_ms",
    "v_925_ms",
    "u_700_ms",
    "v_700_ms",
)


class _ReservoirSampler:
    """Deterministic bounded sample for archive-scale percentile estimates."""

    def __init__(self, *, limit: int, seed: int) -> None:
        self.limit = limit
        self.values: list[float] = []
        self.seen = 0
        self.random = random.Random(seed)

    def add(self, value: float) -> None:
        if not math.isfinite(value):
            return
        self.seen += 1
        if len(self.values) < self.limit:
            self.values.append(value)
            return
        replacement = self.random.randrange(self.seen)
        if replacement < self.limit:
            self.values[replacement] = value


def _model_colour_scales(frames: list[dict[str, Any]]) -> dict[str, object]:
    values = {target: [] for target in INTENSITY_TARGETS}
    for frame in frames:
        for cell in frame.get("cells") or []:
            if not isinstance(cell, dict):
                continue
            for target in INTENSITY_TARGETS:
                value = _number(cell.get(target))
                if value is not None:
                    values[target].append(value)
    return {
        "mtr_birds_km_h": log_colour_scale(
            values["mtr_birds_km_h"],
            units="birds km-1 h-1",
        ),
        "vid_birds_per_km2": log_colour_scale(
            values["vid_birds_per_km2"],
            units="birds km-2",
        ),
    }


def prepare_training_table(
    *,
    joined_features: Path,
    output: Path,
    window_days: int = 365,
    min_profiles_per_hour: int = 3,
    extra_era5_features: tuple[str, ...] = (),
) -> dict[str, object]:
    """Create a reproducible, pulse-separated rolling ERA5/VPTS model table."""

    unknown_features = set(extra_era5_features) - set(OPTIONAL_ERA5_FEATURES)
    if unknown_features:
        raise ValueError(f"unsupported optional ERA5 features: {', '.join(sorted(unknown_features))}")
    required_features = (*ERA5_FEATURES, *extra_era5_features)

    payload = _read_json(joined_features)
    source_rows = payload.get("rows")
    if not isinstance(source_rows, list):
        raise ValueError("joined features artifact has no rows")
    candidates = [_normalise_row(row, min_profiles_per_hour) for row in source_rows if isinstance(row, dict)]
    candidates = [
        row
        for row in candidates
        if row is not None and all(_number(row.get(feature)) is not None for feature in required_features)
    ]
    if not candidates:
        raise ValueError(
            "joined features contains no quality-controlled LP/SP hourly rows "
            "with the complete declared ERA5 predictor set"
        )

    complete_days = _complete_days(candidates)
    if not complete_days:
        raise ValueError("joined features has no UTC day with all 24 ERA5 hours")
    latest_day = max(complete_days)
    first_day = latest_day - timedelta(days=window_days - 1)
    rows = [row for row in candidates if first_day <= _parse_time(str(row["time_utc"])).date() <= latest_day]
    if not rows:
        raise ValueError("no rows remain in selected rolling window")

    fieldnames = _fieldnames(rows, required_features)
    feature_ranges = {
        name: {"lower": lower, "upper": upper}
        for name in required_features
        if (bounds := _percentile_range(rows, name)) is not None
        for lower, upper in (bounds,)
    }
    csv_path = output.with_suffix(".csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    result = {
        "schema_version": REANALYSIS_SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "processing_version": PROCESSING_VERSION,
        "model_time_terms": "none",
        "excluded_predictors": ["timestamp", "hour", "day_of_year", "season", "sunrise", "sunset", "solar_period"],
        "source": str(joined_features),
        "csv": str(csv_path),
        "first_day_utc": first_day.isoformat(),
        "latest_complete_day_utc": latest_day.isoformat(),
        "window_days": window_days,
        "row_count": len(rows),
        "radar_count": len({str(row["radar"]) for row in rows}),
        "pulse_counts": {pulse: sum(row["pulse"] == pulse for row in rows) for pulse in PULSES},
        "feature_columns": [name for name in required_features if name in fieldnames],
        "feature_ranges": feature_ranges,
        "target_columns": [*INTENSITY_TARGETS, *VECTOR_TARGETS],
        "quality_policy": {
            "minimum_profiles_per_hour": min_profiles_per_hour,
            "rain_suspect_fraction_maximum": 0.5,
            "missing_hours": "excluded rather than interpreted as zero",
            "era5_predictors": "complete cases for every declared ERA5 feature",
        },
    }
    write_json(output, result)
    return result


def write_model_spec(
    output: Path,
    *,
    table: Path,
    model_family: str,
    gamm_options_path: Path | None = None,
) -> dict[str, object]:
    """Write the immutable fitting contract consumed by batch runners."""

    if model_family not in MODEL_FAMILIES:
        raise ValueError(f"unknown model family: {model_family}")
    table_payload = _read_json(table)
    features = list(table_payload.get("feature_columns") or [])
    if not features:
        raise ValueError("training table contains no recognised ERA5 feature columns")
    gamm_options: dict[str, object] = {}
    gamm_selection_id: str | None = None
    if gamm_options_path is not None:
        selection = _read_json(gamm_options_path)
        candidate = selection.get("gamm_options")
        if not isinstance(candidate, dict):
            raise ValueError("GAMM options file must contain a gamm_options object")
        gamm_options = candidate
        gamm_selection_id = str(selection.get("selection_id") or "") or None
    payload = {
        "schema_version": REANALYSIS_SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "processing_version": PROCESSING_VERSION,
        "model_family": model_family,
        "training_table": str(table),
        "training_csv": str(table_payload["csv"]),
        "pulses": list(PULSES),
        "intensity_targets": list(INTENSITY_TARGETS),
        "vector_targets": list(VECTOR_TARGETS),
        "predictors": ["easting_m", "northing_m", *features],
        "radar_random_effect": model_family == "gamm",
        "time_predictors": list(gamm_options.get("temporal_smooths") or []),
        "validation": {
            "spatial": "leave-one-radar-out",
            "temporal": "contiguous blocked UTC windows",
            "metrics": ["rmse", "mae", "bias", "r_squared", "top_decile_precision", "top_decile_recall", "speed_mae", "direction_mae_deg"],
        },
    }
    if model_family == "gamm" and gamm_options:
        payload["gamm_options"] = gamm_options
        payload["gamm_selection_id"] = gamm_selection_id
    write_json(output, payload)
    return payload


def compare_models(*, gamm_metrics: Path, xgboost_metrics: Path, output: Path) -> dict[str, object]:
    """Apply the pre-declared conservative production-model decision rule."""

    gamm = _read_json(gamm_metrics)
    xgboost = _read_json(xgboost_metrics)
    gamm_rows = _metric_index(gamm, validation="leave_one_radar_out")
    xgb_rows = _metric_index(xgboost, validation="leave_one_radar_out")
    gamm_time_rows = _metric_index(gamm, validation="blocked_time")
    xgb_time_rows = _metric_index(xgboost, validation="blocked_time")
    checks = []
    temporal_checks = []
    for pulse in PULSES:
        for target in INTENSITY_TARGETS:
            baseline = gamm_rows.get((pulse, target))
            candidate = xgb_rows.get((pulse, target))
            if baseline is None or candidate is None:
                checks.append({"pulse": pulse, "target": target, "passed": False, "reason": "missing_metrics"})
                continue
            improvement = _improvement(baseline.get("rmse"), candidate.get("rmse"))
            event_better = _number(candidate.get("top_decile_precision")) >= _number(baseline.get("top_decile_precision")) and _number(candidate.get("top_decile_recall")) >= _number(baseline.get("top_decile_recall"))
            checks.append({"pulse": pulse, "target": target, "rmse_improvement_fraction": improvement, "event_detection_improved": event_better, "passed": improvement >= 0.10 and event_better})
            temporal_baseline = gamm_time_rows.get((pulse, target))
            temporal_candidate = xgb_time_rows.get((pulse, target))
            if temporal_baseline is None or temporal_candidate is None:
                temporal_checks.append({"pulse": pulse, "target": target, "passed": False, "reason": "missing_metrics"})
                continue
            temporal_improvement = _improvement(temporal_baseline.get("rmse"), temporal_candidate.get("rmse"))
            temporal_events = _number(temporal_candidate.get("top_decile_precision")) >= _number(temporal_baseline.get("top_decile_precision")) and _number(temporal_candidate.get("top_decile_recall")) >= _number(temporal_baseline.get("top_decile_recall"))
            temporal_checks.append({"pulse": pulse, "target": target, "rmse_improvement_fraction": temporal_improvement, "event_detection_improved": temporal_events, "passed": temporal_improvement >= 0.10 and temporal_events})
    vector_ok = _vectors_not_worse(gamm_rows, xgb_rows)
    temporal_required = _has_validation_rows(gamm, "blocked_time") or _has_validation_rows(xgboost, "blocked_time")
    temporal_ok = bool(temporal_checks) and all(bool(check["passed"]) for check in temporal_checks)
    selected = "xgboost" if checks and all(bool(check["passed"]) for check in checks) and vector_ok and (temporal_ok if temporal_required else True) else "gamm"
    payload = {
        "schema_version": REANALYSIS_SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "processing_version": PROCESSING_VERSION,
        "selected_model_family": selected,
        "decision_rule": "Promote XGBoost only if it improves held-out-radar RMSE by at least 10 percent for MTR and VID in LP and SP, improves top-decile event precision and recall, does not worsen vector error, and meets the same criteria on blocked-time validation when available.",
        "intensity_checks": checks,
        "temporal_validation_required": temporal_required,
        "temporal_intensity_checks": temporal_checks,
        "vectors_not_worse": vector_ok,
        "sources": {"gamm": str(gamm_metrics), "xgboost": str(xgboost_metrics)},
    }
    write_json(output, payload)
    return payload


def publish_reanalysis(
    *,
    predictions: Path,
    comparison: Path,
    output_root: Path,
) -> dict[str, object]:
    """Publish daily pulse-separated browser frames and atomically update latest."""

    source = _read_json(predictions)
    selection = _read_json(comparison)
    frames = source.get("frames")
    grid = source.get("grid")
    if not isinstance(frames, list) or not frames or not isinstance(grid, dict):
        raise ValueError("predictions must contain a grid and non-empty frames list")
    family = str(selection.get("selected_model_family") or "gamm")
    frame_rows = [frame for frame in frames if isinstance(frame, dict) and frame.get("model_family") == family]
    if not frame_rows:
        raise ValueError(f"predictions has no frames for selected model family {family}")
    run_id = str(source.get("run_id") or source.get("latest_complete_day_utc") or utc_now()).replace(":", "").replace("-", "")
    archive_relative = Path("archive") / "reanalysis" / "gam-era5" / run_id
    archive_dir = output_root / archive_relative
    output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(mkdtemp(prefix=".reanalysis.", dir=output_root))
    try:
        daily_assets: dict[str, dict[str, str]] = {pulse: {} for pulse in PULSES}
        by_day: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for frame in frame_rows:
            pulse = str(frame.get("pulse") or "")
            timestamp = _parse_time(str(frame.get("time_utc") or ""))
            if pulse not in PULSES:
                continue
            by_day.setdefault((pulse, timestamp.date().isoformat()), []).append(frame)
        for (pulse, day), day_frames in sorted(by_day.items()):
            relative = Path("daily") / pulse / f"{day.replace('-', '')}.json"
            destination = staging / relative
            write_json(destination, {"schema_version": REANALYSIS_SCHEMA_VERSION, "grid": grid, "pulse": pulse, "date_utc": day, "frames": sorted(day_frames, key=lambda item: str(item["time_utc"]))})
            daily_assets[pulse][day] = str(relative)
        write_json(staging / "validation.json", selection)
        write_json(staging / "source.json", {key: value for key, value in source.items() if key != "frames"})
        if archive_dir.exists():
            raise FileExistsError(f"refusing to overwrite immutable reanalysis archive: {archive_dir}")
        archive_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, archive_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    times = sorted(str(frame["time_utc"]) for frame in frame_rows)
    latest = {
        "schema_version": REANALYSIS_SCHEMA_VERSION,
        "data_available": True,
        "generated_at_utc": utc_now(),
        "model_family": family,
        "archive_prefix": str(archive_relative),
        "first_time_utc": times[0],
        "latest_time_utc": times[-1],
        "grid": grid,
        "colour_scales": _model_colour_scales(frame_rows),
        "pulses": list(PULSES),
        "variables": ["mtr_birds_km_h", "vid_birds_per_km2", "bird_u_ms", "bird_v_ms", "uncertainty", "support"],
        "assets": {
            **{pulse: {day: str(archive_relative / path) for day, path in assets.items()} for pulse, assets in daily_assets.items()},
            "boundary": "assets/uk-boundary.geojson",
        },
        "comparison": str(archive_relative / "validation.json"),
        "source": str(archive_relative / "source.json"),
        "interpretation": "Historical modelled reanalysis. No phenology, solar-period, daylight, or timestamp predictor is used.",
    }
    write_json(output_root / "latest" / "gam-era5.json", latest)
    return latest


def publish_wide_reanalysis(
    *,
    lp_csv: Path,
    sp_csv: Path,
    comparison: Path,
    output_root: Path,
    model_family: str,
) -> dict[str, object]:
    """Stream wide national predictions into daily browser assets."""

    if model_family not in MODEL_FAMILIES:
        raise ValueError(f"unknown model family: {model_family}")
    selection = _read_json(comparison)
    selected = str(selection.get("selected_model_family") or "")
    if selected != model_family:
        raise ValueError(f"model selection is {selected}, not {model_family}")
    run_id = utc_now().replace(":", "").replace("-", "")
    archive_relative = Path("archive") / "reanalysis" / "gam-era5" / run_id
    archive_dir = output_root / archive_relative
    output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(mkdtemp(prefix=".reanalysis.", dir=output_root))
    try:
        assets: dict[str, dict[str, str]] = {}
        grids = []
        first_times = []
        last_times = []
        scale_samples: dict[str, list[float]] = {target: [] for target in INTENSITY_TARGETS}
        for pulse, source in (("lp", lp_csv), ("sp", sp_csv)):
            pulse_assets, grid, first_time, last_time, pulse_samples = _stream_wide_daily_assets(
                source,
                staging,
                pulse=pulse,
                model_family=model_family,
            )
            assets[pulse] = {
                day: str(archive_relative / relative)
                for day, relative in pulse_assets.items()
            }
            grids.append(grid)
            first_times.append(first_time)
            last_times.append(last_time)
            for target in INTENSITY_TARGETS:
                scale_samples[target].extend(pulse_samples[target])
        if grids[0] != grids[1]:
            raise ValueError("LP and SP prediction grids differ")
        write_json(staging / "validation.json", selection)
        write_json(
            staging / "source.json",
            {
                "generated_at_utc": utc_now(),
                "model_family": model_family,
                "lp_csv": str(lp_csv),
                "sp_csv": str(sp_csv),
                "streaming_policy": "one complete UTC day at a time",
            },
        )
        if archive_dir.exists():
            raise FileExistsError(f"refusing to overwrite immutable reanalysis archive: {archive_dir}")
        archive_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, archive_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    latest = {
        "schema_version": REANALYSIS_SCHEMA_VERSION,
        "data_available": True,
        "generated_at_utc": utc_now(),
        "model_family": model_family,
        "archive_prefix": str(archive_relative),
        "first_time_utc": min(first_times),
        "latest_time_utc": max(last_times),
        "grid": grids[0],
        "colour_scales": {
            "mtr_birds_km_h": log_colour_scale(
                scale_samples["mtr_birds_km_h"],
                units="birds km-1 h-1",
            ),
            "vid_birds_per_km2": log_colour_scale(
                scale_samples["vid_birds_per_km2"],
                units="birds km-2",
            ),
        },
        "pulses": list(PULSES),
        "variables": ["mtr_birds_km_h", "vid_birds_per_km2", "bird_u_ms", "bird_v_ms", "uncertainty", "support"],
        "assets": {
            **assets,
            "boundary": "assets/uk-boundary.geojson",
        },
        "comparison": str(archive_relative / "validation.json"),
        "source": str(archive_relative / "source.json"),
        "interpretation": "Historical modelled reanalysis. No phenology, solar-period, daylight, or timestamp predictor is used.",
    }
    write_json(output_root / "latest" / "gam-era5.json", latest)
    return latest


def _stream_wide_daily_assets(
    source: Path,
    staging: Path,
    *,
    pulse: str,
    model_family: str,
) -> tuple[dict[str, str], dict[str, object], str, str, dict[str, list[float]]]:
    required = {
        "time_utc",
        "longitude",
        "latitude",
        "support",
        *INTENSITY_TARGETS,
        *VECTOR_TARGETS,
    }
    assets: dict[str, str] = {}
    grid: dict[str, object] | None = None
    first_time: str | None = None
    last_time: str | None = None
    current_day: str | None = None
    frames: dict[str, list[dict[str, object]]] = {}
    samplers = {
        target: _ReservoirSampler(limit=100_000, seed=index)
        for index, target in enumerate(INTENSITY_TARGETS)
    }

    def flush(day: str) -> None:
        nonlocal grid
        if len(frames) != 24:
            raise ValueError(f"{source} has {len(frames)} hourly frames for {day}, expected 24")
        ordered = []
        cell_counts = set()
        for time_utc, cells in sorted(frames.items()):
            cells.sort(key=lambda cell: (-float(cell["latitude"]), float(cell["longitude"])))
            cell_counts.add(len(cells))
            ordered.append({"model_family": model_family, "pulse": pulse, "time_utc": time_utc, "cells": cells})
        if len(cell_counts) != 1 or not cell_counts or next(iter(cell_counts)) < 1:
            raise ValueError(f"{source} has an inconsistent prediction grid for {day}")
        if grid is None:
            first_cells = ordered[0]["cells"]
            longitudes = sorted({float(cell["longitude"]) for cell in first_cells})
            latitudes = sorted({float(cell["latitude"]) for cell in first_cells}, reverse=True)
            grid = {
                "longitude_step": _grid_step(longitudes),
                "latitude_step": _grid_step(latitudes),
                "longitude_count": len(longitudes),
                "latitude_count": len(latitudes),
                "cell_count": len(first_cells),
                "bounds": {
                    "west": min(longitudes) - _grid_step(longitudes) / 2,
                    "east": max(longitudes) + _grid_step(longitudes) / 2,
                    "south": min(latitudes) - _grid_step(latitudes) / 2,
                    "north": max(latitudes) + _grid_step(latitudes) / 2,
                },
                "domain_policy": "union_of_radar_ranges",
                "resolution": "ERA5 native 0.25 degree grid within physical radar range over land and water",
            }
        relative = Path("daily") / pulse / f"{day.replace('-', '')}.json"
        write_json(
            staging / relative,
            {
                "schema_version": REANALYSIS_SCHEMA_VERSION,
                "grid": grid,
                "pulse": pulse,
                "date_utc": day,
                "frames": ordered,
            },
        )
        assets[day] = str(relative)

    with source.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            missing = sorted(required - set(reader.fieldnames or []))
            raise ValueError(f"wide prediction CSV is missing fields: {', '.join(missing)}")
        for row in reader:
            timestamp = _canonical_time(row.get("time_utc"))
            day = timestamp[:10]
            if current_day is not None and day != current_day:
                flush(current_day)
                frames = {}
            current_day = day
            first_time = min(first_time, timestamp) if first_time else timestamp
            last_time = max(last_time, timestamp) if last_time else timestamp
            uncertainty_values = [
                _number(row.get(f"uncertainty_{target}"))
                for target in (*INTENSITY_TARGETS, *VECTOR_TARGETS)
            ]
            uncertainty = max((value for value in uncertainty_values if value is not None), default=0.0)
            cell = {
                "longitude": float(row["longitude"]),
                "latitude": float(row["latitude"]),
                "support": float(row["support"]),
                "uncertainty": uncertainty,
                **{target: float(row[target]) for target in (*INTENSITY_TARGETS, *VECTOR_TARGETS)},
            }
            for target in INTENSITY_TARGETS:
                samplers[target].add(float(row[target]))
            frames.setdefault(timestamp, []).append(cell)
    if current_day is None or first_time is None or last_time is None:
        raise ValueError(f"wide prediction CSV is empty: {source}")
    flush(current_day)
    assert grid is not None
    return assets, grid, first_time, last_time, {
        target: sampler.values for target, sampler in samplers.items()
    }


def build_prediction_frames(*, predictions_csv: Path, output: Path, model_family: str) -> dict[str, object]:
    """Pivot batch-model long predictions into the browser frame contract.

    The national ERA5 grid builder must provide a support score in ``[0, 1]``
    for every cell.  This fail-closed requirement prevents an attractive map
    from silently presenting unsupported extrapolation as equally reliable.
    """

    if model_family not in MODEL_FAMILIES:
        raise ValueError(f"unknown model family: {model_family}")
    with predictions_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"time_utc", "longitude", "latitude", "pulse", "target", "value", "support"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("prediction CSV must contain time_utc, longitude, latitude, pulse, target, value, and support")
    cells: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for row in rows:
        pulse = str(row.get("pulse") or "")
        target = str(row.get("target") or "")
        if pulse not in PULSES or target not in {*INTENSITY_TARGETS, *VECTOR_TARGETS}:
            continue
        key = (str(row["time_utc"]), pulse, str(row["longitude"]), str(row["latitude"]))
        cell = cells.setdefault(
            key,
            {
                "longitude": float(row["longitude"]),
                "latitude": float(row["latitude"]),
                "support": float(row["support"]),
                "uncertainty": _number(row.get("uncertainty")) or 0.0,
            },
        )
        cell[target] = float(row["value"])
        if _number(row.get("uncertainty")) is not None:
            cell["uncertainty"] = max(float(cell["uncertainty"]), float(row["uncertainty"]))
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for (time_utc, pulse, _, _), cell in cells.items():
        if not all(target in cell for target in (*INTENSITY_TARGETS, *VECTOR_TARGETS)):
            continue
        grouped.setdefault((time_utc, pulse), []).append(cell)
    if not grouped:
        raise ValueError("prediction CSV contains no complete intensity/vector cells")
    longitudes = sorted({float(cell["longitude"]) for cell in cells.values()})
    latitudes = sorted({float(cell["latitude"]) for cell in cells.values()}, reverse=True)
    grid = {
        "longitude_step": _grid_step(longitudes),
        "latitude_step": _grid_step(latitudes),
        "longitude_count": len(longitudes),
        "latitude_count": len(latitudes),
        "resolution": "ERA5 native 0.25 degree grid",
    }
    payload = {
        "schema_version": REANALYSIS_SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "model_family": model_family,
        "grid": grid,
        "frames": [
            {"model_family": model_family, "pulse": pulse, "time_utc": time_utc, "cells": sorted(values, key=lambda cell: (-float(cell["latitude"]), float(cell["longitude"]))) }
            for (time_utc, pulse), values in sorted(grouped.items())
        ],
    }
    write_json(output, payload)
    return {key: value for key, value in payload.items() if key != "frames"} | {"frame_count": len(payload["frames"])}


def _normalise_row(row: dict[str, Any], min_profiles: int) -> dict[str, object] | None:
    pulse = str(row.get("observed_pulse") or row.get("pulse") or "").lower()
    if pulse not in PULSES:
        return None
    profiles = _number(row.get("observed_usable_mtr_profile_count"))
    rain = _number(row.get("observed_rain_suspect_fraction"))
    mtr = _number(row.get("observed_mean_mtr_birds_km_h"))
    vid = _number(row.get("observed_mean_vid_birds_per_km2"))
    speed = _number(row.get("observed_mean_ground_speed_ms"))
    direction = _number(row.get("observed_dominant_direction_deg"))
    if profiles is None or profiles < min_profiles or rain is None or rain > 0.5 or mtr is None or vid is None:
        return None
    latitude = _number(row.get("latitude") or row.get("observed_latitude"))
    longitude = _number(row.get("longitude") or row.get("observed_longitude"))
    if latitude is None or longitude is None:
        return None
    easting, northing = _project(longitude, latitude)
    result: dict[str, object] = {
        "radar": str(row.get("radar") or ""), "pulse": pulse, "time_utc": _canonical_time(row.get("time_utc")),
        "latitude": latitude, "longitude": longitude, "easting_m": easting, "northing_m": northing,
        "mtr_birds_km_h": mtr, "vid_birds_per_km2": vid, "profile_count": profiles, "rain_suspect_fraction": rain,
    }
    if speed is not None and direction is not None:
        radians = math.radians(direction)
        result["bird_u_ms"] = speed * math.sin(radians)
        result["bird_v_ms"] = speed * math.cos(radians)
    for target, aliases in _ERA5_ALIASES.items():
        value = next((_number(row.get(alias)) for alias in aliases if _number(row.get(alias)) is not None), None)
        if value is not None:
            result[target] = value
    return result


_ERA5_ALIASES = {
    "temperature_850_k": ("t_pressure_level_850", "t_pressure_level_850.0", "t_isobaricInhPa_850", "temperature_850"),
    "relative_humidity_850_percent": ("r_pressure_level_850", "r_pressure_level_850.0", "r_isobaricInhPa_850", "relative_humidity_850"),
    "u_850_ms": ("u_pressure_level_850", "u_pressure_level_850.0", "u_isobaricInhPa_850", "u_850"),
    "v_850_ms": ("v_pressure_level_850", "v_pressure_level_850.0", "v_isobaricInhPa_850", "v_850"),
    "u_925_ms": ("u_pressure_level_925", "u_pressure_level_925.0", "u_isobaricInhPa_925", "u_925"),
    "v_925_ms": ("v_pressure_level_925", "v_pressure_level_925.0", "v_isobaricInhPa_925", "v_925"),
    "u_700_ms": ("u_pressure_level_700", "u_pressure_level_700.0", "u_isobaricInhPa_700", "u_700"),
    "v_700_ms": ("v_pressure_level_700", "v_pressure_level_700.0", "v_isobaricInhPa_700", "v_700"),
    "surface_pressure_pa": ("sp", "surface_pressure"),
    "mean_sea_level_pressure_pa": ("msl", "mean_sea_level_pressure"),
    "total_cloud_cover_fraction": ("tcc", "total_cloud_cover"),
    "boundary_layer_height_m": ("blh", "boundary_layer_height"),
    "hourly_precipitation_m": ("tp_hourly", "total_precipitation_hourly", "tp", "total_precipitation"),
}


def _complete_days(rows: list[dict[str, object]]):
    hours: dict[object, set[int]] = {}
    for row in rows:
        stamp = _parse_time(str(row["time_utc"]))
        hours.setdefault(stamp.date(), set()).add(stamp.hour)
    return [day for day, present in hours.items() if len(present) == 24]


def _fieldnames(rows: list[dict[str, object]], era5_features: tuple[str, ...] = ERA5_FEATURES) -> list[str]:
    preferred = ["radar", "pulse", "time_utc", "latitude", "longitude", "easting_m", "northing_m", *INTENSITY_TARGETS, *VECTOR_TARGETS, "profile_count", "rain_suspect_fraction", *era5_features]
    observed = {key for row in rows for key in row}
    return [key for key in preferred if key in observed] + sorted(observed - set(preferred))


def _percentile_range(rows: list[dict[str, object]], name: str) -> tuple[float, float] | None:
    values = sorted(value for row in rows if (value := _number(row.get(name))) is not None)
    if not values:
        return None
    lower = values[max(0, int(len(values) * 0.01) - 1)]
    upper = values[min(len(values) - 1, int(len(values) * 0.99))]
    return lower, upper


def _grid_step(values: list[float]) -> float:
    if len(values) < 2:
        return 0.25
    steps = [abs(right - left) for left, right in zip(values, values[1:]) if right != left]
    return min(steps) if steps else 0.25


def _metric_index(payload: dict[str, Any], *, validation: str) -> dict[tuple[str, str], dict[str, Any]]:
    rows = payload.get("metrics")
    if not isinstance(rows, list):
        return {}
    matching = [row for row in rows if isinstance(row, dict) and row.get("validation", "leave_one_radar_out") == validation]
    return {(str(row.get("pulse")), str(row.get("target"))): row for row in matching}


def _has_validation_rows(payload: dict[str, Any], validation: str) -> bool:
    rows = payload.get("metrics")
    return isinstance(rows, list) and any(
        isinstance(row, dict) and row.get("validation") == validation
        for row in rows
    )


def _vectors_not_worse(gamm: dict[tuple[str, str], dict[str, Any]], xgb: dict[tuple[str, str], dict[str, Any]]) -> bool:
    for pulse in PULSES:
        for target in VECTOR_TARGETS:
            baseline, candidate = gamm.get((pulse, target)), xgb.get((pulse, target))
            if baseline is None or candidate is None:
                return False
            if _number(candidate.get("rmse")) > _number(baseline.get("rmse")) * 1.02:
                return False
    return True


def _improvement(baseline: object, candidate: object) -> float:
    before, after = _number(baseline), _number(candidate)
    if before is None or after is None or before <= 0:
        return float("-inf")
    return (before - after) / before


def _canonical_time(value: object) -> str:
    return _parse_time(str(value)).replace(minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def _project(longitude: float, latitude: float) -> tuple[float, float]:
    try:
        transformer = _projection_transformer()
        return tuple(float(value) for value in transformer.transform(longitude, latitude))
    except ModuleNotFoundError:
        return longitude * 111_320.0 * math.cos(math.radians(latitude)), latitude * 110_540.0


@lru_cache(maxsize=1)
def _projection_transformer():
    from pyproj import Transformer

    return Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True)


def _number(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload
