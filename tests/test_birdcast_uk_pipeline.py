from __future__ import annotations

import json
from pathlib import Path
import sys
from types import ModuleType

from birdcast_uk.era5 import (
    EARTHKIT_BACKEND,
    _features_for_radar,
    _open_datasets,
    _support_score,
    build_day,
    build_period_request,
    cds_readiness,
    download_request,
    split_period_file,
    validate_day,
    write_request,
)
from birdcast_uk.config import (
    ERA5_PRESSURE_LEVELS,
    ERA5_PRESSURE_LEVEL_VARIABLES,
    ERA5_SINGLE_LEVEL_VARIABLES,
)
from birdcast_uk.observed import build_observed_products
from birdcast_uk.radars import BirdcastRadar, radars_from_pvol_catalog, write_radars
from birdcast_uk.vpts import validate_manifest


def test_radars_from_pvol_catalog_extracts_coordinates(tmp_path: Path) -> None:
    catalog = tmp_path / "pvol_catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "radars": [
                    {
                        "radar": "chenies",
                        "radar_num": "05",
                        "spatial": {
                            "latitude": 51.68944444444444,
                            "longitude": -0.5302777777777778,
                            "height_m": 153.0,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    radars = radars_from_pvol_catalog(catalog)

    assert len(radars) == 1
    assert radars[0].slug == "chenies"
    assert radars[0].latitude == 51.68944444444444
    assert radars[0].height_m == 153.0


def test_observed_products_from_rows(tmp_path: Path) -> None:
    radars_path = tmp_path / "radars.json"
    write_radars(
        radars_path,
        radars_from_pvol_catalog(
            _write_json(
                tmp_path / "catalog.json",
                {
                    "radars": [
                        {
                            "radar": "chenies",
                            "radar_num": "05",
                            "spatial": {"latitude": 51.0, "longitude": -0.5},
                        }
                    ]
                },
            )
        ),
    )
    rows = tmp_path / "vpts.json"
    rows.write_text(
        json.dumps(
            [
                {
                    "radar": "chenies",
                    "timestamp_utc": "2026-07-13T22:00:00Z",
                    "mtr": 12.5,
                    "direction_deg": 45.0,
                    "ground_speed_ms": 8.0,
                    "height_m": 900.0,
                },
                {
                    "radar": "chenies",
                    "timestamp_utc": "2026-07-13T22:10:00Z",
                    "mtr": 7.5,
                    "direction_deg": 90.0,
                    "ground_speed_ms": 10.0,
                    "height_m": 1100.0,
                },
            ]
        ),
        encoding="utf-8",
    )

    result = build_observed_products(input_path=rows, output_dir=tmp_path / "out", radars_path=radars_path)
    summary = json.loads((tmp_path / "out" / "latest" / "latest_nightly_summary.json").read_text(encoding="utf-8"))
    geojson = json.loads((tmp_path / "out" / "latest" / "latest_observed.geojson").read_text(encoding="utf-8"))

    assert result["night_count"] == 1
    assert summary["data_available"] is True
    assert summary["nights"][0]["migration_traffic_birds_per_km"] == 1.666667
    assert summary["nights"][0]["mean_mtr_birds_km_h"] == 10.0
    assert len(geojson["features"]) == 1


def test_vpts_manifest_validation_keeps_required_contract(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([{"radar": "chenies", "date": "20260713"}]), encoding="utf-8")

    result = validate_manifest(manifest)

    assert result["ok"] is False
    assert result["errors"]


def test_era5_request_declares_earthkit_backend(tmp_path: Path) -> None:
    request = write_request(
        "2026-07-09",
        "single-levels",
        tmp_path / "era5.nc",
        tmp_path / "request.json",
    )

    payload = json.loads((tmp_path / "request.json").read_text(encoding="utf-8"))
    assert request.backend == EARTHKIT_BACKEND
    assert payload["backend"] == EARTHKIT_BACKEND
    assert "total_precipitation" in payload["request"]["variable"]


def test_era5_requests_match_the_nine_model_predictors() -> None:
    assert ERA5_SINGLE_LEVEL_VARIABLES == (
        "surface_pressure",
        "mean_sea_level_pressure",
        "total_cloud_cover",
        "boundary_layer_height",
        "total_precipitation",
    )
    assert ERA5_PRESSURE_LEVEL_VARIABLES == (
        "temperature",
        "relative_humidity",
        "u_component_of_wind",
        "v_component_of_wind",
    )
    assert ERA5_PRESSURE_LEVELS == ("850",)


def test_era5_period_request_contains_each_day_in_one_month(tmp_path: Path) -> None:
    request = build_period_request(
        "2026-06-28",
        "2026-06-30",
        "single-levels",
        tmp_path / "period.nc",
    )

    assert request.request["year"] == ["2026"]
    assert request.request["month"] == ["06"]
    assert request.request["day"] == ["28", "29", "30"]


def test_era5_period_request_rejects_cross_month_range(tmp_path: Path) -> None:
    try:
        build_period_request(
            "2026-06-30",
            "2026-07-01",
            "single-levels",
            tmp_path / "period.nc",
        )
    except ValueError as exc:
        assert "calendar month" in str(exc)
    else:
        raise AssertionError("cross-month ERA5 requests must be partitioned")


def test_era5_period_split_writes_atomic_daily_files(tmp_path: Path, monkeypatch) -> None:
    import numpy as np
    import xarray as xr

    times = np.arange(
        np.datetime64("2026-07-01T00:00"),
        np.datetime64("2026-07-03T00:00"),
        np.timedelta64(1, "h"),
    )
    dataset = xr.Dataset(
        {"t2m": (("valid_time", "latitude", "longitude"), np.ones((48, 1, 1)))},
        coords={"valid_time": times, "latitude": [51.5], "longitude": [-0.5]},
    )
    monkeypatch.setattr("birdcast_uk.era5._open_datasets", lambda *paths: [dataset])

    outputs = split_period_file(
        source=tmp_path / "period.nc",
        kind="single-levels",
        start_day="2026-07-01",
        end_day="2026-07-02",
        raw_dir=tmp_path / "raw",
    )

    assert [Path(path).name for path in outputs] == [
        "era5_single_levels_20260701_uk.nc",
        "era5_single_levels_20260702_uk.nc",
    ]
    for path in outputs:
        with xr.open_dataset(path) as daily:
            assert daily.sizes["valid_time"] == 24
    assert not list((tmp_path / "raw").glob("*.partial"))


def test_era5_download_uses_earthkit_cds_source(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []

    class FakeData:
        def to_target(self, kind: str, output: str) -> None:
            calls.append(("to_target", kind, output))
            Path(output).write_bytes(b"netcdf")

    data_module = ModuleType("earthkit.data")

    def from_source(source: str, dataset: str, **kwargs):
        calls.append(("from_source", source, dataset, kwargs))
        return FakeData()

    data_module.from_source = from_source  # type: ignore[attr-defined]
    earthkit_module = ModuleType("earthkit")
    earthkit_module.__path__ = []  # type: ignore[attr-defined]
    earthkit_module.data = data_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "earthkit", earthkit_module)
    monkeypatch.setitem(sys.modules, "earthkit.data", data_module)

    request_path = tmp_path / "request.json"
    output_path = tmp_path / "era5.nc"
    write_request("2026-07-09", "single-levels", output_path, request_path)
    result = download_request(request_path)

    assert result["ok"] is True
    assert result["backend"] == EARTHKIT_BACKEND
    assert calls[0][0:3] == ("from_source", "cds", "reanalysis-era5-single-levels")
    assert calls[0][3]["prompt"] is False  # type: ignore[index]
    assert calls[1][0:2] == ("to_target", "file")
    assert str(calls[1][2]).endswith(".partial.nc")
    assert output_path.is_file()


def test_era5_download_merges_mixed_expver_response_atomically(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import numpy as np
    import xarray as xr

    calls = []

    class MergeError(Exception):
        pass

    class FakeData:
        def to_target(self, kind: str, output: str) -> None:
            raise MergeError("conflicting values for variable 'expver'")

        def to_xarray(self, **kwargs):
            calls.append(kwargs)
            return xr.Dataset(
                {"tcc": (("valid_time",), np.array([0.5]))},
                coords={"valid_time": [np.datetime64("2026-05-01T00:00")]},
            )

    class FakeEarthkit:
        @staticmethod
        def from_source(*args, **kwargs):
            return FakeData()

    monkeypatch.setattr("birdcast_uk.era5._earthkit_data", lambda: FakeEarthkit())
    output_path = tmp_path / "era5.nc"
    request_path = tmp_path / "request.json"
    write_request("2026-05-01", "single-levels", output_path, request_path)

    result = download_request(request_path)

    assert result["ok"] is True
    assert calls == [{"compat": "override"}]
    assert output_path.is_file()
    assert not list(tmp_path.glob(".*.partial.nc"))


def test_era5_open_dataset_uses_earthkit_file_source(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []
    expected_dataset = object()

    class FakeData:
        def to_xarray(self):
            calls.append(("to_xarray",))
            return expected_dataset

    data_module = ModuleType("earthkit.data")

    def from_source(source: str, path: str):
        calls.append(("from_source", source, path))
        return FakeData()

    data_module.from_source = from_source  # type: ignore[attr-defined]
    earthkit_module = ModuleType("earthkit")
    earthkit_module.__path__ = []  # type: ignore[attr-defined]
    earthkit_module.data = data_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "earthkit", earthkit_module)
    monkeypatch.setitem(sys.modules, "earthkit.data", data_module)

    input_path = tmp_path / "era5.nc"
    input_path.write_bytes(b"netcdf")

    assert _open_datasets(input_path) == [expected_dataset]
    assert calls == [("from_source", "file", str(input_path)), ("to_xarray",)]


def test_era5_features_iterate_time_dimension_positionally() -> None:
    isel_calls: list[dict[str, int]] = []

    class FakeCoordinate:
        values = ["2026-07-09T00:00:00", "2026-07-09T01:00:00"]

    class FakePoint:
        data_vars: dict[str, object] = {}

    class FakeSelection:
        coords = {"valid_time": FakeCoordinate()}
        dims = {"valid_time": 2}
        sizes = {"valid_time": 2}

        def __getitem__(self, name: str):
            return self.coords[name]

        def isel(self, indexers: dict[str, int]):
            isel_calls.append(indexers)
            return FakePoint()

    class FakeDataset:
        def sel(self, **kwargs):
            assert kwargs == {"latitude": 51.0, "longitude": -0.5, "method": "nearest"}
            return FakeSelection()

    radar = BirdcastRadar("chenies", "05", "Chenies", latitude=51.0, longitude=-0.5)
    rows = _features_for_radar(radar, [FakeDataset()])

    assert isel_calls == [{"valid_time": 0}, {"valid_time": 1}]
    assert [row["time_utc"] for row in rows] == ["2026-07-09T00:00:00", "2026-07-09T01:00:00"]


def test_era5_build_status_identifies_earthkit_without_download(tmp_path: Path) -> None:
    result = build_day(
        day="2026-07-09",
        raw_dir=tmp_path / "raw",
        feature_output=tmp_path / "features.json",
        radars_path=None,
    )

    assert result["ok"] is True
    assert result["backend"] == EARTHKIT_BACKEND
    assert result["download_requested"] is False


def test_era5_day_validation_requires_both_feature_datasets(tmp_path: Path) -> None:
    day = "2026-07-13"
    for kind in ("single_levels", "pressure_levels"):
        (tmp_path / f"era5_{kind}_20260713_uk.nc").write_bytes(b"netcdf")
    feature_output = tmp_path / "features.json"
    rows = []
    for hour in range(24):
        common = {
            "radar": "chenies",
            "time_utc": f"{day}T{hour:02d}:00:00",
        }
        rows.append(
            {
                **common,
                "sp": 101000.0,
                "msl": 101200.0,
                "tcc": 0.5,
                "blh": 800.0,
                "tp": 0.0,
            }
        )
        rows.append(
            {
                **common,
                "t_pressure_level_850.0": 280.0,
                "r_pressure_level_850.0": 75.0,
                "u_pressure_level_850.0": 4.0,
                "v_pressure_level_850.0": 2.0,
            }
        )
    feature_output.write_text(json.dumps({"rows": rows}), encoding="utf-8")

    valid = validate_day(day=day, raw_dir=tmp_path, feature_output=feature_output)
    assert valid["ok"] is True
    assert valid["radar_hour_count"] == 24

    feature_output.write_text(
        json.dumps({"rows": [row for row in rows if "sp" in row]}),
        encoding="utf-8",
    )
    invalid = validate_day(day=day, raw_dir=tmp_path, feature_output=feature_output)
    assert invalid["ok"] is False
    assert invalid["incomplete_radar_hour_count"] == 24


def test_cds_readiness_rejects_legacy_endpoint_and_uid_key(tmp_path: Path, monkeypatch) -> None:
    credentials = tmp_path / ".cdsapirc"
    credentials.write_text(
        "url: https://cds.climate.copernicus.eu/api/v2\nkey: 12345:legacy\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("birdcast_uk.era5._earthkit_data", lambda: object())
    monkeypatch.setattr("birdcast_uk.era5._earthkit_version", lambda: "test")

    result = cds_readiness(credentials)

    assert result["ok"] is False
    assert result["legacy_uid_prefixed_key"] is True
    assert any("CDS URL must be" in note for note in result["notes"])


def test_historical_reanalysis_submission_preflights_cds_credentials() -> None:
    script = (
        Path(__file__).parents[1]
        / "deploy"
        / "slurm"
        / "submit-historical-reanalysis.sh"
    ).read_text(encoding="utf-8")

    assert '"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli era5 readiness' in script
    assert "compgen -A variable BIRDCAST_UK_" in script
    assert script.index("compgen -A variable BIRDCAST_UK_") < script.index('inventory="$(sbatch')
    assert script.index("era5 readiness") < script.index('inventory="$(sbatch')
    assert 'published="$(sbatch --parsable --dependency="afterok:${model}"' in script
    assert "birdcast-uk-object-store-publish.sbatch" in script


def test_slurm_scripts_initialise_jasmin_modules() -> None:
    slurm_dir = Path(__file__).parents[1] / "deploy" / "slurm"
    scripts_using_modules = [
        path
        for path in slurm_dir.glob("*.sbatch")
        if "\nmodule load " in path.read_text(encoding="utf-8")
    ]

    assert scripts_using_modules
    for script in scripts_using_modules:
        content = script.read_text(encoding="utf-8")
        assert ". /etc/profile.d/modules.sh" in content, script.name
        assert ". /etc/profile.d/zz-modules.sh" in content, script.name
        assert content.index(". /etc/profile.d/modules.sh") < content.index(". /etc/profile.d/zz-modules.sh")
        assert content.index(". /etc/profile.d/zz-modules.sh") < content.index("set -u")
        assert content.index(". /etc/profile.d/modules.sh") < content.index("module load ")


def test_vpts_inventory_accepts_a_pinned_common_end_date() -> None:
    script = (
        Path(__file__).parents[1]
        / "deploy"
        / "slurm"
        / "birdcast-uk-vpts-historical-inventory.sbatch"
    ).read_text(encoding="utf-8")

    assert 'BIRDCAST_UK_REANALYSIS_END_DATE:-' in script
    assert 'inventory_args+=(--end-date "$BIRDCAST_UK_REANALYSIS_END_DATE")' in script


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
