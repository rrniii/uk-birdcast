#!/usr/bin/env python3
"""Build a geographic UK coastal-corridor GAMM cohort from derived tables.

Continental radars are selected by their shortest distance to the Natural Earth
UK coastline, then UK radars are retained only when their location overlaps
the selected continental radar corridor.  Raw radar files are never read or
copied by this tool.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

EARTH_RADIUS_KM = 6371.0088


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def haversine_km(first: tuple[float, float], second: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    a = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def point_to_segment_km(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    """Local tangent-plane point-to-coastline-segment distance in kilometres."""
    lat0 = math.radians(point[0])
    scale = EARTH_RADIUS_KM * math.pi / 180
    px, py = point[1] * math.cos(lat0) * scale, point[0] * scale
    ax, ay = start[1] * math.cos(lat0) * scale, start[0] * scale
    bx, by = end[1] * math.cos(lat0) * scale, end[0] * scale
    dx, dy = bx - ax, by - ay
    denominator = dx * dx + dy * dy
    fraction = (
        0.0
        if denominator == 0
        else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denominator))
    )
    return math.hypot(px - (ax + fraction * dx), py - (ay + fraction * dy))


def rings(coordinates: Any) -> Iterable[list[list[float]]]:
    if not coordinates:
        return
    first = coordinates[0]
    if isinstance(first, list) and first and isinstance(first[0], (int, float)):
        yield coordinates
        return
    for child in coordinates:
        yield from rings(child)


def uk_coast_segments(boundaries: Path) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    payload = json.loads(boundaries.read_text(encoding="utf-8"))
    feature = next(
        (
            item
            for item in payload["features"]
            if item.get("properties", {}).get("ADM0_A3") == "GBR"
        ),
        None,
    )
    if feature is None:
        raise ValueError("Natural Earth boundary data has no United Kingdom feature")
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for ring in rings(feature["geometry"]["coordinates"]):
        for first, second in zip(ring, ring[1:]):
            # Ignore UK overseas territories retained in the admin-0 geometry.
            if all(
                -12.5 <= point[0] <= 4.0 and 48.0 <= point[1] <= 61.5 for point in (first, second)
            ):
                segments.append(
                    ((float(first[1]), float(first[0])), (float(second[1]), float(second[0])))
                )
    if not segments:
        raise ValueError("UK coastline extraction produced no local segments")
    return segments


def coastline_distance_km(
    location: tuple[float, float], segments: list[tuple[tuple[float, float], tuple[float, float]]]
) -> float:
    return min(point_to_segment_km(location, first, second) for first, second in segments)


def locations(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            radar = row["radar"]
            if radar not in result:
                result[radar] = {
                    "radar": radar,
                    "country": row["country"],
                    "source": row["source"],
                    "latitude": float(row["latitude"]),
                    "longitude": float(row["longitude"]),
                }
    return result


def write_subset(source: Path, destination: Path, radars: set[str]) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with (
        source.open(newline="", encoding="utf-8") as input_handle,
        destination.open("w", newline="", encoding="utf-8") as output_handle,
    ):
        reader = csv.DictReader(input_handle)
        if not reader.fieldnames:
            raise ValueError("training table has no header")
        writer = csv.DictWriter(output_handle, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            if row["radar"] in radars:
                writer.writerow(row)
                count += 1
    return count


def prepare(
    *,
    model_spec: Path,
    boundaries: Path,
    continental_countries: set[str],
    coastline_distance_limit_km: float,
    overlap_distance_limit_km: float,
    output_training_csv: Path,
    output_cohort: Path,
    output_spec: Path,
    model_id: str,
) -> dict[str, Any]:
    base = json.loads(model_spec.read_text(encoding="utf-8"))
    training_csv = Path(base["training_csv"])
    if not training_csv.is_file():
        raise ValueError("base runtime specification references a missing training table")
    segments = uk_coast_segments(boundaries)
    radar_locations = locations(training_csv)
    continental: list[dict[str, Any]] = []
    for radar in radar_locations.values():
        if radar["country"] not in continental_countries:
            continue
        distance = coastline_distance_km((radar["latitude"], radar["longitude"]), segments)
        if distance <= coastline_distance_limit_km:
            continental.append({**radar, "uk_coastline_distance_km": distance})
    continental.sort(key=lambda item: (item["country"], item["radar"]))
    if len(continental) < 3:
        raise ValueError("coastal corridor has fewer than three continental radars")

    continental_locations = [(item["latitude"], item["longitude"]) for item in continental]
    uk: list[dict[str, Any]] = []
    for radar in radar_locations.values():
        if radar["country"] != "GBR":
            continue
        nearest = min(
            haversine_km((radar["latitude"], radar["longitude"]), location)
            for location in continental_locations
        )
        coast_distance = coastline_distance_km((radar["latitude"], radar["longitude"]), segments)
        if nearest <= overlap_distance_limit_km and coast_distance <= overlap_distance_limit_km:
            uk.append(
                {
                    **radar,
                    "nearest_continental_radar_km": nearest,
                    "uk_coastline_distance_km": coast_distance,
                }
            )
    uk.sort(key=lambda item: item["radar"])
    if len(uk) < 3:
        raise ValueError("coastal corridor has fewer than three overlapping UK radars")

    retained_radars = {item["radar"] for item in [*continental, *uk]}
    row_count = write_subset(training_csv, output_training_csv, retained_radars)
    spec = dict(base)
    spec["model_id"] = model_id
    spec["training_csv"] = str(output_training_csv)
    spec["cohort_restriction"] = {
        "type": "uk_coastal_corridor_geographic_sensitivity_analysis",
        "uk_boundary_source": "Natural Earth 1:10m admin-0 country geometry",
        "continental_countries_considered": sorted(continental_countries),
        "continental_distance_to_uk_coastline_km_max": coastline_distance_limit_km,
        "uk_to_continental_overlap_distance_km_max": overlap_distance_limit_km,
        "continental_radars": [item["radar"] for item in continental],
        "uk_radars": [item["radar"] for item in uk],
        "publication_eligible": False,
        "reason": "Post-baseline geographic corridor experiment; it requires independent cross-coast validation.",
    }
    spec["runtime_spec_schema_version"] = "birdcast-euro-uk-coastal-corridor-spec-1.0"
    spec["source_runtime_spec_sha256"] = sha256(model_spec)
    spec["frozen_input_sha256"] = {"training_csv": sha256(output_training_csv)}
    output_spec.parent.mkdir(parents=True, exist_ok=True)
    output_spec.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    cohort = {
        "schema_version": "birdcast-euro-uk-coastal-corridor-1.0",
        "source_training_csv": str(training_csv),
        "source_training_csv_sha256": sha256(training_csv),
        "derived_training_csv": str(output_training_csv),
        "derived_training_csv_sha256": sha256(output_training_csv),
        "boundary_source": str(boundaries),
        "boundary_source_sha256": sha256(boundaries),
        "coastline_segment_count": len(segments),
        "continental_countries_considered": sorted(continental_countries),
        "continental_distance_to_uk_coastline_km_max": coastline_distance_limit_km,
        "uk_to_continental_overlap_distance_km_max": overlap_distance_limit_km,
        "continental_radars": continental,
        "uk_radars": uk,
        "retained_row_count": row_count,
        "raw_input_persisted": False,
        "publication_eligible": False,
    }
    output_cohort.parent.mkdir(parents=True, exist_ok=True)
    output_cohort.write_text(json.dumps(cohort, indent=2) + "\n", encoding="utf-8")
    return cohort


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-spec", type=Path, required=True)
    parser.add_argument("--boundaries", type=Path, required=True)
    parser.add_argument(
        "--continental-country", action="append", required=True, help="ISO country code; repeatable"
    )
    parser.add_argument("--coastline-distance-km", type=float, default=350.0)
    parser.add_argument("--overlap-distance-km", type=float, default=350.0)
    parser.add_argument("--output-training-csv", type=Path, required=True)
    parser.add_argument("--output-cohort", type=Path, required=True)
    parser.add_argument("--output-spec", type=Path, required=True)
    parser.add_argument("--model-id", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(
                model_spec=args.model_spec,
                boundaries=args.boundaries,
                continental_countries={value.upper() for value in args.continental_country},
                coastline_distance_limit_km=args.coastline_distance_km,
                overlap_distance_limit_km=args.overlap_distance_km,
                output_training_csv=args.output_training_csv,
                output_cohort=args.output_cohort,
                output_spec=args.output_spec,
                model_id=args.model_id,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
