#!/usr/bin/env python3
"""Validate and atomically merge daily component-selected GAMM predictions."""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

REQUIRED_COLUMNS = {
    "time_utc",
    "longitude",
    "latitude",
    "support",
    "mtr_birds_km_h",
    "vid_birds_per_km2",
    "bird_u_ms",
    "bird_v_ms",
}
NUMERIC_COLUMNS = REQUIRED_COLUMNS - {"time_utc"}


def _daily_files(root: Path, pulse: str) -> list[Path]:
    files = sorted(root.glob(f"*/predictions_wide_{pulse}.csv"))
    if not files:
        raise ValueError(f"no {pulse} prediction files under {root}")
    return files


def _parse_hour(value: str, source: Path) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{source} contains an invalid UTC timestamp: {value}") from exc
    parsed = parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    if parsed.minute or parsed.second or parsed.microsecond:
        raise ValueError(f"{source} contains a non-hourly timestamp: {value}")
    return parsed


def _expected_dates(first_day: date, last_day: date) -> list[date]:
    if last_day < first_day:
        raise ValueError("expected end day precedes start day")
    return [first_day + timedelta(days=offset) for offset in range((last_day - first_day).days + 1)]


def merge(
    root: Path,
    pulse: str,
    output: Path,
    expected_days: int,
    *,
    expected_start_day: date | None = None,
    expected_end_day: date | None = None,
    expected_component_manifest_sha256: str | None = None,
) -> dict[str, int | str]:
    if (
        expected_component_manifest_sha256 is not None
        and re.fullmatch(r"[0-9a-f]{64}", expected_component_manifest_sha256) is None
    ):
        raise ValueError("expected component manifest SHA-256 is invalid")
    files = _daily_files(root, pulse)
    if len(files) != expected_days:
        raise ValueError(f"expected {expected_days} {pulse} days, found {len(files)}")
    if (expected_start_day is None) != (expected_end_day is None):
        raise ValueError("expected start and end days must be supplied together")
    declared_dates = (
        _expected_dates(expected_start_day, expected_end_day)
        if expected_start_day is not None and expected_end_day is not None
        else None
    )
    if declared_dates is not None and len(declared_dates) != expected_days:
        raise ValueError("expected date range does not match expected day count")

    output.parent.mkdir(parents=True, exist_ok=True)
    reference_coordinates: set[tuple[float, float]] | None = None
    observed_dates: list[date] = []
    row_count = 0
    with NamedTemporaryFile(
        "w",
        dir=output.parent,
        prefix=f".{output.name}.",
        encoding="utf-8",
        newline="",
        delete=False,
    ) as target:
        temporary = Path(target.name)
        writer: csv.DictWriter[str] | None = None
        try:
            for source in files:
                if expected_component_manifest_sha256 is not None:
                    sidecar = source.parent / "component-manifest.sha256"
                    if (
                        sidecar.is_symlink()
                        or not sidecar.is_file()
                        or sidecar.read_text(encoding="ascii").strip()
                        != expected_component_manifest_sha256
                    ):
                        raise ValueError(f"{source} has the wrong component authority")
                with source.open("r", encoding="utf-8", newline="") as handle:
                    reader = csv.DictReader(handle)
                    fields = set(reader.fieldnames or [])
                    missing = REQUIRED_COLUMNS - fields
                    if missing:
                        raise ValueError(f"{source} missing columns: {', '.join(sorted(missing))}")
                    if writer is None:
                        writer = csv.DictWriter(target, fieldnames=reader.fieldnames or [])
                        writer.writeheader()
                    elif reader.fieldnames != writer.fieldnames:
                        raise ValueError(f"{source} has a different column layout")
                    rows = list(reader)
                if not rows:
                    raise ValueError(f"{source} is empty")

                by_hour: dict[datetime, set[tuple[float, float]]] = {}
                source_days: set[date] = set()
                for row in rows:
                    timestamp = _parse_hour(row["time_utc"], source)
                    source_days.add(timestamp.date())
                    numbers: dict[str, float] = {}
                    for name in NUMERIC_COLUMNS:
                        try:
                            value = float(row[name])
                        except (TypeError, ValueError) as exc:
                            raise ValueError(f"{source} has invalid {name}") from exc
                        if not math.isfinite(value):
                            raise ValueError(f"{source} has non-finite {name}")
                        numbers[name] = value
                    if not 0 <= numbers["support"] <= 1:
                        raise ValueError(f"{source} support is outside [0, 1]")
                    coordinate = (numbers["longitude"], numbers["latitude"])
                    coordinates = by_hour.setdefault(timestamp, set())
                    if coordinate in coordinates:
                        raise ValueError(
                            f"{source} repeats {coordinate} at {timestamp.isoformat()}"
                        )
                    coordinates.add(coordinate)
                if len(source_days) != 1:
                    raise ValueError(f"{source} contains more than one UTC day")
                source_day = next(iter(source_days))
                expected_hours = {
                    datetime.combine(source_day, datetime.min.time(), tzinfo=timezone.utc)
                    + timedelta(hours=hour)
                    for hour in range(24)
                }
                if set(by_hour) != expected_hours:
                    raise ValueError(f"{source} does not contain canonical 00-23 UTC hours")
                daily_coordinates = next(iter(by_hour.values()))
                if any(coordinates != daily_coordinates for coordinates in by_hour.values()):
                    raise ValueError(f"{source} changes grid coordinates within the day")
                if reference_coordinates is None:
                    reference_coordinates = daily_coordinates
                elif daily_coordinates != reference_coordinates:
                    raise ValueError(f"{source} grid coordinates differ from the release grid")
                if source_day in observed_dates:
                    raise ValueError(f"duplicate {pulse} prediction day: {source_day}")
                observed_dates.append(source_day)
                assert writer is not None
                writer.writerows(rows)
                row_count += len(rows)
            if observed_dates != sorted(observed_dates):
                raise ValueError(f"{pulse} prediction files are not ordered by UTC day")
            continuous = _expected_dates(observed_dates[0], observed_dates[-1])
            if observed_dates != continuous:
                raise ValueError(f"{pulse} prediction dates are not contiguous")
            if declared_dates is not None and observed_dates != declared_dates:
                raise ValueError(
                    f"{pulse} prediction dates do not match the declared release window"
                )
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    temporary.chmod(0o644)
    os.replace(temporary, output)
    return {
        "days": len(files),
        "first_day": observed_dates[0].isoformat(),
        "last_day": observed_dates[-1].isoformat(),
        "rows": row_count,
        "cells_per_hour": len(reference_coordinates or ()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--pulse", choices=("lp", "sp"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-days", type=int, default=365)
    parser.add_argument("--expected-start-day", type=date.fromisoformat)
    parser.add_argument("--expected-end-day", type=date.fromisoformat)
    parser.add_argument("--expected-component-manifest-sha256")
    args = parser.parse_args()
    print(
        merge(
            args.input_dir,
            args.pulse,
            args.output,
            args.expected_days,
            expected_start_day=args.expected_start_day,
            expected_end_day=args.expected_end_day,
            expected_component_manifest_sha256=args.expected_component_manifest_sha256,
        )
    )


if __name__ == "__main__":
    main()
