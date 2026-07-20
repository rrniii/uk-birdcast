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
    assert (site_root / "live-uk-bird-maps-icon.svg").is_file()
    assert (site_root / "radar-marker.svg").is_file()
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
