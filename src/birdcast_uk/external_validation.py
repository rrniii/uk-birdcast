"""Evaluate a fitted Bird Maps model against external VPTS observations.

The external VPTS CSV is only read.  This module writes compact validation
reports and deliberately records the spatial-transfer limitations instead of
presenting a nearby European radar as a same-radar validation source.
"""

from __future__ import annotations

import csv
from collections import defaultdict
import math
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from .observed import _profiles_from_rows
from .static_artifacts import utc_now, write_json


MODEL_VARIABLES = ("mtr_birds_km_h", "vid_birds_per_km2", "bird_u_ms", "bird_v_ms")


def hourly_vpts_observations(
    rows: Iterable[dict[str, Any]],
    *,
    altitude_min_m: float = 200.0,
    altitude_max_m: float = 4000.0,
) -> list[dict[str, Any]]:
    """Integrate external VPTS profiles then aggregate valid samples by UTC hour."""

    profiles = _profiles_from_rows(
        list(rows),
        altitude_min_m=altitude_min_m,
        altitude_max_m=altitude_max_m,
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for profile in profiles:
        if not _finite(profile.get("mtr_birds_km_h")) or not _finite(profile.get("vid_birds_per_km2")):
            continue
        grouped[f"{str(profile['time_utc'])[:13]}:00:00Z"].append(profile)

    result: list[dict[str, Any]] = []
    for timestamp, values in sorted(grouped.items()):
        vectors = [_vector_components(value) for value in values]
        result.append(
            {
                "time_utc": timestamp,
                "profile_count": len(values),
                "mtr_birds_km_h": _mean_number(values, "mtr_birds_km_h"),
                "vid_birds_per_km2": _mean_number(values, "vid_birds_per_km2"),
                "bird_u_ms": mean(vector[0] for vector in vectors),
                "bird_v_ms": mean(vector[1] for vector in vectors),
            }
        )
    return result


def evaluate_external_vpts(
    *,
    observations: Iterable[dict[str, Any]],
    predictions: Iterable[dict[str, Any]],
    site: dict[str, Any],
    model: dict[str, Any],
) -> dict[str, Any]:
    """Score hourly model predictions at a documented external observation site."""

    by_time = {_normal_time(row.get("time_utc")): row for row in predictions}
    matched = [
        {"time_utc": _normal_time(row.get("time_utc")), "observed": row, "modelled": by_time[_normal_time(row.get("time_utc"))]}
        for row in observations
        if _normal_time(row.get("time_utc")) in by_time
    ]
    metrics = {variable: _metrics(matched, variable) for variable in MODEL_VARIABLES}
    return {
        "schema_version": "birdcast-uk-external-vpts-validation-1.0",
        "generated_at_utc": utc_now(),
        "validation_class": "external_spatial_transfer",
        "site": site,
        "model": model,
        "altitude_band_m": [200.0, 4000.0],
        "matched_hour_count": len(matched),
        "metrics": metrics,
        "source_policy": "Aloft VPTS are read only. This report does not create VP, VPTS, or PVOL products.",
        "interpretation": (
            "This is an external radar spatial-transfer evaluation. It is not a same-radar "
            "comparison and does not establish absolute accuracy without a multi-day, multi-site sample."
        ),
    }


def validate_external_vpts_csv(
    *,
    vpts_csv: Path,
    predictions_csv: Path,
    output: Path,
    site: dict[str, Any],
    model: dict[str, Any],
) -> dict[str, Any]:
    """Read existing VPTS/prediction CSVs and write a compact validation report."""

    with vpts_csv.open("r", encoding="utf-8", newline="") as handle:
        observations = hourly_vpts_observations(csv.DictReader(handle))
    with predictions_csv.open("r", encoding="utf-8", newline="") as handle:
        predictions = list(csv.DictReader(handle))
    report = evaluate_external_vpts(
        observations=observations,
        predictions=predictions,
        site=site,
        model=model,
    )
    write_json(output, report)
    return report


def _metrics(matched: list[dict[str, Any]], variable: str) -> dict[str, float | int | None]:
    pairs = [
        (float(match["observed"][variable]), float(match["modelled"][variable]))
        for match in matched
        if _finite(match["observed"].get(variable)) and _finite(match["modelled"].get(variable))
    ]
    if not pairs:
        return {"count": 0, "observed_mean": None, "modelled_mean": None, "bias": None, "mae": None, "rmse": None}
    observed, modelled = zip(*pairs)
    residuals = [predicted - actual for actual, predicted in pairs]
    return {
        "count": len(pairs),
        "observed_mean": mean(observed),
        "modelled_mean": mean(modelled),
        "bias": mean(residuals),
        "mae": mean(abs(value) for value in residuals),
        "rmse": math.sqrt(mean(value * value for value in residuals)),
    }


def _mean_number(rows: Iterable[dict[str, Any]], field: str) -> float:
    values = [float(row[field]) for row in rows if _finite(row.get(field))]
    return mean(values)


def _vector_components(row: dict[str, Any]) -> tuple[float, float]:
    speed = float(row.get("mean_ground_speed_ms") or 0.0)
    direction = math.radians(float(row.get("dominant_direction_deg") or 0.0))
    return speed * math.sin(direction), speed * math.cos(direction)


def _normal_time(value: Any) -> str:
    text = str(value or "")
    return text.replace(".000000000", "") if text.endswith("Z") else f"{text.replace('.000000000', '')}Z"


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
