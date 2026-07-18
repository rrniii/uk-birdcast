"""Independent ERA5 helpers for BirdCast UK."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import zipfile
from typing import Any, Iterable
import math

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


def extract_grid_features(
    *,
    single_levels: Path | None,
    pressure_levels: Path | None,
    radars_path: Path | None,
    output: Path,
    training_table: Path | None = None,
) -> dict[str, object]:
    """Extract native ERA5 grid features plus an explicit support score.

    The result is designed for the batch GAMM/XGBoost runners.  It remains at
    ERA5's 0.25-degree resolution; visual smoothing happens only in the client.
    ``support`` combines proximity to a UK radar and whether meteorological
    values lie within the rolling model table's observed range.
    """

    datasets = _open_datasets(single_levels, pressure_levels)
    if not datasets:
        raise ValueError("at least one ERA5 dataset is required")
    single = datasets[0] if single_levels is not None and single_levels.exists() else None
    pressure = datasets[-1] if pressure_levels is not None and pressure_levels.exists() else None
    radars = [radar for radar in load_radars(radars_path) if radar.latitude is not None and radar.longitude is not None]
    ranges = _training_feature_ranges(training_table)
    training_window = _training_window(training_table)
    rows: list[dict[str, object]] = []
    try:
        reference = single or pressure
        if reference is None:
            raise ValueError("ERA5 datasets could not be opened")
        latitude_name = _coordinate_name(reference, ("latitude", "lat"))
        longitude_name = _coordinate_name(reference, ("longitude", "lon"))
        time_name = "valid_time" if "valid_time" in reference.coords else "time"
        latitudes = [float(value) for value in reference[latitude_name].values.tolist()]
        longitudes = [float(value) for value in reference[longitude_name].values.tolist()]
        for time_value, point in _time_points(reference, time_name):
            timestamp = str(time_value) if time_value is not None else ""
            if training_window is not None:
                try:
                    selected_day = date.fromisoformat(timestamp[:10])
                except ValueError:
                    continue
                if not training_window[0] <= selected_day <= training_window[1]:
                    continue
            single_point = _select_time(single, time_name, time_value) if single is not None else None
            pressure_point = _select_time(pressure, time_name, time_value) if pressure is not None else None
            for latitude in latitudes:
                for longitude in longitudes:
                    row = {
                        "time_utc": timestamp,
                        "latitude": latitude,
                        "longitude": longitude,
                    }
                    row.update(_grid_weather_values(single_point, pressure_point, latitude_name, longitude_name, latitude, longitude))
                    row["support"] = round(_support_score(latitude, longitude, row, radars, ranges), 6)
                    rows.append(row)
    finally:
        for dataset in datasets:
            close = getattr(dataset, "close", None)
            if callable(close):
                close()
    _write_feature_rows(output, rows)
    status = {
        "ok": True,
        "generated_at_utc": utc_now(),
        "output": str(output),
        "row_count": len(rows),
        "grid_resolution": "ERA5 native 0.25 degree",
        "support_definition": "radar proximity multiplied by covariate-range support",
        "training_table": str(training_table) if training_table else None,
        "training_window": [value.isoformat() for value in training_window] if training_window else None,
    }
    write_json(output.with_suffix(output.suffix + ".status.json"), status)
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


def _coordinate_name(dataset: object, candidates: tuple[str, ...]) -> str:
    coords = getattr(dataset, "coords", {})
    for candidate in candidates:
        if candidate in coords:
            return candidate
    raise ValueError(f"ERA5 dataset is missing coordinates: {', '.join(candidates)}")


def _select_time(dataset: object | None, time_name: str, time_value: object) -> object | None:
    if dataset is None:
        return None
    coords = getattr(dataset, "coords", {})
    if time_name not in coords:
        return dataset
    dimensions = getattr(dataset, "dims", {})
    if time_name not in dimensions:
        return dataset
    return dataset.sel({time_name: time_value})  # type: ignore[attr-defined]


def _grid_weather_values(
    single: object | None,
    pressure: object | None,
    latitude_name: str,
    longitude_name: str,
    latitude: float,
    longitude: float,
) -> dict[str, object]:
    values: dict[str, object] = {}
    point = {latitude_name: latitude, longitude_name: longitude}
    for target, candidates in {
        "surface_pressure_pa": ("sp", "surface_pressure"),
        "mean_sea_level_pressure_pa": ("msl", "mean_sea_level_pressure"),
        "total_cloud_cover_fraction": ("tcc", "total_cloud_cover"),
        "boundary_layer_height_m": ("blh", "boundary_layer_height"),
        # ERA5 hourly total precipitation is an accumulation for the preceding
        # output interval when requested at hourly cadence.
        "hourly_precipitation_m": ("tp", "total_precipitation"),
    }.items():
        value = _point_variable(single, candidates, point)
        if value is not None:
            values[target] = value
    for target, candidates in {
        "temperature_850_k": ("t", "temperature"),
        "relative_humidity_850_percent": ("r", "relative_humidity"),
        "u_850_ms": ("u", "u_component_of_wind"),
        "v_850_ms": ("v", "v_component_of_wind"),
    }.items():
        value = _point_variable(pressure, candidates, point, pressure_level=850)
        if value is not None:
            values[target] = value
    return values


def _point_variable(
    dataset: object | None,
    candidates: tuple[str, ...],
    point: dict[str, float],
    *,
    pressure_level: int | None = None,
) -> float | None:
    if dataset is None:
        return None
    variables = getattr(dataset, "data_vars", {})
    name = next((candidate for candidate in candidates if candidate in variables), None)
    if name is None:
        return None
    value = dataset[name].sel(point, method="nearest")  # type: ignore[index,attr-defined]
    if pressure_level is not None:
        for dimension in ("pressure_level", "isobaricInhPa", "level"):
            if dimension in getattr(value, "dims", ()):
                value = value.sel({dimension: pressure_level}, method="nearest")
                break
    return _as_float(getattr(value, "values", value))


def _training_feature_ranges(table: Path | None) -> dict[str, tuple[float, float]]:
    if table is None or not table.exists():
        return {}
    payload = json.loads(table.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    result: dict[str, tuple[float, float]] = {}
    for name in (
        "temperature_850_k", "relative_humidity_850_percent", "u_850_ms", "v_850_ms",
        "surface_pressure_pa", "mean_sea_level_pressure_pa", "total_cloud_cover_fraction",
        "boundary_layer_height_m", "hourly_precipitation_m",
    ):
        values = sorted(_as_float(row.get(name)) for row in rows if isinstance(row, dict) and _as_float(row.get(name)) is not None)
        if values:
            lower = values[max(0, int(len(values) * 0.01) - 1)]
            upper = values[min(len(values) - 1, int(len(values) * 0.99))]
            result[name] = (float(lower), float(upper))
    return result


def _training_window(table: Path | None) -> tuple[date, date] | None:
    if table is None or not table.exists():
        return None
    payload = json.loads(table.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    try:
        return date.fromisoformat(str(payload["first_day_utc"])), date.fromisoformat(str(payload["latest_complete_day_utc"]))
    except (KeyError, ValueError):
        return None


def _support_score(
    latitude: float,
    longitude: float,
    row: dict[str, object],
    radars: list[BirdcastRadar],
    ranges: dict[str, tuple[float, float]],
) -> float:
    if radars:
        distance_km = min(_great_circle_km(latitude, longitude, float(radar.latitude), float(radar.longitude)) for radar in radars)
        radar_support = math.exp(-((distance_km / 250.0) ** 2))
    else:
        radar_support = 0.0
    covariate_support = 1.0
    for name, (lower, upper) in ranges.items():
        value = _as_float(row.get(name))
        if value is None:
            covariate_support = 0.0
            continue
        width = max(upper - lower, 1e-12)
        if value < lower:
            covariate_support *= math.exp(-((lower - value) / width) * 3.0)
        elif value > upper:
            covariate_support *= math.exp(-((value - upper) / width) * 3.0)
    return max(0.0, min(1.0, radar_support * covariate_support))


def _great_circle_km(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    radians = math.pi / 180.0
    lat_a, lon_a, lat_b, lon_b = latitude_a * radians, longitude_a * radians, latitude_b * radians, longitude_b * radians
    haversine = math.sin((lat_b - lat_a) / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin((lon_b - lon_a) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(haversine))


def _write_feature_rows(output: Path, rows: list[dict[str, object]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".json":
        write_json(output, {"generated_at_utc": utc_now(), "rows": rows})
        return
    try:
        import pandas as pd  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError("pandas is required for CSV/Parquet grid ERA5 features") from exc
    frame = pd.DataFrame.from_records(rows)
    if output.suffix.lower() == ".parquet":
        frame.to_parquet(output, index=False)
    else:
        frame.to_csv(output, index=False)


def _as_float(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


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
