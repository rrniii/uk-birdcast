#!/usr/bin/env python3
"""Select an explicitly geographic, derived-input region for relative flow.

This consumes only existing hourly derivative CSVs. It never downloads or
rewrites radar source products.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


REQUIRED = {"radar", "country", "longitude", "time_utc"}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def prepare(
    *, input_csvs: list[Path], output_csv: Path, output_manifest: Path,
    countries: set[str], western_germany_longitude_max: float,
) -> dict:
    if not input_csvs:
        raise ValueError("at least one derived input CSV is required")
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    radars: dict[str, dict] = {}
    row_count = 0
    fieldnames: list[str] | None = None
    with output_csv.open("w", newline="", encoding="utf-8") as destination:
        writer = None
        for source in input_csvs:
            if not source.is_file():
                raise FileNotFoundError(f"derived input CSV is missing: {source}")
            with source.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                columns = set(reader.fieldnames or [])
                missing = REQUIRED - columns
                if missing:
                    raise ValueError(f"{source} is missing columns: {', '.join(sorted(missing))}")
                if fieldnames is None:
                    fieldnames = list(reader.fieldnames or [])
                    writer = csv.DictWriter(destination, fieldnames=fieldnames)
                    writer.writeheader()
                elif list(reader.fieldnames or []) != fieldnames:
                    raise ValueError(f"derived input schema differs: {source}")
                for row in reader:
                    country = str(row["country"]).upper()
                    if country not in countries:
                        continue
                    if country == "DE" and float(row["longitude"]) > western_germany_longitude_max:
                        continue
                    assert writer is not None
                    writer.writerow(row)
                    row_count += 1
                    radars.setdefault(row["radar"].lower(), {
                        "radar": row["radar"].lower(), "country": country,
                        "latitude": float(row["latitude"]), "longitude": float(row["longitude"]),
                    })
    if not row_count:
        raise ValueError("regional selector retained no derived rows")
    manifest = {
        "schema_version": "birdcast-relative-north-sea-region-1.0",
        "input_csvs": [{"path": str(path), "sha256": digest(path)} for path in input_csvs],
        "output_csv": str(output_csv), "output_csv_sha256": digest(output_csv),
        "countries": sorted(countries),
        "western_germany_longitude_max": western_germany_longitude_max,
        "radars": [radars[key] for key in sorted(radars)],
        "radar_count": len(radars), "retained_row_count": row_count,
        "raw_radar_products_written": False,
    }
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path, action="append", required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--country", action="append", required=True)
    parser.add_argument("--western-germany-longitude-max", type=float, default=10.0)
    args = parser.parse_args()
    print(json.dumps(prepare(
        input_csvs=args.input_csv, output_csv=args.output_csv, output_manifest=args.output_manifest,
        countries={value.upper() for value in args.country},
        western_germany_longitude_max=args.western_germany_longitude_max,
    ), indent=2))


if __name__ == "__main__":
    main()
