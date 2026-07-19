"""Build scientifically explicit observed migration products from UK VPTS."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
from statistics import median
from typing import Any

from .config import (
    PROCESSING_VERSION,
    VPTS_ALTITUDE_MAX_M,
    VPTS_ALTITUDE_MIN_M,
    VPTS_MAX_INTEGRATION_GAP_MINUTES,
    VPTS_PULSE_POLICY,
    VPTS_RAIN_DBZH_THRESHOLD,
    VPTS_RAIN_LAYER_FRACTION,
)
from .radars import load_radars
from .static_artifacts import utc_now, write_json
from .vpts import (
    commit_inventory_cursor,
    iter_vpts_record_batches_from_inventory,
    load_records,
)

MTR_FORMULA = "sum(dens_birds_km3 * ff_ms * 3.6 * layer_width_km)"
MT_FORMULA = "trapezoid_integral(mtr_birds_km_h, time_hours)"


def build_observed_products(
    *,
    input_path: Path,
    output_dir: Path,
    radars_path: Path | None = None,
    input_kind: str = "records",
    max_files: int | None = None,
    cursor_path: Path | None = None,
    altitude_min_m: float = VPTS_ALTITUDE_MIN_M,
    altitude_max_m: float = VPTS_ALTITUDE_MAX_M,
    max_integration_gap_minutes: float = VPTS_MAX_INTEGRATION_GAP_MINUTES,
) -> dict[str, object]:
    inventory: dict[str, Any] | None = None
    if input_kind == "inventory":
        inventory = json.loads(input_path.read_text(encoding="utf-8"))
        if inventory.get("ok") is not True:
            raise ValueError(
                "refusing unhealthy VPTS inventory: "
                + "; ".join(inventory.get("errors") or [])
            )
        if inventory.get("no_change") is True and not inventory.get("records"):
            return _record_no_change(output_dir, inventory)
        profiles = []
        input_row_count = 0
        for batch in iter_vpts_record_batches_from_inventory(
            input_path,
            max_files=max_files,
        ):
            input_row_count += len(batch)
            profiles.extend(
                _profiles_from_rows(
                    batch,
                    altitude_min_m=altitude_min_m,
                    altitude_max_m=altitude_max_m,
                )
            )
    else:
        rows = load_records(input_path)
        input_row_count = len(rows)
        profiles = _profiles_from_rows(
            rows,
            altitude_min_m=altitude_min_m,
            altitude_max_m=altitude_max_m,
        )
    if not profiles:
        raise ValueError("VPTS input produced no parseable profiles")

    source_max_by_radar = _source_max_dates(inventory)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for profile in profiles:
        night_date = profile.get("night_date")
        if not night_date:
            continue
        radar = str(profile["radar"])
        if source_max_by_radar and str(night_date) >= source_max_by_radar.get(radar, ""):
            # A sunset-to-sunrise night is complete only after the following
            # source date has arrived.
            continue
        groups[(radar, str(night_date))].append(profile)

    radars = {radar.slug: radar for radar in load_radars(radars_path)}
    nights = [
        _night_metrics(
            radar,
            night_date,
            values,
            radars.get(radar),
            max_integration_gap_minutes=max_integration_gap_minutes,
        )
        for (radar, night_date), values in sorted(groups.items())
    ]
    nights = [night for night in nights if night["integrated_interval_count"] > 0]
    if not nights:
        raise ValueError("VPTS input contained no complete night with integrable MTR")

    observed_radars = {str(night["radar"]) for night in nights}
    expected_processing_radars = {
        str(record.get("radar") or "")
        for record in (inventory or {}).get("records", [])
        if isinstance(record, dict) and record.get("radar")
    }
    if inventory is not None and observed_radars != expected_processing_radars:
        missing = sorted(expected_processing_radars - observed_radars)
        raise ValueError(
            f"observed radar coverage failed: expected={len(expected_processing_radars)} "
            f"observed={len(observed_radars)} missing={','.join(missing)}"
        )

    hourly_rows = _hourly_rows(profiles)
    latest_dir = output_dir / "latest"
    for night in nights:
        archive = (
            output_dir
            / "archive"
            / "observed"
            / f"year={str(night['night_date'])[:4]}"
            / f"date={night['night_date']}"
        )
        write_json(archive / f"{night['radar']}.json", night)

    latest_by_radar = _merge_latest_nights(latest_dir, nights)
    latest_nights = [latest_by_radar[key] for key in sorted(latest_by_radar)]
    latest_date = max((str(night["night_date"]) for night in latest_nights), default=None)
    generated_at = utc_now()
    provenance = _provenance(inventory, altitude_min_m, altitude_max_m, max_integration_gap_minutes)
    summary = {
        "data_available": bool(latest_nights),
        "generated_at_utc": generated_at,
        "latest_observed_date": latest_date,
        "radar_count": len(latest_nights),
        "nights": latest_nights,
        "processing_version": PROCESSING_VERSION,
        "provenance": provenance,
    }
    write_json(latest_dir / "latest_nightly_summary.json", summary)
    write_json(latest_dir / "latest_observed.geojson", _geojson(latest_nights))
    write_json(
        latest_dir / "latest_observed_hourly.json",
        {
            "data_available": bool(hourly_rows),
            "generated_at_utc": generated_at,
            "row_count": len(hourly_rows),
            "radar_count": len({str(row["radar"]) for row in hourly_rows}),
            "rows": hourly_rows,
            "processing_version": PROCESSING_VERSION,
            "provenance": provenance,
        },
    )

    previous_status = _read_json(latest_dir / "status.json")
    status = {
        **{
            key: value
            for key, value in previous_status.items()
            if key in {"latest_era5_date", "latest_bto_validation_date", "object_store_prefix"}
        },
        "data_available": True,
        "generated_at_utc": generated_at,
        "latest_vpts_date": latest_date,
        "processing_version": PROCESSING_VERSION,
        "source_health": {
            "status": "ok",
            "catalog_generated_at_utc": (inventory or {}).get("catalog_generated_at_utc"),
            "catalog_age_hours": (inventory or {}).get("catalog_age_hours"),
            "pulse_policy": VPTS_PULSE_POLICY,
        },
        "quality_summary": {
            "radar_count": len(latest_nights),
            "night_count_built": len(nights),
            "latest_radar_count": len(latest_nights),
            "profile_count": len(profiles),
            "hourly_row_count": len(hourly_rows),
            "input_row_count": input_row_count,
        },
    }
    write_json(latest_dir / "status.json", status)

    if inventory is not None and cursor_path is not None:
        commit_inventory_cursor(input_path, cursor_path)

    return {
        "ok": True,
        "no_change": False,
        "input_rows": input_row_count,
        "profile_count": len(profiles),
        "hourly_row_count": len(hourly_rows),
        "night_count": len(nights),
        "radar_count": len(latest_nights),
        "latest_observed_date": latest_date,
        "output_dir": str(output_dir),
    }


def build_hourly_observations(
    *,
    inventory_path: Path,
    output: Path,
    max_files: int | None = None,
    altitude_min_m: float = VPTS_ALTITUDE_MIN_M,
    altitude_max_m: float = VPTS_ALTITUDE_MAX_M,
) -> dict[str, object]:
    """Build an all-hour VPTS table for ERA5 model fitting.

    This intentionally does not derive, select, or exclude a solar period,
    migration season, or phenological phase.  It processes one VPTS object at
    a time and retains LP and SP as independent observations, so a full-year
    archive stays bounded in memory while preserving the model contract.
    """

    inventory = _read_json(inventory_path)
    if inventory.get("ok") is not True:
        raise ValueError("refusing unhealthy VPTS inventory: " + "; ".join(inventory.get("errors") or []))
    hourly_rows: list[dict[str, object]] = []
    input_row_count = 0
    file_count = 0
    for batch in iter_vpts_record_batches_from_inventory(inventory_path, max_files=max_files):
        file_count += 1
        input_row_count += len(batch)
        profiles = _profiles_from_rows(
            batch,
            altitude_min_m=altitude_min_m,
            altitude_max_m=altitude_max_m,
        )
        hourly_rows.extend(_hourly_rows(profiles, include_phenology=False))
        if file_count % 100 == 0:
            print(
                json.dumps(
                    {
                        "event": "hourly_vpts_progress",
                        "files_processed": file_count,
                        "input_rows": input_row_count,
                        "hourly_rows": len(hourly_rows),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    if not hourly_rows:
        raise ValueError("VPTS archive produced no parseable hourly profiles")
    hourly_rows.sort(key=lambda row: (str(row["radar"]), str(row["pulse"]), str(row["time_utc"])))
    payload = {
        "schema_version": "birdcast-uk-hourly-vpts-1.0",
        "data_available": True,
        "generated_at_utc": utc_now(),
        "processing_version": PROCESSING_VERSION,
        "source_inventory": str(inventory_path),
        "window": inventory.get("window"),
        "pulse_policy": inventory.get("pulse_policy") or VPTS_PULSE_POLICY,
        "analysis_policy": {
            "cadence": "UTC hourly",
            "daylight_filter": "none",
            "twilight_filter": "none",
            "season_filter": "none",
            "phenology_filter": "none",
        },
        "quality_policy": {
            "altitude_min_m": altitude_min_m,
            "altitude_max_m": altitude_max_m,
            "rain_suspect_profiles_excluded_from_mtr_mean": True,
        },
        "file_count": file_count,
        "input_row_count": input_row_count,
        "row_count": len(hourly_rows),
        "radar_count": len({str(row["radar"]) for row in hourly_rows}),
        "pulse_counts": {
            pulse: sum(str(row["pulse"]) == pulse for row in hourly_rows)
            for pulse in ("lp", "sp")
        },
        "first_time_utc": min(str(row["time_utc"]) for row in hourly_rows),
        "last_time_utc": max(str(row["time_utc"]) for row in hourly_rows),
        "rows": hourly_rows,
    }
    write_json(output, payload)
    return {key: value for key, value in payload.items() if key != "rows"}


def _profiles_from_rows(
    rows: list[dict[str, Any]],
    *,
    altitude_min_m: float,
    altitude_max_m: float,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        radar = str(row.get("radar") or "")
        timestamp = _timestamp(row)
        if not radar or timestamp is None:
            continue
        pulse = str(row.get("pulse") or "unknown")
        grouped[(radar, _format_datetime(timestamp), pulse)].append(row)

    profiles = []
    for (radar, _, pulse), values in sorted(grouped.items()):
        timestamp = _timestamp(values[0])
        if timestamp is None:
            continue
        if any(_number(row, "dens") is not None for row in values):
            profile = _profile_from_layers(
                radar,
                pulse,
                timestamp,
                values,
                altitude_min_m=altitude_min_m,
                altitude_max_m=altitude_max_m,
            )
        else:
            profile = _profile_from_summary(radar, pulse, timestamp, values[0])
        profiles.append(profile)
    return profiles


def _profile_from_layers(
    radar: str,
    pulse: str,
    timestamp: datetime,
    rows: list[dict[str, Any]],
    *,
    altitude_min_m: float,
    altitude_max_m: float,
) -> dict[str, Any]:
    selected: list[tuple[dict[str, Any], float]] = []
    heights = []
    for row in rows:
        height = _number(row, "height", "height_m", "altitude_m")
        if height is None or height < altitude_min_m or height > altitude_max_m:
            continue
        selected.append((row, height))
        heights.append(height)
    layer_width_km = _median_layer_width_km(heights)

    density_layer_count = 0
    mtr_layer_count = 0
    gap_layer_count = 0
    vid = 0.0
    mtr = 0.0
    height_weight_sum = 0.0
    speed_weight_sum = 0.0
    speed_weight_denominator = 0.0
    direction_values: list[float] = []
    direction_weights: list[float] = []
    rain_bins = 0
    rain_positive_bins = 0

    for row, height in selected:
        gap = _boolean(row.get("gap"))
        if gap is True:
            gap_layer_count += 1
            continue
        dbzh = _number(row, "DBZH", "dbz")
        if dbzh is not None and height <= 2000.0:
            rain_bins += 1
            if dbzh > VPTS_RAIN_DBZH_THRESHOLD:
                rain_positive_bins += 1
        density = _number(row, "dens", "density")
        if density is None or density < 0:
            continue
        density_layer_count += 1
        density_weight = density * layer_width_km
        vid += density_weight
        height_weight_sum += height * density_weight
        speed = _number(row, "ff", "ground_speed_ms", "speed")
        if speed is None or speed < 0:
            continue
        mtr_layer_count += 1
        layer_mtr = density * (speed * 3.6) * layer_width_km
        mtr += layer_mtr
        speed_weight_sum += speed * density_weight
        speed_weight_denominator += density_weight
        direction = _number(row, "dd", "direction_deg", "direction")
        if direction is not None and layer_mtr > 0:
            direction_values.append(direction)
            direction_weights.append(layer_mtr)

    rain_fraction = rain_positive_bins / rain_bins if rain_bins else 0.0
    rain_suspect = rain_bins >= 5 and rain_fraction >= VPTS_RAIN_LAYER_FRACTION
    if density_layer_count == 0:
        profile_mtr: float | None = None
    elif vid == 0:
        profile_mtr = 0.0
    elif mtr_layer_count == 0 or rain_suspect:
        profile_mtr = None
    else:
        profile_mtr = mtr

    first = rows[0]
    latitude = _number(first, "radar_latitude", "latitude")
    longitude = _number(first, "radar_longitude", "longitude")
    sunrise = _timestamp_value(first.get("sunrise"))
    sunset = _timestamp_value(first.get("sunset"))
    is_day = _day_state(first, timestamp, sunrise, sunset, longitude)
    return {
        "radar": radar,
        "pulse": pulse,
        "time_utc": _format_datetime(timestamp),
        "timestamp": timestamp,
        "night_date": _night_date(timestamp, is_day, sunrise, sunset, longitude),
        "is_day": is_day,
        "sunrise_utc": _format_datetime(sunrise) if sunrise else None,
        "sunset_utc": _format_datetime(sunset) if sunset else None,
        "latitude": latitude,
        "longitude": longitude,
        "vid_birds_per_km2": vid if density_layer_count else None,
        "mtr_birds_km_h": profile_mtr,
        "mean_ground_speed_ms": (
            speed_weight_sum / speed_weight_denominator
            if speed_weight_denominator > 0
            else None
        ),
        "dominant_direction_deg": _weighted_circular_mean(direction_values, direction_weights),
        "density_weighted_mean_height_m": height_weight_sum / vid if vid > 0 else None,
        "density_layer_count": density_layer_count,
        "mtr_layer_count": mtr_layer_count,
        "gap_layer_count": gap_layer_count,
        "layer_width_km": layer_width_km,
        "rain_suspect": rain_suspect,
        "rain_layer_fraction": rain_fraction,
        "source_key": str(first.get("source_key") or ""),
        "source_etag": str(first.get("source_etag") or ""),
    }


def _profile_from_summary(
    radar: str,
    pulse: str,
    timestamp: datetime,
    row: dict[str, Any],
) -> dict[str, Any]:
    sunrise = _timestamp_value(row.get("sunrise"))
    sunset = _timestamp_value(row.get("sunset"))
    longitude = _number(row, "longitude", "radar_longitude")
    is_day = _day_state(row, timestamp, sunrise, sunset, longitude)
    return {
        "radar": radar,
        "pulse": pulse,
        "time_utc": _format_datetime(timestamp),
        "timestamp": timestamp,
        "night_date": str(row.get("night_date") or "")
        or _night_date(timestamp, is_day, sunrise, sunset, longitude),
        "is_day": is_day,
        "sunrise_utc": _format_datetime(sunrise) if sunrise else None,
        "sunset_utc": _format_datetime(sunset) if sunset else None,
        "latitude": _number(row, "latitude", "radar_latitude"),
        "longitude": longitude,
        "vid_birds_per_km2": _number(row, "vid", "vid_birds_per_km2"),
        "mtr_birds_km_h": _number(
            row,
            "mtr",
            "MTR",
            "migration_traffic_rate",
            "traffic_rate",
        ),
        "mean_ground_speed_ms": _number(row, "ground_speed_ms", "speed", "ff"),
        "dominant_direction_deg": _number(row, "direction_deg", "direction", "dd"),
        "density_weighted_mean_height_m": _number(row, "height_m", "height"),
        "density_layer_count": 0,
        "mtr_layer_count": 0,
        "gap_layer_count": 0,
        "layer_width_km": None,
        "rain_suspect": False,
        "rain_layer_fraction": 0.0,
        "source_key": str(row.get("source_key") or ""),
        "source_etag": str(row.get("source_etag") or ""),
    }


def _night_metrics(
    radar: str,
    night_date: str,
    profiles: list[dict[str, Any]],
    site: object | None,
    *,
    max_integration_gap_minutes: float,
) -> dict[str, object]:
    ordered = sorted(profiles, key=lambda item: item["timestamp"])
    valid = [
        profile
        for profile in ordered
        if _finite(profile.get("mtr_birds_km_h")) and not profile.get("rain_suspect")
    ]
    interval_count = 0
    integrated_hours = 0.0
    migration_traffic = 0.0
    for left, right in zip(valid, valid[1:]):
        delta_hours = (right["timestamp"] - left["timestamp"]).total_seconds() / 3600.0
        if delta_hours <= 0 or delta_hours * 60.0 > max_integration_gap_minutes:
            continue
        migration_traffic += (
            (float(left["mtr_birds_km_h"]) + float(right["mtr_birds_km_h"]))
            / 2.0
            * delta_hours
        )
        integrated_hours += delta_hours
        interval_count += 1

    expected_hours, sunset_utc, sunrise_utc = _expected_night_window(night_date, ordered)
    coverage = (
        min(1.0, integrated_hours / expected_hours)
        if expected_hours and expected_hours > 0
        else None
    )
    mean_mtr = migration_traffic / integrated_hours if integrated_hours > 0 else None
    mtr_values = [float(profile["mtr_birds_km_h"]) for profile in valid]
    speed_values = [
        float(profile["mean_ground_speed_ms"])
        for profile in valid
        if _finite(profile.get("mean_ground_speed_ms"))
    ]
    heights = [
        float(profile["density_weighted_mean_height_m"])
        for profile in valid
        if _finite(profile.get("density_weighted_mean_height_m"))
    ]
    directions = [
        float(profile["dominant_direction_deg"])
        for profile in valid
        if _finite(profile.get("dominant_direction_deg"))
    ]
    direction_weights = [
        float(profile["mtr_birds_km_h"])
        for profile in valid
        if _finite(profile.get("dominant_direction_deg"))
    ]
    rain_count = sum(bool(profile.get("rain_suspect")) for profile in ordered)
    if coverage is not None and coverage >= 0.75 and interval_count >= 2:
        quality_class = "good"
    elif interval_count > 0:
        quality_class = "partial"
    else:
        quality_class = "insufficient"
    latitude = getattr(site, "latitude", None)
    longitude = getattr(site, "longitude", None)
    if latitude is None:
        latitude = next((profile.get("latitude") for profile in ordered if profile.get("latitude") is not None), None)
    if longitude is None:
        longitude = next((profile.get("longitude") for profile in ordered if profile.get("longitude") is not None), None)
    return {
        "radar": radar,
        "night_date": night_date,
        "profile_count": len(ordered),
        "usable_profile_count": len(valid),
        "integrated_interval_count": interval_count,
        "integrated_hours": round(integrated_hours, 6),
        "expected_night_hours": round(expected_hours, 6) if expected_hours is not None else None,
        "sunset_utc": sunset_utc,
        "sunrise_utc": sunrise_utc,
        "migration_traffic_birds_per_km": round(migration_traffic, 6),
        "mean_mtr_birds_km_h": round(mean_mtr, 6) if mean_mtr is not None else None,
        "peak_mtr_birds_km_h": round(max(mtr_values), 6) if mtr_values else None,
        "mean_ground_speed_ms": _rounded_mean(speed_values),
        "dominant_direction_deg": _weighted_circular_mean(directions, direction_weights),
        "mean_flight_height_m": _rounded_mean(heights),
        "coverage_fraction": round(coverage, 6) if coverage is not None else None,
        "rain_contamination_fraction": round(rain_count / len(ordered), 6) if ordered else None,
        "quality_class": quality_class,
        "intensity_class": _intensity_class(mean_mtr),
        "pulse_products": sorted({str(profile["pulse"]) for profile in ordered}),
        "pulse_policy": VPTS_PULSE_POLICY,
        "source_keys": sorted({str(profile["source_key"]) for profile in ordered if profile.get("source_key")}),
        "latitude": latitude,
        "longitude": longitude,
        "processing_version": PROCESSING_VERSION,
        "units": {
            "migration_traffic_birds_per_km": "birds km-1",
            "mean_mtr_birds_km_h": "birds km-1 h-1",
            "peak_mtr_birds_km_h": "birds km-1 h-1",
        },
    }


def _hourly_rows(
    profiles: list[dict[str, Any]],
    *,
    include_phenology: bool = True,
) -> list[dict[str, object]]:
    """Aggregate VPTS profiles by radar, pulse mode, and UTC hour.

    LP and SP have different sampling characteristics.  Keeping them separate
    here is essential: downstream reanalysis models must never be trained on
    a silently blended pulse product.
    """

    grouped: dict[tuple[str, str, datetime], list[dict[str, Any]]] = defaultdict(list)
    for profile in profiles:
        timestamp = profile["timestamp"].replace(minute=0, second=0, microsecond=0)
        grouped[(str(profile["radar"]), str(profile.get("pulse") or "unknown"), timestamp)].append(profile)
    rows = []
    for (radar, pulse, hour), values in sorted(grouped.items()):
        valid_mtr = [
            float(value["mtr_birds_km_h"])
            for value in values
            if _finite(value.get("mtr_birds_km_h")) and not value.get("rain_suspect")
        ]
        valid_vid = [
            float(value["vid_birds_per_km2"])
            for value in values
            if _finite(value.get("vid_birds_per_km2"))
        ]
        speeds = [
            float(value["mean_ground_speed_ms"])
            for value in values
            if _finite(value.get("mean_ground_speed_ms"))
        ]
        directions = [
            float(value["dominant_direction_deg"])
            for value in values
            if _finite(value.get("dominant_direction_deg"))
        ]
        direction_weights = [
            float(value["mtr_birds_km_h"])
            for value in values
            if _finite(value.get("dominant_direction_deg"))
            and _finite(value.get("mtr_birds_km_h"))
        ]
        row: dict[str, object] = {
            "radar": radar,
            "pulse": pulse,
            "time_utc": _format_datetime(hour),
            "profile_count": len(values),
            "usable_mtr_profile_count": len(valid_mtr),
            "mean_mtr_birds_km_h": _rounded_mean(valid_mtr),
            "mean_vid_birds_per_km2": _rounded_mean(valid_vid),
            "mean_ground_speed_ms": _rounded_mean(speeds),
            "dominant_direction_deg": _weighted_circular_mean(directions, direction_weights),
            "rain_suspect_fraction": round(
                sum(bool(value.get("rain_suspect")) for value in values) / len(values),
                6,
            ),
            "pulse_products": [pulse],
        }
        if include_phenology:
            row["night_profile_fraction"] = round(
                sum(bool(value.get("night_date")) for value in values) / len(values),
                6,
            )
        rows.append(row)
    return rows


def _source_max_dates(inventory: dict[str, Any] | None) -> dict[str, str]:
    if not inventory:
        return {}
    proposed = inventory.get("proposed_cursor")
    if not isinstance(proposed, dict):
        return {}
    radars = proposed.get("radars")
    if not isinstance(radars, dict):
        return {}
    return {
        str(radar): str(value.get("source_date") or "")
        for radar, value in radars.items()
        if isinstance(value, dict)
    }


def _merge_latest_nights(
    latest_dir: Path,
    new_nights: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    existing = _read_json(latest_dir / "latest_nightly_summary.json")
    latest: dict[str, dict[str, object]] = {}
    for night in existing.get("nights", []):
        if isinstance(night, dict) and night.get("radar") and night.get("night_date"):
            latest[str(night["radar"])] = night
    for night in new_nights:
        radar = str(night["radar"])
        current = latest.get(radar)
        if current is None or str(night["night_date"]) >= str(current["night_date"]):
            latest[radar] = night
    return latest


def _record_no_change(output_dir: Path, inventory: dict[str, Any]) -> dict[str, object]:
    status_path = output_dir / "latest" / "status.json"
    status = _read_json(status_path)
    if not status.get("data_available"):
        raise ValueError("inventory reports no change but no observed product exists")
    status["generated_at_utc"] = utc_now()
    status["source_health"] = {
        "status": "up_to_date",
        "catalog_generated_at_utc": inventory.get("catalog_generated_at_utc"),
        "catalog_age_hours": inventory.get("catalog_age_hours"),
        "pulse_policy": VPTS_PULSE_POLICY,
    }
    write_json(status_path, status)
    return {
        "ok": True,
        "no_change": True,
        "input_rows": 0,
        "night_count": 0,
        "radar_count": status.get("quality_summary", {}).get("radar_count", 0),
        "latest_observed_date": status.get("latest_vpts_date"),
        "output_dir": str(output_dir),
    }


def _provenance(
    inventory: dict[str, Any] | None,
    altitude_min_m: float,
    altitude_max_m: float,
    max_gap_minutes: float,
) -> dict[str, object]:
    return {
        "source_catalog_url": (inventory or {}).get("catalog_url"),
        "source_catalog_generated_at_utc": (inventory or {}).get("catalog_generated_at_utc"),
        "source_object_prefix": (inventory or {}).get("object_prefix"),
        "pulse_policy": VPTS_PULSE_POLICY,
        "altitude_min_m": altitude_min_m,
        "altitude_max_m": altitude_max_m,
        "gap_filled_layers": "excluded",
        "rain_qc": (
            f"reject profile when at least {VPTS_RAIN_LAYER_FRACTION:.0%} of finite "
            f"DBZH layers at or below 2000 m exceed {VPTS_RAIN_DBZH_THRESHOLD:g} dBZ"
        ),
        "mtr_formula": MTR_FORMULA,
        "nightly_migration_traffic_formula": MT_FORMULA,
        "max_integration_gap_minutes": max_gap_minutes,
    }


def _expected_night_window(
    night_date: str,
    profiles: list[dict[str, Any]],
) -> tuple[float | None, str | None, str | None]:
    target = datetime.strptime(night_date, "%Y%m%d").date()
    sunset_values = []
    sunrise_values = []
    for profile in profiles:
        sunset = _timestamp_value(profile.get("sunset_utc"))
        sunrise = _timestamp_value(profile.get("sunrise_utc"))
        if sunset and sunset.date() == target:
            sunset_values.append(sunset)
        if sunrise and sunrise.date() == target + timedelta(days=1):
            sunrise_values.append(sunrise)
    sunset = min(sunset_values) if sunset_values else None
    sunrise = max(sunrise_values) if sunrise_values else None
    if sunset and sunrise and sunrise > sunset:
        return (
            (sunrise - sunset).total_seconds() / 3600.0,
            _format_datetime(sunset),
            _format_datetime(sunrise),
        )
    return None, _format_datetime(sunset) if sunset else None, _format_datetime(sunrise) if sunrise else None


def _day_state(
    row: dict[str, Any],
    timestamp: datetime,
    sunrise: datetime | None,
    sunset: datetime | None,
    longitude: float | None,
) -> bool:
    explicit = _boolean(row.get("day"))
    if explicit is not None:
        return explicit
    if sunrise and sunset:
        return sunrise <= timestamp <= sunset
    local_solar = timestamp + timedelta(minutes=4 * (longitude or 0.0))
    return 6 <= local_solar.hour < 18


def _night_date(
    timestamp: datetime,
    is_day: bool,
    sunrise: datetime | None,
    sunset: datetime | None,
    longitude: float | None,
) -> str | None:
    if is_day:
        return None
    if sunrise and timestamp <= sunrise:
        value = timestamp.date() - timedelta(days=1)
    elif sunset and timestamp >= sunset:
        value = timestamp.date()
    else:
        local_solar = timestamp + timedelta(minutes=4 * (longitude or 0.0))
        value = local_solar.date() - timedelta(days=1) if local_solar.hour < 12 else local_solar.date()
    return value.strftime("%Y%m%d")


def _median_layer_width_km(heights: list[float]) -> float:
    unique = sorted(set(heights))
    differences = [right - left for left, right in zip(unique, unique[1:]) if right > left]
    width_m = median(differences) if differences else 200.0
    return width_m / 1000.0


def _timestamp(row: dict[str, Any]) -> datetime | None:
    for key in ("timestamp_utc", "datetime", "time", "datetime_utc", "date_time"):
        parsed = _timestamp_value(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _timestamp_value(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(row: dict[str, Any], *names: str) -> float | None:
    for name in names:
        value = row.get(name)
        if value in (None, ""):
            continue
        text = str(value).strip()
        if text.upper() == "NA":
            continue
        try:
            result = float(text)
        except (TypeError, ValueError):
            continue
        if math.isfinite(result):
            return result
    return None


def _boolean(value: object) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "t", "1", "yes", "y"}:
        return True
    if text in {"false", "f", "0", "no", "n"}:
        return False
    return None


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _rounded_mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _weighted_circular_mean(values: list[float], weights: list[float]) -> float | None:
    if not values or len(values) != len(weights):
        return None
    sin_sum = sum(math.sin(math.radians(value)) * weight for value, weight in zip(values, weights))
    cos_sum = sum(math.cos(math.radians(value)) * weight for value, weight in zip(values, weights))
    if sin_sum == 0 and cos_sum == 0:
        return None
    return round((math.degrees(math.atan2(sin_sum, cos_sum)) + 360.0) % 360.0, 6)


def _intensity_class(mean_mtr: float | None) -> str:
    if mean_mtr is None:
        return "missing"
    if mean_mtr <= 0:
        return "none"
    if mean_mtr < 100:
        return "low"
    if mean_mtr < 500:
        return "moderate"
    if mean_mtr < 1000:
        return "high"
    return "very_high"


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _geojson(nights: list[dict[str, object]]) -> dict[str, object]:
    features = []
    for night in nights:
        latitude = night.get("latitude")
        longitude = night.get("longitude")
        if latitude is None or longitude is None:
            continue
        properties = {
            key: value for key, value in night.items() if key not in {"latitude", "longitude"}
        }
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
                "properties": properties,
            }
        )
    return {
        "type": "FeatureCollection",
        "features": features,
        "generated_at_utc": utc_now(),
        "properties": {
            "data_available": bool(features),
            "processing_version": PROCESSING_VERSION,
            "metric": "migration_traffic_birds_per_km",
        },
    }
