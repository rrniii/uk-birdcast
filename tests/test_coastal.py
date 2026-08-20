from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from birdcast_uk.coastal import build_coastal_relative_flow, install_coastal_static_site


def test_relative_flow_uses_within_radar_percentiles_and_no_absolute_mtr(tmp_path: Path) -> None:
    training = tmp_path / "corridor.csv"
    rows = [
        {
            "radar": "uk1",
            "source": "jasmin-uk-sp",
            "country": "GBR",
            "time_utc": "2026-07-01T00:00:00Z",
            "latitude": "50",
            "longitude": "-2",
            "mtr_birds_km_h": "1",
            "bird_u_ms": "2",
            "bird_v_ms": "1",
        },
        {
            "radar": "uk1",
            "source": "jasmin-uk-sp",
            "country": "GBR",
            "time_utc": "2026-07-01T01:00:00Z",
            "latitude": "50",
            "longitude": "-2",
            "mtr_birds_km_h": "9",
            "bird_u_ms": "3",
            "bird_v_ms": "2",
        },
        {
            "radar": "fr1",
            "source": "aloft-baltrad",
            "country": "FRA",
            "time_utc": "2026-07-01T00:00:00Z",
            "latitude": "50",
            "longitude": "1",
            "mtr_birds_km_h": "100",
            "bird_u_ms": "-1",
            "bird_v_ms": "2",
        },
        {
            "radar": "fr1",
            "source": "aloft-baltrad",
            "country": "FRA",
            "time_utc": "2026-07-01T01:00:00Z",
            "latitude": "50",
            "longitude": "1",
            "mtr_birds_km_h": "1000",
            "bird_u_ms": "-2",
            "bird_v_ms": "3",
        },
    ]
    with training.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    cohort = tmp_path / "cohort.json"
    cohort.write_text(
        json.dumps({"continental_radars": [{"radar": "fr1"}], "uk_radars": [{"radar": "uk1"}]}),
        encoding="utf-8",
    )

    manifest = build_coastal_relative_flow(
        training_csv=training, cohort_json=cohort, output_root=tmp_path / "out"
    )
    day = json.loads(
        (
            tmp_path / "out" / manifest["assets"]["integrity"]["daily"]["2026-07-01"]["path"]
        ).read_text(encoding="utf-8")
    )

    assert manifest["release_status"] == "published-relative-research-product"
    assert manifest["activity_index"]["comparable_between_radars"] is False
    assert manifest["map"]["resolution_degrees"] == 0.25
    assert manifest["map"]["land_mask_applied"] is False
    assert manifest["source"]["raw_radar_products_written"] is False
    assert manifest["identical_duplicate_source_row_count"] == 0
    assert manifest["available_dates"] == ["2026-07-01"]
    assert manifest["release_id"].startswith("relative-")
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
    assert "if (!event.ctrlKey && !event.metaKey) return;" in javascript
    styles = (tmp_path / "styles.css").read_text(encoding="utf-8")
    assert "touch-action:pan-y" in styles
    assert "height:clamp(500px,68vh,820px)" in styles
    assert (tmp_path / "regional-boundaries.geojson").is_file()


def test_relative_release_activation_is_atomic_and_revalidates_reuse(tmp_path: Path) -> None:
    training = tmp_path / "training.csv"
    training.write_text(
        "radar,source,country,time_utc,latitude,longitude,mtr_birds_km_h,bird_u_ms,bird_v_ms\n"
        "uk1,jasmin-uk-sp,GBR,2026-07-01T00:00:00Z,50,-2,1,2,1\n",
        encoding="utf-8",
    )
    cohort = tmp_path / "cohort.json"
    cohort.write_text(json.dumps({"radars": [{"radar": "uk1"}]}), encoding="utf-8")
    source = tmp_path / "source"
    manifest = build_coastal_relative_flow(
        training_csv=training,
        cohort_json=cohort,
        output_root=source,
    )
    script = Path(__file__).parents[1] / "deploy/scripts/birdcast-coastal-activate.sh"
    stage = tmp_path / "releases"
    current = tmp_path / "current"

    release_source = source / "archive" / "relative-flow" / manifest["release_id"]
    secret = release_source / "private-model.csv"
    secret.write_text("must not publish\n", encoding="utf-8")
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run([script, source, stage, current], check=True)
    assert not current.exists()
    secret.unlink()

    archive_manifest = release_source / "manifest.json"
    archive_manifest_bytes = archive_manifest.read_bytes()
    archive_manifest.unlink()
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run([script, source, stage, current], check=True)
    archive_manifest.write_bytes(archive_manifest_bytes)

    subprocess.run([script, source, stage, current], check=True)
    assert current.is_symlink()
    assert current.resolve().name == manifest["release_id"]

    daily = current.resolve() / manifest["assets"]["integrity"]["daily"]["2026-07-01"]["path"]
    daily.write_text("corrupt\n", encoding="utf-8")
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run([script, source, stage, current], check=True)


def test_north_sea_selector_keeps_named_countries_and_western_germany(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "relative_region", Path(__file__).parents[1] / "scripts" / "prepare_relative_region.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "input.csv"
    rows = [
        {
            "radar": "irish",
            "country": "IE",
            "latitude": "52",
            "longitude": "-9",
            "time_utc": "2026-07-01T00:00:00Z",
        },
        {
            "radar": "west-de",
            "country": "DE",
            "latitude": "53",
            "longitude": "8",
            "time_utc": "2026-07-01T00:00:00Z",
        },
        {
            "radar": "east-de",
            "country": "DE",
            "latitude": "53",
            "longitude": "12",
            "time_utc": "2026-07-01T00:00:00Z",
        },
        {
            "radar": "norway",
            "country": "NO",
            "latitude": "60",
            "longitude": "5",
            "time_utc": "2026-07-01T00:00:00Z",
        },
    ]
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    manifest = module.prepare(
        input_csvs=[source],
        output_csv=tmp_path / "out.csv",
        output_manifest=tmp_path / "manifest.json",
        countries={"IE", "DE", "NO"},
        western_germany_longitude_max=10.0,
    )
    assert manifest["radar_count"] == 3
    assert {item["radar"] for item in manifest["radars"]} == {"irish", "west-de", "norway"}

    all_europe = module.prepare(
        input_csvs=[source],
        output_csv=tmp_path / "all-europe.csv",
        output_manifest=tmp_path / "all-europe.json",
        countries=None,
        western_germany_longitude_max=10.0,
    )
    assert all_europe["countries"] == "all_available_europe_sources"
    assert {item["radar"] for item in all_europe["radars"]} == {
        "irish",
        "west-de",
        "east-de",
        "norway",
    }
