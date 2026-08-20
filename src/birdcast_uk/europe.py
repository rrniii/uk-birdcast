"""Europe-wide Aloft and UK-SP model inputs and public artifacts.

Raw Aloft VPTS objects are consumed as HTTP streams. Only compact hourly
derivatives and provenance manifests may be persisted by this module.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import os
import re
import shutil
import signal
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any, BinaryIO, Callable, Iterable, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from .archive import VptsObject
from .config import (
    ALOFT_COVERAGE_URL,
    ALOFT_PUBLIC_BASE_URL,
    EUROPE_INTERPOLATION_DISTANCE_KM,
    EUROPE_MAX_SUPPORT_DISTANCE_KM,
    EUROPE_MIN_TRAINING_DAYS,
    EUROPE_MIN_TRANSFER_DAYS,
    EUROPE_PROCESSING_VERSION,
    EUROPE_REFERENCE_SOURCE,
    VPTS_ALTITUDE_MAX_M,
    VPTS_ALTITUDE_MIN_M,
)
from .observed import _hourly_rows, _profile_from_layers, _timestamp
from .static_artifacts import utc_now, write_json

OpenUrl = Callable[..., BinaryIO]


_MODEL_ID_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?\Z")
_PREDICTION_VALUE_FIELDS = (
    "mtr_birds_km_h",
    "vid_birds_per_km2",
    "bird_u_ms",
    "bird_v_ms",
    "nearest_radar_km",
)
_PREDICTION_UNCERTAINTY_FIELDS = (
    "uncertainty_mtr_birds_km_h",
    "uncertainty_vid_birds_per_km2",
)


@dataclass(frozen=True)
class AloftCohortEntry:
    radar: str
    source: str
    advertised_day_count: int
    first_day: str
    last_day: str
    role: str


@dataclass
class StreamAudit:
    source: str
    radar: str
    day: str
    url: str
    etag: str | None = None
    last_modified: str | None = None
    content_length: int | None = None
    bytes_read: int = 0
    row_count: int = 0
    profile_count: int = 0
    hourly_row_count: int = 0
    sha256: str = ""
    availability: str = "available"
    unavailable_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _HashingReader(io.RawIOBase):
    def __init__(self, raw: BinaryIO) -> None:
        self.raw = raw
        self.digest = hashlib.sha256()
        self.bytes_read = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray) -> int:
        chunk = self.raw.read(len(buffer))
        if not chunk:
            return 0
        size = len(chunk)
        buffer[:size] = chunk
        self.digest.update(chunk)
        self.bytes_read += size
        return size


def iter_aloft_coverage(
    *,
    coverage_url: str = ALOFT_COVERAGE_URL,
    public_base_url: str = ALOFT_PUBLIC_BASE_URL,
    source: str = "baltrad",
    opener: OpenUrl = urlopen,
    timeout_seconds: float = 60.0,
) -> Iterator[VptsObject]:
    """Yield advertised Aloft daily objects without buffering coverage.csv."""

    response = _open(opener, coverage_url, timeout_seconds)
    with response:
        text = io.TextIOWrapper(response, encoding="utf-8", newline="")
        for row in csv.DictReader(text):
            directory = str(row.get("directory") or "").strip("/")
            parts = directory.split("/")
            if len(parts) < 6 or parts[0] != source or parts[1] != "hdf5":
                continue
            radar = parts[2].strip().lower()
            try:
                day = date(int(parts[3]), int(parts[4]), int(parts[5]))
            except (TypeError, ValueError):
                continue
            key = f"{source}/daily/{radar}/{day:%Y}/{radar}_vpts_{day:%Y%m%d}.csv"
            yield VptsObject(
                source=source,
                radar=radar,
                day=day.strftime("%Y%m%d"),
                url=f"{public_base_url.rstrip('/')}/{key}",
            )


def build_aloft_cohort(
    objects: Iterable[VptsObject],
    *,
    minimum_training_days: int = EUROPE_MIN_TRAINING_DAYS,
    minimum_transfer_days: int = EUROPE_MIN_TRANSFER_DAYS,
) -> list[AloftCohortEntry]:
    """Classify radars before observations or model errors are inspected."""

    days: dict[tuple[str, str], set[str]] = defaultdict(set)
    for obj in objects:
        days[(obj.source, obj.radar)].add(obj.day)
    entries = []
    for (source, radar), values in sorted(days.items()):
        ordered = sorted(values)
        count = len(ordered)
        entries.append(
            AloftCohortEntry(
                radar=radar,
                source=source,
                advertised_day_count=count,
                first_day=ordered[0],
                last_day=ordered[-1],
                role=(
                    "training"
                    if count >= minimum_training_days
                    else "transfer-validation"
                    if count >= minimum_transfer_days
                    else "excluded-insufficient-coverage"
                ),
            )
        )
    return entries


def stream_aloft_hourly(
    obj: VptsObject,
    *,
    opener: OpenUrl = urlopen,
    timeout_seconds: float = 120.0,
    altitude_min_m: float = VPTS_ALTITUDE_MIN_M,
    altitude_max_m: float = VPTS_ALTITUDE_MAX_M,
) -> tuple[list[dict[str, Any]], StreamAudit]:
    """Stream one daily VPTS object and return only derived hourly rows."""

    audit = StreamAudit(obj.source, obj.radar, obj.day, obj.url)
    try:
        with _stream_deadline(timeout_seconds):
            response = _open(opener, obj.url, timeout_seconds)
            headers = getattr(response, "headers", {})
            audit.etag = _header(headers, "ETag")
            audit.last_modified = _header(headers, "Last-Modified")
            audit.content_length = _integer(_header(headers, "Content-Length"))
            hashing = _HashingReader(response)
            profiles_by_time: dict[str, list[dict[str, Any]]] = defaultdict(list)
            with response:
                text = io.TextIOWrapper(io.BufferedReader(hashing), encoding="utf-8", newline="")
                reader = csv.DictReader(text)
                required_columns = {"datetime", "height", "dens"}
                missing_columns = required_columns - set(reader.fieldnames or [])
                if missing_columns:
                    raise ValueError(
                        "Aloft VPTS CSV is missing required columns: "
                        + ", ".join(sorted(missing_columns))
                    )
                for row in reader:
                    audit.row_count += 1
                    timestamp = str(row.get("datetime") or "")
                    if not timestamp:
                        continue
                    row["radar"] = obj.radar
                    row["pulse"] = "aloft"
                    row["source"] = f"aloft-{obj.source}"
                    row["source_url"] = obj.url
                    profiles_by_time[timestamp].append(row)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        audit.availability = "unavailable"
        audit.unavailable_reason = "source_vpts_object_not_found"
        return [], audit
    audit.bytes_read = hashing.bytes_read
    audit.sha256 = hashing.digest.hexdigest()
    if audit.content_length is not None and audit.content_length != audit.bytes_read:
        raise ValueError(
            f"Aloft VPTS content length changed: expected {audit.content_length}, "
            f"read {audit.bytes_read}"
        )
    if audit.row_count == 0:
        raise ValueError("Aloft VPTS CSV contains no data rows")

    profiles = []
    for _, layers in sorted(profiles_by_time.items()):
        timestamp = _timestamp(layers[0])
        if timestamp is None:
            continue
        profiles.append(
            _profile_from_layers(
                obj.radar,
                "aloft",
                timestamp,
                layers,
                altitude_min_m=altitude_min_m,
                altitude_max_m=altitude_max_m,
            )
        )
    audit.profile_count = len(profiles)
    if not profiles:
        raise ValueError("Aloft VPTS CSV contains no parseable profiles")
    rows = _hourly_rows(profiles, include_phenology=False)
    for row in rows:
        direction = _number(row.get("dominant_direction_deg"))
        speed = _number(row.get("mean_ground_speed_ms"))
        row.update(
            {
                "source": f"aloft-{obj.source}",
                "network": obj.source,
                "source_reference": EUROPE_REFERENCE_SOURCE,
                "latitude": _profile_coordinate(profiles, "latitude"),
                "longitude": _profile_coordinate(profiles, "longitude"),
                "bird_u_ms": speed * math.sin(math.radians(direction))
                if _finite(speed, direction)
                else None,
                "bird_v_ms": speed * math.cos(math.radians(direction))
                if _finite(speed, direction)
                else None,
                "source_day": obj.day,
                "source_url": obj.url,
                "source_sha256": audit.sha256,
            }
        )
    audit.hourly_row_count = len(rows)
    if not rows:
        raise ValueError("Aloft VPTS CSV contains no usable hourly observations")
    return rows, audit


def write_aloft_cohort(entries: Iterable[AloftCohortEntry], output: Path) -> dict[str, Any]:
    records = [asdict(entry) for entry in entries]
    payload = {
        "schema_version": "birdcast-euro-aloft-cohort-1.0",
        "generated_at_utc": utc_now(),
        "processing_version": EUROPE_PROCESSING_VERSION,
        "selection_locked_before_scoring": True,
        "minimum_training_days": EUROPE_MIN_TRAINING_DAYS,
        "minimum_transfer_days": EUROPE_MIN_TRANSFER_DAYS,
        "entry_count": len(records),
        "training_count": sum(row["role"] == "training" for row in records),
        "transfer_validation_count": sum(row["role"] == "transfer-validation" for row in records),
        "excluded_insufficient_coverage_count": sum(
            row["role"] == "excluded-insufficient-coverage" for row in records
        ),
        "entries": records,
    }
    write_json(output, payload)
    return payload


def write_aloft_chunk_manifest(
    objects: Iterable[VptsObject],
    *,
    cohort: dict[str, Any],
    output: Path,
    start_day: str | None = None,
    end_day: str | None = None,
) -> dict[str, Any]:
    roles = {
        (str(entry["source"]), str(entry["radar"])): str(entry["role"])
        for entry in cohort.get("entries", [])
    }
    chunks: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
    for obj in objects:
        if start_day is not None and obj.day < start_day.replace("-", ""):
            continue
        if end_day is not None and obj.day > end_day.replace("-", ""):
            continue
        role = roles.get((obj.source, obj.radar))
        if role is None:
            continue
        chunks[(obj.source, obj.radar, obj.day[:4], obj.day[4:6])].append(obj.day)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for index, ((source, radar, year, month), days) in enumerate(sorted(chunks.items())):
            handle.write(
                json.dumps(
                    {
                        "index": index,
                        "source": source,
                        "radar": radar,
                        "year": year,
                        "month": month,
                        "role": roles[(source, radar)],
                        "days": sorted(set(days)),
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
    return {
        "schema_version": "birdcast-euro-stream-chunks-1.0",
        "chunk_count": len(chunks),
        "source_object_count": sum(len(set(days)) for days in chunks.values()),
        "start_day": start_day,
        "end_day": end_day,
        "output": str(output),
    }


def stream_aloft_chunk(
    chunk: dict[str, Any],
    *,
    output_root: Path,
    public_base_url: str = ALOFT_PUBLIC_BASE_URL,
    opener: OpenUrl = urlopen,
    release_id: str = "unversioned",
    retry_attempts: int = 3,
    retry_delay_seconds: float = 2.0,
) -> dict[str, Any]:
    source = str(chunk["source"])
    radar = str(chunk["radar"])
    year = str(chunk["year"])
    month = str(chunk["month"])
    output = (
        output_root / f"source={source}" / f"year={year}" / f"month={month}" / f"{radar}.parquet"
    )
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    if output.is_file() and manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("status") == "complete" and existing.get("release_id") == release_id:
            return {**existing, "skipped": True}
    if retry_attempts < 1:
        raise ValueError("retry_attempts must be at least one")
    rows: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for day in chunk.get("days", []):
        obj = VptsObject(
            source=source,
            radar=radar,
            day=str(day),
            url=(
                f"{public_base_url.rstrip('/')}/{source}/daily/{radar}/{year}/"
                f"{radar}_vpts_{day}.csv"
            ),
        )
        for attempt in range(retry_attempts):
            try:
                hourly, audit = stream_aloft_hourly(obj, opener=opener)
                break
            except HTTPError:
                # A non-404 response is an explicit source failure, never an
                # unavailable observation or a transient retry candidate.
                raise
            except (TimeoutError, URLError, OSError) as error:
                if attempt + 1 == retry_attempts:
                    raise RuntimeError(
                        f"Aloft VPTS stream failed after {retry_attempts} attempts for {obj.url}"
                    ) from error
                time.sleep(retry_delay_seconds * (attempt + 1))
        for row in hourly:
            row["cohort_role"] = chunk.get("role")
        if audit.availability != "available" or not hourly:
            raise ValueError(f"advertised Aloft source day is unavailable or empty: {obj.day}")
        rows.extend(hourly)
        audits.append(audit.to_dict())
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("pyarrow is required for Europe hourly partitions") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    pq.write_table(pa.Table.from_pylist(rows), temporary, compression="zstd")
    temporary.replace(output)
    manifest = {
        "schema_version": "birdcast-euro-hourly-chunk-1.0",
        "status": "complete",
        "generated_at_utc": utc_now(),
        "processing_version": EUROPE_PROCESSING_VERSION,
        "release_id": release_id,
        "raw_source_persisted": False,
        "role": chunk.get("role"),
        "derived_hourly_path": str(output),
        "day_count": len(audits),
        "available_day_count": sum(audit.get("availability") == "available" for audit in audits),
        "unavailable_day_count": sum(audit.get("availability") != "available" for audit in audits),
        "hourly_row_count": len(rows),
        "audits": audits,
    }
    write_json(manifest_path, manifest)
    return {**manifest, "skipped": False}


def read_jsonl_record(path: Path, index: int) -> dict[str, Any]:
    if index < 0:
        raise ValueError("manifest index must be non-negative")
    with path.open(encoding="utf-8") as handle:
        for current, line in enumerate(handle):
            if current == index:
                return json.loads(line)
    raise IndexError(f"manifest has no record at index {index}")


def write_hourly_parquet(
    rows: Iterable[dict[str, Any]],
    output: Path,
    *,
    audit: StreamAudit,
) -> dict[str, Any]:
    """Persist derived hourly rows only; raw source rows are never accepted."""

    records = list(rows)
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("pyarrow is required for Europe hourly partitions") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    pq.write_table(pa.Table.from_pylist(records), temporary, compression="zstd")
    temporary.replace(output)
    manifest = {
        "schema_version": "birdcast-euro-hourly-partition-1.0",
        "generated_at_utc": utc_now(),
        "processing_version": EUROPE_PROCESSING_VERSION,
        "raw_source_persisted": False,
        "derived_hourly_path": str(output),
        "hourly_row_count": len(records),
        "audit": audit.to_dict(),
    }
    write_json(output.with_suffix(output.suffix + ".manifest.json"), manifest)
    return manifest


def build_europe_manifest(
    *,
    model_id: str,
    first_time_utc: str,
    latest_time_utc: str,
    aloft_radar_count: int,
    uk_sp_radar_count: int,
    grid_asset: str,
    daily_asset_template: str,
    validation_url: str,
    output: Path,
    release_status: str = "research-preview",
    radar_asset: str | None = None,
    available_dates: Iterable[str] | None = None,
) -> dict[str, Any]:
    payload = _europe_manifest_payload(
        model_id=model_id,
        first_time_utc=first_time_utc,
        latest_time_utc=latest_time_utc,
        aloft_radar_count=aloft_radar_count,
        uk_sp_radar_count=uk_sp_radar_count,
        grid_asset=grid_asset,
        daily_asset_template=daily_asset_template,
        validation_url=validation_url,
        release_status=release_status,
        radar_asset=radar_asset,
        available_dates=available_dates,
    )
    write_json(output, payload)
    return payload


def _europe_manifest_payload(
    *,
    model_id: str,
    first_time_utc: str,
    latest_time_utc: str,
    aloft_radar_count: int,
    uk_sp_radar_count: int,
    grid_asset: str,
    daily_asset_template: str,
    validation_url: str,
    release_status: str,
    radar_asset: str | None,
    available_dates: Iterable[str] | None,
) -> dict[str, Any]:
    model_id = _validate_model_id(model_id)
    dates = sorted(set(available_dates or ()))
    return {
        "schema_version": "birdcast-euro-reanalysis-1.0",
        "data_available": True,
        "generated_at_utc": utc_now(),
        "processing_version": EUROPE_PROCESSING_VERSION,
        "model_id": model_id,
        "model_family": "source-aware-gamm",
        "reference_scale": EUROPE_REFERENCE_SOURCE,
        "training_sources": ["aloft-baltrad", "jasmin-uk-sp"],
        "first_time_utc": first_time_utc,
        "latest_time_utc": latest_time_utc,
        "available_dates": dates,
        "aloft_radar_count": aloft_radar_count,
        "uk_sp_radar_count": uk_sp_radar_count,
        "crs": "EPSG:3035",
        "era5_resolution_degrees": 0.25,
        "support": {
            "interpolation_distance_km": EUROPE_INTERPOLATION_DISTANCE_KM,
            "maximum_distance_km": EUROPE_MAX_SUPPORT_DISTANCE_KM,
            "land_mask_applied": False,
        },
        "uncertainty_contract": {
            "fields": list(_PREDICTION_UNCERTAINTY_FIELDS),
            "scale": "model linear-predictor standard error",
            "calibrated_prediction_uncertainty": False,
        },
        "assets": {
            "grid": grid_asset,
            "daily_template": daily_asset_template,
            "validation": validation_url,
            "radars": radar_asset,
        },
        "release_status": release_status,
        "interpretation": (
            "Historical cross-network research reanalysis on the Aloft BALTRAD "
            "reference scale. It is not a forecast or absolute biological calibration."
        ),
    }


def publish_europe_predictions(
    *,
    predictions_csv: Path,
    output_root: Path,
    model_id: str,
    aloft_radar_count: int,
    uk_sp_radar_count: int,
    validation_url: str,
    radars_json: Path | None = None,
    release_status: str = "research-preview",
) -> dict[str, Any]:
    """Publish a fixed grid and immutable daily assets for the Europe page."""

    model_id = _validate_model_id(model_id)
    archive_root, assets = _europe_release_paths(output_root, model_id)
    with predictions_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("Europe prediction CSV has no rows")
    coordinates = _prediction_coordinates_from_rows(rows, predictions_csv)
    if not coordinates:
        raise ValueError("Europe prediction CSV has no valid coordinates")
    cell_index = {coordinate: index for index, coordinate in enumerate(coordinates)}
    daily_frames, row_count = _prediction_frames(rows, cell_index, predictions_csv)
    published_days = sorted(daily_frames)
    first = str(daily_frames[published_days[0]][0]["time_utc"])
    latest_time = str(daily_frames[published_days[-1]][-1]["time_utc"])
    grid = {
        "schema_version": "birdcast-euro-grid-1.0",
        "crs": "EPSG:4326",
        "cells": [{"longitude": lon, "latitude": lat} for lon, lat in coordinates],
    }
    radar_payload = (
        json.loads(radars_json.read_text(encoding="utf-8")) if radars_json is not None else None
    )
    radar_asset = (
        f"archive/reanalysis/{model_id}/radars.json" if radar_payload is not None else None
    )
    manifest = _europe_manifest_payload(
        model_id=model_id,
        first_time_utc=first,
        latest_time_utc=latest_time,
        aloft_radar_count=aloft_radar_count,
        uk_sp_radar_count=uk_sp_radar_count,
        grid_asset=f"archive/reanalysis/{model_id}/grid.json",
        daily_asset_template=f"archive/reanalysis/{model_id}/{{date}}.json",
        validation_url=validation_url,
        release_status=release_status,
        radar_asset=radar_asset,
        available_dates=published_days,
    )
    with TemporaryDirectory(
        prefix=f".{model_id}.", suffix=".staging", dir=archive_root
    ) as temporary:
        staging = Path(temporary)
        _write_compact_json(staging / "grid.json", grid)
        if radar_payload is not None:
            _write_compact_json(staging / "radars.json", radar_payload)
        for day, frames in sorted(daily_frames.items()):
            _write_compact_json(
                staging / f"{day}.json",
                {"schema_version": "birdcast-euro-daily-1.0", "date": day, "frames": frames},
            )
        staging.rename(assets)
    write_json(output_root / "latest" / "reanalysis.json", manifest)
    return {
        "ok": True,
        "day_count": len(published_days),
        "prediction_row_count": row_count,
        "cell_count": len(coordinates),
        "first_time_utc": first,
        "latest_time_utc": latest_time,
        "manifest": manifest,
    }


def publish_europe_prediction_partitions(
    *,
    predictions_root: Path,
    output_root: Path,
    model_id: str,
    aloft_radar_count: int,
    uk_sp_radar_count: int,
    validation_url: str,
    radars_json: Path | None = None,
    release_status: str = "research-preview",
) -> dict[str, Any]:
    """Publish day-partitioned predictions without materialising a model year.

    Each input file must contain every supported cell at every UTC hour for its
    day.  The strict per-frame reconciliation prevents a partitioned storage
    layout from concealing dropped or shifted model values.
    """

    model_id = _validate_model_id(model_id)
    archive_root, assets = _europe_release_paths(output_root, model_id)
    paths = sorted(
        path
        for path in predictions_root.glob("prediction_*.csv*")
        if path.is_file() and path.stat().st_size
    )
    if not paths:
        raise ValueError("Europe prediction partition root has no daily CSV files")
    coordinates = _prediction_coordinates(paths[0])
    if not coordinates:
        raise ValueError("Europe prediction partitions have no valid coordinates")
    cell_index = {coordinate: index for index, coordinate in enumerate(coordinates)}
    radar_payload = (
        json.loads(radars_json.read_text(encoding="utf-8")) if radars_json is not None else None
    )
    radar_asset = (
        f"archive/reanalysis/{model_id}/radars.json" if radar_payload is not None else None
    )

    first_time: str | None = None
    latest_time: str | None = None
    frame_count = 0
    row_count = 0
    published_days: set[str] = set()
    with TemporaryDirectory(
        prefix=f".{model_id}.", suffix=".staging", dir=archive_root
    ) as temporary:
        staging = Path(temporary)
        _write_compact_json(
            staging / "grid.json",
            {
                "schema_version": "birdcast-euro-grid-1.0",
                "crs": "EPSG:4326",
                "cells": [{"longitude": lon, "latitude": lat} for lon, lat in coordinates],
            },
        )
        if radar_payload is not None:
            _write_compact_json(staging / "radars.json", radar_payload)
        for path in paths:
            day, frames, rows = _prediction_day_frames(path, cell_index)
            if day in published_days:
                raise ValueError(f"duplicate Europe prediction day: {day}")
            published_days.add(day)
            _write_compact_json(
                staging / f"{day}.json",
                {"schema_version": "birdcast-euro-daily-1.0", "date": day, "frames": frames},
            )
            frame_count += len(frames)
            row_count += rows
            times = [str(frame["time_utc"]) for frame in frames]
            first_time = min([first_time, *times] if first_time else times)
            latest_time = max([latest_time, *times] if latest_time else times)
        if first_time is None or latest_time is None:
            raise ValueError("Europe prediction partitions have no UTC frames")
        manifest = _europe_manifest_payload(
            model_id=model_id,
            first_time_utc=first_time,
            latest_time_utc=latest_time,
            aloft_radar_count=aloft_radar_count,
            uk_sp_radar_count=uk_sp_radar_count,
            grid_asset=f"archive/reanalysis/{model_id}/grid.json",
            daily_asset_template=f"archive/reanalysis/{model_id}/{{date}}.json",
            validation_url=validation_url,
            release_status=release_status,
            radar_asset=radar_asset,
            available_dates=sorted(published_days),
        )
        staging.rename(assets)
    write_json(output_root / "latest" / "reanalysis.json", manifest)
    return {
        "ok": True,
        "day_count": len(published_days),
        "frame_count": frame_count,
        "prediction_row_count": row_count,
        "cell_count": len(coordinates),
        "first_time_utc": first_time,
        "latest_time_utc": latest_time,
        "manifest": manifest,
    }


def _prediction_coordinates(path: Path) -> list[tuple[float, float]]:
    with _prediction_text(path) as handle:
        return _prediction_coordinates_from_rows(csv.DictReader(handle), path)


def _prediction_coordinates_from_rows(
    rows: Iterable[dict[str, str]], source: Path
) -> list[tuple[float, float]]:
    coordinates: set[tuple[float, float]] = set()
    for row in rows:
        coordinates.add(_prediction_coordinate(row, source))
    return sorted(coordinates)


def _prediction_coordinate(row: dict[str, str], source: Path) -> tuple[float, float]:
    longitude = _number(row.get("longitude"))
    latitude = _number(row.get("latitude"))
    if (
        not _finite(longitude, latitude)
        or not -180 <= longitude <= 180
        or not -90 <= latitude <= 90
    ):
        raise ValueError(f"invalid Europe prediction coordinate in {source}")
    return longitude, latitude


def _prediction_day_frames(
    path: Path,
    cell_index: dict[tuple[float, float], int],
) -> tuple[str, list[dict[str, Any]], int]:
    with _prediction_text(path) as handle:
        by_day, row_count = _prediction_frames(csv.DictReader(handle), cell_index, path)
    if len(by_day) != 1:
        raise ValueError(f"Europe prediction partition must contain exactly one UTC day: {path}")
    day = next(iter(by_day))
    return day, by_day[day], row_count


def _prediction_frames(
    rows: Iterable[dict[str, str]],
    cell_index: dict[tuple[float, float], int],
    source: Path,
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    frames: dict[str, dict[str, Any]] = {}
    seen: dict[str, set[int]] = defaultdict(set)
    row_count = 0
    for row in rows:
        timestamp = str(row.get("time_utc") or "")
        if len(timestamp) < 10:
            raise ValueError(f"Europe prediction row has no UTC timestamp in {source}")
        coordinate = _prediction_coordinate(row, source)
        index = cell_index.get(coordinate)
        if index is None:
            raise ValueError(f"Europe prediction coordinate is inconsistent: {coordinate}")
        support = str(row.get("prediction_class") or row.get("support") or "").strip()
        if support.lower() == "unsupported":
            raise ValueError("unsupported Europe prediction leaked into publication")
        if index in seen[timestamp]:
            raise ValueError(f"duplicate Europe prediction cell: {timestamp} {coordinate}")
        seen[timestamp].add(index)
        frame = frames.setdefault(timestamp, _empty_prediction_frame(timestamp, len(cell_index)))
        for field in _PREDICTION_VALUE_FIELDS:
            frame[field][index] = _optional_number(row.get(field))
        for field in _PREDICTION_UNCERTAINTY_FIELDS:
            frame[field][index] = _optional_number(row.get(field))
        frame["support"][index] = support
        row_count += 1
    days = {timestamp[:10] for timestamp in frames}
    if not days:
        raise ValueError(f"Europe prediction input has no UTC frames: {source}")
    result: dict[str, list[dict[str, Any]]] = {}
    for day in sorted(days):
        day_frames = {
            timestamp: frame for timestamp, frame in frames.items() if timestamp[:10] == day
        }
        _require_complete_utc_day(day, day_frames, source)
        for timestamp in day_frames:
            if len(seen[timestamp]) != len(cell_index):
                raise ValueError(f"Europe prediction frame is incomplete: {timestamp}")
        result[day] = [day_frames[key] for key in sorted(day_frames)]
    return result, row_count


def _require_complete_utc_day(
    day: str,
    timestamps: Iterable[str],
    source: Path,
) -> None:
    """Require the 24 canonical top-of-hour UTC timestamps for one day."""

    expected = {f"{day}T{hour:02d}:00:00Z" for hour in range(24)}
    actual = set(timestamps)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    raise ValueError(
        f"Europe prediction day must contain exactly 24 canonical UTC hourly frames: "
        f"{source} day={day} missing={missing} unexpected={unexpected}"
    )


def _empty_prediction_frame(timestamp: str, size: int) -> dict[str, Any]:
    return {
        "time_utc": timestamp,
        "mtr_birds_km_h": [None] * size,
        "vid_birds_per_km2": [None] * size,
        "bird_u_ms": [None] * size,
        "bird_v_ms": [None] * size,
        "uncertainty_mtr_birds_km_h": [None] * size,
        "uncertainty_vid_birds_per_km2": [None] * size,
        "support": [None] * size,
        "nearest_radar_km": [None] * size,
    }


def _prediction_text(path: Path):
    return (
        gzip.open(path, "rt", newline="")
        if path.suffix == ".gz"
        else path.open(newline="", encoding="utf-8")
    )


def _validate_model_id(model_id: str) -> str:
    value = str(model_id)
    if not _MODEL_ID_PATTERN.fullmatch(value) or value in {".", ".."}:
        raise ValueError(
            "Europe model_id must be a lowercase ASCII slug containing only letters, "
            "digits, dots, underscores, and hyphens"
        )
    return value


def _europe_release_paths(output_root: Path, model_id: str) -> tuple[Path, Path]:
    archive_root = output_root / "archive" / "reanalysis"
    archive_root.mkdir(parents=True, exist_ok=True)
    try:
        archive_root.resolve().relative_to(output_root.resolve())
    except ValueError as exc:
        raise ValueError("Europe release archive escapes the output root") from exc
    release = archive_root / model_id
    if release.exists() or release.is_symlink():
        raise FileExistsError(f"Europe model release is immutable and already exists: {model_id}")
    try:
        release.resolve().relative_to(archive_root.resolve())
    except ValueError as exc:
        raise ValueError("Europe model release escapes the output root") from exc
    return archive_root, release


def install_europe_static_site(site_root: Path) -> dict[str, Any]:
    source = Path(__file__).with_name("static_europe")
    if not source.is_dir():
        raise FileNotFoundError(f"Europe static source is missing: {source}")
    site_root.mkdir(parents=True, exist_ok=True)
    for path in source.iterdir():
        if path.is_file():
            shutil.copy2(path, site_root / path.name)
    shared = Path(__file__).with_name("static")
    for name in (
        "regional-boundaries.geojson",
        "live-uk-bird-maps-logo.jpg",
        "live-uk-bird-maps-favicon.png",
        "radar-marker.svg",
    ):
        shutil.copy2(shared / name, site_root / name)
    return {"ok": True, "site_root": str(site_root), "file_count": len(list(site_root.iterdir()))}


def _open(opener: OpenUrl, url: str, timeout_seconds: float) -> BinaryIO:
    try:
        return opener(url, timeout=timeout_seconds)
    except TypeError:
        return opener(url)


class _StreamDeadline:
    def __init__(self, seconds: float):
        self.seconds = seconds
        self.previous_handler: Any = None

    def __enter__(self) -> None:
        if self.seconds <= 0:
            raise ValueError("Aloft stream timeout must be positive")
        self.previous_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, self._expired)
        signal.setitimer(signal.ITIMER_REAL, self.seconds)

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, self.previous_handler)

    @staticmethod
    def _expired(_signum: int, _frame: object) -> None:
        raise TimeoutError("Aloft VPTS stream exceeded its wall-clock deadline")


def _stream_deadline(seconds: float) -> _StreamDeadline:
    return _StreamDeadline(seconds)


def _header(headers: Any, name: str) -> str | None:
    if hasattr(headers, "get"):
        value = headers.get(name)
        return str(value) if value not in (None, "") else None
    return None


def _integer(value: object) -> int | None:
    try:
        return int(str(value)) if value not in (None, "") else None
    except ValueError:
        return None


def _number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _finite(*values: float) -> bool:
    return all(math.isfinite(value) for value in values)


def _profile_coordinate(profiles: Iterable[dict[str, Any]], name: str) -> float | None:
    for profile in profiles:
        value = _number(profile.get(name))
        if math.isfinite(value):
            return value
    return None


def _optional_number(value: object) -> float | None:
    parsed = _number(value)
    return parsed if math.isfinite(parsed) else None


def _write_compact_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n"
    with NamedTemporaryFile(
        "w",
        dir=path.parent,
        encoding="utf-8",
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(content)
        temporary_path = Path(handle.name)
    try:
        temporary_path.chmod(0o644)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
