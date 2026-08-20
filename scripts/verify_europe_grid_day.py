#!/usr/bin/env python3
"""Independently reconstruct one Europe grid day and deny changed model input."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from birdcast_uk.era5 import extract_grid_features


def verify(
    day: str,
    raw_dir: Path,
    radars: Path,
    training_contract: Path,
    grid_output: Path,
    output: Path,
    *,
    release_id: str,
) -> dict[str, object]:
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("pyarrow is required for Europe grid fidelity") from exc
    stamp = day.replace("-", "")
    single = raw_dir / f"era5_single_levels_{stamp}_uk.nc"
    pressure = raw_dir / f"era5_pressure_levels_{stamp}_uk.nc"
    for label, path in (
        ("single-level", single),
        ("pressure-level", pressure),
        ("grid", grid_output),
    ):
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Europe {label} input is missing or empty: {path}")
    with TemporaryDirectory(prefix="birdcast-euro-grid-fidelity-") as temporary:
        rebuilt = Path(temporary) / "grid.parquet"
        status = extract_grid_features(
            single_levels=single,
            pressure_levels=pressure,
            radars_path=radars,
            training_table=training_contract,
            output=rebuilt,
        )
        if status.get("ok") is not True:
            raise ValueError("Europe grid reconstruction did not succeed")
        actual = parquet.read_table(grid_output)
        expected = parquet.read_table(rebuilt)
    if actual.schema != expected.schema or not actual.equals(expected, check_metadata=False):
        raise ValueError(f"Europe grid reconstruction mismatch for {day}")
    timestamps = actual.column("time_utc").to_pylist()
    if not timestamps or any(str(value)[:10] != day for value in timestamps):
        raise ValueError(f"Europe grid day contains an invalid timestamp: {day}")
    payload = {
        "schema_version": "birdcast-euro-grid-fidelity-day-1.0",
        "status": "passed",
        "release_id": release_id,
        "day": day,
        "grid_output": str(grid_output),
        "grid_sha256": _sha256(grid_output),
        "single_levels_sha256": _sha256(single),
        "pressure_levels_sha256": _sha256(pressure),
        "radars_sha256": _sha256(radars),
        "training_contract_sha256": _sha256(training_contract),
        "row_count": actual.num_rows,
        "column_names": actual.column_names,
        "raw_source_persisted": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


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
    parser.add_argument("--training-contract", required=True)
    parser.add_argument("--grid-output", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            verify(
                args.day,
                Path(args.raw_dir),
                Path(args.radars),
                Path(args.training_contract),
                Path(args.grid_output),
                Path(args.output),
                release_id=args.release_id,
            ),
            indent=2,
        )
    )
