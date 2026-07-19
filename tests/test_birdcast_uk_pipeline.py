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
    cds_readiness,
    download_request,
    write_request,
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
    assert calls[1] == ("to_target", "file", str(output_path))


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
    assert script.index("era5 readiness") < script.index('inventory="$(sbatch')


def _write_json(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
