"""Read-only UK and Aloft VPTS access and comparison helpers.

This module deliberately works with existing daily VPTS CSV objects. A VP is
represented by the rows at one selected timestamp; no new VP or VPTS product
is written. Persistent output is restricted to compact comparison reports.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import io
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Iterable
from urllib.request import urlopen

from .config import ALOFT_COVERAGE_URL, ALOFT_PUBLIC_BASE_URL, ALOFT_SOURCES, BIORAD_VPTS_PREFIX, DEFAULT_PUBLIC_BASE_URL
from .static_artifacts import utc_now, write_json


FetchText = Callable[[str], str]


@dataclass(frozen=True)
class VptsObject:
    """An immutable source VPTS object and its provenance."""

    source: str
    radar: str
    day: str
    url: str
    pulse: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "source": self.source,
            "radar": self.radar,
            "date": self.day,
            "url": self.url,
            "pulse": self.pulse,
        }


def fetch_text(url: str, *, timeout_seconds: float = 60.0) -> str:
    with urlopen(url, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8")


def uk_vpts_object(
    *,
    radar: str,
    day: str | date,
    pulse: str = "lp",
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
) -> VptsObject:
    parsed_day = _parse_day(day)
    if pulse not in {"lp", "sp"}:
        raise ValueError("UK VPTS pulse must be 'lp' or 'sp'")
    slug = radar.strip().lower()
    stamp = parsed_day.strftime("%Y%m%d")
    url = (
        f"{public_base_url.rstrip('/')}/{BIORAD_VPTS_PREFIX}/"
        f"{slug}/{parsed_day:%Y}/{stamp}_{pulse}_vpts.csv"
    )
    return VptsObject("jasmin-uk", slug, stamp, url, pulse)


def aloft_daily_objects(
    *,
    radar: str,
    start_day: str | date,
    end_day: str | date,
    source: str = "baltrad",
    coverage_url: str = ALOFT_COVERAGE_URL,
    public_base_url: str = ALOFT_PUBLIC_BASE_URL,
    fetch: FetchText = fetch_text,
) -> list[VptsObject]:
    """Resolve existing Aloft daily VPTS objects from its published coverage."""
    if source not in ALOFT_SOURCES:
        raise ValueError(f"Unsupported Aloft source: {source}")
    start = _parse_day(start_day)
    end = _parse_day(end_day)
    if end < start:
        raise ValueError("end_day must not be before start_day")
    radar = radar.strip().lower()
    rows = list(csv.DictReader(io.StringIO(fetch(coverage_url))))
    objects: list[VptsObject] = []
    for row in rows:
        directory = str(row.get("directory") or "").strip("/")
        parts = directory.split("/")
        if len(parts) < 6 or parts[0] != source or parts[1] != "hdf5":
            continue
        row_radar = parts[2].lower()
        if row_radar != radar:
            continue
        try:
            object_day = date(int(parts[3]), int(parts[4]), int(parts[5]))
        except ValueError:
            continue
        if not start <= object_day <= end:
            continue
        key = f"{source}/daily/{row_radar}/{object_day:%Y}/{row_radar}_vpts_{object_day:%Y%m%d}.csv"
        objects.append(VptsObject(source, row_radar, object_day.strftime("%Y%m%d"), f"{public_base_url.rstrip('/')}/{key}"))
    return sorted(objects, key=lambda item: item.day)


def load_vpts_rows(
    obj: VptsObject,
    *,
    fetch: FetchText = fetch_text,
) -> list[dict[str, Any]]:
    """Load one existing CSV object, adding provenance only in memory."""
    rows: list[dict[str, Any]] = []
    for row in csv.DictReader(io.StringIO(fetch(obj.url))):
        record = {key: value for key, value in row.items()}
        record["source"] = obj.source
        record["source_url"] = obj.url
        record["archive_radar"] = obj.radar
        record["archive_date"] = obj.day
        if obj.pulse is not None:
            record["pulse"] = obj.pulse
        rows.append(record)
    return rows


def select_vp(rows: Iterable[dict[str, Any]], requested: str | datetime) -> dict[str, Any]:
    """Select the nearest timestamp profile from in-memory VPTS rows."""
    target = _parse_time(requested)
    profiles: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row.get("datetime")
        if not value:
            continue
        try:
            profiles[_parse_time(str(value))].append(row)
        except ValueError:
            continue
    if not profiles:
        raise ValueError("VPTS rows contain no valid datetime profiles")
    selected = min(profiles, key=lambda value: abs((value - target).total_seconds()))
    values = sorted(profiles[selected], key=lambda row: _number(row.get("height"), default=math.inf))
    return {
        "requested_time_utc": _iso_time(target),
        "selected_time_utc": _iso_time(selected),
        "time_offset_seconds": abs((selected - target).total_seconds()),
        "row_count": len(values),
        "rows": values,
        "provenance": _profile_provenance(values),
    }


def compare_vpts_profiles(
    uk_rows: Iterable[dict[str, Any]],
    aloft_rows: Iterable[dict[str, Any]],
    *,
    requested: str | datetime,
    max_time_offset_seconds: float = 300.0,
) -> dict[str, Any]:
    """Compare existing UK and Aloft profiles without generating radar products."""
    uk = select_vp(uk_rows, requested)
    aloft = select_vp(aloft_rows, requested)
    time_difference = abs(
        (_parse_time(uk["selected_time_utc"]) - _parse_time(aloft["selected_time_utc"])).total_seconds()
    )
    common = _common_altitude_rows(uk["rows"], aloft["rows"])
    variables = ("dens", "eta", "dbz", "dbz_all", "u", "v", "ff", "dd")
    metrics = {name: _comparison_metrics(common, name) for name in variables}
    match_class = "exact" if time_difference == 0 else "cadence-adjusted"
    return {
        "schema_version": "birdcast-uk-vpts-comparison-1.0",
        "generated_at_utc": utc_now(),
        "requested_time_utc": _iso_time(_parse_time(requested)),
        "match_class": match_class,
        "time_difference_seconds": time_difference,
        "within_time_tolerance": time_difference <= max_time_offset_seconds,
        "common_altitude_count": len(common),
        "uk": {key: value for key, value in uk.items() if key != "rows"},
        "aloft": {key: value for key, value in aloft.items() if key != "rows"},
        "metrics": metrics,
        "source_policy": "Existing UK and Aloft VPTS objects are read only; this report contains comparison statistics only.",
    }


def build_crosswalk(
    uk_radars: Iterable[dict[str, Any]],
    mappings: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Build an explicit UK-to-Aloft comparison crosswalk.

    A match is never inferred from proximity or a similar name. The mapping
    must name the UK archive radar, the Aloft source and radar, and its
    comparison class. This prevents the interface presenting unrelated radar
    observations as an exact validation pair.
    """
    by_slug = {str(radar.get("slug") or radar.get("radar") or "").lower(): radar for radar in uk_radars}
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mapping in mappings:
        uk_radar = str(mapping.get("uk_radar") or "").lower()
        if uk_radar not in by_slug:
            raise ValueError(f"Crosswalk references unknown UK radar: {uk_radar}")
        source = str(mapping.get("aloft_source") or "")
        if source not in ALOFT_SOURCES:
            raise ValueError(f"Crosswalk has unsupported Aloft source: {source}")
        aloft_radar = str(mapping.get("aloft_radar") or "").lower()
        comparison_class = str(mapping.get("comparison_class") or "")
        if not aloft_radar or comparison_class not in {"exact", "cadence-adjusted", "nearby-radar"}:
            raise ValueError("Crosswalk entries require aloft_radar and a valid comparison_class")
        if uk_radar in seen:
            raise ValueError(f"Crosswalk has multiple mappings for UK radar: {uk_radar}")
        seen.add(uk_radar)
        entries.append(
            {
                "uk_radar": uk_radar,
                "uk_label": by_slug[uk_radar].get("label"),
                "aloft_source": source,
                "aloft_radar": aloft_radar,
                "comparison_class": comparison_class,
                "max_time_offset_seconds": float(mapping.get("max_time_offset_seconds", 0 if comparison_class == "exact" else 300)),
                "notes": str(mapping.get("notes") or ""),
            }
        )
    unmatched = sorted(slug for slug in by_slug if slug and slug not in seen)
    return {
        "schema_version": "birdcast-uk-archive-crosswalk-1.0",
        "generated_at_utc": utc_now(),
        "entry_count": len(entries),
        "entries": sorted(entries, key=lambda entry: entry["uk_radar"]),
        "unmatched_uk_radars": unmatched,
        "matching_policy": "Only explicitly configured physical-radar or documented nearby-radar mappings are comparable.",
    }


def write_comparison_report(report: dict[str, Any], output: Path) -> dict[str, Any]:
    write_json(output, report)
    return {"ok": True, "output": str(output), "common_altitude_count": report["common_altitude_count"]}


def _common_altitude_rows(
    uk_rows: Iterable[dict[str, Any]], aloft_rows: Iterable[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    uk_by_height = {_number(row.get("height")): row for row in uk_rows if math.isfinite(_number(row.get("height")))}
    aloft_by_height = {_number(row.get("height")): row for row in aloft_rows if math.isfinite(_number(row.get("height")))}
    return [(uk_by_height[height], aloft_by_height[height]) for height in sorted(uk_by_height.keys() & aloft_by_height.keys())]


def _comparison_metrics(rows: Iterable[tuple[dict[str, Any], dict[str, Any]]], variable: str) -> dict[str, float | int | None]:
    differences: list[float] = []
    pairs: list[tuple[float, float]] = []
    for uk, aloft in rows:
        left, right = _number(uk.get(variable)), _number(aloft.get(variable))
        if math.isfinite(left) and math.isfinite(right):
            pairs.append((left, right))
            differences.append(left - right)
    if not differences:
        return {"count": 0, "bias": None, "mae": None, "rmse": None}
    return {
        "count": len(differences),
        "bias": mean(differences),
        "mae": mean(abs(value) for value in differences),
        "rmse": math.sqrt(mean(value * value for value in differences)),
    }


def _profile_provenance(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    first = next(iter(rows), {})
    return {
        "source": first.get("source"),
        "source_url": first.get("source_url"),
        "radar": first.get("archive_radar") or first.get("radar"),
        "pulse": first.get("pulse"),
    }


def _parse_day(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(value.replace("-", ""), "%Y%m%d").date()


def _parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        normalised = value.strip().replace("Z", "+00:00")
        result = datetime.fromisoformat(normalised)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _iso_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _number(value: object, *, default: float = math.nan) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
