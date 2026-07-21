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

from .config import OBJECT_PREFIX, PROCESSING_VERSION, UKMO_VPTS_CATALOG_URL
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
    temporary_path.chmod(0o644)
    os.replace(temporary_path, path)


def write_placeholder_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a placeholder unless a data-bearing artifact already exists."""

    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
        nested_properties = existing.get("properties", {})
        if isinstance(existing, dict) and (
            existing.get("data_available") is True
            or (
                isinstance(nested_properties, dict)
                and nested_properties.get("data_available") is True
            )
            or bool(existing.get("features"))
            or bool(existing.get("valid_times_utc"))
            or existing.get("latest_vpts_date")
            or existing.get("latest_observed_date")
            or existing.get("latest_date")
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
    write_placeholder_json(
        latest_dir / "forecast.json",
        {
            "schema_version": "birdcast-uk-forecast-1.0",
            "data_available": False,
            "generated_at_utc": generated_at,
            "mode": "unavailable",
            "valid_times_utc": [],
            "assets": {"frames": []},
        },
    )
    write_placeholder_json(
        latest_dir / "historical.json",
        {
            "schema_version": "live-uk-bird-maps-historical-1.1",
            "data_available": False,
            "generated_at_utc": generated_at,
            "first_date": None,
            "latest_date": None,
            "years": [],
            "assets": {"daily_by_year": {}, "plots": []},
        },
    )
    write_placeholder_json(
        latest_dir / "gam-era5.json",
        {
            "schema_version": "live-uk-bird-maps-gam-era5-1.1",
            "data_available": False,
            "generated_at_utc": generated_at,
            "model_family": None,
            "assets": {"lp": {}, "sp": {}},
            "interpretation": "Historical modelled reanalysis is not available yet.",
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
    for name in (
        "index.html",
        "app.js",
        "styles.css",
        "live-uk-bird-maps-logo.jpg",
        "live-uk-bird-maps-icon.svg",
        "radar-marker.svg",
        "crow-radar-detail.js",
        "regional-boundaries.geojson",
    ):
        source = static_root.joinpath(name)
        shutil.copyfile(source, web_dir / name)
    write_json(
        web_dir / "config.json",
        {
            "data_base_url": data_base_url,
            "generated_at_utc": generated_at,
            "object_prefix": object_prefix,
            "vpts_catalog_url": UKMO_VPTS_CATALOG_URL,
            "vpts_object_url_template": "https://ncas-radar-o.s3-ext.jc.rl.ac.uk/uk-wsr-visualizer-public/ukmo-nimrod/vpts/current_ci_le4/{radar}/{yyyy}/{yyyymmdd}_{pulse}_vpts.csv",
        },
    )

    return {
        "ok": True,
        "generated_at_utc": generated_at,
        "output_dir": str(output_dir),
        "data_base_url": data_base_url,
        "radar_count": len(radars),
    }


def install_static_site(
    artifact_root: Path,
    site_root: Path,
    *,
    data_base_url: str = "/birdcast-uk/data",
    object_prefix: str = OBJECT_PREFIX,
) -> dict[str, object]:
    """Install the web shell with a same-origin data endpoint."""

    web_root = artifact_root / "web"
    required_files = (
        "index.html",
        "app.js",
        "styles.css",
        "live-uk-bird-maps-logo.jpg",
        "live-uk-bird-maps-icon.svg",
        "radar-marker.svg",
        "crow-radar-detail.js",
        "regional-boundaries.geojson",
    )
    missing = [name for name in required_files if not (web_root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Static web artifacts are missing: {', '.join(missing)}")

    site_root.mkdir(parents=True, exist_ok=True)
    for child in site_root.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    for name in required_files:
        destination = site_root / name
        shutil.copyfile(web_root / name, destination)
        destination.chmod(0o644)

    generated_at = utc_now()
    write_json(
        site_root / "config.json",
        {
            "data_base_url": data_base_url.rstrip("/"),
            "generated_at_utc": generated_at,
            "object_prefix": object_prefix,
            "vpts_catalog_url": UKMO_VPTS_CATALOG_URL,
            "vpts_object_url_template": "https://ncas-radar-o.s3-ext.jc.rl.ac.uk/uk-wsr-visualizer-public/ukmo-nimrod/vpts/current_ci_le4/{radar}/{yyyy}/{yyyymmdd}_{pulse}_vpts.csv",
        },
    )
    site_root.chmod(0o755)

    return {
        "ok": True,
        "generated_at_utc": generated_at,
        "site_root": str(site_root),
        "data_base_url": data_base_url.rstrip("/"),
    }
