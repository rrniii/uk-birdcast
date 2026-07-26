"""Publish a relative coastal migration activity and flow product.

The coastal cohort is unsuitable for a transferable absolute MTR model.  This
module therefore publishes only a within-radar activity percentile and the
observed horizontal flow direction from the immutable derived hourly table.
It never writes or changes VP, VPTS, PVOL, or source radar objects.
"""

from __future__ import annotations

import bisect
import csv
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
from typing import Any

from .static_artifacts import utc_now, write_json


REQUIRED_COLUMNS = {
    "radar", "source", "country", "time_utc", "latitude", "longitude",
    "mtr_birds_km_h", "bird_u_ms", "bird_v_ms",
}


def _number(value: object) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalised_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_coastal_relative_flow(*, training_csv: Path, cohort_json: Path, output_root: Path) -> dict[str, Any]:
    """Build compact day partitions with percentile activity and flow direction.

    Percentiles are calculated independently within every radar over the
    supplied frozen model-year table.  This removes the known network/site
    scale mismatch instead of concealing it behind a multiplier.
    """

    if not training_csv.is_file():
        raise FileNotFoundError(f"Coastal training table is missing: {training_csv}")
    if not cohort_json.is_file():
        raise FileNotFoundError(f"Coastal cohort manifest is missing: {cohort_json}")
    cohort = json.loads(cohort_json.read_text(encoding="utf-8"))

    rows: list[dict[str, Any]] = []
    values_by_radar: dict[str, list[float]] = defaultdict(list)
    radars: dict[str, dict[str, Any]] = {}
    radar_hours: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_source_row_count = 0
    with training_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise ValueError(f"Coastal training table is missing columns: {', '.join(sorted(missing))}")
        for source in reader:
            value = _number(source.get("mtr_birds_km_h"))
            latitude, longitude = _number(source.get("latitude")), _number(source.get("longitude"))
            if value is None or value < 0 or latitude is None or longitude is None:
                continue
            radar = str(source["radar"]).lower()
            timestamp = _normalised_timestamp(str(source["time_utc"]))
            row = {
                "radar": radar,
                "time_utc": timestamp,
                "activity_value": math.log1p(value),
                "bird_u_ms": _number(source.get("bird_u_ms")),
                "bird_v_ms": _number(source.get("bird_v_ms")),
            }
            key = (timestamp, radar)
            previous = radar_hours.get(key)
            if previous is not None:
                identical = all(previous[field] == row[field] for field in ("activity_value", "bird_u_ms", "bird_v_ms"))
                if not identical:
                    raise ValueError(f"Conflicting duplicate coastal radar-hour: {radar} {timestamp}")
                duplicate_source_row_count += 1
                continue
            radar_hours[key] = row
            rows.append(row)
            values_by_radar[radar].append(row["activity_value"])
            radars.setdefault(radar, {
                "radar": radar,
                "source": str(source["source"]),
                "country": str(source["country"]),
                "latitude": latitude,
                "longitude": longitude,
            })

    if not rows:
        raise ValueError("Coastal training table contains no usable hourly observations")
    sorted_values = {radar: sorted(values) for radar, values in values_by_radar.items()}
    days: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        distribution = sorted_values[row["radar"]]
        percentile = 100.0 * bisect.bisect_right(distribution, row["activity_value"]) / len(distribution)
        days[row["time_utc"][:10]][row["time_utc"]].append({
            "radar": row["radar"],
            "activity_index": round(percentile, 2),
            "bird_u_ms": row["bird_u_ms"],
            "bird_v_ms": row["bird_v_ms"],
        })

    output_root.mkdir(parents=True, exist_ok=True)
    daily_root = output_root / "days"
    daily_root.mkdir(parents=True, exist_ok=True)
    for day, frames in sorted(days.items()):
        payload = {
            "schema_version": "birdcast-uk-coastal-relative-flow-day-1.0",
            "date": day,
            "frames": [
                {"time_utc": timestamp, "radars": sorted(entries, key=lambda item: item["radar"])}
                for timestamp, entries in sorted(frames.items())
            ],
        }
        write_json(daily_root / f"{day}.json", payload)

    radar_list = [radars[key] for key in sorted(radars)]
    first, latest = min(row["time_utc"] for row in rows), max(row["time_utc"] for row in rows)
    manifest = {
        "schema_version": "birdcast-uk-coastal-relative-flow-1.0",
        "data_available": True,
        "release_status": "published-relative-research-product",
        "generated_at_utc": utc_now(),
        "first_time_utc": first,
        "latest_time_utc": latest,
        "radars": radar_list,
        "radar_count": len(radar_list),
        "radar_hour_count": len(rows),
        "identical_duplicate_source_row_count": duplicate_source_row_count,
        "cadence": "hourly UTC",
        "activity_index": {
            "label": "Relative activity index",
            "units": "within-radar percentile (0-100)",
            "method": "empirical percentile of log1p archived MTR within each radar over the frozen model year",
            "comparable_between_radars": False,
        },
        "flow": {
            "label": "Observed flow direction",
            "method": "hourly VPTS-derived horizontal bird velocity; arrows encode direction only",
            "absolute_speed_published": False,
        },
        "cohort": {
            "continental_radars": [item["radar"] for item in cohort.get("continental_radars", [])],
            "uk_radars": [item["radar"] for item in cohort.get("uk_radars", [])],
            "geographic_rule": "UK coastal corridor, as declared in the frozen cohort manifest",
        },
        "source": {
            "derived_training_csv": str(training_csv),
            "derived_training_csv_sha256": _sha256(training_csv),
            "cohort_json": str(cohort_json),
            "cohort_json_sha256": _sha256(cohort_json),
            "raw_radar_products_written": False,
        },
        "assets": {"daily_template": "days/{date}.json"},
        "interpretation": (
            "Relative activity and observed flow at reporting radar locations. "
            "It is not an absolute migration traffic rate, a coast-wide interpolation, or a forecast."
        ),
    }
    write_json(output_root / "latest" / "relative-flow.json", manifest)
    return manifest


def install_coastal_static_site(site_root: Path) -> dict[str, Any]:
    """Install the independent coastal relative-product web shell."""

    source = Path(__file__).with_name("static_coastal")
    if not source.is_dir():
        raise FileNotFoundError(f"Coastal static source is missing: {source}")
    site_root.mkdir(parents=True, exist_ok=True)
    for path in source.iterdir():
        if path.is_file():
            shutil.copy2(path, site_root / path.name)
    shared = Path(__file__).with_name("static")
    for name in ("regional-boundaries.geojson", "live-uk-bird-maps-logo.jpg", "live-uk-bird-maps-favicon.png", "radar-marker.svg"):
        shutil.copy2(shared / name, site_root / name)
    return {"ok": True, "site_root": str(site_root), "file_count": len(list(site_root.iterdir()))}
