"""Operational gridded reanalysis and forecast production."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import mkdtemp
from typing import Any

from .config import (
    FORECAST_ENSEMBLE_SIZE,
    FORECAST_HORIZON_HOURS,
    FORECAST_MODEL_ID,
    FORECAST_SCHEMA_VERSION,
    FORECAST_STEP_HOURS,
)
from .grid import ForecastGrid, canonical_grid
from .radars import load_radars
from .state_space import (
    RadarObservation,
    assimilate_localised,
    initialise_ensemble,
    operational_mode,
    process_step,
    radar_age_hours,
)
from .static_artifacts import utc_now, write_json


def build_forecast(
    *,
    observed_hourly: Path,
    radars_path: Path,
    output_root: Path,
    ecmwf_manifest: Path | None = None,
    analysis_time: str | None = None,
    members: int = FORECAST_ENSEMBLE_SIZE,
) -> dict[str, object]:
    import numpy as np

    output_root.mkdir(parents=True, exist_ok=True)
    grid = canonical_grid()
    analysis = _analysis_time(analysis_time)
    observed = json.loads(observed_hourly.read_text(encoding="utf-8"))
    rows = observed.get("rows", [])
    if not isinstance(rows, list) or not rows:
        raise ValueError("observed hourly input contains no rows")
    radar_metadata = {radar.slug: radar for radar in load_radars(radars_path)}
    newest_observation = max(_parse_time(str(row["time_utc"])) for row in rows)
    age = radar_age_hours(analysis, newest_observation)
    mode = operational_mode(age)
    latest_rows = _latest_radar_rows(rows)
    observations = _radar_observations(latest_rows, radar_metadata, grid)
    usable_observations = observations if mode == "assimilated" else []
    climatology = _seasonal_climatology(rows, analysis.month)
    seed = int(analysis.strftime("%Y%m%d%H"))
    ensemble = initialise_ensemble(
        (grid.height, grid.width),
        usable_observations,
        members=members,
        seed=seed,
        climatology=climatology,
    )
    if mode == "propagated":
        ensemble *= np.random.default_rng(seed + 7).lognormal(0, 0.55, ensemble.shape).astype("float32")

    wind_u, wind_v, weather_cycle, weather_source = _weather_winds(
        ecmwf_manifest, grid, latest_rows, radar_metadata
    )
    if mode == "assimilated":
        ensemble, influence = assimilate_localised(ensemble, observations)
    else:
        influence = np.zeros((grid.height, grid.width), dtype="float32")

    valid_times = [analysis + timedelta(hours=hour) for hour in range(0, FORECAST_HORIZON_HOURS + 1, FORECAST_STEP_HOURS)]
    fields: dict[str, list[Any]] = {
        "bird_density_p10": [],
        "bird_density_p50": [],
        "bird_density_p90": [],
        "migration_u": [],
        "migration_v": [],
        "median_flight_height_m": [],
        "flight_height_iqr_m": [],
        "contamination_probability": [],
        "observation_influence": [],
        "quality_flag": [],
    }
    contamination = 0.65 if analysis.month in (5, 6, 7, 8) else 0.25
    quality_value = {"assimilated": 0, "propagated": 1, "weather_only": 2}[mode]
    for index, valid_time in enumerate(valid_times):
        step_u = wind_u[min(index, wind_u.shape[0] - 1)]
        step_v = wind_v[min(index, wind_v.shape[0] - 1)]
        if index:
            for internal_hour in range(1, FORECAST_STEP_HOURS + 1):
                internal_time = valid_time - timedelta(hours=FORECAST_STEP_HOURS - internal_hour)
                ensemble = process_step(
                    ensemble,
                    step_u,
                    step_v,
                    internal_time,
                    resolution_m=grid.resolution_m,
                    hours=1,
                    seed=seed + index * FORECAST_STEP_HOURS + internal_hour,
                )
        quantiles = np.quantile(ensemble, [0.1, 0.5, 0.9], axis=0).astype("float32")
        fields["bird_density_p10"].append(quantiles[0])
        fields["bird_density_p50"].append(quantiles[1])
        fields["bird_density_p90"].append(quantiles[2])
        fields["migration_u"].append(step_u.astype("float32"))
        fields["migration_v"].append(step_v.astype("float32"))
        fields["median_flight_height_m"].append(np.full(quantiles[0].shape, 850.0, dtype="float32"))
        fields["flight_height_iqr_m"].append(np.full(quantiles[0].shape, 700.0, dtype="float32"))
        fields["contamination_probability"].append(np.full(quantiles[0].shape, contamination, dtype="float32"))
        fields["observation_influence"].append(influence * math.exp(-index / 4.0))
        fields["quality_flag"].append(np.full(quantiles[0].shape, quality_value, dtype="uint8"))

    arrays = {name: np.stack(values) for name, values in fields.items()}
    issue_stamp = analysis.strftime("%Y%m%dT%H00Z")
    archive_relative = Path("archive") / "forecast" / issue_stamp
    archive_dir = output_root / archive_relative
    staging = Path(mkdtemp(prefix=f".{issue_stamp}.", dir=archive_dir.parent if archive_dir.parent.exists() else output_root))
    try:
        science_assets = _write_scientific_assets(staging, grid, valid_times, arrays)
        public_assets = _write_public_frames(staging, grid, valid_times, arrays, mode)
        run_manifest = {
            "schema_version": FORECAST_SCHEMA_VERSION,
            "generated_at_utc": utc_now(),
            "issue_time_utc": analysis.isoformat().replace("+00:00", "Z"),
            "analysis_time_utc": analysis.isoformat().replace("+00:00", "Z"),
            "radar_observation_time_utc": newest_observation.isoformat().replace("+00:00", "Z"),
            "radar_age_hours": round(age, 3),
            "weather_cycle_utc": weather_cycle,
            "weather_source": weather_source,
            "model_id": FORECAST_MODEL_ID,
            "git_commit": _git_commit(),
            "schema_hash": _schema_hash(),
            "mode": mode,
            "ensemble_members": members,
            "grid": grid.to_dict(),
            "map_overlay": _map_overlay(grid, radar_metadata),
            "valid_times_utc": [value.isoformat().replace("+00:00", "Z") for value in valid_times],
            "variables": list(arrays),
            "assets": {**science_assets, **public_assets},
            "freshness": {
                "radar": "fresh" if age <= 6 else "stale",
                "weather": "available" if weather_cycle else "fallback",
            },
            "quality": {
                "quality_flag_meanings": {"0": "assimilated", "1": "propagated", "2": "weather_only"},
                "year_round_taxon": "biological_targets_not_species_resolved",
                "warning": "Weather radar cannot fully separate birds from insects; uncertainty is wider in summer.",
            },
        }
        write_json(staging / "manifest.json", run_manifest)
        staging.chmod(0o755)
        if archive_dir.exists():
            shutil.rmtree(archive_dir)
        archive_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, archive_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    latest = dict(run_manifest)
    latest["archive_prefix"] = str(archive_relative)
    latest["assets"] = {
        key: _prefix_asset(archive_relative, value)
        for key, value in run_manifest["assets"].items()
    }
    write_json(output_root / "latest" / "forecast.json", latest)
    return latest


def _prefix_asset(prefix: Path, value):
    if isinstance(value, str):
        return str(prefix / value)
    if isinstance(value, list):
        return [str(prefix / item) for item in value]
    return value


def _map_overlay(grid: ForecastGrid, radar_metadata) -> dict[str, object]:
    # Lightweight coastline coordinates are transformed into the canonical
    # grid so the browser does not need a projection library.
    coastlines_lonlat = [
        [
            (-5.7, 50.0), (-3.6, 50.1), (-2.0, 50.6), (0.9, 51.1),
            (1.6, 52.8), (0.2, 53.8), (-1.7, 55.0), (-2.0, 56.2),
            (-3.0, 58.7), (-5.0, 58.6), (-6.2, 57.5), (-5.1, 55.7),
            (-3.0, 54.8), (-4.8, 53.3), (-5.7, 51.8), (-5.7, 50.0),
        ],
        [
            (-8.2, 54.0), (-6.1, 54.0), (-5.4, 54.8), (-6.0, 55.3),
            (-7.5, 55.3), (-8.2, 54.7), (-8.2, 54.0),
        ],
        [
            (-3.2, 51.4), (-2.7, 51.5), (-3.0, 52.0), (-4.1, 52.5),
            (-4.8, 52.0), (-4.1, 51.6), (-3.2, 51.4),
        ],
    ]
    coastlines = []
    for polygon in coastlines_lonlat:
        projected = [grid.cell_for_lonlat(lon, lat) for lon, lat in polygon]
        coastlines.append(
            [{"row": cell[0], "col": cell[1]} for cell in projected if cell is not None]
        )
    radars = []
    for radar in radar_metadata.values():
        if radar.longitude is None or radar.latitude is None:
            continue
        cell = grid.cell_for_lonlat(radar.longitude, radar.latitude)
        if cell is not None:
            radars.append({"slug": radar.slug, "label": radar.label, "row": cell[0], "col": cell[1]})
    return {"coastlines": coastlines, "radars": radars}


def _analysis_time(value: str | None) -> datetime:
    if value:
        parsed = _parse_time(value)
    else:
        parsed = datetime.now(timezone.utc)
    hour = parsed.hour - parsed.hour % FORECAST_STEP_HOURS
    return parsed.replace(hour=hour, minute=0, second=0, microsecond=0)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def _latest_radar_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        radar = str(row.get("radar") or "")
        if not radar:
            continue
        if radar not in latest or str(row.get("time_utc", "")) > str(latest[radar].get("time_utc", "")):
            latest[radar] = row
    return list(latest.values())


def _radar_observations(rows, radar_metadata, grid: ForecastGrid) -> list[RadarObservation]:
    observations = []
    for row in rows:
        radar = radar_metadata.get(str(row.get("radar")))
        if not radar or radar.longitude is None or radar.latitude is None:
            continue
        cell = grid.cell_for_lonlat(radar.longitude, radar.latitude)
        if cell is None:
            continue
        speed = float(row.get("mean_ground_speed_ms") or 0.0)
        direction = math.radians(float(row.get("dominant_direction_deg") or 0.0))
        density = max(float(row.get("mean_vid_birds_per_km2") or 0.0), 0.0)
        observations.append(
            RadarObservation(
                row=cell[0],
                col=cell[1],
                density_birds_km2=density,
                observation_variance=max(0.04, density * 0.5) ** 2,
                u_ms=speed * math.sin(direction),
                v_ms=speed * math.cos(direction),
            )
        )
    return observations


def _seasonal_climatology(rows: list[dict[str, Any]], month: int) -> float:
    values = [
        float(row.get("mean_vid_birds_per_km2"))
        for row in rows
        if row.get("mean_vid_birds_per_km2") is not None
        and _parse_time(str(row["time_utc"])).month == month
    ]
    if not values:
        return 0.25
    values.sort()
    return max(values[len(values) // 2], 0.02)


def _weather_winds(manifest_path, grid, rows, radar_metadata):
    import numpy as np

    if manifest_path and manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") == "complete":
            pressure = next((Path(item["path"]) for item in manifest.get("files", []) if item.get("kind") == "pressure"), None)
            if pressure and pressure.is_file():
                try:
                    return (*_earthkit_wind_grid(pressure, grid), manifest.get("cycle_time_utc"), "ECMWF Open Data")
                except Exception:
                    pass
    observations = _radar_observations(rows, radar_metadata, grid)
    if observations:
        weights = np.array([max(obs.density_birds_km2, 0.05) for obs in observations])
        u = float(np.average([obs.u_ms for obs in observations], weights=weights))
        v = float(np.average([obs.v_ms for obs in observations], weights=weights))
    else:
        u = v = 0.0
    shape = (FORECAST_HORIZON_HOURS // FORECAST_STEP_HOURS + 1, grid.height, grid.width)
    return np.full(shape, u, dtype="float32"), np.full(shape, v, dtype="float32"), None, "radar_climatological_wind_fallback"


def _earthkit_wind_grid(path: Path, grid: ForecastGrid):
    import earthkit.data as ekd
    import numpy as np

    fields = ekd.from_source("file", str(path)).to_fieldlist()
    selected = fields.sel(
        {"vertical.level": 850, "parameter.variable": ["u", "v"]}
    ).to_xarray()
    u_name = next(name for name in ("u", "u_component_of_wind") if name in selected)
    v_name = next(name for name in ("v", "v_component_of_wind") if name in selected)
    lon, lat = grid.lonlat()
    source_lon = selected["longitude"].values
    source_lat = selected["latitude"].values
    u_values = np.asarray(selected[u_name].values).squeeze()
    v_values = np.asarray(selected[v_name].values).squeeze()
    if u_values.ndim == 2:
        u_values = u_values[None, :, :]
        v_values = v_values[None, :, :]
    u = np.stack([_nearest_regular_grid(item, source_lon, source_lat, lon, lat) for item in u_values])
    v = np.stack([_nearest_regular_grid(item, source_lon, source_lat, lon, lat) for item in v_values])
    return np.asarray(u, dtype="float32"), np.asarray(v, dtype="float32")


def _nearest_regular_grid(values, source_lon, source_lat, target_lon, target_lat):
    import numpy as np

    lon_axis = source_lon[0] if np.ndim(source_lon) == 2 else source_lon
    lat_axis = source_lat[:, 0] if np.ndim(source_lat) == 2 else source_lat
    adjusted_lon = np.where((lon_axis.min() >= 0) & (target_lon < 0), target_lon + 360, target_lon)
    lon_idx = _nearest_axis_indices(lon_axis, adjusted_lon)
    lat_idx = _nearest_axis_indices(lat_axis, target_lat)
    return values[lat_idx, lon_idx]


def _nearest_axis_indices(axis, targets):
    import numpy as np

    axis = np.asarray(axis)
    descending = axis[0] > axis[-1]
    ordered = axis[::-1] if descending else axis
    indices = np.searchsorted(ordered, targets)
    indices = np.clip(indices, 1, len(ordered) - 1)
    left = ordered[indices - 1]
    right = ordered[indices]
    indices -= (np.abs(targets - left) <= np.abs(targets - right)).astype("int64")
    return len(axis) - 1 - indices if descending else indices


def _write_scientific_assets(root, grid, valid_times, arrays):
    import numpy as np
    import xarray as xr

    coordinates = {
        "time": [value.replace(tzinfo=None) for value in valid_times],
        "y": grid.y_centres(),
        "x": grid.x_centres(),
    }
    dataset = xr.Dataset(
        {name: (("time", "y", "x"), values) for name, values in arrays.items()},
        coords=coordinates,
        attrs={"crs": grid.crs, "schema_version": FORECAST_SCHEMA_VERSION, "model_id": FORECAST_MODEL_ID},
    )
    encoding = {name: {"chunks": (1, min(120, grid.height), min(120, grid.width))} for name in arrays}
    zarr_path = root / "forecast.zarr"
    dataset.to_zarr(
        zarr_path,
        mode="w",
        encoding=encoding,
        zarr_format=2,
        consolidated=True,
    )

    cog_dir = root / "cog"
    cog_dir.mkdir()
    try:
        import rasterio
        from rasterio.transform import from_origin

        transform = from_origin(grid.x_min_m, grid.y_max_m, grid.resolution_m, grid.resolution_m)
        for index, valid_time in enumerate(valid_times):
            path = cog_dir / f"bird_density_p50_{valid_time:%Y%m%dT%H00Z}.tif"
            with rasterio.open(
                path,
                "w",
                driver="COG",
                height=grid.height,
                width=grid.width,
                count=1,
                dtype="float32",
                crs=grid.crs,
                transform=transform,
                compress="deflate",
                nodata=np.nan,
            ) as target:
                target.write(arrays["bird_density_p50"][index], 1)
    except ImportError:
        cog_dir.rmdir()
    return {
        "zarr": "forecast.zarr",
        "cog_prefix": "cog" if cog_dir.exists() else None,
    }


def _write_public_frames(root, grid, valid_times, arrays, mode):
    import numpy as np

    frames_dir = root / "frames"
    frames_dir.mkdir()
    stride = 4
    frame_names = []
    for index, valid_time in enumerate(valid_times):
        p10 = arrays["bird_density_p10"][index, ::stride, ::stride]
        p50 = arrays["bird_density_p50"][index, ::stride, ::stride]
        p90 = arrays["bird_density_p90"][index, ::stride, ::stride]
        payload = {
            "valid_time_utc": valid_time.isoformat().replace("+00:00", "Z"),
            "mode": mode,
            "shape": list(p50.shape),
            "stride": stride,
            "density_p50": np.round(p50, 3).tolist(),
            "uncertainty_width": np.round(p90 - p10, 3).tolist(),
            "u_ms": np.round(arrays["migration_u"][index, ::stride, ::stride], 2).tolist(),
            "v_ms": np.round(arrays["migration_v"][index, ::stride, ::stride], 2).tolist(),
            "contamination_probability": np.round(
                arrays["contamination_probability"][index, ::stride, ::stride], 2
            ).tolist(),
        }
        name = f"{valid_time:%Y%m%dT%H00Z}.json"
        write_json(frames_dir / name, payload)
        frame_names.append(f"frames/{name}")
    return {"frames": frame_names}


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _schema_hash() -> str:
    contract = "|".join(
        [
            FORECAST_SCHEMA_VERSION,
            "bird_density_p10,p50,p90",
            "migration_u,v",
            "median_flight_height_m,flight_height_iqr_m",
            "contamination_probability,observation_influence,quality_flag",
        ]
    )
    return hashlib.sha256(contract.encode("ascii")).hexdigest()
