from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

import pytest

from birdcast_uk.joined import join_observed_to_era5
from birdcast_uk.observed import (
    _hourly_rows,
    _profiles_from_rows,
    build_hourly_observations,
    build_observed_products,
)
from birdcast_uk.vpts import (
    build_catalog_inventory,
    build_historical_inventory,
    commit_inventory_cursor,
    download_public_object,
    head_public_object,
    load_vpts_rows_from_inventory,
)

NOW = datetime(2026, 7, 17, 6, 0, tzinfo=timezone.utc)


def test_head_public_object_retries_transient_503(monkeypatch) -> None:
    attempts = 0

    class Response:
        headers = {
            "Content-Length": "123",
            "ETag": '"abc"',
            "Content-Type": "text/csv",
        }

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def open_with_transient_failure(request, *, timeout):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise HTTPError(request.full_url, 503, "Service Unavailable", {}, None)
        return Response()

    monkeypatch.setattr("birdcast_uk.vpts.urlopen", open_with_transient_failure)
    monkeypatch.setattr("birdcast_uk.vpts.time.sleep", lambda _seconds: None)

    result = head_public_object("https://example.invalid/file.csv")

    assert attempts == 2
    assert result == {
        "size": 123,
        "etag": "abc",
        "modified_time": None,
        "content_type": "text/csv",
    }


def test_catalog_inventory_is_bounded_and_cursor_driven(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, generated_at="2026-07-17T05:00:00Z")
    calls: list[str] = []

    def head(url: str):
        calls.append(url)
        if "_lp_vpts.csv" in url:
            return {
                "size": 123,
                "etag": Path(url).name,
                "modified_time": "2026-07-17T05:10:00Z",
                "content_type": "text/csv",
            }
        return None

    inventory_path = tmp_path / "inventory.json"
    cursor_path = tmp_path / "cursor.json"
    result = build_catalog_inventory(
        output=inventory_path,
        cursor_path=cursor_path,
        catalog_url=str(catalog),
        public_base_url="https://example.invalid/bucket",
        bootstrap_lookback_days=2,
        now=NOW,
        head=head,
    )

    assert result["ok"] is True
    assert result["record_count"] == 3
    assert result["records"][0]["pulse"] == "lp"
    assert result["records"][0]["selection_policy"] == "lp_preferred_sp_fallback"
    # Three exact dates, with one HEAD for each of LP and SP.
    assert len(calls) == 6

    commit_inventory_cursor(inventory_path, cursor_path)
    second = build_catalog_inventory(
        output=inventory_path,
        cursor_path=cursor_path,
        catalog_url=str(catalog),
        public_base_url="https://example.invalid/bucket",
        bootstrap_lookback_days=2,
        now=NOW,
        head=head,
    )
    assert second["ok"] is True
    assert second["no_change"] is True
    assert second["record_count"] == 0


def test_catalog_inventory_uses_sp_only_as_fallback(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, generated_at="2026-07-17T05:00:00Z")

    def head(url: str):
        if "_sp_vpts.csv" in url:
            return {"size": 10, "etag": "sp", "content_type": "text/csv"}
        return None

    result = build_catalog_inventory(
        output=tmp_path / "inventory.json",
        cursor_path=tmp_path / "cursor.json",
        catalog_url=str(catalog),
        public_base_url="https://example.invalid/bucket",
        bootstrap_lookback_days=1,
        now=NOW,
        head=head,
    )

    assert result["ok"] is True
    assert {row["pulse"] for row in result["records"]} == {"sp"}


def test_historical_inventory_keeps_lp_and_sp_separate(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, generated_at="2026-07-17T05:00:00Z")
    calls: list[str] = []

    def head(url: str):
        calls.append(url)
        return {"size": 10, "etag": Path(url).name, "content_type": "text/csv"}

    result = build_historical_inventory(
        output=tmp_path / "historical.json",
        catalog_url=str(catalog),
        public_base_url="https://example.invalid/bucket",
        days=3,
        end_date="20260713",
        max_workers=2,
        now=NOW,
        head=head,
    )

    assert result["ok"] is True
    assert result["window"]["start_date"] == "20260711"
    assert result["window"]["end_date"] == "20260713"
    assert result["record_count"] == 6
    assert {row["pulse"] for row in result["records"]} == {"lp", "sp"}
    assert all(
        row["selection_policy"] == "all_available_lp_and_sp_separate" for row in result["records"]
    )
    assert len(calls) == 6


def test_historical_inventory_rejects_latest_common_source_day(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, generated_at="2026-07-17T05:00:00Z")

    result = build_historical_inventory(
        output=tmp_path / "historical.json",
        catalog_url=str(catalog),
        public_base_url="https://example.invalid/bucket",
        days=1,
        end_date="20260714",
        max_workers=1,
        now=NOW,
        head=lambda _url: {"size": 10, "etag": "x", "content_type": "text/csv"},
    )

    assert result["ok"] is False
    assert any("requested_end_not_complete" in error for error in result["errors"])


def test_download_verifies_frozen_size_and_hash(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_bytes(b"abc")

    with pytest.raises(OSError, match="size changed"):
        download_public_object(source.as_uri(), tmp_path / "size.csv", expected_size=4)
    with pytest.raises(OSError, match="SHA-256 mismatch"):
        download_public_object(
            source.as_uri(),
            tmp_path / "hash.csv",
            expected_sha256="0" * 64,
        )


def test_catalog_inventory_fails_closed_for_stale_or_missing_target(
    tmp_path: Path,
) -> None:
    stale = _catalog(tmp_path, generated_at="2026-07-14T00:00:00Z")
    result = build_catalog_inventory(
        output=tmp_path / "stale-inventory.json",
        cursor_path=tmp_path / "cursor.json",
        catalog_url=str(stale),
        public_base_url="https://example.invalid/bucket",
        now=NOW,
        head=lambda _url: None,
    )

    assert result["ok"] is False
    assert result["record_count"] == 0
    assert any("stale_catalog" in error for error in result["errors"])
    assert any("missing_target_csv" in error for error in result["errors"])


def test_inventory_loader_overrides_unknown_radar_from_object_key(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.csv"
    source.write_text(
        "radar,datetime,height,dens,ff,gap\nUNKNOWN,2026-07-14 00:00:00,200,10,8,FALSE\n",
        encoding="utf-8",
    )
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "ok": True,
                "records": [
                    {
                        "radar": "chenies",
                        "date": "20260714",
                        "pulse": "lp",
                        "key": "ukmo-nimrod/vpts/current_ci_le4/chenies/2026/20260714_lp_vpts.csv",
                        "source_uri": "s3://bucket/key",
                        "public_url": source.as_uri(),
                        "etag": "abc",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    rows = load_vpts_rows_from_inventory(inventory)

    assert rows[0]["radar"] == "chenies"
    assert rows[0]["date"] == "20260714"
    assert rows[0]["pulse"] == "lp"
    assert rows[0]["source_etag"] == "abc"


def test_hourly_observations_are_all_hour_and_without_phenology_filter(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text(
        "radar,datetime,height,dens,ff,gap\n"
        "UNKNOWN,2026-07-14 12:05:00,200,10,8,FALSE\n"
        "UNKNOWN,2026-07-14 12:05:00,400,11,9,FALSE\n",
        encoding="utf-8",
    )
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "ok": True,
                "window": {"days": 1, "start_date": "20260714", "end_date": "20260714"},
                "pulse_policy": "all_available_lp_and_sp_separate",
                "records": [
                    {
                        "radar": "chenies",
                        "date": "20260714",
                        "pulse": "lp",
                        "key": "ukmo-nimrod/vpts/current_ci_le4/chenies/2026/20260714_lp_vpts.csv",
                        "source_uri": "s3://bucket/key",
                        "public_url": source.as_uri(),
                        "etag": "abc",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = build_hourly_observations(inventory_path=inventory, output=tmp_path / "hourly.json")
    payload = json.loads((tmp_path / "hourly.json").read_text(encoding="utf-8"))

    assert result["row_count"] == 1
    assert payload["analysis_policy"]["daylight_filter"] == "none"
    assert payload["rows"][0]["time_utc"] == "2026-07-14T12:00:00Z"
    assert "night_profile_fraction" not in payload["rows"][0]


def test_hourly_direction_uses_aligned_finite_non_rain_mtr_pairs() -> None:
    timestamp = datetime(2026, 7, 14, 12, 5, tzinfo=timezone.utc)
    common = {
        "radar": "chenies",
        "pulse": "lp",
        "timestamp": timestamp,
        "vid_birds_per_km2": 1.0,
        "mean_ground_speed_ms": 10.0,
        "night_date": "",
    }
    rows = _hourly_rows(
        [
            {
                **common,
                "mtr_birds_km_h": 10.0,
                "dominant_direction_deg": 90.0,
                "rain_suspect": False,
            },
            {
                **common,
                "mtr_birds_km_h": None,
                "dominant_direction_deg": 180.0,
                "rain_suspect": False,
            },
            {
                **common,
                "mtr_birds_km_h": 100.0,
                "dominant_direction_deg": 270.0,
                "rain_suspect": True,
            },
        ]
    )

    assert rows[0]["mean_mtr_birds_km_h"] == 10.0
    assert rows[0]["dominant_direction_deg"] == 90.0


def test_layer_mtr_and_nightly_time_integration(tmp_path: Path) -> None:
    rows = []
    for timestamp in ("2026-07-13T22:00:00Z", "2026-07-13T22:10:00Z"):
        for height, density in ((200, 10), (400, 20)):
            rows.append(
                {
                    "radar": "chenies",
                    "pulse": "lp",
                    "datetime": timestamp,
                    "height": height,
                    "dens": density,
                    "ff": 10,
                    "dd": 90,
                    "gap": "FALSE",
                    "DBZH": -10,
                    "day": "FALSE",
                    "radar_latitude": 51.689,
                    "radar_longitude": -0.53,
                }
            )
    input_path = tmp_path / "rows.json"
    input_path.write_text(json.dumps(rows), encoding="utf-8")

    result = build_observed_products(
        input_path=input_path,
        input_kind="records",
        output_dir=tmp_path / "out",
    )
    summary = json.loads(
        (tmp_path / "out" / "latest" / "latest_nightly_summary.json").read_text(encoding="utf-8")
    )
    night = summary["nights"][0]

    # Per profile: (10 + 20) birds/km3 * 10 m/s * 3.6 * 0.2 km = 216.
    assert result["profile_count"] == 2
    assert night["mean_mtr_birds_km_h"] == 216.0
    # Two profiles ten minutes apart integrate to 36 birds/km.
    assert night["migration_traffic_birds_per_km"] == 36.0
    assert night["dominant_direction_deg"] == 90.0


def test_nightly_product_never_blends_lp_and_sp(tmp_path: Path) -> None:
    rows = []
    for pulse, density in (("lp", 10), ("sp", 100)):
        for timestamp in ("2026-07-13T22:00:00Z", "2026-07-13T22:10:00Z"):
            for height in (200, 400):
                rows.append(
                    {
                        "radar": "chenies",
                        "pulse": pulse,
                        "datetime": timestamp,
                        "height": height,
                        "dens": density,
                        "ff": 10,
                        "dd": 90,
                        "gap": "FALSE",
                        "DBZH": -10,
                        "day": "FALSE",
                    }
                )
    input_path = tmp_path / "mixed-pulse.json"
    input_path.write_text(json.dumps(rows), encoding="utf-8")

    build_observed_products(
        input_path=input_path,
        input_kind="records",
        output_dir=tmp_path / "out",
    )
    summary = json.loads(
        (tmp_path / "out" / "latest" / "latest_nightly_summary.json").read_text(encoding="utf-8")
    )

    assert summary["nights"][0]["selected_pulse"] == "lp"
    assert summary["nights"][0]["pulse_products"] == ["lp"]


def test_profile_mtr_requires_speed_for_every_density_layer() -> None:
    rows = [
        {
            "radar": "chenies",
            "pulse": "lp",
            "datetime": "2026-07-13T22:00:00Z",
            "height": 200,
            "dens": 10,
            "ff": 10,
            "gap": "FALSE",
            "DBZH": -10,
        },
        {
            "radar": "chenies",
            "pulse": "lp",
            "datetime": "2026-07-13T22:00:00Z",
            "height": 400,
            "dens": 20,
            "ff": "",
            "gap": "FALSE",
            "DBZH": -10,
        },
    ]

    profile = _profiles_from_rows(rows, altitude_min_m=200, altitude_max_m=4000)[0]

    assert profile["mtr_birds_km_h"] is None
    assert profile["mtr_layer_coverage_fraction"] == 0.5


def test_rain_suspect_profiles_are_not_integrated(tmp_path: Path) -> None:
    rows = []
    for timestamp in ("2026-07-13T22:00:00Z", "2026-07-13T22:10:00Z"):
        for height in (200, 400, 600, 800, 1000):
            rows.append(
                {
                    "radar": "chenies",
                    "pulse": "lp",
                    "datetime": timestamp,
                    "height": height,
                    "dens": 10,
                    "ff": 10,
                    "gap": "FALSE",
                    "DBZH": 20,
                    "day": "FALSE",
                }
            )
    input_path = tmp_path / "rain.json"
    input_path.write_text(json.dumps(rows), encoding="utf-8")

    with pytest.raises(ValueError, match="no complete night"):
        build_observed_products(
            input_path=input_path,
            input_kind="records",
            output_dir=tmp_path / "out",
        )


def test_hourly_observed_join_to_two_era5_datasets(tmp_path: Path) -> None:
    observed = tmp_path / "observed.json"
    observed.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "radar": "chenies",
                        "time_utc": "2026-07-09T00:00:00Z",
                        "mean_mtr_birds_km_h": 123.0,
                        "profile_count": 12,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    era5_dir = tmp_path / "era5"
    era5_dir.mkdir()
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps({"data_available": True, "latest_vpts_date": "20260708"}),
        encoding="utf-8",
    )
    (era5_dir / "era5_site_features_20260709.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "radar": "chenies",
                        "time_utc": "2026-07-09T00:00:00.000000000",
                        "dataset_index": 0,
                        "source_kind": "single_levels",
                        "sp": 101000.0,
                        "msl": 101200.0,
                        "tcc": 0.5,
                        "blh": 800.0,
                        "tp": 0.0,
                    },
                    {
                        "radar": "chenies",
                        "time_utc": "2026-07-09T00:00:00.000000000",
                        "dataset_index": 1,
                        "source_kind": "pressure_levels",
                        "t_pressure_level_850.0": 280.0,
                        "r_pressure_level_850.0": 75.0,
                        "u_pressure_level_850.0": 5.0,
                        "v_pressure_level_850.0": 2.0,
                        "u_pressure_level_925.0": 4.0,
                        "v_pressure_level_925.0": 1.0,
                        "u_pressure_level_700.0": 6.0,
                        "v_pressure_level_700.0": 3.0,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = join_observed_to_era5(
        observed_hourly=observed,
        era5_dir=era5_dir,
        output=tmp_path / "joined.json",
    )
    payload = json.loads((tmp_path / "joined.json").read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["row_count"] == 1
    assert payload["rows"][0]["sp"] == 101000.0
    assert payload["rows"][0]["u_pressure_level_850.0"] == 5.0
    assert payload["rows"][0]["observed_mean_mtr_birds_km_h"] == 123.0
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["latest_era5_date"] == "20260709"
    assert status["model_feature_summary"]["row_count"] == 1


def _catalog(tmp_path: Path, *, generated_at: str) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "object_prefix": "ukmo-nimrod/vpts/current_ci_le4",
                "radar_count": 1,
                "radars": [
                    {
                        "radar": "chenies",
                        "first_date": "20131218",
                        "last_date": "20260714",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path
