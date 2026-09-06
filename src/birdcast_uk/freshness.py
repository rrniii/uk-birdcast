"""Read-only operational checks against published data, not web build times.

These thresholds are retrospective publication service levels. They do not
constitute, or enable, the separate forecasting data-latency contract.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import DEFAULT_PUBLIC_BASE_URL, OBJECT_PREFIX, UKMO_VPTS_CATALOG_URL
from .radars import DEFAULT_RADARS, load_radars
from .selected_model import RETROSPECTIVE_LAG_DAYS, SELECTION_ID
from .static_artifacts import write_json
from .vpts import fetch_json


def catalog_window(catalog: dict, radars: set[str], now: datetime) -> tuple[datetime, date]:
    """Find the common advertised UTC end, requiring every configured radar."""

    generated = datetime.fromisoformat(str(catalog["generated_at"]).replace("Z", "+00:00"))
    if generated.tzinfo is None or generated > now + timedelta(minutes=5):
        raise ValueError("Source catalogue has an invalid or future generation time")
    records = catalog.get("radars", [])
    by_radar = {row["radar"]: row for row in records if row.get("radar") in radars}
    if set(by_radar) != radars or len(by_radar) != sum(
        row.get("radar") in radars for row in records
    ):
        raise ValueError("Source catalogue has missing or duplicate configured radars")
    ends = [datetime.strptime(str(row["last_date"]), "%Y%m%d").date() for row in by_radar.values()]
    if any(day > now.date() for day in ends):
        raise ValueError("Source catalogue includes a future UTC day")
    return generated, min(*ends, now.date() - timedelta(days=1))


def evaluate_freshness(
    catalog: dict,
    historical: dict,
    model: dict,
    forecast: dict,
    *,
    now: datetime | None = None,
    radars: set[str] | None = None,
    max_catalog_age_hours: float = 72,
    max_source_age_days: int = 7,
    max_publication_lag_days: int = 2,
    model_weather_lag_days: int = RETROSPECTIVE_LAG_DAYS,
) -> dict:
    """Report source delay separately from an avoidable publication backlog."""

    now = now or datetime.now(timezone.utc)
    radars = radars or {radar.slug for radar in DEFAULT_RADARS}
    generated, source_end = catalog_window(catalog, radars, now)
    # UK local solar days can use profiles from the following UTC source file.
    expected_end = source_end - timedelta(days=1)
    published_end = date.fromisoformat(str(historical.get("latest_date") or ""))
    coverage = {row["radar"]: row["last_date"] for row in historical.get("radar_coverage", [])}
    catalog_age = (now - generated).total_seconds() / 3600
    source_age = (now.date() - source_end).days
    lag = max(0, (expected_end - published_end).days)
    model_end = date.fromisoformat(str(model.get("latest_time_utc") or "")[:10])
    expected_model_end = now.date() - timedelta(days=model_weather_lag_days)
    model_lag = max(0, (expected_model_end - model_end).days)
    checks = {
        "catalogue_recent": catalog_age <= max_catalog_age_hours,
        "source_recent": source_age <= max_source_age_days,
        "publication_caught_up": lag <= max_publication_lag_days,
        "historical_available": historical.get("data_available") is True,
        "all_radars_reach_publication_date": all(
            coverage.get(radar, "") >= published_end.isoformat() for radar in radars
        ),
        "publication_date_not_future": published_end < now.date(),
        "selected_model_available": model.get("data_available") is True
        and model.get("selection_id") == SELECTION_ID,
        "model_publication_caught_up": model_lag <= max_publication_lag_days,
        "model_date_not_future": model_end < now.date(),
        "forecast_disabled": forecast.get("data_available") is False
        and forecast.get("mode") == "disabled"
        and forecast.get("valid_times_utc") in (None, []),
    }
    messages = {
        "catalogue_recent": "Source catalogue has not refreshed within 72 hours.",
        "source_recent": "Radar source data is more than 7 days behind UTC today.",
        "publication_caught_up": "Published observations lag the complete source window.",
        "historical_available": "Historical observations are unavailable.",
        "all_radars_reach_publication_date": "Some radars do not reach the published end date.",
        "publication_date_not_future": "Published observation date is unfinished or in the future.",
        "selected_model_available": "The qualified selected-model release is unavailable.",
        "model_publication_caught_up": "Modelled migration lags the expected complete ERA5 window.",
        "model_date_not_future": "Published model date is unfinished or in the future.",
        "forecast_disabled": "Forecast fail-closed policy is not satisfied.",
    }
    return {
        "schema_version": "birdcast-uk-freshness-1.0",
        "checked_at_utc": now.isoformat().replace("+00:00", "Z"),
        "ok": all(checks.values()),
        "status": "ok" if all(checks.values()) else "stale",
        "published_through": published_end.isoformat(),
        "source_common_utc_through": source_end.isoformat(),
        "expected_observations_through": expected_end.isoformat(),
        "publication_lag_days": lag,
        "source_age_days": source_age,
        "catalog_age_hours": round(catalog_age, 2),
        "model_through": model.get("latest_time_utc"),
        "model_policy": "daily_frozen_model_retrospective_not_forecast",
        "expected_model_through": expected_model_end.isoformat(),
        "model_publication_lag_days": model_lag,
        "thresholds": {
            "max_catalog_age_hours": max_catalog_age_hours,
            "max_source_age_days": max_source_age_days,
            "max_publication_lag_days": max_publication_lag_days,
            "model_weather_lag_days": model_weather_lag_days,
        },
        "checks": checks,
        "alerts": [messages[name] for name, passed in checks.items() if not passed],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--catalog-url", default=UKMO_VPTS_CATALOG_URL)
    parser.add_argument("--public-base-url", default=DEFAULT_PUBLIC_BASE_URL)
    parser.add_argument("--object-prefix", default=OBJECT_PREFIX)
    parser.add_argument("--radars", type=Path)
    args = parser.parse_args()
    base = f"{args.public_base_url.rstrip('/')}/{args.object_prefix.strip('/')}"
    try:
        report = evaluate_freshness(
            fetch_json(args.catalog_url),
            fetch_json(f"{base}/latest/historical.json"),
            fetch_json(f"{base}/latest/gam-era5.json"),
            fetch_json(f"{base}/latest/forecast.json"),
            radars={radar.slug for radar in load_radars(args.radars)},
        )
    except Exception as exc:
        # Replace a formerly green report on failure; never preserve false health.
        report = {
            "schema_version": "birdcast-uk-freshness-1.0",
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "status": "error",
            "alerts": [f"Freshness verification failed: {type(exc).__name__}: {exc}"],
        }
    write_json(args.output, report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
