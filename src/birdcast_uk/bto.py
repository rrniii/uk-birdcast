"""BTO validation scaffolding for UK BirdCast."""

from __future__ import annotations

from pathlib import Path

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
