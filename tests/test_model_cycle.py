from __future__ import annotations

import argparse
import copy
import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from test_publication import _write_selected_model_release

from birdcast_uk import model_cycle as cycle
from birdcast_uk.publication import build_model_extension_plan, validate_publication_plan
from birdcast_uk.reanalysis import _stream_wide_daily_assets
from birdcast_uk.selected_model import SELECTED_GRID_FIELDS, TARGETS
from birdcast_uk.static_artifacts import write_json

DAY = date(2026, 7, 14)
POINTS = {(-1.0, 52.0), (-1.25, 52.25)}


def predictions(root: Path, day=DAY) -> Path:
    root.mkdir(parents=True)
    fields = [
        "time_utc",
        "longitude",
        "latitude",
        "support",
        *TARGETS,
        *(f"uncertainty_{target}" for target in TARGETS),
    ]
    for pulse in ("lp", "sp"):
        with (root / f"predictions_wide_{pulse}.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for hour in range(24):
                for lon, lat in sorted(POINTS):
                    row = dict.fromkeys(fields, 1)
                    row.update(time_utc=f"{day}T{hour:02d}:00:00Z", longitude=lon, latitude=lat)
                    writer.writerow(row)
    return root


def staged(tmp_path):
    baseline = _write_selected_model_release(tmp_path / "baseline")
    previous = json.loads((baseline / "latest/gam-era5.json").read_text())
    source = predictions(tmp_path / "predictions")
    _, grid, _, _, _ = _stream_wide_daily_assets(
        source / "predictions_wide_lp.csv",
        tmp_path / "sample",
        pulse="lp",
        model_family="gamm",
    )
    previous["grid"] = grid
    root = tmp_path / "extension"
    current = cycle.stage_extension(
        previous=previous,
        day=DAY,
        predictions=source,
        output_root=root,
        coordinates=POINTS,
        provenance={},
    )
    return root, previous, current


def test_extension_preserves_evidence_and_plans_only_four_objects(tmp_path):
    root, previous, current = staged(tmp_path)
    for key in ("source", "comparison", "component_provenance", "grid", "first_time_utc"):
        assert current[key] == previous[key]
    for pulse in ("lp", "sp"):
        assert len(current["assets"][pulse]) == 366
        assert all(
            current["assets"][pulse][day] == asset
            for day, asset in previous["assets"][pulse].items()
        )
    plan = build_model_extension_plan(root, root / "plan.json")
    assert plan["object_count"] == 4
    assert all(
        "forecast" not in obj["key"] and "historical" not in obj["key"] for obj in plan["objects"]
    )
    assert validate_publication_plan(root / "plan.json") == plan


@pytest.mark.parametrize("change", ["gap", "old_asset", "model", "evidence", "external", "future"])
def test_extension_rejects_contract_regressions(tmp_path, change):
    root, _, current = staged(tmp_path)
    if change == "gap":
        del current["assets"]["lp"]["2026-07-13"]
    elif change == "old_asset":
        current["assets"]["lp"]["2026-07-13"] = current["assets"]["lp"]["2026-07-14"]
    elif change == "model":
        current["component_provenance"]["component_manifest_sha256"] = "0" * 64
    elif change == "evidence":
        current["rolling_update"]["evidence_window"][1] = "2026-07-14"
    elif change == "external":
        current["rolling_update"]["source"] = "https://example.com/private"
    else:
        current["latest_time_utc"] = "2999-01-01T23:00:00Z"
    write_json(root / "latest/gam-era5.json", current)
    with pytest.raises(ValueError):
        build_model_extension_plan(root, root / "plan.json")


def test_extension_plan_detects_changed_base_or_uploaded_source(tmp_path):
    root, previous, current = staged(tmp_path)
    plan = root / "plan.json"
    build_model_extension_plan(root, plan)
    previous["generated_at_utc"] = "changed"
    write_json(root / "previous-model.json", previous)
    with pytest.raises(ValueError, match="base snapshot changed"):
        validate_publication_plan(plan)
    # A new plan still validates the scientific relationship, but detects later tampering.
    build_model_extension_plan(root, plan)
    (root / current["assets"]["sp"][DAY.isoformat()]).write_text("{}")
    with pytest.raises(ValueError, match="source size changed"):
        validate_publication_plan(plan)


def test_publish_does_not_promote_when_public_asset_verification_fails(tmp_path, monkeypatch):
    root, previous, _ = staged(tmp_path)
    calls = []
    monkeypatch.setattr(cycle, "assert_public_base", lambda *args: None)
    monkeypatch.setattr(cycle.subprocess, "run", lambda command, **kwargs: calls.append(command))

    def fail(*args):
        raise ValueError("public object mismatch")

    monkeypatch.setattr(cycle, "verify_public_plan", fail)
    args = argparse.Namespace(
        object_prefix="birdcast-uk",
        public_base_url="https://example.com",
        bucket="test",
        s3cmd_config=Path("/private/config"),
    )
    with pytest.raises(ValueError, match="public object mismatch"):
        cycle.publish_extension(args, root, previous)
    assert len(calls) == 1 and calls[0][-1].endswith("publish-assets.sh")
    assert not (root / "publish-manifests.sh").exists()


def test_publish_orders_verified_assets_before_single_latest_promotion(tmp_path, monkeypatch):
    root, previous, _ = staged(tmp_path)
    events = []
    monkeypatch.setattr(cycle, "assert_public_base", lambda *args: events.append("compare"))
    monkeypatch.setattr(
        cycle.subprocess, "run", lambda command, **kwargs: events.append(Path(command[-1]).name)
    )
    monkeypatch.setattr(
        cycle,
        "verify_public_plan",
        lambda plan, base: events.append(f"verify-{len(plan['objects'])}"),
    )
    args = argparse.Namespace(
        object_prefix="birdcast-uk",
        public_base_url="https://example.com",
        bucket="test",
        s3cmd_config=Path("/private/config"),
    )
    cycle.publish_extension(args, root, previous)
    assert events == [
        "compare",
        "publish-assets.sh",
        "verify-3",
        "compare",
        "publish-manifests.sh",
        "verify-4",
    ]


def test_public_precondition_checks_forecast_and_concurrent_model_changes(monkeypatch):
    previous = {"test": 1}
    forecast = {"mode": "disabled", "data_available": False, "valid_times_utc": []}
    monkeypatch.setattr(
        cycle, "fetch_public", lambda url: previous if "gam-era5" in url else forecast
    )
    cycle.assert_public_base("https://example.com", previous)
    with pytest.raises(RuntimeError, match="manifest changed"):
        cycle.assert_public_base("https://example.com", {"test": 2})
    forecast["data_available"] = True
    with pytest.raises(RuntimeError, match="forecast fail-closed"):
        cycle.assert_public_base("https://example.com", previous)


@pytest.mark.parametrize(
    "change", [None, "missing_hour", "nan", "wrong_day", "wrong_grid", "duplicate"]
)
def test_selected_daily_predictors_are_complete_and_grid_locked(tmp_path, change):
    fields = sorted(SELECTED_GRID_FIELDS)
    rows = []
    for hour in range(24):
        for lon, lat in POINTS:
            row = dict.fromkeys(fields, 1)
            row.update(time_utc=f"{DAY}T{hour:02d}:00:00Z", longitude=lon, latitude=lat)
            rows.append(row)
    if change == "missing_hour":
        rows = rows[2:]
    elif change == "nan":
        rows[0]["u_925_ms"] = "NaN"
    elif change == "wrong_day":
        rows[0]["time_utc"] = "2026-07-15T00:00:00Z"
    elif change == "wrong_grid":
        rows[0]["longitude"] = 0
    elif change == "duplicate":
        rows.append(rows[0])
    path = tmp_path / "grid.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    if change is None:
        cycle.validate_grid_day(path, DAY, POINTS)
    else:
        with pytest.raises(ValueError):
            cycle.validate_grid_day(path, DAY, POINTS)


def test_daily_outputs_reject_negative_uncertainty_and_shifted_grid(tmp_path):
    root, _, current = staged(tmp_path)
    payload = json.loads((root / current["assets"]["lp"][DAY.isoformat()]).read_text())
    assert cycle.frame_coordinates(payload, DAY, "lp") == POINTS
    bad = copy.deepcopy(payload)
    bad["frames"][0]["cells"][0]["uncertainty_by_target"][TARGETS[0]] = -1
    with pytest.raises(ValueError, match="negative"):
        cycle.frame_coordinates(bad, DAY, "lp")
    bad = copy.deepcopy(payload)
    bad["frames"][0]["cells"][0]["longitude"] = 0
    with pytest.raises(ValueError, match="grid changes"):
        cycle.frame_coordinates(bad, DAY, "lp")


def test_target_allows_only_delayed_retrospective_days():
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    assert cycle.completed_target(now) == date(2026, 9, 1)
    for lag in (-1, 0, 4):
        with pytest.raises(ValueError):
            cycle.completed_target(now, lag)
    with pytest.raises(ValueError):
        cycle.completed_target(now.replace(tzinfo=None))


def test_unavailable_weather_waits_without_download_or_publication(tmp_path, monkeypatch):
    baseline = _write_selected_model_release(tmp_path / "baseline")
    previous = json.loads((baseline / "latest/gam-era5.json").read_text())
    forecast = {"mode": "disabled", "data_available": False, "valid_times_utc": []}
    monkeypatch.setattr(cycle, "completed_target", lambda *args: DAY)
    monkeypatch.setattr(cycle, "available_weather_through", lambda **kwargs: date(2026, 7, 13))
    monkeypatch.setattr(
        cycle, "fetch_public", lambda url: forecast if "forecast.json" in url else previous
    )
    monkeypatch.setattr(cycle, "frame_coordinates", lambda *args: POINTS)

    def unexpected(*args, **kwargs):
        pytest.fail("Unavailable weather must not trigger download, inference or publication")

    monkeypatch.setattr(cycle.era5, "build_period", unexpected)
    monkeypatch.setattr(cycle, "publish_extension", unexpected)
    monkeypatch.setattr(cycle, "validate_component_manifest", unexpected)
    args = argparse.Namespace(
        lag_days=5,
        public_base_url="https://example.com",
        object_prefix="birdcast-uk",
        release_sha="test",
    )
    result = cycle.run_cycle(args)
    assert result["state"] == "waiting_for_weather"
    assert result["published_through"] == "2026-07-13"
    assert result["target_through"] == "2026-07-14"
    assert result["processable_through"] == "2026-07-13"


@pytest.mark.parametrize("valid_before,valid_after", [(True, True), (False, True), (False, False)])
def test_unpublished_partial_weather_retries_and_validates(
    tmp_path, monkeypatch, valid_before, valid_after
):
    results = iter(
        [
            {"ok": valid_before, "errors": ["missing hours"]},
            {"ok": valid_after, "errors": ["missing hours"]},
        ]
    )
    monkeypatch.setattr(cycle.era5, "validate_day", lambda **kwargs: next(results))
    calls = []
    monkeypatch.setattr(cycle.era5, "build_period", lambda **kwargs: calls.append(kwargs))
    if valid_after:
        single, pressure = cycle.prepare_weather_day(tmp_path, tmp_path / "radars", DAY)
        assert single.name == "era5_single_levels_20260714_uk.nc"
        assert pressure.name == "era5_pressure_levels_20260714_uk.nc"
    else:
        with pytest.raises(ValueError, match="validation failed"):
            cycle.prepare_weather_day(tmp_path, tmp_path / "radars", DAY)
    assert len(calls) == int(not valid_before)
    if calls:
        assert calls[0]["overwrite"] is True
        assert calls[0]["start_day"] == calls[0]["end_day"] == DAY.isoformat()


def test_era5_daily_extension_keeps_support_ranges_but_not_training_date_filter(
    tmp_path, monkeypatch
):
    import numpy as np
    import xarray as xr

    from birdcast_uk import era5
    from birdcast_uk.radars import BirdcastRadar

    source = tmp_path / "input.nc"
    source.touch()
    table = tmp_path / "training.json"
    write_json(
        table,
        {
            "first_day_utc": "2025-07-14",
            "latest_complete_day_utc": "2026-07-13",
            "feature_ranges": {"surface_pressure_pa": {"lower": 99000, "upper": 101000}},
        },
    )
    dataset = xr.Dataset(
        {"sp": (("valid_time", "latitude", "longitude"), np.full((2, 1, 1), 100000.0))},
        coords={
            "valid_time": np.array(
                ["2026-07-13T00:00:00", "2026-07-14T00:00:00"], dtype="datetime64[ns]"
            ),
            "latitude": [52.0],
            "longitude": [-1.0],
        },
    )
    monkeypatch.setattr(era5, "_open_datasets", lambda *args: [dataset.copy(deep=True)])
    monkeypatch.setattr(
        era5,
        "load_radars",
        lambda *args: [
            BirdcastRadar("test", "01", "Test", 52.0, -1.0, 100.0, 255000.0, "test"),
        ],
    )
    kwargs = dict(
        single_levels=source, pressure_levels=None, radars_path=None, training_table=table
    )
    original = era5.extract_grid_features(**kwargs, output=tmp_path / "original.csv")
    extension = era5.extract_grid_features(
        **kwargs, output=tmp_path / "extension.csv", restrict_to_training_window=False
    )
    assert original["row_count"] == 1
    assert extension["row_count"] == 2
    assert extension["training_window"] == original["training_window"]
    assert not extension["restricted_to_training_window"]
    rows = list(csv.DictReader((tmp_path / "extension.csv").open()))
    assert all(float(row["support"]) == 1.0 for row in rows)
