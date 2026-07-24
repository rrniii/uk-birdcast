from __future__ import annotations

import json
from pathlib import Path

from birdcast_uk.static_artifacts import (
    build_static_artifacts,
    install_static_site,
    write_json,
    write_placeholder_json,
)


def test_write_json_creates_web_readable_file(tmp_path: Path) -> None:
    output = tmp_path / "status.json"

    write_json(output, {"data_available": True})

    assert output.stat().st_mode & 0o777 == 0o644


def test_placeholder_does_not_replace_data_bearing_geojson(tmp_path: Path) -> None:
    output = tmp_path / "latest_observed.geojson"
    observed = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"radar": "chenies"}}],
        "properties": {"data_available": True},
    }
    write_json(output, observed)

    write_placeholder_json(
        output,
        {
            "type": "FeatureCollection",
            "features": [],
            "properties": {"data_available": False},
        },
    )

    assert json.loads(output.read_text(encoding="utf-8")) == observed


def test_placeholder_does_not_replace_historical_manifest(tmp_path: Path) -> None:
    output = tmp_path / "historical.json"
    historical = {"data_available": True, "latest_date": "2026-07-03"}
    write_json(output, historical)

    write_placeholder_json(output, {"data_available": False, "latest_date": None})

    assert json.loads(output.read_text(encoding="utf-8")) == historical


def test_install_static_site_uses_same_origin_data_url(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    site_root = tmp_path / "site"
    build_static_artifacts(
        artifact_root,
        public_base_url="https://example.invalid/bucket",
    )

    result = install_static_site(
        artifact_root,
        site_root,
        data_base_url="/birdcast-uk/data/",
    )
    config = json.loads((site_root / "config.json").read_text(encoding="utf-8"))

    assert result["data_base_url"] == "/birdcast-uk/data"
    assert config["data_base_url"] == "/birdcast-uk/data"
    assert (site_root / "index.html").is_file()
    assert (site_root / "live-uk-bird-maps-logo.jpg").is_file()
    assert (site_root / "live-uk-bird-maps-icon.svg").is_file()
    assert (site_root / "radar-marker.svg").is_file()
    radar_marker = (site_root / "radar-marker.svg").read_text(encoding="utf-8")
    assert 'viewBox="95 45 230 270"' in radar_marker
    assert "105.27299,306.47299" in radar_marker
    assert "currentColor" in radar_marker
    assert (site_root / "crow-radar-detail.js").is_file()
    crow_detail = (site_root / "crow-radar-detail.js").read_text(encoding="utf-8")
    assert "drawTimeAxis" in crow_detail
    assert 'intervalHours === 72 ? 6 : 3' in crow_detail
    assert 'formatAxisHour' in crow_detail
    assert 'formatAxisDate' in crow_detail
    assert 'this.data.axisFirst = first.getTime()' in crow_detail
    assert 'this.data.axisLast = plusDays(first, days.length).getTime()' in crow_detail
    assert (site_root / "regional-boundaries.geojson").is_file()
    assert "ukmo-nimrod/vpts/current_ci_le4" in config["vpts_object_url_template"]
    assert config["archive_sources"]["jasmin-uk"]["kind"] == "vpts"
    assert config["archive_sources"]["aloft"]["coverage_url"].endswith("coverage.csv")
    assert config["archive_comparison_index_url"] == "/birdcast-uk/data/archive/comparisons/latest.json"
    regional = json.loads((site_root / "regional-boundaries.geojson").read_text(encoding="utf-8"))
    regional_codes = {feature["properties"]["ADM0_A3"] for feature in regional["features"]}
    assert {"GBR", "IRL", "JEY", "FRA", "DEU", "ESP", "SWE", "POL"} <= regional_codes
    assert (site_root / "config.json").stat().st_mode & 0o777 == 0o644


def test_install_static_site_rejects_incomplete_artifacts(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    (artifact_root / "web").mkdir(parents=True)

    try:
        install_static_site(artifact_root, tmp_path / "site")
    except FileNotFoundError as error:
        assert "index.html" in str(error)
    else:
        raise AssertionError("Expected incomplete static artifacts to be rejected")
