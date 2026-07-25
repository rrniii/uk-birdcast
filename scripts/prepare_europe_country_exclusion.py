#!/usr/bin/env python3
"""Create a declared country-exclusion validation experiment for Europe GAMM.

The source training and transfer tables remain immutable.  This tool writes a
derived transfer-validation subset plus a runtime specification that records
the excluded countries, source hashes and retained radar cohort.  It is for
sensitivity analysis only: a restricted cohort cannot promote a previously
failed Europe-wide absolute-density release.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def country_counts(path: Path) -> tuple[int, dict[str, int], dict[str, set[str]]]:
    count = 0
    countries: dict[str, int] = {}
    radars: dict[str, set[str]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            count += 1
            country = (row.get("country") or "unknown").upper()
            countries[country] = countries.get(country, 0) + 1
            radars.setdefault(country, set()).add(row.get("radar") or "unknown")
    return count, countries, radars


def prepare(
    *,
    model_spec: Path,
    excluded_country: set[str],
    output_validation_csv: Path,
    output_spec: Path,
    output_audit: Path,
    model_id: str,
) -> dict[str, Any]:
    base = json.loads(model_spec.read_text(encoding="utf-8"))
    training_csv = Path(base["training_csv"])
    validation_csv = Path(base["validation_csv"])
    if not training_csv.is_file() or not validation_csv.is_file():
        raise ValueError("base runtime specification references missing training or validation input")

    training_rows, training_countries, training_radars = country_counts(training_csv)
    training_excluded = {
        country: {
            "row_count": training_countries.get(country, 0),
            "radars": sorted(training_radars.get(country, set())),
        }
        for country in sorted(excluded_country)
    }
    if any(item["row_count"] for item in training_excluded.values()):
        raise ValueError(
            "country exclusion would alter training data; create a separately audited training subset instead"
        )

    output_validation_csv.parent.mkdir(parents=True, exist_ok=True)
    retained_rows = 0
    removed_rows = 0
    removed_radars: set[str] = set()
    retained_radars: set[str] = set()
    with validation_csv.open(newline="", encoding="utf-8") as source, output_validation_csv.open(
        "w", newline="", encoding="utf-8"
    ) as destination:
        reader = csv.DictReader(source)
        if not reader.fieldnames or "country" not in reader.fieldnames or "radar" not in reader.fieldnames:
            raise ValueError("validation table must contain country and radar columns")
        writer = csv.DictWriter(destination, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            country = (row.get("country") or "unknown").upper()
            if country in excluded_country:
                removed_rows += 1
                removed_radars.add(row["radar"])
                continue
            retained_rows += 1
            retained_radars.add(row["radar"])
            writer.writerow(row)

    spec = dict(base)
    spec["model_id"] = model_id
    spec["validation_csv"] = str(output_validation_csv)
    spec["cohort_restriction"] = {
        "type": "country_exclusion_sensitivity_analysis",
        "excluded_countries": sorted(excluded_country),
        "training_rows_removed": 0,
        "validation_rows_removed": removed_rows,
        "validation_radars_removed": sorted(removed_radars),
        "validation_radars_retained": sorted(retained_radars),
        "publication_eligible": False,
        "reason": "Post-baseline restricted cohort; does not validate absolute MTR outside retained countries.",
    }
    spec["runtime_spec_schema_version"] = "birdcast-euro-country-exclusion-spec-1.0"
    spec["source_runtime_spec_sha256"] = sha256(model_spec)
    spec["frozen_input_sha256"] = {
        "training_csv": sha256(training_csv),
        "validation_csv": sha256(output_validation_csv),
    }
    output_spec.parent.mkdir(parents=True, exist_ok=True)
    output_spec.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    audit = {
        "schema_version": "birdcast-euro-country-exclusion-audit-1.0",
        "source_runtime_spec": str(model_spec),
        "source_runtime_spec_sha256": sha256(model_spec),
        "training_csv": str(training_csv),
        "training_csv_sha256": sha256(training_csv),
        "validation_csv": str(validation_csv),
        "validation_csv_sha256": sha256(validation_csv),
        "derived_validation_csv": str(output_validation_csv),
        "derived_validation_csv_sha256": sha256(output_validation_csv),
        "excluded_countries": sorted(excluded_country),
        "training_row_count": training_rows,
        "training_excluded_country_rows": training_excluded,
        "validation_rows_retained": retained_rows,
        "validation_rows_removed": removed_rows,
        "validation_radars_retained": sorted(retained_radars),
        "validation_radars_removed": sorted(removed_radars),
        "raw_input_persisted": False,
        "publication_eligible": False,
    }
    output_audit.parent.mkdir(parents=True, exist_ok=True)
    output_audit.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-spec", type=Path, required=True)
    parser.add_argument("--exclude-country", action="append", required=True, help="ISO country code; repeatable")
    parser.add_argument("--output-validation-csv", type=Path, required=True)
    parser.add_argument("--output-spec", type=Path, required=True)
    parser.add_argument("--output-audit", type=Path, required=True)
    parser.add_argument("--model-id", required=True)
    args = parser.parse_args()
    result = prepare(
        model_spec=args.model_spec,
        excluded_country={value.upper() for value in args.exclude_country},
        output_validation_csv=args.output_validation_csv,
        output_spec=args.output_spec,
        output_audit=args.output_audit,
        model_id=args.model_id,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
