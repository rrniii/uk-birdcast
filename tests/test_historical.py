from __future__ import annotations

import csv
import json
from pathlib import Path

from birdcast_uk.historical import build_historical_products


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_build_historical_products_keeps_pulses_separate_and_builds_high_res_map(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "analysis_summary.json").write_text(
        json.dumps(
            {
                "alt_min_m": 200,
                "alt_max_m": 4000,
                "files_seen": 2,
                "profiles_seen": 20,
                "rows_seen": 500,
                "failure_count": 0,
                "finished_at": "2026-07-01T00:00:00Z",
                "trend_end_year": 2025,
            }
        ),
        encoding="utf-8",
    )
    daily_fields = [
        "radar", "year", "season", "solar_period", "date", "pulse", "profile_count",
        "vid_birds_per_km2", "mean_vid_birds_per_km2_per_profile",
        "mean_weighted_height_m", "mean_ff_ms",
    ]
    _write_csv(
        source / "daily_totals.csv",
        daily_fields,
        [
            {
                "radar": "test-radar", "year": 2025, "season": "autumn",
                "solar_period": "night", "date": "2025-09-01", "pulse": "lp",
                "profile_count": 10, "vid_birds_per_km2": 12,
                "mean_vid_birds_per_km2_per_profile": 1.2,
                "mean_weighted_height_m": 800, "mean_ff_ms": 10,
            },
            {
                "radar": "test-radar", "year": 2025, "season": "autumn",
                "solar_period": "night", "date": "2025-09-01", "pulse": "sp",
                "profile_count": 10, "vid_birds_per_km2": 4,
                "mean_vid_birds_per_km2_per_profile": 0.4,
                "mean_weighted_height_m": 700, "mean_ff_ms": 9,
            },
        ],
    )
    annual_fields = [
        "day_count", "mean_daily_vid_birds_per_km2", "mean_profile_vid_birds_per_km2",
        "profile_count", "pulse", "radar_count", "season", "solar_period",
        "vid_birds_per_km2", "year",
    ]
    annual_rows = []
    for pulse, multiplier in (("lp", 1), ("sp", 0.5)):
        for season in ("spring", "autumn"):
            for period in ("day", "civil_twilight", "night"):
                annual_rows.append(
                    {
                        "day_count": 10, "mean_daily_vid_birds_per_km2": 10 * multiplier,
                        "mean_profile_vid_birds_per_km2": multiplier, "profile_count": 100,
                        "pulse": pulse, "radar_count": 1, "season": season,
                        "solar_period": period, "vid_birds_per_km2": 100 * multiplier,
                        "year": 2025,
                    }
                )
    _write_csv(source / "network_annual_seasonal_totals.csv", annual_fields, annual_rows)
    phenology_fields = [
        "day_count", "duration_days_10_90", "early_10_date", "late_90_date",
        "median_50_date", "onset_5_date", "peak_date", "peak_vid_birds_per_km2",
        "pulse", "radar", "season", "solar_period", "vid_birds_per_km2", "year",
    ]
    _write_csv(
        source / "phenology.csv",
        phenology_fields,
        [
            {
                "day_count": 30, "duration_days_10_90": 20, "early_10_date": "2025-03-01",
                "late_90_date": "2025-04-01", "median_50_date": "2025-03-15",
                "onset_5_date": "2025-02-25", "peak_date": "2025-03-16",
                "peak_vid_birds_per_km2": 5, "pulse": "lp", "radar": "test-radar",
                "season": "spring", "solar_period": "night", "vid_birds_per_km2": 100,
                "year": 2025,
            },
            {
                "day_count": 30, "duration_days_10_90": 20, "early_10_date": "2025-09-01",
                "late_90_date": "2025-11-01", "median_50_date": "2025-10-01",
                "onset_5_date": "2025-08-25", "peak_date": "2025-10-02",
                "peak_vid_birds_per_km2": 8, "pulse": "lp", "radar": "test-radar",
                "season": "autumn", "solar_period": "night", "vid_birds_per_km2": 150,
                "year": 2025,
            },
        ],
    )
    _write_csv(
        source / "coverage.csv",
        ["failed_file_count", "file_count", "first_date", "last_date", "profile_count", "pulse", "radar", "row_count", "year"],
        [
            {
                "failed_file_count": 0, "file_count": 2, "first_date": "20250101",
                "last_date": "20251231", "profile_count": 20, "pulse": "lp",
                "radar": "test-radar", "row_count": 500, "year": 2025,
            }
        ],
    )
    radars = tmp_path / "radars.json"
    radars.write_text(
        json.dumps(
            {
                "radars": [
                    {
                        "slug": "test-radar", "radar_num": "01", "label": "Test Radar",
                        "latitude": 52.0, "longitude": -1.0, "height_m": 100,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    boundary = tmp_path / "boundary.geojson"
    boundary.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature", "properties": {"ADM0_A3": "GBR"},
                        "geometry": {"type": "MultiPolygon", "coordinates": [[[[-2, 50], [-1, 50], [-1, 51], [-2, 50]]]]},
                    },
                    {
                        "type": "Feature", "properties": {"ADM0_A3": "IRL"},
                        "geometry": {"type": "MultiPolygon", "coordinates": [[[[-8, 52], [-7, 52], [-7, 53], [-8, 52]]]]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = build_historical_products(
        source,
        tmp_path / "output",
        radars_path=radars,
        boundary_source=boundary,
    )

    manifest = json.loads((tmp_path / "output/latest/historical.json").read_text(encoding="utf-8"))
    daily = json.loads((tmp_path / "output/historical/daily/2025.json").read_text(encoding="utf-8"))
    map_data = json.loads((tmp_path / "output/historical/uk_boundary.geojson").read_text(encoding="utf-8"))
    annual_plot = (tmp_path / "output/historical/plots/annual_nocturnal_lp.svg").read_text(encoding="ascii")

    assert result["daily_row_count"] == 2
    assert {row["pulse"] for row in daily["rows"]} == {"lp", "sp"}
    assert manifest["default_pulse"] == "lp"
    assert manifest["metric"]["interpretation"].startswith("VID integrated")
    assert {feature["properties"]["ADM0_A3"] for feature in map_data["features"]} == {"GBR", "IRL"}
    assert "Natural Earth 1:10m" in map_data["properties"]["resolution"]
    assert "LP only" in annual_plot
    assert "combined" not in annual_plot.lower()


def test_static_ui_is_historical_not_forecast() -> None:
    static_root = Path(__file__).parents[1] / "src" / "birdcast_uk" / "static"
    html = (static_root / "index.html").read_text(encoding="utf-8")
    javascript = (static_root / "app.js").read_text(encoding="utf-8")

    assert "96-hour forecast" not in html
    assert "UK Bird Maps" in html
    assert "Live UK Bird Maps" not in html
    assert "Historical UK weather-radar and ERA5 reanalysis" in html
    assert "uk_boundary.geojson" not in javascript
    assert "devicePixelRatio" in javascript
    assert "const longitudeFactor = Math.cos(centreLat * Math.PI / 180)" in javascript
    assert "const scale = Math.min(" in javascript
    assert 'fetchJson("regional-boundaries.geojson", null)' in javascript
    assert "function zoomMap(factor, focus)" in javascript
    assert "function beginMapDrag(event)" in javascript
    assert "function radarRadiusPixels(radar, width, height, radiusKm)" in javascript
    assert "radiusKm / 111.195" in javascript
    assert "ctx.moveTo(point.x, 0); ctx.lineTo(point.x, height)" in javascript
    assert "periodControl" not in html
    assert "row.period" not in javascript
    assert "aggregateObservedRows" in javascript
    assert "clipToUK" not in javascript
    assert 'ctx.clip("evenodd")' not in javascript
    assert "drawBoundary(ctx, rect.width, rect.height)" in javascript
    assert "ctx.fill(\"evenodd\")" not in javascript
    assert "Physical radar range; land and water" in javascript
    assert 'ctx.strokeStyle = "#050806"' in javascript
    assert "const stride = width <= 650 ? 12 : 8" in javascript
    assert 'isAvailable ? "#22ed5a" : "#f14640"' not in javascript
    assert "max_range_m" in javascript
    assert "drawRadarMarker" in javascript
    assert "const RADAR_MARKER_PATH = new Path2D" in javascript
    assert "ctx.fill(RADAR_MARKER_PATH)" in javascript
    assert "const MTR_CUTOFF_BIRDS_KM_H = 10" in javascript
    assert "crow-radar-detail.js" in html
    assert "syncCrowRadarDetail" in javascript
    assert "archiveComparisonText" in javascript
    assert "archiveComparisonIndex" in javascript
    assert "visibleModelCells" in javascript
    assert "function availableModelDates()" in javascript
    assert "async function stepHour(direction)" in javascript
    assert "window.setTimeout(advanceAnimation, 900)" in javascript
    assert "window.setInterval" not in javascript
    assert "COLOUR_SCHEMES" in javascript
    assert "colourSchemeSelect" in html
    assert 'id="arrowsLabel"' in html
    assert 'state.showArrows && state.pulse === "sp"' in javascript
    assert "LP vector transfer is not validated away from reporting radars" in javascript
