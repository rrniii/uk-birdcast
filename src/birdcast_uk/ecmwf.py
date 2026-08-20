"""Transactionally archive operational ECMWF Open Data cycles.

Completed cycle directories are immutable. Downloads are written to a hidden
sibling staging directory, checked, and promoted only after the complete
manifest has been written and validated. Validation covers GRIB2 message
framing and file integrity; decoding fields remains the forecast reader's job.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import mkdtemp
from typing import Any
from uuid import uuid4

from .config import (
    ECMWF_OPEN_DATA_PRESSURE_LEVELS,
    ECMWF_OPEN_DATA_PRESSURE_PARAMETERS,
    ECMWF_OPEN_DATA_SURFACE_PARAMETERS,
    FORECAST_HORIZON_HOURS,
    FORECAST_STEP_HOURS,
)
from .static_artifacts import utc_now, write_json

ECMWF_CYCLE_SCHEMA_VERSION = "birdcast-uk-ecmwf-cycle-2.0"
_FILE_KINDS = ("surface", "pressure")
_SOURCE = "ECMWF Open Data via earthkit-data"
_LICENCE = "CC-BY-4.0"
_REQUEST_DOMAIN = "ECMWF Open Data native grid; no spatial subset requested"
_MANIFEST_KEYS = {
    "schema_version",
    "generated_at_utc",
    "cycle_id",
    "cycle_time_utc",
    "source",
    "licence",
    "request_domain",
    "requests",
    "content_contract",
    "files",
    "status",
}
_FILE_KEYS = {
    "kind",
    "path",
    "relative_path",
    "size_bytes",
    "sha256",
    "format",
    "content_validation",
}
_CONTENT_CONTRACT = {
    "validation_level": "grib2-message-framing",
    "decoded": False,
    "field_inventory_verified": False,
    "expected_file_kinds": list(_FILE_KINDS),
}


def normalise_cycle(cycle: str | None = None) -> datetime:
    """Return a canonical ECMWF 00/06/12/18 UTC cycle time."""

    if cycle:
        parsed = datetime.fromisoformat(cycle.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        if (
            parsed.hour not in (0, 6, 12, 18)
            or parsed.minute
            or parsed.second
            or parsed.microsecond
        ):
            raise ValueError("ECMWF cycle must be 00, 06, 12, or 18 UTC")
        return parsed
    try:
        from ecmwf.opendata import Client

        latest = Client(source="ecmwf", model="ifs", resol="0p25").latest()
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        return normalise_cycle(latest.astimezone(timezone.utc).isoformat())
    except Exception:
        now = datetime.now(timezone.utc) - timedelta(hours=12)
        hour = max(value for value in (0, 6, 12, 18) if value <= now.hour)
        return now.replace(hour=hour, minute=0, second=0, microsecond=0)


def open_data_requests(cycle: datetime) -> list[dict[str, Any]]:
    """Build the surface and pressure-level requests for one cycle."""

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


def archive_cycle(
    output_root: Path,
    *,
    cycle: str | None = None,
    overwrite: bool = False,
) -> dict[str, object]:
    """Download and atomically publish one immutable ECMWF cycle.

    A valid completed cycle is always reused, including when ``overwrite`` is
    true. ``overwrite`` only authorises replacement of an invalid pre-existing
    cycle, after the replacement passes every check. Failures are returned but
    never persisted as cycle manifests.
    """

    selected = normalise_cycle(cycle)
    stamp = selected.strftime("%Y%m%dT%H00Z")
    output_root = output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    cycle_dir = output_root / stamp
    manifest_path = cycle_dir / "manifest.json"

    if cycle_dir.exists():
        try:
            return validate_cycle_manifest(manifest_path)
        except (FileNotFoundError, OSError, ValueError) as exc:
            if not overwrite:
                return _failure_payload(
                    selected,
                    f"invalid cached cycle; pass overwrite=True to rebuild: {type(exc).__name__}: {exc}",
                )

    requests = open_data_requests(selected)
    staging_dir = Path(mkdtemp(prefix=f".{stamp}.staging-", dir=output_root))
    try:
        import earthkit.data as ekd

        files: list[dict[str, object]] = []
        for kind, request in zip(_FILE_KINDS, requests, strict=True):
            filename = f"ecmwf_{stamp}_{kind}.grib2"
            staged_path = staging_dir / filename
            data = ekd.from_source(
                "ecmwf-open-data",
                source="ecmwf",
                model="ifs",
                resol="0p25",
                request=request,
            )
            data.to_target("file", str(staged_path))
            framing = _validate_grib2_framing(staged_path)
            files.append(
                {
                    "kind": kind,
                    # Keep an absolute path for the existing forecast reader.
                    "path": str(cycle_dir / filename),
                    "relative_path": filename,
                    "size_bytes": staged_path.stat().st_size,
                    "sha256": _sha256(staged_path),
                    "format": "GRIB2",
                    "content_validation": framing,
                }
            )

        payload: dict[str, object] = {
            "schema_version": ECMWF_CYCLE_SCHEMA_VERSION,
            "generated_at_utc": utc_now(),
            "cycle_id": stamp,
            "cycle_time_utc": _cycle_isoformat(selected),
            "source": _SOURCE,
            "licence": _LICENCE,
            "request_domain": _REQUEST_DOMAIN,
            "requests": requests,
            "content_contract": _CONTENT_CONTRACT,
            "files": files,
            "status": "complete",
        }
        staged_manifest = staging_dir / "manifest.json"
        write_json(staged_manifest, payload)
        _validate_cycle_manifest(
            staged_manifest,
            asset_root=staging_dir,
            declared_cycle_dir=cycle_dir,
        )
        return _promote_cycle(
            staging_dir,
            cycle_dir,
            replace_invalid=overwrite,
        )
    except Exception as exc:
        return _failure_payload(selected, f"{type(exc).__name__}: {exc}")
    finally:
        # A successful promotion moves the path, making this a harmless no-op.
        shutil.rmtree(staging_dir, ignore_errors=True)


def validate_cycle_manifest(manifest_path: Path) -> dict[str, object]:
    """Validate a completed manifest and every asset it references.

    Raises ``ValueError``, ``FileNotFoundError``, or ``OSError`` on invalid
    input. GRIB2 framing is checked without claiming to decode fields.
    """

    # ``abspath`` makes declared path comparison deterministic without hiding a
    # symlink at the supplied manifest or cycle-directory path.
    manifest_path = Path(os.path.abspath(manifest_path.expanduser()))
    return _validate_cycle_manifest(manifest_path)


def newest_validated_cycle_manifest(output_root: Path) -> Path | None:
    """Return the newest complete, fully validated cycle manifest, if any."""

    root = output_root.expanduser().resolve(strict=False)
    if not root.is_dir():
        return None

    candidates: list[tuple[datetime, Path]] = []
    for cycle_dir in root.iterdir():
        if not cycle_dir.is_dir() or cycle_dir.name.startswith("."):
            continue
        manifest_path = cycle_dir / "manifest.json"
        try:
            payload = validate_cycle_manifest(manifest_path)
            cycle_time = normalise_cycle(str(payload["cycle_time_utc"]))
        except (FileNotFoundError, OSError, TypeError, ValueError):
            continue
        candidates.append((cycle_time, manifest_path.resolve()))
    return max(candidates, default=(None, None), key=lambda item: item[0])[1]


def _validate_cycle_manifest(
    manifest_path: Path,
    *,
    asset_root: Path | None = None,
    declared_cycle_dir: Path | None = None,
) -> dict[str, object]:
    if manifest_path.is_symlink() or manifest_path.parent.is_symlink():
        raise ValueError("cycle manifest and directory must not be symbolic links")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"cycle manifest is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("cycle manifest must be a JSON object")
    if set(payload) != _MANIFEST_KEYS:
        raise ValueError("cycle manifest fields do not match the strict schema")
    if payload.get("schema_version") != ECMWF_CYCLE_SCHEMA_VERSION:
        raise ValueError("unsupported ECMWF cycle manifest schema")
    if payload.get("status") != "complete" or "error" in payload:
        raise ValueError("cycle manifest is not strictly complete")

    try:
        selected = normalise_cycle(str(payload["cycle_time_utc"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("cycle_time_utc is missing or non-canonical") from exc
    if payload["cycle_time_utc"] != _cycle_isoformat(selected):
        raise ValueError("cycle_time_utc must use canonical UTC notation")
    stamp = selected.strftime("%Y%m%dT%H00Z")
    if payload.get("cycle_id") != stamp:
        raise ValueError("cycle_id does not match cycle_time_utc")
    if payload.get("source") != _SOURCE:
        raise ValueError("unexpected ECMWF cycle source")
    if payload.get("licence") != _LICENCE:
        raise ValueError("unexpected ECMWF cycle licence")
    if payload.get("request_domain") != _REQUEST_DOMAIN:
        raise ValueError("unexpected ECMWF request domain")
    try:
        generated = datetime.fromisoformat(str(payload["generated_at_utc"]).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("generated_at_utc is not a timestamp") from exc
    if generated.tzinfo is None or generated.utcoffset() != timedelta(0):
        raise ValueError("generated_at_utc must be UTC")

    declared_dir = (declared_cycle_dir or manifest_path.parent).resolve(strict=False)
    if declared_dir.name != stamp:
        raise ValueError("cycle directory does not match cycle_time_utc")
    if payload.get("requests") != open_data_requests(selected):
        raise ValueError("requests do not match the declared cycle contract")
    if payload.get("content_contract") != _CONTENT_CONTRACT:
        raise ValueError("content contract is missing or unsupported")

    files = payload.get("files")
    if not isinstance(files, list) or len(files) != len(_FILE_KINDS):
        raise ValueError("cycle manifest must reference exactly two assets")
    by_kind: dict[str, dict[str, object]] = {}
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("kind"), str):
            raise ValueError("invalid cycle asset record")
        if set(item) != _FILE_KEYS:
            raise ValueError("cycle asset fields do not match the strict schema")
        kind = item["kind"]
        if kind not in _FILE_KINDS or kind in by_kind:
            raise ValueError("cycle asset kinds must be unique surface and pressure records")
        by_kind[kind] = item
    if tuple(sorted(by_kind, key=_FILE_KINDS.index)) != _FILE_KINDS:
        raise ValueError("cycle assets do not cover the required kinds")

    actual_root = (asset_root or declared_dir).resolve(strict=False)
    for kind in _FILE_KINDS:
        item = by_kind[kind]
        expected_filename = f"ecmwf_{stamp}_{kind}.grib2"
        if item.get("relative_path") != expected_filename:
            raise ValueError(f"invalid relative path for {kind} asset")
        declared_path = Path(str(item.get("path", "")))
        if not declared_path.is_absolute() or declared_path != declared_dir / expected_filename:
            raise ValueError(f"invalid declared path for {kind} asset")
        actual_path = actual_root / expected_filename
        if actual_path.is_symlink():
            raise ValueError(f"{kind} asset must not be a symbolic link")
        if not actual_path.is_file():
            raise FileNotFoundError(f"missing {kind} asset: {actual_path}")
        size = actual_path.stat().st_size
        if not isinstance(item.get("size_bytes"), int) or item["size_bytes"] <= 0:
            raise ValueError(f"invalid recorded size for {kind} asset")
        if item["size_bytes"] != size:
            raise ValueError(f"size mismatch for {kind} asset")
        digest = item.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"invalid SHA-256 for {kind} asset")
        if digest != _sha256(actual_path):
            raise ValueError(f"SHA-256 mismatch for {kind} asset")
        if item.get("format") != "GRIB2":
            raise ValueError(f"invalid format for {kind} asset")
        framing = _validate_grib2_framing(actual_path)
        if item.get("content_validation") != framing:
            raise ValueError(f"GRIB2 framing record mismatch for {kind} asset")
    return payload


def _validate_grib2_framing(path: Path) -> dict[str, object]:
    """Validate concatenated GRIB2 message boundaries without decoding fields."""

    size = path.stat().st_size
    if size == 0:
        raise ValueError(f"empty GRIB2 asset: {path}")
    message_count = 0
    offset = 0
    with path.open("rb") as handle:
        while offset < size:
            handle.seek(offset)
            header = handle.read(16)
            if len(header) != 16 or header[:4] != b"GRIB":
                raise ValueError(f"invalid GRIB2 header at byte {offset}: {path}")
            if header[7] != 2:
                raise ValueError(f"unsupported GRIB edition at byte {offset}: {path}")
            message_length = int.from_bytes(header[8:16], byteorder="big")
            if message_length < 20 or offset + message_length > size:
                raise ValueError(f"invalid GRIB2 message length at byte {offset}: {path}")
            handle.seek(offset + message_length - 4)
            if handle.read(4) != b"7777":
                raise ValueError(f"missing GRIB2 terminator at byte {offset}: {path}")
            offset += message_length
            message_count += 1
    return {
        "method": "grib2-message-framing",
        "decoded": False,
        "edition": 2,
        "message_count": message_count,
    }


def _promote_cycle(
    staging_dir: Path,
    cycle_dir: Path,
    *,
    replace_invalid: bool,
) -> dict[str, object]:
    """Promote a validated staging directory, preserving invalid prior data."""

    quarantine: Path | None = None
    if cycle_dir.exists():
        # A concurrent writer may have completed the cycle meanwhile.
        try:
            return validate_cycle_manifest(cycle_dir / "manifest.json")
        except (FileNotFoundError, OSError, ValueError):
            if not replace_invalid:
                raise ValueError("a concurrent writer created an invalid cycle")
            quarantine = cycle_dir.parent / f".{cycle_dir.name}.invalid-{uuid4().hex}"
            os.replace(cycle_dir, quarantine)
    try:
        os.replace(staging_dir, cycle_dir)
        result = validate_cycle_manifest(cycle_dir / "manifest.json")
    except Exception:
        if cycle_dir.exists():
            failed = cycle_dir.parent / f".{cycle_dir.name}.failed-{uuid4().hex}"
            os.replace(cycle_dir, failed)
        if quarantine is not None and quarantine.exists():
            os.replace(quarantine, cycle_dir)
        raise
    return result


def _failure_payload(selected: datetime, error: str) -> dict[str, object]:
    return {
        "schema_version": ECMWF_CYCLE_SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "cycle_id": selected.strftime("%Y%m%dT%H00Z"),
        "cycle_time_utc": _cycle_isoformat(selected),
        "source": _SOURCE,
        "status": "failed",
        "persisted": False,
        "files": [],
        "error": error,
    }


def _cycle_isoformat(cycle: datetime) -> str:
    return cycle.isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
