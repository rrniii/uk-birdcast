#!/usr/bin/env python3
"""Deny publication unless the Europe map assets exactly reflect passed inputs."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
from collections import defaultdict
from pathlib import Path


def _passed(path: Path, label: str, key: str = "status") -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get(key) is not True and payload.get(key) != "passed":
        raise ValueError(f"{label} is not passed")
    return payload


def validate(
    predictions: Path,
    output_root: Path,
    source_fidelity: Path,
    era5_fidelity: Path,
    training_fidelity: Path,
    model_validation: Path,
    output: Path,
) -> dict[str, object]:
    _passed(source_fidelity, "source fidelity")
    _passed(era5_fidelity, "ERA5 fidelity")
    _passed(training_fidelity, "training fidelity")
    _passed(model_validation, "model validation", key="release_passed")
    manifest_path = output_root / "latest" / "reanalysis.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("data_available") is not True or manifest.get("release_status") != "published":
        raise ValueError("Europe manifest is not a published, data-bearing release")
    validation_asset = str(manifest.get("assets", {}).get("validation") or "")
    if not validation_asset or validation_asset.startswith(("http://", "https://")):
        raise ValueError("Europe manifest must retain a local validation asset")
    if not (output_root / validation_asset).is_file():
        raise ValueError("Europe manifest validation asset is missing")
    grid_path = output_root / str(manifest["assets"]["grid"])
    grid = json.loads(grid_path.read_text(encoding="utf-8"))
    coordinates = [(float(cell["longitude"]), float(cell["latitude"])) for cell in grid.get("cells", [])]
    coordinate_index = {coordinate: index for index, coordinate in enumerate(coordinates)}
    if len(coordinate_index) != len(coordinates):
        raise ValueError("published Europe grid has duplicate cells")
    expected: dict[tuple[str, tuple[float, float]], dict[str, float | None]] = {}
    with predictions.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("prediction_class") == "unsupported":
                raise ValueError("unsupported prediction leaked into publication input")
            key = (str(row["time_utc"]), (float(row["longitude"]), float(row["latitude"])))
            if key in expected:
                raise ValueError(f"duplicate prediction cell: {key}")
            expected[key] = {field: _number(row.get(field)) for field in (
                "mtr_birds_km_h", "vid_birds_per_km2", "bird_u_ms", "bird_v_ms"
            )}
    seen: set[tuple[str, tuple[float, float]]] = set()
    template = str(manifest["assets"]["daily_template"])
    for day in sorted({timestamp[:10] for timestamp, _ in expected}):
        daily_path = output_root / template.replace("{date}", day)
        daily = json.loads(daily_path.read_text(encoding="utf-8"))
        for frame in daily.get("frames", []):
            timestamp = str(frame.get("time_utc") or "")
            for coordinate, index in coordinate_index.items():
                key = (timestamp, coordinate)
                values = {field: _number(frame.get(field, [None] * len(coordinates))[index]) for field in (
                    "mtr_birds_km_h", "vid_birds_per_km2", "bird_u_ms", "bird_v_ms"
                )}
                if key not in expected:
                    if any(value is not None for value in values.values()):
                        raise ValueError(f"daily asset contains an unexpected populated cell: {key}")
                    continue
                if values != expected[key]:
                    raise ValueError(f"daily asset differs from model prediction: {key}")
                seen.add(key)
    missing = set(expected) - seen
    if missing:
        raise ValueError(f"published daily assets omit {len(missing)} model cells")
    payload = {
        "schema_version": "birdcast-euro-publication-fidelity-1.0",
        "status": "passed",
        "model_id": manifest.get("model_id"),
        "prediction_row_count": len(expected),
        "grid_cell_count": len(coordinates),
        "published_frame_cell_count": len(seen),
        "release_status": manifest.get("release_status"),
        "validation_asset": validation_asset,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def validate_partitions(
    predictions_root: Path,
    output_root: Path,
    source_fidelity: Path,
    era5_fidelity: Path,
    training_fidelity: Path,
    model_validation: Path,
    output: Path,
) -> dict[str, object]:
    """Reconcile each published day against its model partition, streaming only one day."""

    _passed(source_fidelity, "source fidelity")
    _passed(era5_fidelity, "ERA5 fidelity")
    _passed(training_fidelity, "training fidelity")
    _passed(model_validation, "model validation", key="release_passed")
    manifest = json.loads((output_root / "latest" / "reanalysis.json").read_text(encoding="utf-8"))
    if manifest.get("data_available") is not True or manifest.get("release_status") != "published":
        raise ValueError("Europe manifest is not a published, data-bearing release")
    validation_asset = str(manifest.get("assets", {}).get("validation") or "")
    if not validation_asset or validation_asset.startswith(("http://", "https://")) or not (output_root / validation_asset).is_file():
        raise ValueError("Europe manifest validation asset is missing")
    grid = json.loads((output_root / str(manifest["assets"]["grid"])).read_text(encoding="utf-8"))
    coordinates = [(float(cell["longitude"]), float(cell["latitude"])) for cell in grid.get("cells", [])]
    coordinate_index = {coordinate: index for index, coordinate in enumerate(coordinates)}
    if len(coordinates) != len(coordinate_index):
        raise ValueError("published Europe grid has duplicate cells")
    paths = sorted(path for path in predictions_root.glob("prediction_*.csv*") if path.is_file() and path.stat().st_size)
    if not paths:
        raise ValueError("Europe prediction partition root has no daily CSV files")
    row_count = 0
    published_count = 0
    template = str(manifest["assets"]["daily_template"])
    for path in paths:
        expected: dict[tuple[str, tuple[float, float]], dict[str, float | None]] = {}
        with _open_prediction(path) as handle:
            for row in csv.DictReader(handle):
                if row.get("prediction_class") == "unsupported":
                    raise ValueError("unsupported prediction leaked into publication input")
                key = (str(row["time_utc"]), (float(row["longitude"]), float(row["latitude"])))
                if key in expected:
                    raise ValueError(f"duplicate prediction cell: {key}")
                expected[key] = {field: _number(row.get(field)) for field in (
                    "mtr_birds_km_h", "vid_birds_per_km2", "bird_u_ms", "bird_v_ms"
                )}
        days = {timestamp[:10] for timestamp, _ in expected}
        if len(days) != 1:
            raise ValueError(f"Europe prediction partition must contain exactly one day: {path}")
        day = next(iter(days))
        daily = json.loads((output_root / template.replace("{date}", day)).read_text(encoding="utf-8"))
        seen: set[tuple[str, tuple[float, float]]] = set()
        for frame in daily.get("frames", []):
            timestamp = str(frame.get("time_utc") or "")
            for coordinate, index in coordinate_index.items():
                key = (timestamp, coordinate)
                values = {field: _number(frame.get(field, [None] * len(coordinates))[index]) for field in (
                    "mtr_birds_km_h", "vid_birds_per_km2", "bird_u_ms", "bird_v_ms"
                )}
                if key not in expected:
                    if any(value is not None for value in values.values()):
                        raise ValueError(f"daily asset contains an unexpected populated cell: {key}")
                    continue
                if values != expected[key]:
                    raise ValueError(f"daily asset differs from model prediction: {key}")
                seen.add(key)
        missing = set(expected) - seen
        if missing:
            raise ValueError(f"published daily asset omits {len(missing)} model cells for {day}")
        row_count += len(expected)
        published_count += len(seen)
    payload = {
        "schema_version": "birdcast-euro-publication-fidelity-1.0",
        "status": "passed",
        "model_id": manifest.get("model_id"),
        "prediction_row_count": row_count,
        "grid_cell_count": len(coordinates),
        "published_frame_cell_count": published_count,
        "release_status": manifest.get("release_status"),
        "validation_asset": validation_asset,
        "partitioned_prediction_input": True,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def _open_prediction(path: Path):
    return gzip.open(path, "rt", newline="") if path.suffix == ".gz" else path.open(newline="", encoding="utf-8")


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    result = float(value)
    return result if math.isfinite(result) else None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    prediction_input = parser.add_mutually_exclusive_group(required=True)
    prediction_input.add_argument("--predictions")
    prediction_input.add_argument("--predictions-root")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--source-fidelity", required=True)
    parser.add_argument("--era5-fidelity", required=True)
    parser.add_argument("--training-fidelity", required=True)
    parser.add_argument("--model-validation", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    common = (
        Path(args.output_root), Path(args.source_fidelity), Path(args.era5_fidelity),
        Path(args.training_fidelity), Path(args.model_validation), Path(args.output),
    )
    result = validate_partitions(Path(args.predictions_root), *common) if args.predictions_root else validate(
        Path(args.predictions), *common
    )
    print(json.dumps(result, indent=2))
