from __future__ import annotations

import csv
import json
from pathlib import Path
import importlib.util

from birdcast_uk.coastal import build_coastal_relative_flow, install_coastal_static_site


def test_relative_flow_uses_within_radar_percentiles_and_no_absolute_mtr(tmp_path: Path) -> None:
    training = tmp_path / "corridor.csv"
    rows = [
        {"radar": "uk1", "source": "jasmin-uk-sp", "country": "GBR", "time_utc": "2026-07-01T00:00:00Z", "latitude": "50", "longitude": "-2", "mtr_birds_km_h": "1", "bird_u_ms": "2", "bird_v_ms": "1"},
        {"radar": "uk1", "source": "jasmin-uk-sp", "country": "GBR", "time_utc": "2026-07-01T01:00:00Z", "latitude": "50", "longitude": "-2", "mtr_birds_km_h": "9", "bird_u_ms": "3", "bird_v_ms": "2"},
        {"radar": "fr1", "source": "aloft-baltrad", "country": "FRA", "time_utc": "2026-07-01T00:00:00Z", "latitude": "50", "longitude": "1", "mtr_birds_km_h": "100", "bird_u_ms": "-1", "bird_v_ms": "2"},
        {"radar": "fr1", "source": "aloft-baltrad", "country": "FRA", "time_utc": "2026-07-01T01:00:00Z", "latitude": "50", "longitude": "1", "mtr_birds_km_h": "1000", "bird_u_ms": "-2", "bird_v_ms": "3"},
    ]
    with training.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    cohort = tmp_path / "cohort.json"
    cohort.write_text(json.dumps({"continental_radars": [{"radar": "fr1"}], "uk_radars": [{"radar": "uk1"}]}), encoding="utf-8")

    manifest = build_coastal_relative_flow(training_csv=training, cohort_json=cohort, output_root=tmp_path / "out")
    day = json.loads((tmp_path / "out" / "days" / "2026-07-01.json").read_text(encoding="utf-8"))

    assert manifest["release_status"] == "published-relative-research-product"
    assert manifest["activity_index"]["comparable_between_radars"] is False
    assert manifest["map"]["resolution_degrees"] == 0.25
    assert manifest["map"]["land_mask_applied"] is False
    assert manifest["source"]["raw_radar_products_written"] is False
    assert manifest["identical_duplicate_source_row_count"] == 0
    assert "mtr_birds_km_h" not in json.dumps(day)
    assert [row["activity_index"] for row in day["frames"][0]["radars"]] == [50.0, 50.0]
    assert [row["activity_index"] for row in day["frames"][1]["radars"]] == [100.0, 100.0]


def test_coastal_static_page_is_independently_installable(tmp_path: Path) -> None:
    result = install_coastal_static_site(tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    javascript = (tmp_path / "app.js").read_text(encoding="utf-8")

    assert result["ok"] is True
    assert "Relative activity and flow" in html
    assert "birds km" not in html
    assert "activity_index" in javascript
    assert "within-radar percentile" in html
    assert (tmp_path / "regional-boundaries.geojson").is_file()


def test_north_sea_selector_keeps_named_countries_and_western_germany(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("relative_region", Path(__file__).parents[1] / "scripts" / "prepare_relative_region.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "input.csv"
    rows = [
        {"radar": "irish", "country": "IE", "latitude": "52", "longitude": "-9", "time_utc": "2026-07-01T00:00:00Z"},
        {"radar": "west-de", "country": "DE", "latitude": "53", "longitude": "8", "time_utc": "2026-07-01T00:00:00Z"},
        {"radar": "east-de", "country": "DE", "latitude": "53", "longitude": "12", "time_utc": "2026-07-01T00:00:00Z"},
        {"radar": "norway", "country": "NO", "latitude": "60", "longitude": "5", "time_utc": "2026-07-01T00:00:00Z"},
    ]
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    manifest = module.prepare(
        input_csvs=[source], output_csv=tmp_path / "out.csv", output_manifest=tmp_path / "manifest.json",
        countries={"IE", "DE", "NO"}, western_germany_longitude_max=10.0,
    )
    assert manifest["radar_count"] == 3
    assert {item["radar"] for item in manifest["radars"]} == {"irish", "west-de", "norway"}
