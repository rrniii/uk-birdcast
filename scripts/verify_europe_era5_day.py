#!/usr/bin/env python3
"""Reconstruct one Europe ERA5 site-feature day and deny any changed value."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from birdcast_uk.era5 import extract_site_features


def verify(
    day: str,
    raw_dir: Path,
    radars: Path,
    feature_output: Path,
    output: Path,
    *,
    release_id: str,
) -> dict[str, object]:
    stamp = day.replace("-", "")
    single = raw_dir / f"era5_single_levels_{stamp}_uk.nc"
    pressure = raw_dir / f"era5_pressure_levels_{stamp}_uk.nc"
    for label, path in (("single-level", single), ("pressure-level", pressure), ("site features", feature_output)):
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Europe {label} input is missing or empty: {path}")
    with TemporaryDirectory(prefix="birdcast-euro-era5-fidelity-") as temporary:
        rebuilt = Path(temporary) / "features.json"
        status = extract_site_features(
            single_levels=single,
            pressure_levels=pressure,
            radars_path=radars,
            output=rebuilt,
        )
        if status.get("ok") is not True:
            raise ValueError("Europe ERA5 feature reconstruction did not succeed")
        actual = json.loads(feature_output.read_text(encoding="utf-8"))
        expected = json.loads(rebuilt.read_text(encoding="utf-8"))
    actual_rows = _normalise_rows(actual.get("rows"))
    expected_rows = _normalise_rows(expected.get("rows"))
    if actual_rows != expected_rows:
        raise ValueError(f"Europe ERA5 site-feature reconstruction mismatch for {day}")
    status_path = feature_output.with_suffix(feature_output.suffix + ".status.json")
    if not status_path.is_file():
        raise ValueError(f"Europe ERA5 feature provenance is missing for {day}")
    feature_status = json.loads(status_path.read_text(encoding="utf-8"))
    radars_sha256 = _sha256(radars)
    if feature_status.get("radars_sha256") != radars_sha256:
        raise ValueError(f"Europe ERA5 radar metadata provenance mismatch for {day}")
    payload = {
        "schema_version": "birdcast-euro-era5-fidelity-day-1.0",
        "status": "passed",
        "release_id": release_id,
        "day": day,
        "single_levels": str(single),
        "pressure_levels": str(pressure),
        "feature_output": str(feature_output),
        "single_levels_sha256": _sha256(single),
        "pressure_levels_sha256": _sha256(pressure),
        "feature_sha256": _sha256(feature_output),
        "radars_sha256": radars_sha256,
        "row_count": len(actual_rows),
        "raw_source_persisted": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def _normalise_rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError("Europe ERA5 site features have no rows list")
    rows = []
    for row in value:
        if not isinstance(row, dict):
            raise ValueError("Europe ERA5 site features contain a non-object row")
        normalised = {}
        for key, item in row.items():
            if isinstance(item, float):
                normalised[key] = None if math.isnan(item) else round(item, 10)
            else:
                normalised[key] = item
        rows.append(normalised)
    return sorted(rows, key=lambda row: (str(row.get("radar")), str(row.get("time_utc")), int(row.get("dataset_index") or 0)))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", required=True)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--radars", required=True)
    parser.add_argument("--feature-output", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()
    print(json.dumps(verify(
        args.day, Path(args.raw_dir), Path(args.radars), Path(args.feature_output), Path(args.output),
        release_id=args.release_id,
    ), indent=2))
