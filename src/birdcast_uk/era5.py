"""Independent ERA5 helpers for BirdCast UK."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import zipfile
from typing import Any, Iterable

from .config import (
    ERA5_PRESSURE_LEVELS,
    ERA5_PRESSURE_LEVEL_VARIABLES,
    ERA5_SINGLE_LEVEL_VARIABLES,
    UK_ERA5_AREA,
)
from .radars import BirdcastRadar, load_radars
from .static_artifacts import utc_now, write_json


EARTHKIT_BACKEND = "earthkit-data"


@dataclass(frozen=True)
class Era5Request:
    dataset: str
    request: dict[str, Any]
    output_file: str
    backend: str = EARTHKIT_BACKEND

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_request(day: str, kind: str, output_file: Path, area: dict[str, float] | None = None) -> Era5Request:
    selected = date.fromisoformat(day)
    domain = area or UK_ERA5_AREA
    base = {
        "product_type": ["reanalysis"],
        "variable": list(ERA5_SINGLE_LEVEL_VARIABLES if kind == "single-levels" else ERA5_PRESSURE_LEVEL_VARIABLES),
        "year": [f"{selected.year:04d}"],
        "month": [f"{selected.month:02d}"],
        "day": [f"{selected.day:02d}"],
        "time": [f"{hour:02d}:00" for hour in range(24)],
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": [domain["north"], domain["west"], domain["south"], domain["east"]],
    }
    if kind == "single-levels":
        dataset = "reanalysis-era5-single-levels"
    elif kind == "pressure-levels":
        dataset = "reanalysis-era5-pressure-levels"
        base["pressure_level"] = list(ERA5_PRESSURE_LEVELS)
    else:
        raise ValueError("kind must be single-levels or pressure-levels")
    return Era5Request(dataset=dataset, request=base, output_file=str(output_file))


def write_request(day: str, kind: str, output_file: Path, request_json: Path, area: dict[str, float] | None = None) -> Era5Request:
    request = build_request(day, kind, output_file, area=area)
    write_json(request_json, request.to_dict())
    return request


def download_request(request_json: Path, *, overwrite: bool = False) -> dict[str, object]:
    payload = json.loads(request_json.read_text(encoding="utf-8"))
    request = Era5Request(
        dataset=str(payload["dataset"]),
        request=dict(payload["request"]),
        output_file=str(payload["output_file"]),
        backend=str(payload.get("backend") or EARTHKIT_BACKEND),
    )
    output = Path(request.output_file)
    if output.exists() and not overwrite:
        return {
            "ok": True,
            "skipped": True,
            "backend": EARTHKIT_BACKEND,
            "backend_version": _earthkit_version(),
            "reason": "output exists",
            "output_file": str(output),
        }
    earthkit = _earthkit_data()
    output.parent.mkdir(parents=True, exist_ok=True)
    data = earthkit.from_source(
        "cds",
        request.dataset,
        request=request.request,
        prompt=False,
    )
    data.to_target("file", str(output))
    return {
        "ok": True,
        "skipped": False,
        "backend": EARTHKIT_BACKEND,
        "backend_version": _earthkit_version(),
        "dataset": request.dataset,
        "output_file": str(output),
        "size": output.stat().st_size,
    }


def extract_zip_archive(archive: Path, output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zip_file:
        names = [name for name in zip_file.namelist() if not name.endswith("/")]
        zip_file.extractall(output_dir)
    return {
        "ok": True,
        "archive": str(archive),
        "output_dir": str(output_dir),
        "members": names,
    }


def extract_site_features(
    *,
    single_levels: Path | None,
    pressure_levels: Path | None,
    radars_path: Path | None,
    output: Path,
) -> dict[str, object]:
    """Extract nearest-grid ERA5 features for radar sites with known coordinates.

    This intentionally accepts absent files so the command can produce a clear
    readiness report before large ERA5 backfills exist.
    """

    radars = load_radars(radars_path)
    rows: list[dict[str, object]] = []
    skipped = []
    datasets = _open_datasets(single_levels, pressure_levels)
    try:
        for radar in radars:
            if radar.latitude is None or radar.longitude is None:
                skipped.append({"radar": radar.slug, "reason": "missing latitude/longitude"})
                continue
            rows.extend(_features_for_radar(radar, datasets))
    finally:
        for dataset in datasets:
            close = getattr(dataset, "close", None)
            if callable(close):
                close()

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".json":
        payload = {
            "generated_at_utc": utc_now(),
            "rows": rows,
            "skipped": skipped,
        }
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        try:
            import pandas as pd  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise RuntimeError("pandas is required for Parquet/CSV ERA5 feature outputs") from exc
        frame = pd.DataFrame.from_records(rows)
        if output.suffix.lower() == ".csv":
            frame.to_csv(output, index=False)
        else:
            frame.to_parquet(output, index=False)
    status = {
        "ok": True,
        "generated_at_utc": utc_now(),
        "output": str(output),
        "row_count": len(rows),
        "skipped_count": len(skipped),
        "skipped": skipped,
    }
    status_path = output.with_suffix(output.suffix + ".status.json")
    write_json(status_path, status)
    return status


def build_day(
    *,
    day: str,
    raw_dir: Path,
    feature_output: Path,
    radars_path: Path | None,
    download: bool = False,
    overwrite: bool = False,
) -> dict[str, object]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    pressure_file = raw_dir / f"era5_pressure_levels_{day.replace('-', '')}_uk.nc"
    single_file = raw_dir / f"era5_single_levels_{day.replace('-', '')}_uk.nc"
    pressure_request = raw_dir / f"era5_pressure_levels_{day.replace('-', '')}_request.json"
    single_request = raw_dir / f"era5_single_levels_{day.replace('-', '')}_request.json"
    write_request(day, "pressure-levels", pressure_file, pressure_request)
    write_request(day, "single-levels", single_file, single_request)
    downloads = []
    if download:
        for request_path in (pressure_request, single_request):
            try:
                downloads.append(download_request(request_path, overwrite=overwrite))
            except Exception as exc:  # Keep scheduled jobs observable when CDS rejects a request.
                downloads.append(
                    {
                        "ok": False,
                        "backend": EARTHKIT_BACKEND,
                        "backend_version": _earthkit_version(),
                        "request_json": str(request_path),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
    feature_status = None
    if pressure_file.exists() or single_file.exists():
        try:
            feature_status = extract_site_features(
                single_levels=single_file if single_file.exists() else None,
                pressure_levels=pressure_file if pressure_file.exists() else None,
                radars_path=radars_path,
                output=feature_output,
            )
        except Exception as exc:
            feature_status = {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
    downloads_ok = all(bool(item.get("ok")) for item in downloads) if downloads else True
    features_ok = feature_status is None or bool(feature_status.get("ok"))
    status = {
        "ok": downloads_ok and features_ok,
        "backend": EARTHKIT_BACKEND,
        "backend_version": _earthkit_version(),
        "day": day,
        "generated_at_utc": utc_now(),
        "download_requested": download,
        "requests": {
            "pressure_levels": str(pressure_request),
            "single_levels": str(single_request),
        },
        "downloads": downloads,
        "features": feature_status,
    }
    write_json(feature_output.with_suffix(feature_output.suffix + ".build-status.json"), status)
    return status


def _open_datasets(*paths: Path | None) -> list[object]:
    existing = [path for path in paths if path is not None and path.exists()]
    if not existing:
        return []
    earthkit = _earthkit_data()
    datasets = []
    for path in existing:
        data = earthkit.from_source("file", str(path))
        datasets.append(data.to_xarray())
    return datasets


def _earthkit_data() -> Any:
    try:
        import earthkit.data as earthkit_data  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "earthkit-data is required for ERA5 access and decoding. "
            "Install birdcast-uk[birdcast] and configure CDSAPI_RC or ~/.cdsapirc."
        ) from exc
    return earthkit_data


def _earthkit_version() -> str:
    try:
        return version("earthkit-data")
    except PackageNotFoundError:
        return "unknown"


def _features_for_radar(radar: BirdcastRadar, datasets: Iterable[object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for dataset_index, dataset in enumerate(datasets):
        selected = dataset.sel(latitude=radar.latitude, longitude=radar.longitude, method="nearest")  # type: ignore[attr-defined]
        time_name = "valid_time" if "valid_time" in selected.coords else "time"
        for time_value, point in _time_points(selected, time_name):
            base: dict[str, object] = {
                "radar": radar.slug,
                "radar_num": radar.radar_num,
                "latitude": radar.latitude,
                "longitude": radar.longitude,
                "dataset_index": dataset_index,
                "time_utc": str(time_value) if time_value is not None else "",
            }
            for name in point.data_vars:
                value = point[name]
                if getattr(value, "ndim", 0) == 0:
                    base[str(name)] = _scalar(value.values)
                elif getattr(value, "ndim", 0) == 1:
                    dim = str(value.dims[0])
                    coords = value[dim].values.tolist() if dim in value.coords else list(range(value.shape[0]))
                    if not isinstance(coords, list):
                        coords = [coords]
                    for coord, cell in zip(coords, value.values.tolist()):
                        base[f"{name}_{dim}_{coord}"] = _scalar(cell)
            rows.append(base)
    return rows


def _time_points(selected: object, time_name: str):
    coords = getattr(selected, "coords", {})
    if time_name not in coords:
        yield None, selected
        return

    dimensions = getattr(selected, "dims", {})
    if time_name not in dimensions:
        yield selected[time_name].values, selected  # type: ignore[index]
        return

    values = selected[time_name].values  # type: ignore[index]
    size = int(selected.sizes[time_name])  # type: ignore[attr-defined,index]
    for index in range(size):
        yield values[index], selected.isel({time_name: index})  # type: ignore[attr-defined]


def _scalar(value: object) -> object:
    try:
        item = value.item()  # type: ignore[attr-defined]
    except AttributeError:
        return value
    if hasattr(item, "isoformat"):
        return item.isoformat()
    return item
