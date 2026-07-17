"""BTO validation scaffolding for UK BirdCast."""

from __future__ import annotations

import csv
from datetime import date
import math
from pathlib import Path
from typing import Any

from .config import PROCESSING_VERSION
from .static_artifacts import utc_now, write_json


BTO_REQUEST_TEMPLATE = """# BTO Data Request for UK BirdCast Validation

## Purpose

Validate radar-derived nocturnal migration metrics from UK BirdCast against independent ecological observations.

## Requested Products

- BirdTrack daily preferred, weekly acceptable, migrant species summaries from 2004 onwards.
- Spatial aggregation at 10 km grid, county, or supplied radar-region polygons.
- Effort fields where available: complete-list flag, list count, visit count, duration, observer effort, and reporting-rate denominator.
- Species/date/region summaries for agreed migrant groups; avoid sensitive-location disclosure.
- Ringing movement summaries for broad seasonal directionality checks.
- WeBS monthly summaries for waterbird and wader validation where relevant.

Request route: https://www.bto.org/data/request

BirdTrack data guidance:
https://www.bto.org/get-involved/volunteer/projects/birdtrack/maps-reports

## Intended Validation Metrics

- Seasonal peak timing difference between radar migration traffic rate and BirdTrack activity.
- Weekly regional correlation between radar intensity and BirdTrack reporting-rate/count summaries.
- Agreement on top migration-event weeks by region.
- Directional plausibility against ringing and Migration Atlas summaries.
- Waterbird/coastal plausibility checks against WeBS where species groups are relevant.

## Data Handling

- Store licensed BTO data outside public Object Store prefixes.
- Publish only aggregated validation scores and non-sensitive summaries.
- Keep source version, licence terms, and aggregation level in every derived validation artifact.
"""


def write_request_template(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(BTO_REQUEST_TEMPLATE, encoding="utf-8")


def write_validation_status(output: Path, *, data_available: bool = False, status: str = "request_pending") -> dict[str, object]:
    payload = {
        "bto_data_available": data_available,
        "generated_at_utc": utc_now(),
        "latest_bto_validation_date": None,
        "processing_version": PROCESSING_VERSION,
        "status": status,
        "validation_role": "ecological plausibility and phenology validation, not direct radar ground truth",
    }
    write_json(output, payload)
    return payload


def validate_aggregates(bto_csv: Path, radar_csv: Path, output: Path) -> dict[str, object]:
    """Compare private weekly BirdTrack aggregates with radar migration metrics."""

    bto_rows = _read_aggregate_csv(bto_csv, "reporting_rate")
    radar_rows = _read_aggregate_csv(radar_csv, "radar_intensity")
    joined = []
    radar_by_key = {(row["region"], row["week_start"]): row for row in radar_rows}
    for row in bto_rows:
        match = radar_by_key.get((row["region"], row["week_start"]))
        if match:
            joined.append(
                {
                    "region": row["region"],
                    "week_start": row["week_start"],
                    "reporting_rate": row["value"],
                    "radar_intensity": match["value"],
                    "complete_list_count": row["effort"],
                }
            )
    if len(joined) < 3:
        raise ValueError("at least three matched region-week rows are required")

    bto_values = [float(row["reporting_rate"]) for row in joined]
    radar_values = [float(row["radar_intensity"]) for row in joined]
    regions = sorted({str(row["region"]) for row in joined})
    peak_errors = []
    for region in regions:
        regional = [row for row in joined if row["region"] == region]
        bto_peak = max(regional, key=lambda row: float(row["reporting_rate"]))
        radar_peak = max(regional, key=lambda row: float(row["radar_intensity"]))
        peak_errors.append(
            abs(
                (
                    date.fromisoformat(str(bto_peak["week_start"]))
                    - date.fromisoformat(str(radar_peak["week_start"]))
                ).days
            )
            / 7.0
        )

    payload = {
        "bto_data_available": True,
        "generated_at_utc": utc_now(),
        "latest_bto_validation_date": max(str(row["week_start"]) for row in joined),
        "processing_version": PROCESSING_VERSION,
        "status": "validated",
        "validation_role": "ecological plausibility and phenology validation, not direct radar ground truth",
        "source_handling": "licensed source aggregates retained outside public prefixes",
        "matched_region_week_count": len(joined),
        "region_count": len(regions),
        "metrics": {
            "pearson_correlation": round(_pearson(bto_values, radar_values), 6),
            "spearman_correlation": round(_pearson(_ranks(bto_values), _ranks(radar_values)), 6),
            "median_peak_timing_error_weeks": round(_median(peak_errors), 3),
            "top_event_quintile_overlap": round(_top_overlap(bto_values, radar_values), 6),
        },
        "effort": {
            "complete_list_count": round(
                sum(float(row["complete_list_count"]) for row in joined), 3
            )
        },
    }
    write_json(output, payload)
    return payload


def _read_aggregate_csv(path: Path, value_field: str) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            if not raw.get("region") or not raw.get("week_start") or raw.get(value_field) in (None, ""):
                continue
            date.fromisoformat(str(raw["week_start"]))
            rows.append(
                {
                    "region": str(raw["region"]),
                    "week_start": str(raw["week_start"]),
                    "value": float(raw[value_field]),
                    "effort": float(raw.get("complete_list_count") or 0.0),
                }
            )
    return rows


def _pearson(left: list[float], right: list[float]) -> float:
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    denominator = math.sqrt(
        sum((x - left_mean) ** 2 for x in left)
        * sum((y - right_mean) ** 2 for y in right)
    )
    return numerator / denominator if denominator else 0.0


def _ranks(values: list[float]) -> list[float]:
    ordered = sorted((value, index) for index, value in enumerate(values))
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][0] == ordered[start][0]:
            end += 1
        rank = (start + end - 1) / 2.0 + 1.0
        for _, index in ordered[start:end]:
            ranks[index] = rank
        start = end
    return ranks


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _top_overlap(left: list[float], right: list[float]) -> float:
    count = max(1, math.ceil(len(left) * 0.2))
    left_top = set(sorted(range(len(left)), key=left.__getitem__, reverse=True)[:count])
    right_top = set(sorted(range(len(right)), key=right.__getitem__, reverse=True)[:count])
    return len(left_top & right_top) / len(left_top | right_top)
