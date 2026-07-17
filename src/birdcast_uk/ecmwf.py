"""Archive operational ECMWF Open Data cycles through Earthkit."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .config import (
    ECMWF_OPEN_DATA_PRESSURE_LEVELS,
    ECMWF_OPEN_DATA_PRESSURE_PARAMETERS,
    ECMWF_OPEN_DATA_SURFACE_PARAMETERS,
    FORECAST_HORIZON_HOURS,
    FORECAST_STEP_HOURS,
    UK_ERA5_AREA,
)
from .static_artifacts import utc_now, write_json


def normalise_cycle(cycle: str | None = None) -> datetime:
    if cycle:
        parsed = datetime.fromisoformat(cycle.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed.hour not in (0, 6, 12, 18) or parsed.minute or parsed.second:
            raise ValueError("ECMWF cycle must be 00, 06, 12, or 18 UTC")
        return parsed.astimezone(timezone.utc)
    try:
        from ecmwf.opendata import Client

        latest = Client(source="ecmwf", model="ifs", resol="0p25").latest()
        return latest.replace(tzinfo=timezone.utc)
    except Exception:
        now = datetime.now(timezone.utc) - timedelta(hours=12)
        hour = max(value for value in (0, 6, 12, 18) if value <= now.hour)
        return now.replace(hour=hour, minute=0, second=0, microsecond=0)


def open_data_requests(cycle: datetime) -> list[dict[str, Any]]:
    steps = list(range(0, FORECAST_HORIZON_HOURS + 1, FORECAST_STEP_HOURS))
    common = {
        "date": cycle.strftime("%Y%m%d"),
        "time": cycle.hour,
        "type": "fc",
        "stream": "oper",
        "step": steps,
    }
    return [
        {**common, "levtype": "sfc", "param": list(ECMWF_OPEN_DATA_SURFACE_PARAMETERS)},
        {
            **common,
            "levtype": "pl",
            "levelist": list(ECMWF_OPEN_DATA_PRESSURE_LEVELS),
            "param": list(ECMWF_OPEN_DATA_PRESSURE_PARAMETERS),
        },
    ]


def archive_cycle(output_root: Path, *, cycle: str | None = None, overwrite: bool = False) -> dict[str, object]:
    selected = normalise_cycle(cycle)
    stamp = selected.strftime("%Y%m%dT%H00Z")
    cycle_dir = output_root / stamp
    manifest_path = cycle_dir / "manifest.json"
    if manifest_path.exists() and not overwrite:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("status") == "complete":
            return existing

    cycle_dir.mkdir(parents=True, exist_ok=True)
    requests = open_data_requests(selected)
    files = []
    try:
        import earthkit.data as ekd

        for index, request in enumerate(requests):
            kind = "surface" if index == 0 else "pressure"
            destination = cycle_dir / f"ecmwf_{stamp}_{kind}.grib2"
            data = ekd.from_source(
                "ecmwf-open-data",
                source="ecmwf",
                model="ifs",
                resol="0p25",
                request=request,
            )
            data.to_target("file", str(destination))
            files.append(
                {
                    "kind": kind,
                    "path": str(destination),
                    "size": destination.stat().st_size,
                    "sha256": _sha256(destination),
                }
            )
        status = "complete"
        error = None
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"

    payload = {
        "schema_version": "birdcast-uk-ecmwf-cycle-1.0",
        "generated_at_utc": utc_now(),
        "cycle_time_utc": selected.isoformat().replace("+00:00", "Z"),
        "source": "ECMWF Open Data via earthkit-data",
        "licence": "CC-BY-4.0",
        "area": UK_ERA5_AREA,
        "requests": requests,
        "files": files,
        "status": status,
        "error": error,
    }
    write_json(manifest_path, payload)
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
