"""Publish a relative European migration activity and flow product.

The available European radar sources are unsuitable for a transferable absolute MTR model. This
module therefore publishes only a within-radar activity percentile and the
observed horizontal flow direction from the immutable derived hourly table.
It never writes or changes VP, VPTS, PVOL, or source radar objects.
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import json
import math
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any

from .static_artifacts import utc_now, write_json

REQUIRED_COLUMNS = {
    "radar",
    "source",
    "country",
    "time_utc",
    "latitude",
    "longitude",
    "mtr_birds_km_h",
    "bird_u_ms",
    "bird_v_ms",
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


def build_coastal_relative_flow(
    *, training_csv: Path, cohort_json: Path, output_root: Path
) -> dict[str, Any]:
    """Build compact day partitions with percentile activity and flow direction.

    Percentiles are calculated independently within every radar over the
    supplied frozen model-year table.  This removes the known network/site
    scale mismatch instead of concealing it behind a multiplier.
    """

    if not training_csv.is_file():
        raise FileNotFoundError(f"Relative activity training table is missing: {training_csv}")
    if not cohort_json.is_file():
        raise FileNotFoundError(f"Relative activity cohort manifest is missing: {cohort_json}")
    cohort = json.loads(cohort_json.read_text(encoding="utf-8"))
    cohort_items = [
        *cohort.get("radars", []),
        *cohort.get("continental_radars", []),
        *cohort.get("uk_radars", []),
    ]
    cohort_radars = {
        str(item.get("radar") or "").lower()
        for item in cohort_items
        if isinstance(item, dict) and item.get("radar")
    }
    if not cohort_radars:
        raise ValueError("Relative activity cohort contains no radar slugs")

    rows: list[dict[str, Any]] = []
    values_by_radar: dict[str, list[float]] = defaultdict(list)
    radars: dict[str, dict[str, Any]] = {}
    radar_hours: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_source_row_count = 0
    excluded_non_cohort_row_count = 0
    with training_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - columns
        if missing:
            raise ValueError(
                f"Coastal training table is missing columns: {', '.join(sorted(missing))}"
            )
        for source in reader:
            radar = str(source.get("radar") or "").lower()
            if radar not in cohort_radars:
                excluded_non_cohort_row_count += 1
                continue
            value = _number(source.get("mtr_birds_km_h"))
            latitude, longitude = _number(source.get("latitude")), _number(source.get("longitude"))
            if value is None or value < 0 or latitude is None or longitude is None:
                continue
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
                identical = all(
                    previous[field] == row[field]
                    for field in ("activity_value", "bird_u_ms", "bird_v_ms")
                )
                if not identical:
                    raise ValueError(
                        f"Conflicting duplicate relative-activity radar-hour: {radar} {timestamp}"
                    )
                duplicate_source_row_count += 1
                continue
            radar_hours[key] = row
            rows.append(row)
            values_by_radar[radar].append(row["activity_value"])
            radars.setdefault(
                radar,
                {
                    "radar": radar,
                    "source": str(source["source"]),
                    "country": str(source["country"]),
                    "latitude": latitude,
                    "longitude": longitude,
                },
            )

    if not rows:
        raise ValueError("Relative activity training table contains no usable hourly observations")
    missing_cohort_radars = sorted(cohort_radars - set(radars))
    if missing_cohort_radars:
        raise ValueError(
            "Relative activity training table omits cohort radars: "
            f"{', '.join(missing_cohort_radars)}"
        )
    sorted_values = {radar: sorted(values) for radar, values in values_by_radar.items()}
    days: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        distribution = sorted_values[row["radar"]]
        percentile = (
            100.0 * bisect.bisect_right(distribution, row["activity_value"]) / len(distribution)
        )
        days[row["time_utc"][:10]][row["time_utc"]].append(
            {
                "radar": row["radar"],
                "activity_index": round(percentile, 2),
                "bird_u_ms": row["bird_u_ms"],
                "bird_v_ms": row["bird_v_ms"],
            }
        )

    radar_list = [radars[key] for key in sorted(radars)]
    first, latest = min(row["time_utc"] for row in rows), max(row["time_utc"] for row in rows)
    west = min(item["longitude"] for item in radar_list) - 3.0
    east = max(item["longitude"] for item in radar_list) + 3.0
    south = max(35.0, min(item["latitude"] for item in radar_list) - 3.0)
    north = min(75.0, max(item["latitude"] for item in radar_list) + 3.0)
    training_hash = _sha256(training_csv)
    cohort_hash = _sha256(cohort_json)
    release_digest = hashlib.sha256(
        f"birdcast-europe-relative-flow-1.1\0{training_hash}\0{cohort_hash}".encode()
    ).hexdigest()
    release_id = f"relative-{release_digest[:16]}"
    asset_prefix = Path("archive") / "relative-flow" / release_id
    available_dates = sorted(days)
    manifest: dict[str, Any] = {
        "schema_version": "birdcast-europe-relative-flow-1.1",
        "release_id": release_id,
        "data_available": True,
        "release_status": "published-relative-research-product",
        "generated_at_utc": utc_now(),
        "first_time_utc": first,
        "latest_time_utc": latest,
        "radars": radar_list,
        "radar_count": len(radar_list),
        "radar_hour_count": len(rows),
        "identical_duplicate_source_row_count": duplicate_source_row_count,
        "excluded_non_cohort_row_count": excluded_non_cohort_row_count,
        "available_dates": available_dates,
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
        "map": {
            "bounds": {"west": west, "east": east, "south": south, "north": north},
            "resolution_degrees": 0.25,
            "support_radius_km": 250.0,
            "method": "inverse-distance weighted relative radar activity field at native 0.25 degree cells",
            "land_mask_applied": False,
        },
        "cohort": {
            "radars": sorted(cohort_radars),
            "countries": cohort.get("countries", "declared in the frozen cohort manifest"),
            "geographic_rule": "all available European radar sources declared in the frozen cohort manifest",
        },
        "source": {
            "derived_training_csv_sha256": training_hash,
            "cohort_json_sha256": cohort_hash,
            "raw_radar_products_written": False,
        },
        "assets": {
            "daily_template": (asset_prefix / "days" / "{date}.json").as_posix(),
            "release_manifest": (asset_prefix / "manifest.json").as_posix(),
            "integrity": {"daily": {}},
        },
        "interpretation": (
            "Relative activity and observed flow at reporting radar locations. "
            "It is not an absolute migration traffic rate, a cross-network calibration, or a forecast."
        ),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    archive_root = output_root / "archive" / "relative-flow"
    archive_root.mkdir(parents=True, exist_ok=True)
    release_dir = archive_root / release_id
    if release_dir.exists():
        existing = json.loads((release_dir / "manifest.json").read_text(encoding="utf-8"))
        _validate_relative_release(output_root, existing)
        write_json(output_root / "latest" / "relative-flow.json", existing)
        return existing

    with TemporaryDirectory(prefix=f".{release_id}.", dir=archive_root) as temporary:
        staging = Path(temporary)
        daily_root = staging / "days"
        daily_root.mkdir()
        integrity = manifest["assets"]["integrity"]["daily"]
        for day, frames in sorted(days.items()):
            payload = {
                "schema_version": "birdcast-europe-relative-flow-day-1.0",
                "date": day,
                "frames": [
                    {
                        "time_utc": timestamp,
                        "radars": sorted(entries, key=lambda item: item["radar"]),
                    }
                    for timestamp, entries in sorted(frames.items())
                ],
            }
            path = daily_root / f"{day}.json"
            write_json(path, payload)
            integrity[day] = {
                "path": (asset_prefix / "days" / f"{day}.json").as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        write_json(staging / "manifest.json", manifest)
        staging.rename(release_dir)
    _validate_relative_release(output_root, manifest)
    write_json(output_root / "latest" / "relative-flow.json", manifest)
    return manifest


def _validate_relative_release(output_root: Path, manifest: dict[str, Any]) -> None:
    """Verify every immutable relative-flow asset before manifest promotion."""

    if manifest.get("schema_version") != "birdcast-europe-relative-flow-1.1":
        raise ValueError("Relative activity release has an unsupported schema")
    release_id = manifest.get("release_id")
    if (
        not isinstance(release_id, str)
        or re.fullmatch(r"relative-[0-9a-f]{16}", release_id) is None
    ):
        raise ValueError("Relative activity release has an invalid release id")
    dates = manifest.get("available_dates")
    assets = manifest.get("assets")
    if not isinstance(dates, list) or not dates or dates != sorted(set(dates)):
        raise ValueError("Relative activity release has invalid available dates")
    if not isinstance(assets, dict):
        raise ValueError("Relative activity release has no assets")
    expected_manifest_path = (
        PurePosixPath("archive") / "relative-flow" / release_id / "manifest.json"
    )
    if assets.get("release_manifest") != expected_manifest_path.as_posix():
        raise ValueError("Relative activity release manifest path is invalid")
    expected_template = (
        PurePosixPath("archive") / "relative-flow" / release_id / "days" / "{date}.json"
    )
    if assets.get("daily_template") != expected_template.as_posix():
        raise ValueError("Relative activity daily template is invalid")
    integrity = assets.get("integrity")
    daily = integrity.get("daily") if isinstance(integrity, dict) else None
    if not isinstance(daily, dict) or set(daily) != set(dates):
        raise ValueError("Relative activity release integrity index is incomplete")
    root = output_root.resolve()
    release_dir = root.joinpath("archive", "relative-flow", release_id)
    archive_manifest = release_dir / "manifest.json"
    if archive_manifest.is_symlink() or not archive_manifest.is_file():
        raise FileNotFoundError("Relative activity archive manifest is missing")
    if json.loads(archive_manifest.read_text(encoding="utf-8")) != manifest:
        raise ValueError("Relative activity archive and latest manifests differ")
    expected_files = {Path("manifest.json")}
    for day, record in daily.items():
        if not isinstance(record, dict):
            raise ValueError(f"Relative activity integrity record is invalid: {day}")
        relative = PurePosixPath(str(record.get("path") or ""))
        expected = PurePosixPath("archive") / "relative-flow" / release_id / "days" / f"{day}.json"
        if relative != expected or relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Relative activity asset path is unsafe: {relative}")
        path = root.joinpath(*relative.parts)
        current = path
        uses_symlink = False
        while current != root:
            uses_symlink = uses_symlink or current.is_symlink()
            current = current.parent
        if uses_symlink or not path.is_file() or root not in path.resolve().parents:
            raise FileNotFoundError(f"Relative activity asset is missing: {relative}")
        digest = record.get("sha256")
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or path.stat().st_size != record.get("size_bytes")
            or _sha256(path) != digest
        ):
            raise ValueError(f"Relative activity asset integrity failed: {relative}")
        expected_files.add(Path("days") / f"{day}.json")
    actual_files: set[Path] = set()
    for path in release_dir.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Relative activity release contains a symlink: {path}")
        if path.is_file():
            actual_files.add(path.relative_to(release_dir))
    if actual_files != expected_files:
        raise ValueError("Relative activity release contains unreferenced or missing files")


def install_coastal_static_site(site_root: Path) -> dict[str, Any]:
    """Replace managed web files atomically, independent of release permissions.

    Immutable code releases are read-only. Copying their modes to the live site
    made the second scheduled refresh fail; staging fresh 0644 files and renaming
    them also repairs existing read-only destinations without chmodding a release.
    """

    source = Path(__file__).with_name("static_coastal")
    if not source.is_dir():
        raise FileNotFoundError(f"European relative static source is missing: {source}")
    sources = {path.name: path for path in source.iterdir() if path.is_file()}
    shared = Path(__file__).with_name("static")
    for name in (
        "regional-boundaries.geojson",
        "live-uk-bird-maps-logo.jpg",
        "live-uk-bird-maps-favicon.png",
        "radar-marker.svg",
    ):
        sources[name] = shared / name
    site_root.parent.mkdir(parents=True, exist_ok=True)
    site_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".refresh-", dir=site_root) as temporary:
        staging = Path(temporary)
        for name, path in sources.items():
            shutil.copyfile(path, staging / name)
            (staging / name).chmod(0o644)
        # Promote HTML last. Unknown operator files are deliberately preserved.
        for name in sorted(sources, key=lambda name: name == "index.html"):
            os.replace(staging / name, site_root / name)
    return {"ok": True, "site_root": str(site_root), "file_count": len(list(site_root.iterdir()))}
