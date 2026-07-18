"""Build browser-ready historical VPTS reanalysis products."""

from __future__ import annotations

from collections import defaultdict
import csv
from datetime import date
from html import escape
import json
import math
import os
from pathlib import Path
from statistics import median
from tempfile import NamedTemporaryFile
from typing import Any, Iterable
from urllib.request import urlopen

from .radars import load_radars
from .static_artifacts import utc_now


NATURAL_EARTH_10M_COUNTRIES_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "master/geojson/ne_10m_admin_0_countries.geojson"
)
REQUIRED_SOURCE_FILES = (
    "analysis_summary.json",
    "daily_totals.csv",
    "network_annual_seasonal_totals.csv",
    "phenology.csv",
    "coverage.csv",
)


def _write_compact_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        dir=path.parent,
        encoding="utf-8",
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        json.dump(payload, handle, separators=(",", ":"), ensure_ascii=True)
        handle.write("\n")
        temporary_path = Path(handle.name)
    temporary_path.chmod(0o644)
    os.replace(temporary_path, path)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _number(value: str | None, *, integer: bool = False) -> int | float | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value)) if integer else round(float(value), 8)
    except ValueError:
        return None


def _daily_record(row: dict[str, str]) -> dict[str, object]:
    return {
        "radar": row["radar"],
        "date": row["date"],
        "pulse": row["pulse"],
        "period": row["solar_period"],
        "season": row["season"],
        "profiles": _number(row.get("profile_count"), integer=True),
        "vid": _number(row.get("vid_birds_per_km2")),
        "mean_vid": _number(row.get("mean_vid_birds_per_km2_per_profile")),
        "height_m": _number(row.get("mean_weighted_height_m")),
        "speed_ms": _number(row.get("mean_ff_ms")),
    }


def _annual_record(row: dict[str, str]) -> dict[str, object]:
    return {
        "year": _number(row.get("year"), integer=True),
        "pulse": row["pulse"],
        "period": row["solar_period"],
        "season": row["season"],
        "days": _number(row.get("day_count"), integer=True),
        "radars": _number(row.get("radar_count"), integer=True),
        "profiles": _number(row.get("profile_count"), integer=True),
        "mean_daily_vid": _number(row.get("mean_daily_vid_birds_per_km2")),
        "mean_profile_vid": _number(row.get("mean_profile_vid_birds_per_km2")),
        "vid": _number(row.get("vid_birds_per_km2")),
    }


def _phenology_record(row: dict[str, str]) -> dict[str, object]:
    return {
        "year": _number(row.get("year"), integer=True),
        "radar": row["radar"],
        "pulse": row["pulse"],
        "period": row["solar_period"],
        "season": row["season"],
        "days": _number(row.get("day_count"), integer=True),
        "onset_5": row.get("onset_5_date") or None,
        "early_10": row.get("early_10_date") or None,
        "median_50": row.get("median_50_date") or None,
        "late_90": row.get("late_90_date") or None,
        "peak": row.get("peak_date") or None,
        "peak_vid": _number(row.get("peak_vid_birds_per_km2")),
    }


def _coverage_record(row: dict[str, str]) -> dict[str, object]:
    return {
        "year": _number(row.get("year"), integer=True),
        "radar": row["radar"],
        "pulse": row["pulse"],
        "files": _number(row.get("file_count"), integer=True),
        "profiles": _number(row.get("profile_count"), integer=True),
        "failed": _number(row.get("failed_file_count"), integer=True),
        "first_date": row.get("first_date") or None,
        "last_date": row.get("last_date") or None,
    }


def _load_boundary(source: str | Path) -> dict[str, object]:
    text = str(source)
    if text.startswith(("https://", "http://")):
        with urlopen(text, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
    else:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
    if payload.get("type") != "FeatureCollection":
        raise ValueError("boundary source must be a GeoJSON FeatureCollection")
    features = [
        feature
        for feature in payload.get("features", [])
        if feature.get("properties", {}).get("ADM0_A3") in {"GBR", "IRL"}
    ]
    if {feature.get("properties", {}).get("ADM0_A3") for feature in features} != {"GBR", "IRL"}:
        raise ValueError("boundary source does not contain GBR and IRL features")
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "bbox": [-12.5, 48.5, 3.5, 61.5],
            "resolution": "Natural Earth 1:10m",
            "source": text,
        },
    }


def _svg_frame(title: str, subtitle: str, body: str, *, width: int = 1200, height: int = 680) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">
  <title>{escape(title)}</title>
  <desc>{escape(subtitle)}</desc>
  <rect width="{width}" height="{height}" fill="#ffffff"/>
  <text x="72" y="58" font-family="system-ui,sans-serif" font-size="26" font-weight="700" fill="#17251f">{escape(title)}</text>
  <text x="72" y="86" font-family="system-ui,sans-serif" font-size="14" fill="#66736b">{escape(subtitle)}</text>
  {body}
</svg>
"""


def _scale(value: float, low: float, high: float, start: float, end: float) -> float:
    if high <= low:
        return (start + end) / 2
    return start + ((value - low) / (high - low)) * (end - start)


def _line_plot(
    title: str,
    subtitle: str,
    series: dict[str, list[tuple[int, float]]],
    *,
    y_label: str,
) -> str:
    left, right, top, bottom = 92, 1140, 125, 590
    points = [point for values in series.values() for point in values]
    years = [point[0] for point in points]
    values = [point[1] for point in points]
    x_min, x_max = min(years), max(years)
    y_max = max(values) * 1.08 if values else 1
    colours = ("#1d6b58", "#c94f3d", "#5876a8", "#d39b2b")
    parts = [
        f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="#98a59d"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" stroke="#98a59d"/>',
        f'<text x="24" y="{(top + bottom) / 2}" transform="rotate(-90 24 {(top + bottom) / 2})" '
        f'font-family="system-ui,sans-serif" font-size="13" fill="#56635c">{escape(y_label)}</text>',
    ]
    for tick in range(5):
        value = y_max * tick / 4
        y = _scale(value, 0, y_max, bottom, top)
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="#e4e9e5"/>')
        parts.append(
            f'<text x="{left - 12}" y="{y + 5:.1f}" text-anchor="end" '
            f'font-family="system-ui,sans-serif" font-size="12" fill="#66736b">{value:.1f}</text>'
        )
    for year in range(x_min, x_max + 1, 2):
        x = _scale(year, x_min, x_max, left, right)
        parts.append(
            f'<text x="{x:.1f}" y="{bottom + 28}" text-anchor="middle" '
            f'font-family="system-ui,sans-serif" font-size="12" fill="#66736b">{year}</text>'
        )
    for index, (name, rows) in enumerate(series.items()):
        colour = colours[index % len(colours)]
        coords = [
            (_scale(year, x_min, x_max, left, right), _scale(value, 0, y_max, bottom, top))
            for year, value in sorted(rows)
        ]
        path = " ".join(f"{'M' if i == 0 else 'L'} {x:.1f} {y:.1f}" for i, (x, y) in enumerate(coords))
        parts.append(f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="3"/>')
        parts.extend(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{colour}"/>' for x, y in coords)
        legend_x = 720 + (index % 2) * 200
        legend_y = 104 + (index // 2) * 22
        parts.append(f'<line x1="{legend_x}" y1="{legend_y}" x2="{legend_x + 26}" y2="{legend_y}" stroke="{colour}" stroke-width="3"/>')
        parts.append(
            f'<text x="{legend_x + 34}" y="{legend_y + 5}" font-family="system-ui,sans-serif" '
            f'font-size="13" fill="#344139">{escape(name)}</text>'
        )
    return _svg_frame(title, subtitle, "".join(parts))


def _bar_plot(title: str, subtitle: str, values: list[tuple[str, float]]) -> str:
    left, right, top, bottom = 92, 1140, 125, 590
    maximum = max(value for _, value in values) * 1.08 if values else 1
    gap = 18
    width = (right - left - gap * (len(values) - 1)) / max(1, len(values))
    colours = ("#1d6b58", "#d39b2b", "#c94f3d")
    parts = [f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="#98a59d"/>']
    for index, (label, value) in enumerate(values):
        x = left + index * (width + gap)
        y = _scale(value, 0, maximum, bottom, top)
        colour = colours[index % 3]
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{bottom - y:.1f}" fill="{colour}"/>')
        parts.append(
            f'<text x="{x + width / 2:.1f}" y="{bottom + 25}" text-anchor="middle" '
            f'font-family="system-ui,sans-serif" font-size="12" fill="#56635c">{escape(label)}</text>'
        )
        parts.append(
            f'<text x="{x + width / 2:.1f}" y="{y - 10:.1f}" text-anchor="middle" '
            f'font-family="system-ui,sans-serif" font-size="12" font-weight="700" fill="#344139">{value:.1f}</text>'
        )
    return _svg_frame(title, subtitle, "".join(parts))


def _coverage_plot(rows: list[dict[str, object]], pulse: str) -> str:
    filtered = [row for row in rows if row["pulse"] == pulse]
    radars = sorted({str(row["radar"]) for row in filtered})
    years = sorted({int(row["year"]) for row in filtered if row["year"] is not None})
    left, right, top, bottom = 220, 1140, 122, 610
    row_height = (bottom - top) / max(1, len(radars))
    parts = []
    for year in years:
        x = _scale(year, min(years), max(years), left, right)
        parts.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{bottom}" stroke="#e4e9e5"/>')
        parts.append(
            f'<text x="{x:.1f}" y="{bottom + 24}" text-anchor="middle" '
            f'font-family="system-ui,sans-serif" font-size="11" fill="#66736b">{year}</text>'
        )
    available = {(str(row["radar"]), int(row["year"])) for row in filtered if row["year"] is not None}
    for index, radar in enumerate(radars):
        y = top + (index + 0.5) * row_height
        parts.append(
            f'<text x="{left - 12}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-family="system-ui,sans-serif" font-size="11" fill="#344139">{escape(radar.replace("-", " ").title())}</text>'
        )
        for year in years:
            if (radar, year) in available:
                x = _scale(year, min(years), max(years), left, right)
                parts.append(f'<rect x="{x - 11:.1f}" y="{y - 7:.1f}" width="22" height="14" fill="#1d6b58"/>')
    return _svg_frame(
        "Radar archive coverage",
        f"Years with {pulse.upper()} VPTS data; availability is not equal sampling effort",
        "".join(parts),
    )


def _make_plots(
    output_dir: Path,
    annual: list[dict[str, object]],
    phenology: list[dict[str, object]],
    coverage: list[dict[str, object]],
    *,
    pulse: str = "lp",
    trend_end_year: int = 2025,
) -> list[str]:
    plots_dir = output_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    nocturnal: dict[str, list[tuple[int, float]]] = {"Spring night": [], "Autumn night": []}
    for row in annual:
        if (
            row["pulse"] == pulse
            and row["period"] == "night"
            and row["season"] in {"spring", "autumn"}
            and isinstance(row["year"], int)
            and row["year"] <= trend_end_year
            and isinstance(row["mean_daily_vid"], (int, float))
        ):
            nocturnal[f"{str(row['season']).title()} night"].append((row["year"], float(row["mean_daily_vid"])))
    annual_svg = _line_plot(
        "Annual nocturnal bird passage",
        f"Network mean daily VID passage index, {pulse.upper()} only; partial 2026 excluded",
        nocturnal,
        y_label="Mean daily VID (birds km-2 per radar-day)",
    )

    period_values: list[tuple[str, float]] = []
    for period in ("day", "civil_twilight", "night"):
        values = [
            float(row["mean_daily_vid"])
            for row in annual
            if row["pulse"] == pulse
            and row["period"] == period
            and isinstance(row["year"], int)
            and row["year"] <= trend_end_year
            and isinstance(row["mean_daily_vid"], (int, float))
        ]
        period_values.append((period.replace("_", " ").title(), sum(values) / len(values) if values else 0))
    activity_svg = _bar_plot(
        "Bird passage by solar period",
        f"Mean of annual-season network mean daily VID, {pulse.upper()} only, 2013-{trend_end_year}",
        period_values,
    )

    phenology_series: dict[str, list[tuple[int, float]]] = {"Spring median": [], "Autumn median": []}
    grouped: dict[tuple[int, str], list[int]] = defaultdict(list)
    for row in phenology:
        if (
            row["pulse"] == pulse
            and row["period"] == "night"
            and row["season"] in {"spring", "autumn"}
            and isinstance(row["year"], int)
            and row["year"] <= trend_end_year
            and isinstance(row["median_50"], str)
        ):
            grouped[(row["year"], str(row["season"]))].append(date.fromisoformat(row["median_50"]).timetuple().tm_yday)
    for (year, season), values in grouped.items():
        phenology_series[f"{season.title()} median"].append((year, float(median(values))))
    phenology_svg = _line_plot(
        "Nocturnal migration phenology",
        f"Median passage date across radars, {pulse.upper()} only",
        phenology_series,
        y_label="Day of year",
    )

    assets = {
        "annual_nocturnal_lp.svg": annual_svg,
        "solar_period_lp.svg": activity_svg,
        "phenology_night_lp.svg": phenology_svg,
        "coverage_lp.svg": _coverage_plot(coverage, pulse),
    }
    for name, content in assets.items():
        path = plots_dir / name
        path.write_text(content, encoding="ascii")
        path.chmod(0o644)
    return [f"historical/plots/{name}" for name in assets]


def _radar_payload(radars_path: Path) -> list[dict[str, object]]:
    return [
        {
            "slug": radar.slug,
            "label": radar.label,
            "latitude": radar.latitude,
            "longitude": radar.longitude,
            "height_m": radar.height_m,
        }
        for radar in load_radars(radars_path)
        if radar.latitude is not None and radar.longitude is not None
    ]


def build_historical_products(
    source_dir: Path,
    output_root: Path,
    *,
    radars_path: Path,
    boundary_source: str | Path = NATURAL_EARTH_10M_COUNTRIES_URL,
) -> dict[str, object]:
    missing = [name for name in REQUIRED_SOURCE_FILES if not (source_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"historical source is missing: {', '.join(missing)}")

    analysis = json.loads((source_dir / "analysis_summary.json").read_text(encoding="utf-8"))
    historical_dir = output_root / "historical"
    daily_dir = historical_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    daily_by_year: dict[int, list[dict[str, object]]] = defaultdict(list)
    min_date: str | None = None
    max_date: str | None = None
    for row in _read_csv(source_dir / "daily_totals.csv"):
        record = _daily_record(row)
        year = int(row["year"])
        daily_by_year[year].append(record)
        min_date = record["date"] if min_date is None or record["date"] < min_date else min_date
        max_date = record["date"] if max_date is None or record["date"] > max_date else max_date

    daily_assets: dict[str, str] = {}
    for year, rows in sorted(daily_by_year.items()):
        path = daily_dir / f"{year}.json"
        _write_compact_json(path, {"year": year, "rows": rows})
        daily_assets[str(year)] = f"historical/daily/{year}.json"

    annual = [_annual_record(row) for row in _read_csv(source_dir / "network_annual_seasonal_totals.csv")]
    phenology = [_phenology_record(row) for row in _read_csv(source_dir / "phenology.csv")]
    coverage = [_coverage_record(row) for row in _read_csv(source_dir / "coverage.csv")]
    radars = _radar_payload(radars_path)

    _write_compact_json(historical_dir / "annual.json", {"rows": annual})
    _write_compact_json(historical_dir / "phenology.json", {"rows": phenology})
    _write_compact_json(historical_dir / "coverage.json", {"rows": coverage})
    _write_compact_json(historical_dir / "uk_boundary.geojson", _load_boundary(boundary_source))
    plot_assets = _make_plots(
        historical_dir,
        annual,
        phenology,
        coverage,
        trend_end_year=int(analysis.get("trend_end_year", 2025)),
    )

    radar_dates: dict[str, list[str]] = defaultdict(list)
    for rows in daily_by_year.values():
        for row in rows:
            radar_dates[str(row["radar"])].append(str(row["date"]))
    radar_coverage = [
        {
            "radar": radar["slug"],
            "first_date": min(radar_dates[radar["slug"]]) if radar_dates[radar["slug"]] else None,
            "last_date": max(radar_dates[radar["slug"]]) if radar_dates[radar["slug"]] else None,
        }
        for radar in radars
    ]
    generated_at = utc_now()
    manifest = {
        "schema_version": "birdcast-uk-historical-1.0",
        "data_available": True,
        "generated_at_utc": generated_at,
        "first_date": min_date,
        "latest_date": max_date,
        "years": sorted(daily_by_year),
        "default_pulse": "lp",
        "pulse_products": ["lp", "sp"],
        "solar_periods": ["night", "civil_twilight", "day"],
        "metric": {
            "id": "vid",
            "label": "Vertical integrated density passage index",
            "units": "birds km-2",
            "altitude_min_m": analysis.get("alt_min_m"),
            "altitude_max_m": analysis.get("alt_max_m"),
            "interpretation": (
                "VID integrated over altitude is a passage index. It is not an absolute bird count "
                "or a population estimate."
            ),
        },
        "source": {
            "dataset": "bioRad VPTS current_ci_le4",
            "files_seen": analysis.get("files_seen"),
            "profiles_seen": analysis.get("profiles_seen"),
            "rows_seen": analysis.get("rows_seen"),
            "failure_count": analysis.get("failure_count"),
            "finished_at_utc": analysis.get("finished_at"),
        },
        "radars": radars,
        "radar_coverage": radar_coverage,
        "assets": {
            "daily_by_year": daily_assets,
            "annual": "historical/annual.json",
            "phenology": "historical/phenology.json",
            "coverage": "historical/coverage.json",
            "boundary": "historical/uk_boundary.geojson",
            "plots": plot_assets,
        },
    }
    _write_compact_json(historical_dir / "manifest.json", manifest)
    _write_compact_json(output_root / "latest" / "historical.json", manifest)
    return {
        "ok": True,
        "generated_at_utc": generated_at,
        "first_date": min_date,
        "latest_date": max_date,
        "year_count": len(daily_by_year),
        "daily_row_count": sum(len(rows) for rows in daily_by_year.values()),
        "radar_count": len(radars),
        "plot_count": len(plot_assets),
        "output_root": str(output_root),
    }
