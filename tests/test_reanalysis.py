from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from birdcast_uk.reanalysis import (
    ERA5_FEATURES,
    build_prediction_frames,
    compare_models,
    prepare_training_table,
    publish_reanalysis,
    publish_wide_reanalysis,
    write_model_spec,
)


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
from birdcast_uk.era5 import _point_in_boundary, _project_grid_point, _support_score
from birdcast_uk.radars import BirdcastRadar


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
                    "sp": 101300.0,
                    "msl": 101500.0,
                    "tcc": 0.5,
                    "blh": 800.0,
                    "tp_hourly": 0.0,
                }
            )
    return rows


def test_prepare_table_is_pulse_separated_and_has_no_time_predictor(tmp_path: Path) -> None:
    joined = tmp_path / "joined.json"
    joined.write_text(json.dumps({"rows": _joined_rows()}), encoding="utf-8")

    result = prepare_training_table(joined_features=joined, output=tmp_path / "table.json", window_days=365)
    table = json.loads((tmp_path / "table.json").read_text(encoding="utf-8"))
    spec = write_model_spec(tmp_path / "gamm.json", table=tmp_path / "table.json", model_family="gamm")

    assert result["row_count"] == 48
    assert table["pulse_counts"] == {"lp": 24, "sp": 24}
    assert table["model_time_terms"] == "none"
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
                "gamm_options": {"temporal_smooths": ["day_of_year", "utc_hour"], "spatial_k": 10},
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
    assert spec["time_predictors"] == ["day_of_year", "utc_hour"]


def test_prepare_table_accepts_decimal_pressure_level_keys(tmp_path: Path) -> None:
    rows = _joined_rows()
    replacements = {
        "t_pressure_level_850": "t_pressure_level_850.0",
        "r_pressure_level_850": "r_pressure_level_850.0",
        "u_pressure_level_850": "u_pressure_level_850.0",
        "v_pressure_level_850": "v_pressure_level_850.0",
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
            rows.append({"pulse": pulse, "target": target, "rmse": rmse, "top_decile_precision": precision, "top_decile_recall": recall})
        for target in ("bird_u_ms", "bird_v_ms"):
            rows.append({"pulse": pulse, "target": target, "rmse": rmse})
    return {"metrics": rows}


def test_model_comparison_requires_all_pulses_targets_and_vectors(tmp_path: Path) -> None:
    gamm = tmp_path / "gamm.json"
    xgb = tmp_path / "xgb.json"
    gamm.write_text(json.dumps(_metrics(10.0)), encoding="utf-8")
    xgb.write_text(json.dumps(_metrics(8.5, 0.85, 0.85)), encoding="utf-8")

    result = compare_models(gamm_metrics=gamm, xgboost_metrics=xgb, output=tmp_path / "selection.json")

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

    result = compare_models(gamm_metrics=gamm, xgboost_metrics=xgb, output=tmp_path / "selection.json")

    assert result["temporal_validation_required"] is True
    assert result["selected_model_family"] == "gamm"


def test_publish_writes_immutable_daily_assets_before_latest_manifest(tmp_path: Path) -> None:
    comparison = tmp_path / "comparison.json"
    comparison.write_text(json.dumps({"selected_model_family": "gamm"}), encoding="utf-8")
    predictions = tmp_path / "predictions.json"
    predictions.write_text(
        json.dumps(
            {
                "run_id": "20250701T0000Z",
                "grid": {"longitude_step": 0.25, "latitude_step": 0.25},
                "frames": [
                    {
                        "model_family": "gamm", "pulse": "lp", "time_utc": "2025-07-01T00:00:00Z",
                        "cells": [{"longitude": -0.5, "latitude": 51.5, "mtr_birds_km_h": 2.0, "vid_birds_per_km2": 1.0, "bird_u_ms": 1.0, "bird_v_ms": 0.0, "support": 0.9}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    latest = publish_reanalysis(predictions=predictions, comparison=comparison, output_root=tmp_path / "artifacts")

    assert latest["assets"]["lp"]["2025-07-01"].endswith("daily/lp/20250701.json")
    assert latest["assets"]["boundary"] == "assets/uk-boundary.geojson"
    assert (tmp_path / "artifacts" / latest["assets"]["lp"]["2025-07-01"]).is_file()
    assert (tmp_path / "artifacts" / "latest" / "gam-era5.json").is_file()


def test_publish_wide_streams_complete_daily_pulse_assets(tmp_path: Path) -> None:
    comparison = tmp_path / "comparison.json"
    comparison.write_text(json.dumps({"selected_model_family": "gamm"}), encoding="utf-8")
    header = (
        "time_utc,longitude,latitude,support,mtr_birds_km_h,"
        "vid_birds_per_km2,bird_u_ms,bird_v_ms,"
        "uncertainty_mtr_birds_km_h,uncertainty_vid_birds_per_km2,"
        "uncertainty_bird_u_ms,uncertainty_bird_v_ms\n"
    )
    rows = "".join(
        f"2025-07-01T{hour:02d}:00:00Z,-0.5,51.5,0.8,2.0,1.0,1.0,0.5,0.2,0.1,0.1,0.1\n"
        for hour in range(24)
    )
    lp_csv = tmp_path / "lp.csv"
    sp_csv = tmp_path / "sp.csv"
    lp_csv.write_text(header + rows, encoding="utf-8")
    sp_csv.write_text(header + rows, encoding="utf-8")

    latest = publish_wide_reanalysis(
        lp_csv=lp_csv,
        sp_csv=sp_csv,
        comparison=comparison,
        output_root=tmp_path / "artifacts",
        model_family="gamm",
    )

    assert latest["data_available"] is True
    assert latest["grid"]["cell_count"] == 1
    assert latest["first_time_utc"] == "2025-07-01T00:00:00Z"
    assert latest["latest_time_utc"] == "2025-07-01T23:00:00Z"
    lp_asset = tmp_path / "artifacts" / latest["assets"]["lp"]["2025-07-01"]
    sp_asset = tmp_path / "artifacts" / latest["assets"]["sp"]["2025-07-01"]
    assert len(json.loads(lp_asset.read_text(encoding="utf-8"))["frames"]) == 24
    assert len(json.loads(sp_asset.read_text(encoding="utf-8"))["frames"]) == 24


def test_frames_require_support_and_merge_all_model_targets(tmp_path: Path) -> None:
    source = tmp_path / "predictions.csv"
    source.write_text(
        "time_utc,longitude,latitude,support,pulse,target,value,uncertainty\n"
        "2025-07-01T00:00:00Z,-0.5,51.5,0.8,lp,mtr_birds_km_h,2.0,0.2\n"
        "2025-07-01T00:00:00Z,-0.5,51.5,0.8,lp,vid_birds_per_km2,1.0,0.1\n"
        "2025-07-01T00:00:00Z,-0.5,51.5,0.8,lp,bird_u_ms,1.0,0.1\n"
        "2025-07-01T00:00:00Z,-0.5,51.5,0.8,lp,bird_v_ms,0.5,0.1\n",
        encoding="utf-8",
    )

    result = build_prediction_frames(predictions_csv=source, output=tmp_path / "frames.json", model_family="gamm")
    payload = json.loads((tmp_path / "frames.json").read_text(encoding="utf-8"))

    assert result["frame_count"] == 1
    assert payload["frames"][0]["cells"][0]["support"] == 0.8


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
