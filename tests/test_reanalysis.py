from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import birdcast_uk.selected_model as selected_model
from birdcast_uk.era5 import _point_in_boundary, _project_grid_point, _support_score
from birdcast_uk.radars import BirdcastRadar
from birdcast_uk.reanalysis import (
    ERA5_FEATURES,
    OPTIONAL_ERA5_FEATURES,
    _normalise_row,
    compare_models,
    prepare_training_table,
    publish_wide_reanalysis,
    write_model_spec,
)
from birdcast_uk.selected_model import COMPONENT_SHA256, SELECTION_ID, qualified_dates


def test_projection_transformer_is_reused() -> None:
    from birdcast_uk import reanalysis

    reanalysis._projection_transformer.cache_clear()
    reanalysis._project(-1.5, 52.0)
    reanalysis._project(-2.0, 53.0)

    assert reanalysis._projection_transformer.cache_info().misses == 1
    assert reanalysis._projection_transformer.cache_info().hits == 1


def test_grid_projection_transformer_is_reused() -> None:
    from birdcast_uk import era5

    era5._grid_projection_transformer.cache_clear()
    era5._project_grid_point(-1.5, 52.0)
    era5._project_grid_point(-2.0, 53.0)

    assert era5._grid_projection_transformer.cache_info().misses == 1
    assert era5._grid_projection_transformer.cache_info().hits == 1


def _joined_rows() -> list[dict[str, object]]:
    start = datetime(2025, 7, 1, tzinfo=timezone.utc)
    rows = []
    for hour in range(24):
        for pulse in ("lp", "sp"):
            rows.append(
                {
                    "radar": "chenies",
                    "time_utc": (start + timedelta(hours=hour)).isoformat().replace("+00:00", "Z"),
                    "latitude": 51.6894,
                    "longitude": -0.5303,
                    "observed_pulse": pulse,
                    "observed_usable_mtr_profile_count": 4,
                    "observed_rain_suspect_fraction": 0.0,
                    "observed_mean_mtr_birds_km_h": 20.0,
                    "observed_mean_vid_birds_per_km2": 5.0,
                    "observed_mean_ground_speed_ms": 10.0,
                    "observed_dominant_direction_deg": 90.0,
                    "t_pressure_level_850": 280.0,
                    "r_pressure_level_850": 75.0,
                    "u_pressure_level_850": 4.0,
                    "v_pressure_level_850": 2.0,
                    "u_pressure_level_925": 3.0,
                    "v_pressure_level_925": 1.0,
                    "u_pressure_level_700": 6.0,
                    "v_pressure_level_700": 3.0,
                    "sp": 101300.0,
                    "msl": 101500.0,
                    "tcc": 0.5,
                    "blh": 800.0,
                    "tp_hourly": 0.0,
                }
            )
    return rows


def _write_boundary(output_root: Path) -> None:
    boundary = output_root / "assets" / "uk-boundary.geojson"
    boundary.parent.mkdir(parents=True)
    boundary.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "test"},
                        "geometry": {"type": "Point", "coordinates": [-1.0, 52.0]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_component_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    path = tmp_path / "components.json"
    payload = {
        "schema_version": "uk-gamm-component-selection-v1",
        "selection_id": SELECTION_ID,
        "components": {
            pulse: {
                target: {
                    "model_rds": f"/private/{pulse}-{target}.rds",
                    "sha256": COMPONENT_SHA256[pulse][target],
                    "prediction_transform": "identity",
                    "uncertainty_scale": "model_linear_predictor_standard_error",
                }
                for target in COMPONENT_SHA256[pulse]
            }
            for pulse in COMPONENT_SHA256
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        selected_model,
        "COMPONENT_MANIFEST_SHA256",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    return path


def test_training_row_preserves_zero_primary_coordinates() -> None:
    row = _joined_rows()[0]
    row.update(
        {
            "latitude": 0.0,
            "longitude": 0.0,
            "observed_latitude": 51.5,
            "observed_longitude": -1.5,
        }
    )

    normalised = _normalise_row(row, min_profiles=3)

    assert normalised is not None
    assert normalised["latitude"] == 0.0
    assert normalised["longitude"] == 0.0


def test_prepare_table_defers_derived_time_terms_to_model_spec(tmp_path: Path) -> None:
    joined = tmp_path / "joined.json"
    joined.write_text(json.dumps({"rows": _joined_rows()}), encoding="utf-8")

    result = prepare_training_table(
        joined_features=joined, output=tmp_path / "table.json", window_days=365
    )
    table = json.loads((tmp_path / "table.json").read_text(encoding="utf-8"))
    spec = write_model_spec(
        tmp_path / "gamm.json", table=tmp_path / "table.json", model_family="gamm"
    )

    assert result["row_count"] == 48
    assert table["pulse_counts"] == {"lp": 24, "sp": 24}
    assert table["model_time_terms"] == "configured_in_model_spec"
    assert table["available_derived_time_terms"] == ["day_of_year", "utc_hour"]
    assert "timestamp" not in spec["predictors"]
    assert "u_850_ms" in table["feature_columns"]
    assert table["feature_ranges"]["u_850_ms"] == {"lower": 4.0, "upper": 4.0}
    assert "rows" not in table


def test_model_spec_embeds_selected_gamm_options(tmp_path: Path) -> None:
    joined = tmp_path / "joined.json"
    joined.write_text(json.dumps({"rows": _joined_rows()}), encoding="utf-8")
    prepare_training_table(joined_features=joined, output=tmp_path / "table.json", window_days=365)
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "selection_id": "test-selection",
                "gamm_options": {
                    "temporal_smooths": ["day_of_year", "utc_hour"],
                    "spatial_k": 10,
                    "day_of_year_k": 16,
                    "utc_hour_k": 10,
                },
            }
        ),
        encoding="utf-8",
    )

    spec = write_model_spec(
        tmp_path / "gamm.json",
        table=tmp_path / "table.json",
        model_family="gamm",
        gamm_options_path=selection,
    )

    assert spec["gamm_selection_id"] == "test-selection"
    assert spec["gamm_options"]["spatial_k"] == 10
    assert spec["gamm_options"]["day_of_year_k"] == 16
    assert spec["gamm_options"]["utc_hour_k"] == 10
    assert spec["time_predictors"] == ["day_of_year", "utc_hour"]


def test_prepare_table_accepts_decimal_pressure_level_keys(tmp_path: Path) -> None:
    rows = _joined_rows()
    replacements = {
        "t_pressure_level_850": "t_pressure_level_850.0",
        "r_pressure_level_850": "r_pressure_level_850.0",
        "u_pressure_level_850": "u_pressure_level_850.0",
        "v_pressure_level_850": "v_pressure_level_850.0",
        "u_pressure_level_925": "u_pressure_level_925.0",
        "v_pressure_level_925": "v_pressure_level_925.0",
        "u_pressure_level_700": "u_pressure_level_700.0",
        "v_pressure_level_700": "v_pressure_level_700.0",
    }
    for row in rows:
        for old, new in replacements.items():
            row[new] = row.pop(old)
    joined = tmp_path / "joined.json"
    joined.write_text(json.dumps({"rows": rows}), encoding="utf-8")

    prepare_training_table(joined_features=joined, output=tmp_path / "table.json")
    table = json.loads((tmp_path / "table.json").read_text(encoding="utf-8"))

    assert table["feature_columns"] == list(ERA5_FEATURES)
    assert table["row_count"] == 48


def test_prepare_table_requires_opt_in_vertical_wind_coverage(tmp_path: Path) -> None:
    rows = _joined_rows()
    for row in rows:
        row["u_pressure_level_925.0"] = row.pop("u_pressure_level_925")
        row["v_pressure_level_925.0"] = row.pop("v_pressure_level_925")
        row["u_pressure_level_700.0"] = row.pop("u_pressure_level_700")
        row["v_pressure_level_700.0"] = row.pop("v_pressure_level_700")
    joined = tmp_path / "joined.json"
    joined.write_text(json.dumps({"rows": rows}), encoding="utf-8")

    prepare_training_table(
        joined_features=joined,
        output=tmp_path / "table.json",
        extra_era5_features=OPTIONAL_ERA5_FEATURES,
    )
    table = json.loads((tmp_path / "table.json").read_text(encoding="utf-8"))

    assert table["feature_columns"] == [*ERA5_FEATURES, *OPTIONAL_ERA5_FEATURES]
    assert table["row_count"] == 48


def test_prepare_table_rejects_incomplete_era5_predictors(tmp_path: Path) -> None:
    rows = _joined_rows()
    for row in rows:
        row.pop("u_pressure_level_850")
    joined = tmp_path / "joined.json"
    joined.write_text(json.dumps({"rows": rows}), encoding="utf-8")

    try:
        prepare_training_table(joined_features=joined, output=tmp_path / "table.json")
    except ValueError as exc:
        assert "complete declared ERA5 predictor set" in str(exc)
    else:
        raise AssertionError("incomplete ERA5 predictors must fail preparation")


def _metrics(rmse: float, precision: float = 0.8, recall: float = 0.8) -> dict[str, object]:
    rows = []
    for pulse in ("lp", "sp"):
        for target in ("mtr_birds_km_h", "vid_birds_per_km2"):
            rows.append(
                {
                    "pulse": pulse,
                    "target": target,
                    "rmse": rmse,
                    "top_decile_precision": precision,
                    "top_decile_recall": recall,
                }
            )
        for target in ("bird_u_ms", "bird_v_ms"):
            rows.append({"pulse": pulse, "target": target, "rmse": rmse})
    return {"metrics": rows}


def test_model_comparison_requires_all_pulses_targets_and_vectors(tmp_path: Path) -> None:
    gamm = tmp_path / "gamm.json"
    xgb = tmp_path / "xgb.json"
    gamm.write_text(json.dumps(_metrics(10.0)), encoding="utf-8")
    xgb.write_text(json.dumps(_metrics(8.5, 0.85, 0.85)), encoding="utf-8")

    result = compare_models(
        gamm_metrics=gamm, xgboost_metrics=xgb, output=tmp_path / "selection.json"
    )

    assert result["selected_model_family"] == "xgboost"


def test_model_comparison_keeps_gamm_when_blocked_time_is_worse(tmp_path: Path) -> None:
    gamm = tmp_path / "gamm.json"
    xgb = tmp_path / "xgb.json"
    spatial_gamm = _metrics(10.0)["metrics"]
    spatial_xgb = _metrics(8.5, 0.85, 0.85)["metrics"]
    temporal_gamm = _metrics(10.0)["metrics"]
    temporal_xgb = _metrics(11.0, 0.9, 0.9)["metrics"]
    for row in spatial_gamm + spatial_xgb:
        row["validation"] = "leave_one_radar_out"
    for row in temporal_gamm + temporal_xgb:
        row["validation"] = "blocked_time"
    gamm.write_text(json.dumps({"metrics": spatial_gamm + temporal_gamm}), encoding="utf-8")
    xgb.write_text(json.dumps({"metrics": spatial_xgb + temporal_xgb}), encoding="utf-8")

    result = compare_models(
        gamm_metrics=gamm, xgboost_metrics=xgb, output=tmp_path / "selection.json"
    )

    assert result["temporal_validation_required"] is True
    assert result["selected_model_family"] == "gamm"


def test_publish_wide_streams_complete_daily_pulse_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparison = tmp_path / "comparison.json"
    comparison.write_text(
        json.dumps(
            {
                "selected_model_family": "gamm",
                "selection_id": SELECTION_ID,
                "temporal_terms": ["day_of_year", "utc_hour", "seasonal_diurnal"],
            }
        ),
        encoding="utf-8",
    )
    header = (
        "time_utc,longitude,latitude,support,mtr_birds_km_h,"
        "vid_birds_per_km2,bird_u_ms,bird_v_ms,"
        "uncertainty_mtr_birds_km_h,uncertainty_vid_birds_per_km2,"
        "uncertainty_bird_u_ms,uncertainty_bird_v_ms\n"
    )
    rows = "".join(
        f"{day}T{hour:02d}:00:00Z,-0.5,51.5,0.8,2.0,1.0,1.0,0.5,0.2,0.1,0.1,0.1\n"
        for day in qualified_dates()
        for hour in range(24)
    )
    lp_csv = tmp_path / "lp.csv"
    sp_csv = tmp_path / "sp.csv"
    lp_csv.write_text(header + rows, encoding="utf-8")
    sp_csv.write_text(header + rows, encoding="utf-8")
    component_manifest = _write_component_authority(tmp_path, monkeypatch)

    output_root = tmp_path / "artifacts"
    _write_boundary(output_root)
    latest = publish_wide_reanalysis(
        lp_csv=lp_csv,
        sp_csv=sp_csv,
        comparison=comparison,
        output_root=output_root,
        model_family="gamm",
        component_manifest=component_manifest,
    )

    assert latest["data_available"] is True
    assert latest["grid"]["cell_count"] == 1
    assert latest["first_time_utc"] == "2025-07-14T00:00:00Z"
    assert latest["latest_time_utc"] == "2026-07-13T23:00:00Z"
    lp_asset = tmp_path / "artifacts" / latest["assets"]["lp"]["2025-07-14"]
    sp_asset = tmp_path / "artifacts" / latest["assets"]["sp"]["2025-07-14"]
    assert len(json.loads(lp_asset.read_text(encoding="utf-8"))["frames"]) == 24
    assert len(json.loads(sp_asset.read_text(encoding="utf-8"))["frames"]) == 24
    assert latest["selection_id"] == SELECTION_ID
    assert latest["temporal_terms"] == ["day_of_year", "utc_hour", "seasonal_diurnal"]
    assert "cyclic day-of-year" in latest["interpretation"]
    assert (
        latest["component_provenance"]["components"]["sp"]["bird_u_ms"]["sha256"]
        == COMPONENT_SHA256["sp"]["bird_u_ms"]
    )
    source = json.loads((tmp_path / "artifacts" / latest["source"]).read_text(encoding="utf-8"))
    assert "/private/" not in json.dumps(source)


def test_publish_wide_rejects_lp_sp_date_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparison = tmp_path / "comparison.json"
    comparison.write_text(
        json.dumps({"selected_model_family": "gamm", "selection_id": SELECTION_ID}),
        encoding="utf-8",
    )
    header = (
        "time_utc,longitude,latitude,support,mtr_birds_km_h,vid_birds_per_km2,bird_u_ms,bird_v_ms\n"
    )
    lp_rows = "".join(
        f"2025-07-01T{hour:02d}:00:00Z,-0.5,51.5,0.8,2,1,1,.5\n" for hour in range(24)
    )
    sp_rows = lp_rows.replace("2025-07-01", "2025-07-02")
    lp_csv = tmp_path / "lp.csv"
    sp_csv = tmp_path / "sp.csv"
    lp_csv.write_text(header + lp_rows, encoding="utf-8")
    sp_csv.write_text(header + sp_rows, encoding="utf-8")
    component_manifest = _write_component_authority(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="prediction dates differ"):
        publish_wide_reanalysis(
            lp_csv=lp_csv,
            sp_csv=sp_csv,
            comparison=comparison,
            output_root=tmp_path / "artifacts",
            model_family="gamm",
            component_manifest=component_manifest,
        )


def test_publish_wide_uses_null_for_unavailable_uncertainty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparison = tmp_path / "comparison.json"
    comparison.write_text(
        json.dumps({"selected_model_family": "gamm", "selection_id": SELECTION_ID}),
        encoding="utf-8",
    )
    header = (
        "time_utc,longitude,latitude,support,mtr_birds_km_h,vid_birds_per_km2,bird_u_ms,bird_v_ms\n"
    )
    rows = "".join(
        f"{day}T{hour:02d}:00:00Z,-0.5,51.5,0.8,2,1,1,.5\n"
        for day in qualified_dates()
        for hour in range(24)
    )
    lp_csv = tmp_path / "lp.csv"
    sp_csv = tmp_path / "sp.csv"
    lp_csv.write_text(header + rows, encoding="utf-8")
    sp_csv.write_text(header + rows, encoding="utf-8")
    component_manifest = _write_component_authority(tmp_path, monkeypatch)

    output_root = tmp_path / "artifacts"
    _write_boundary(output_root)
    latest = publish_wide_reanalysis(
        lp_csv=lp_csv,
        sp_csv=sp_csv,
        comparison=comparison,
        output_root=output_root,
        model_family="gamm",
        component_manifest=component_manifest,
    )
    daily = json.loads(
        (tmp_path / "artifacts" / latest["assets"]["lp"]["2025-07-14"]).read_text(encoding="utf-8")
    )

    uncertainty = daily["frames"][0]["cells"][0]["uncertainty_by_target"]
    assert set(uncertainty) == {
        "mtr_birds_km_h",
        "vid_birds_per_km2",
        "bird_u_ms",
        "bird_v_ms",
    }
    assert set(uncertainty.values()) == {None}


def test_grid_support_penalises_distance_and_out_of_range_weather() -> None:
    radars = [
        BirdcastRadar(
            "chenies",
            "05",
            "Chenies",
            latitude=51.6894,
            longitude=-0.5303,
            max_range_m=255_000.0,
        )
    ]
    ranges = {"temperature_850_k": (275.0, 285.0)}
    nearby = _support_score(51.7, -0.5, {"temperature_850_k": 280.0}, radars, ranges)
    distant = _support_score(60.5, -10.0, {"temperature_850_k": 280.0}, radars, ranges)
    novel = _support_score(51.7, -0.5, {"temperature_850_k": 310.0}, radars, ranges)

    assert nearby > distant
    assert nearby > novel


def test_grid_boundary_mask_keeps_land_and_excludes_holes() -> None:
    polygons = [
        [
            [(-2.0, 50.0), (2.0, 50.0), (2.0, 54.0), (-2.0, 54.0), (-2.0, 50.0)],
            [(-0.5, 51.0), (0.5, 51.0), (0.5, 52.0), (-0.5, 52.0), (-0.5, 51.0)],
        ]
    ]

    assert _point_in_boundary(-1.0, 52.0, polygons) is True
    assert _point_in_boundary(0.0, 51.5, polygons) is False
    assert _point_in_boundary(3.0, 52.0, polygons) is False


def test_grid_projection_emits_finite_model_coordinates() -> None:
    easting, northing = _project_grid_point(-0.5, 51.5)

    assert 3_500_000 < easting < 4_500_000
    assert 2_500_000 < northing < 4_000_000
