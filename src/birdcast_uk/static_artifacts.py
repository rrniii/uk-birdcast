"""Build static BirdCast UK placeholder and status artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
from importlib import resources
import json
import os
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .config import OBJECT_PREFIX, PROCESSING_VERSION
from .radars import load_radars, radar_records


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with NamedTemporaryFile(
        "w",
        dir=path.parent,
        encoding="utf-8",
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(content)
        temporary_path = Path(handle.name)
    os.replace(temporary_path, path)


def write_placeholder_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a placeholder unless a data-bearing artifact already exists."""

    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
        if isinstance(existing, dict) and (
            existing.get("data_available") is True
            or existing.get("latest_vpts_date")
            or existing.get("latest_observed_date")
        ):
            return
    write_json(path, payload)


def build_static_artifacts(
    output_dir: Path,
    *,
    public_base_url: str,
    object_prefix: str = OBJECT_PREFIX,
    radars_path: Path | None = None,
) -> dict[str, object]:
    """Create placeholder artifacts and a static web shell.

    The resulting directory is intentionally object-store shaped so it can be
    synced under the configured prefix without a separate packaging step.
    """

    generated_at = utc_now()
    radars = radar_records(tuple(load_radars(radars_path)))
    data_base_url = "/".join(part.strip("/") for part in (public_base_url, object_prefix) if part.strip("/"))

    latest_dir = output_dir / "latest"
    web_dir = output_dir / "web"
    archive_dir = output_dir / "archive"
    bto_dir = output_dir / "validation" / "bto"
    era5_dir = output_dir / "era5"

    write_placeholder_json(
        latest_dir / "status.json",
        {
            "data_available": False,
            "generated_at_utc": generated_at,
            "latest_bto_validation_date": None,
            "latest_era5_date": None,
            "latest_vpts_date": None,
            "object_store_prefix": object_prefix,
            "processing_version": PROCESSING_VERSION,
            "quality_summary": {
                "status": "placeholder",
                "message": "VPTS/bioRad data are not available yet.",
            },
            "source_dataset_version": None,
        },
    )
    write_json(latest_dir / "radars.json", {"generated_at_utc": generated_at, "radars": radars})
    write_placeholder_json(
        latest_dir / "latest_observed.geojson",
        {
            "type": "FeatureCollection",
            "features": [],
            "generated_at_utc": generated_at,
            "properties": {
                "data_available": False,
                "processing_version": PROCESSING_VERSION,
            },
        },
    )
    write_placeholder_json(
        latest_dir / "latest_nightly_summary.json",
        {
            "data_available": False,
            "generated_at_utc": generated_at,
            "nights": [],
            "processing_version": PROCESSING_VERSION,
        },
    )
    write_placeholder_json(
        latest_dir / "era5_status.json",
        {
            "generated_at_utc": generated_at,
            "latest_era5_date": None,
            "processing_version": PROCESSING_VERSION,
            "status": "not_started",
        },
    )
    write_json(
        latest_dir / "validation_status.json",
        {
            "bto_data_available": False,
            "generated_at_utc": generated_at,
            "latest_bto_validation_date": None,
            "processing_version": PROCESSING_VERSION,
            "status": "request_pending",
        },
    )
    write_json(archive_dir / ".keep.json", {"generated_at_utc": generated_at, "purpose": "archive prefix placeholder"})
    write_json(era5_dir / ".keep.json", {"generated_at_utc": generated_at, "purpose": "era5 prefix placeholder"})
    write_json(bto_dir / "validation_status.json", json.loads((latest_dir / "validation_status.json").read_text(encoding="utf-8")))

    static_root = resources.files("birdcast_uk").joinpath("static")
    web_dir.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "app.js", "styles.css"):
        source = static_root.joinpath(name)
        shutil.copyfile(source, web_dir / name)
    write_json(
        web_dir / "config.json",
        {
            "data_base_url": data_base_url,
            "generated_at_utc": generated_at,
            "object_prefix": object_prefix,
        },
    )

    return {
        "ok": True,
        "generated_at_utc": generated_at,
        "output_dir": str(output_dir),
        "data_base_url": data_base_url,
        "radar_count": len(radars),
    }
