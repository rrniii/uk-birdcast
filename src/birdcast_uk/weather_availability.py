"""Read public CDS coverage before requesting unpublished ERA5T inputs.

Catalogue dates are an availability hint, not proof of complete weather. The
model cycle independently requires all 24 hours and every selected predictor.
"""

from datetime import date, datetime, timedelta, timezone

from .vpts import fetch_json

DATASETS = ("reanalysis-era5-single-levels", "reanalysis-era5-pressure-levels")
CATALOGUE_BASE = "https://cds.climate.copernicus.eu/api/catalogue/v1/collections"


def available_weather_through(*, now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    ends = []
    for dataset in DATASETS:
        payload = fetch_json(f"{CATALOGUE_BASE}/{dataset}")
        intervals = payload["extent"]["temporal"]["interval"]
        if payload.get("id") != dataset or len(intervals) != 1 or not intervals[0][1]:
            raise ValueError(f"Ambiguous CDS coverage for {dataset}")
        end = datetime.fromisoformat(intervals[0][1].replace("Z", "+00:00"))
        if end.tzinfo is None or end > now + timedelta(minutes=5):
            raise ValueError(f"Invalid CDS coverage timestamp for {dataset}")
        ends.append(end.astimezone(timezone.utc).date())
    return min(ends)
