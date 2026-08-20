from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

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


def test_write_json_rejects_non_standard_nan_without_replacing_file(tmp_path: Path) -> None:
    output = tmp_path / "status.json"
    write_json(output, {"value": 1})

    with pytest.raises(ValueError, match="Out of range float values"):
        write_json(output, {"value": math.nan})

    assert json.loads(output.read_text(encoding="utf-8")) == {"value": 1}


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


def test_static_refresh_forces_forecast_off_and_preserves_bto_validation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifacts"
    (root / "latest").mkdir(parents=True)
    write_json(
        root / "latest" / "forecast.json",
        {"data_available": True, "valid_times_utc": ["2026-08-20T00:00:00Z"]},
    )
    validated = {
        "bto_data_available": True,
        "latest_bto_validation_date": "2026-08-19",
        "status": "validated",
    }
    write_json(root / "latest" / "validation_status.json", validated)

    build_static_artifacts(root, public_base_url="https://example.invalid/bucket")

    forecast = json.loads((root / "latest" / "forecast.json").read_text(encoding="utf-8"))
    assert forecast["data_available"] is False
    assert forecast["mode"] == "disabled"
    assert forecast["valid_times_utc"] == []
    assert (
        json.loads((root / "latest" / "validation_status.json").read_text(encoding="utf-8"))
        == validated
    )


def test_install_static_site_uses_same_origin_data_url(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    site_root = tmp_path / "site"
    build_static_artifacts(
        artifact_root,
        public_base_url="https://example.invalid/bucket",
    )
    site_root.mkdir()
    (site_root / "operator-note.txt").write_text("keep me\n", encoding="utf-8")

    result = install_static_site(
        artifact_root,
        site_root,
        data_base_url="/birdcast-uk/data/",
    )
    config = json.loads((site_root / "config.json").read_text(encoding="utf-8"))

    assert result["data_base_url"] == "/birdcast-uk/data"
    assert config["data_base_url"] == "/birdcast-uk/data"
    assert config["europe_manifest_url"] == "/europe-bird-maps/data/latest/relative-flow.json"
    assert config["europe_page_url"] == "/europe-bird-maps/"
    assert "coastal_manifest_url" not in config
    assert "coastal_page_url" not in config
    assert (site_root / "index.html").is_file()
    assert (site_root / "live-uk-bird-maps-logo.jpg").is_file()
    favicon = (site_root / "live-uk-bird-maps-favicon.png").read_bytes()
    assert favicon.startswith(b"\x89PNG\r\n\x1a\n")
    assert favicon[25] == 6  # RGBA, preserving transparency outside the round logo.
    html = (site_root / "index.html").read_text(encoding="utf-8")
    assert 'href="live-uk-bird-maps-favicon.png?v=3" type="image/png"' in html
    assert 'rel="icon" href="live-uk-bird-maps-logo.jpg"' not in html
    assert 'data-view="europe"' in html
    assert ">European analysis</button>" in html
    assert 'data-view="coastal"' not in html
    assert (site_root / "radar-marker.svg").is_file()
    radar_marker = (site_root / "radar-marker.svg").read_text(encoding="utf-8")
    assert 'viewBox="95 45 230 270"' in radar_marker
    assert "105.27299,306.47299" in radar_marker
    assert "currentColor" in radar_marker
    assert (site_root / "crow-radar-detail.js").is_file()
    crow_detail = (site_root / "crow-radar-detail.js").read_text(encoding="utf-8")
    assert "drawTimeAxis" in crow_detail
    assert "intervalHours === 72 ? 6 : 3" in crow_detail
    assert "formatAxisHour" in crow_detail
    assert "formatAxisDate" in crow_detail
    assert "this.data.axisFirst = first.getTime()" in crow_detail
    app = (site_root / "app.js").read_text(encoding="utf-8")
    styles = (site_root / "styles.css").read_text(encoding="utf-8")
    assert "if (!event.ctrlKey && !event.metaKey) return;" in app
    assert "touch-action: pan-y" in styles
    nginx = (Path(__file__).parents[1] / "deploy" / "nginx" / "birdcast-uk.conf").read_text(
        encoding="utf-8"
    )
    assert "location ^~ /birdcast-uk/data/" in nginx
    assert "alias /opt/birdcast-uk/data/static-artifacts/;" in nginx
    assert "this.data.axisLast = plusDays(first, days.length).getTime()" in crow_detail
    assert (site_root / "regional-boundaries.geojson").is_file()
    assert "ukmo-nimrod/vpts/current_ci_le4" in config["vpts_object_url_template"]
    assert config["archive_sources"]["jasmin-uk"]["kind"] == "vpts"
    assert config["archive_sources"]["aloft"]["coverage_url"].endswith("coverage.csv")
    assert (
        config["archive_comparison_index_url"]
        == "/birdcast-uk/data/archive/comparisons/latest.json"
    )
    regional = json.loads((site_root / "regional-boundaries.geojson").read_text(encoding="utf-8"))
    regional_codes = {feature["properties"]["ADM0_A3"] for feature in regional["features"]}
    assert {"GBR", "IRL", "JEY", "FRA", "DEU", "ESP", "SWE", "POL"} <= regional_codes
    assert (site_root / "config.json").stat().st_mode & 0o777 == 0o644
    assert (site_root / ".birdcast-uk-site.json").is_file()
    assert (site_root / "operator-note.txt").read_text(encoding="utf-8") == "keep me\n"


def test_install_static_site_rejects_incomplete_artifacts(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    (artifact_root / "web").mkdir(parents=True)

    try:
        install_static_site(artifact_root, tmp_path / "site")
    except FileNotFoundError as error:
        assert "index.html" in str(error)
    else:
        raise AssertionError("Expected incomplete static artifacts to be rejected")
