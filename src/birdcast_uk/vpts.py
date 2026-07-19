"""Bounded discovery and loading of the production UK BioRad VPTS dataset."""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import email.utils
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
import time
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .config import (
    BIORAD_MANIFEST_PREFIX,
    DEFAULT_BUCKET,
    DEFAULT_PUBLIC_BASE_URL,
    UKMO_VPTS_CATALOG_URL,
    VPTS_BOOTSTRAP_LOOKBACK_DAYS,
    VPTS_FILE_SUFFIXES,
    VPTS_MAX_CATALOG_AGE_HOURS,
    VPTS_MAX_INCREMENT_DAYS,
    VPTS_PULSE_POLICY,
    VPTS_REQUIRED_FIELDS,
)
from .static_artifacts import utc_now, write_json

DATE_RE = re.compile(r"(?P<date>[12][0-9]{7})")
VPTS_KEY_RE = re.compile(
    r"^(?P<prefix>.+)/(?P<radar>[^/]+)/(?P<year>[0-9]{4})/"
    r"(?P<date>[0-9]{8})_(?P<pulse>lp|sp)_vpts\.(?P<format>csv|h5)$"
)
HeadFunction = Callable[[str], dict[str, object] | None]


def load_records(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("items", payload.get("records", payload)) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError("JSON manifest must be a list or contain items/records")
        return [dict(row) for row in rows]
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle, delimiter=delimiter)]
    if suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    try:
        import pandas as pd  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError("pandas is required to read non-JSON/CSV VPTS records") from exc
    return pd.read_parquet(path).to_dict(orient="records")


def validate_manifest(path: Path, output: Path | None = None) -> dict[str, Any]:
    rows = load_records(path)
    errors: list[dict[str, object]] = []
    for index, row in enumerate(rows):
        missing = [field for field in VPTS_REQUIRED_FIELDS if row.get(field) in (None, "")]
        if missing:
            errors.append({"row": index, "missing": missing})
    radars = sorted({str(row.get("radar", "")) for row in rows if row.get("radar")})
    dates = sorted({str(row.get("date", "")) for row in rows if row.get("date")})
    result = {
        "ok": not errors,
        "generated_at_utc": utc_now(),
        "path": str(path),
        "record_count": len(rows),
        "radars": radars,
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "errors": errors,
    }
    if output is not None:
        write_json(output, result)
    return result


def fetch_json(source: str, *, timeout_seconds: float = 60.0) -> dict[str, Any]:
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        with urlopen(source, timeout=timeout_seconds) as response:
            payload = json.load(response)
    else:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON source must contain an object: {source}")
    return payload


def head_public_object(
    url: str,
    *,
    timeout_seconds: float = 30.0,
    retries: int = 5,
) -> dict[str, object] | None:
    if retries < 1:
        raise ValueError("retries must be at least one")
    request = Request(url, method="HEAD")
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                headers = response.headers
            break
        except HTTPError as exc:
            if exc.code == 404:
                return None
            retryable = exc.code in {429, 500, 502, 503, 504}
            if not retryable or attempt + 1 >= retries:
                raise
        except (URLError, TimeoutError):
            if attempt + 1 >= retries:
                raise
        time.sleep(2**attempt)
    modified = headers.get("Last-Modified")
    modified_time = None
    if modified:
        parsed = email.utils.parsedate_to_datetime(modified)
        modified_time = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "size": int(headers.get("Content-Length", "0")),
        "etag": str(headers.get("ETag", "")).strip('"'),
        "modified_time": modified_time,
        "content_type": headers.get("Content-Type") or "text/csv",
    }


def build_catalog_inventory(
    *,
    output: Path,
    cursor_path: Path,
    catalog_url: str = UKMO_VPTS_CATALOG_URL,
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
    bucket: str = DEFAULT_BUCKET,
    bootstrap_lookback_days: int = VPTS_BOOTSTRAP_LOOKBACK_DAYS,
    max_increment_days: int = VPTS_MAX_INCREMENT_DAYS,
    max_catalog_age_hours: float = VPTS_MAX_CATALOG_AGE_HOURS,
    now: datetime | None = None,
    head: HeadFunction = head_public_object,
) -> dict[str, Any]:
    """Build a small rolling inventory from the public catalogue.

    The catalogue contains aggregate radar coverage rather than individual
    object keys. Object names are deterministic, so exact HEAD requests are
    used for at most the bounded date window. The archive is never recursively
    listed.
    """

    checked_at = _as_utc(now or datetime.now(timezone.utc))
    errors: list[str] = []
    try:
        catalog = fetch_json(catalog_url)
        catalog_generated = _parse_datetime(catalog.get("generated_at"))
        object_prefix = str(catalog["object_prefix"]).strip("/")
        catalog_radars = _catalog_radars(catalog)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError) as exc:
        payload = _inventory_error(
            catalog_url=catalog_url,
            checked_at=checked_at,
            errors=[f"malformed_catalog: {exc}"],
        )
        write_json(output, payload)
        return payload

    age_hours = (checked_at - catalog_generated).total_seconds() / 3600.0
    if age_hours < -1.0:
        errors.append("catalog_generated_in_future")
    if age_hours > max_catalog_age_hours:
        errors.append(
            f"stale_catalog: age_hours={age_hours:.2f} limit_hours={max_catalog_age_hours:.2f}"
        )

    try:
        cursor = _load_cursor(cursor_path)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        cursor = _empty_cursor(catalog_url)
        errors.append(f"malformed_cursor: {exc}")

    records: list[dict[str, object]] = []
    proposed_radars: dict[str, dict[str, object]] = {}
    radar_status: list[dict[str, object]] = []
    missing_target_radars: list[str] = []
    changed_radar_count = 0

    for radar_record in catalog_radars:
        radar = str(radar_record["radar"])
        last_source_date = _parse_day(str(radar_record["last_date"]))
        previous_state = _cursor_radar(cursor, radar)
        previous_source_date = _optional_day(previous_state.get("source_date"))
        if previous_source_date and previous_source_date > last_source_date:
            errors.append(
                f"source_date_regressed: radar={radar} "
                f"cursor={_day_stamp(previous_source_date)} catalog={_day_stamp(last_source_date)}"
            )
            target_source_date = last_source_date
            discovery_start = last_source_date - timedelta(days=1)
        elif previous_source_date is None:
            target_source_date = last_source_date
            discovery_start = last_source_date - timedelta(days=max(1, bootstrap_lookback_days))
        elif previous_source_date < last_source_date:
            target_source_date = min(
                last_source_date,
                previous_source_date + timedelta(days=max(1, max_increment_days)),
            )
            discovery_start = previous_source_date
        else:
            target_source_date = last_source_date
            discovery_start = last_source_date - timedelta(days=1)

        selected_records: list[dict[str, object]] = []
        for source_day in _date_range(discovery_start, target_source_date):
            selected = _select_pulse_record(
                radar=radar,
                source_day=source_day,
                object_prefix=object_prefix,
                public_base_url=public_base_url,
                bucket=bucket,
                head=head,
            )
            if selected is not None:
                selected_records.append(selected)

        target_stamp = _day_stamp(target_source_date)
        if not any(str(record["date"]) == target_stamp for record in selected_records):
            missing_target_radars.append(radar)

        selected_etags = {
            str(record["key"]): str(record.get("etag") or "")
            for record in selected_records
        }
        previous_etags = {
            str(key): str(value)
            for key, value in dict(previous_state.get("objects") or {}).items()
        }
        etags_changed = any(
            previous_etags.get(key) != value for key, value in selected_etags.items()
        )
        changed = (
            previous_source_date != target_source_date
            or etags_changed
            or not previous_state
        )
        if changed:
            changed_radar_count += 1
            records.extend(selected_records)

        proposed_radars[radar] = {
            "source_date": target_stamp,
            "latest_complete_night": _day_stamp(target_source_date - timedelta(days=1)),
            "objects": selected_etags,
        }
        radar_status.append(
            {
                "radar": radar,
                "catalog_last_date": _day_stamp(last_source_date),
                "cursor_source_date": _day_stamp(previous_source_date) if previous_source_date else None,
                "proposed_source_date": target_stamp,
                "changed": changed,
                "selected_file_count": len(selected_records),
            }
        )

    if missing_target_radars:
        errors.append(
            "missing_target_csv: " + ",".join(sorted(missing_target_radars))
        )
    expected_radar_count = int(catalog.get("radar_count") or len(catalog_radars))
    if expected_radar_count != len(catalog_radars):
        errors.append(
            f"catalog_radar_count_mismatch: declared={expected_radar_count} "
            f"records={len(catalog_radars)}"
        )

    ok = not errors
    no_change = ok and changed_radar_count == 0
    if not ok:
        records = []
    proposed_cursor = {
        "schema_version": 1,
        "catalog_url": catalog_url,
        "catalog_generated_at_utc": _format_datetime(catalog_generated),
        "updated_at_utc": _format_datetime(checked_at),
        "radars": proposed_radars,
    }
    payload = {
        "schema_version": 2,
        "ok": ok,
        "status": "up_to_date" if no_change else ("ready" if ok else "error"),
        "no_change": no_change,
        "generated_at_utc": _format_datetime(checked_at),
        "bucket": bucket,
        "catalog_url": catalog_url,
        "catalog_generated_at_utc": _format_datetime(catalog_generated),
        "catalog_age_hours": round(age_hours, 3),
        "max_catalog_age_hours": max_catalog_age_hours,
        "object_prefix": object_prefix,
        "manifest_prefix": BIORAD_MANIFEST_PREFIX,
        "pulse_policy": VPTS_PULSE_POLICY,
        "expected_radar_count": expected_radar_count,
        "catalog_radar_count": len(catalog_radars),
        "changed_radar_count": changed_radar_count,
        "record_count": len(records),
        "records": sorted(records, key=lambda item: (str(item["radar"]), str(item["date"]))),
        "radars": radar_status,
        "missing_target_radars": sorted(missing_target_radars),
        "errors": errors,
        "proposed_cursor": proposed_cursor,
    }
    write_json(output, payload)
    return payload


def build_historical_inventory(
    *,
    output: Path,
    catalog_url: str = UKMO_VPTS_CATALOG_URL,
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
    bucket: str = DEFAULT_BUCKET,
    days: int = 365,
    end_date: str | None = None,
    max_workers: int = 16,
    max_catalog_age_hours: float = VPTS_MAX_CATALOG_AGE_HOURS,
    now: datetime | None = None,
    head: HeadFunction = head_public_object,
) -> dict[str, Any]:
    """Create an all-hour, pulse-preserving VPTS archive inventory.

    This is intentionally separate from :func:`build_catalog_inventory`.
    The latter protects the operational incremental pipeline by looking at only
    a few days; historical ERA5 reanalysis needs a fixed rolling window.  The
    public catalogue contains coverage rather than object-level manifests, so
    bounded concurrent HEAD requests establish the exact, reproducible input
    set without recursively listing the bucket.
    """

    if days < 1:
        raise ValueError("days must be at least one")
    if max_workers < 1:
        raise ValueError("max_workers must be at least one")
    checked_at = _as_utc(now or datetime.now(timezone.utc))
    try:
        catalog = fetch_json(catalog_url)
        catalog_generated = _parse_datetime(catalog.get("generated_at"))
        object_prefix = str(catalog["object_prefix"]).strip("/")
        catalog_radars = _catalog_radars(catalog)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError) as exc:
        payload = _inventory_error(
            catalog_url=catalog_url,
            checked_at=checked_at,
            errors=[f"malformed_catalog: {exc}"],
        )
        write_json(output, payload)
        return payload

    age_hours = (checked_at - catalog_generated).total_seconds() / 3600.0
    errors: list[str] = []
    if age_hours < -1.0:
        errors.append("catalog_generated_in_future")
    if age_hours > max_catalog_age_hours:
        errors.append(f"stale_catalog: age_hours={age_hours:.2f} limit_hours={max_catalog_age_hours:.2f}")

    catalog_last_days = [_parse_day(str(row["last_date"])) for row in catalog_radars]
    latest_common_source_day = min(catalog_last_days)
    selected_end = _parse_day(end_date) if end_date else latest_common_source_day - timedelta(days=1)
    if selected_end > latest_common_source_day:
        errors.append(
            f"requested_end_after_catalog: end={_day_stamp(selected_end)} "
            f"latest_common={_day_stamp(latest_common_source_day)}"
        )
    selected_start = selected_end - timedelta(days=days - 1)
    tasks = [
        (str(radar_record["radar"]), source_day)
        for radar_record in catalog_radars
        for source_day in _date_range(selected_start, selected_end)
    ]

    def select(task: tuple[str, date]) -> list[dict[str, object]]:
        radar, source_day = task
        return _select_pulse_records(
            radar=radar,
            source_day=source_day,
            object_prefix=object_prefix,
            public_base_url=public_base_url,
            bucket=bucket,
            head=head,
            pulse_mode="both",
        )

    records: list[dict[str, object]] = []
    missing_by_radar: dict[str, int] = {str(row["radar"]): 0 for row in catalog_radars}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for (radar, _), selected in zip(tasks, executor.map(select, tasks)):
            if selected:
                records.extend(selected)
            else:
                missing_by_radar[radar] += 1

    expected_radar_count = int(catalog.get("radar_count") or len(catalog_radars))
    if expected_radar_count != len(catalog_radars):
        errors.append(
            f"catalog_radar_count_mismatch: declared={expected_radar_count} records={len(catalog_radars)}"
        )
    record_days = {(str(row["radar"]), str(row["date"])) for row in records}
    coverage = [
        {
            "radar": radar,
            "requested_day_count": days,
            "available_day_count": days - missing,
            "missing_day_count": missing,
            "coverage_fraction": round((days - missing) / days, 6),
        }
        for radar, missing in sorted(missing_by_radar.items())
    ]
    payload = {
        "schema_version": 3,
        "ok": not errors and bool(records),
        "status": "ready" if not errors and records else "error",
        "no_change": False,
        "generated_at_utc": _format_datetime(checked_at),
        "bucket": bucket,
        "catalog_url": catalog_url,
        "catalog_generated_at_utc": _format_datetime(catalog_generated),
        "catalog_age_hours": round(age_hours, 3),
        "max_catalog_age_hours": max_catalog_age_hours,
        "object_prefix": object_prefix,
        "window": {
            "days": days,
            "start_date": _day_stamp(selected_start),
            "end_date": _day_stamp(selected_end),
            "latest_common_catalog_date": _day_stamp(latest_common_source_day),
            "complete_day_policy": "catalog latest common source date minus one day unless explicitly pinned",
        },
        "pulse_policy": "all_available_lp_and_sp_separate",
        "expected_radar_count": expected_radar_count,
        "catalog_radar_count": len(catalog_radars),
        "record_count": len(records),
        "record_day_count": len(record_days),
        "records": sorted(records, key=lambda item: (str(item["radar"]), str(item["date"]), str(item["pulse"]))),
        "radar_coverage": coverage,
        "errors": errors,
    }
    write_json(output, payload)
    return payload


def load_vpts_rows_from_inventory(
    inventory_path: Path,
    *,
    max_files: int | None = None,
) -> list[dict[str, Any]]:
    rows = []
    for batch in iter_vpts_record_batches_from_inventory(
        inventory_path,
        max_files=max_files,
    ):
        rows.extend(batch)
    return rows


def iter_vpts_record_batches_from_inventory(
    inventory_path: Path,
    *,
    max_files: int | None = None,
) -> Iterator[list[dict[str, Any]]]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("ok") is not True:
        raise ValueError(
            "VPTS inventory is unhealthy: " + "; ".join(inventory.get("errors") or [])
        )
    records = inventory.get("records", [])
    if not isinstance(records, list):
        raise ValueError("VPTS inventory must contain a records list")
    with TemporaryDirectory(prefix="birdcast_uk_vpts_") as tmp:
        tmpdir = Path(tmp)
        for index, record in enumerate(records):
            if max_files is not None and index >= max_files:
                break
            if not isinstance(record, dict):
                raise ValueError(f"VPTS inventory record {index} is not an object")
            public_url = str(record.get("public_url") or "")
            if not public_url:
                raise ValueError(f"VPTS inventory record {index} has no public_url")
            local = (
                tmpdir
                / str(record.get("radar") or "unknown")
                / Path(str(record.get("key") or f"vpts_{index}.csv")).name
            )
            download_public_object(public_url, local)
            batch: list[dict[str, Any]] = []
            for row in load_records(local):
                enriched = dict(row)
                # Upstream CSVs currently contain radar=UNKNOWN. Object-key
                # provenance is authoritative for radar, date, and pulse.
                enriched["radar"] = str(record.get("radar") or "")
                enriched["date"] = str(record.get("date") or "")
                enriched["pulse"] = str(record.get("pulse") or "")
                enriched["source_uri"] = str(record.get("source_uri") or public_url)
                enriched["source_url"] = public_url
                enriched["source_key"] = str(record.get("key") or "")
                enriched["source_etag"] = str(record.get("etag") or "")
                batch.append(enriched)
            yield batch


def download_public_object(
    url: str,
    output: Path,
    *,
    timeout_seconds: float = 120.0,
    retries: int = 3,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(url)
    if parsed.scheme in {"", "file"}:
        source = Path(parsed.path if parsed.scheme == "file" else url)
        shutil.copyfile(source, output)
        return output
    if retries < 1:
        raise ValueError("retries must be at least one")
    request = Request(url, headers={"User-Agent": "birdcast-uk/0.4"})
    temporary = output.with_name(f".{output.name}.part")
    last_error: OSError | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=timeout_seconds) as response, temporary.open("wb") as handle:
                shutil.copyfileobj(response, handle)
            os.replace(temporary, output)
            return output
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    assert last_error is not None
    raise last_error


def commit_inventory_cursor(inventory_path: Path, cursor_path: Path) -> dict[str, Any]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("ok") is not True:
        raise ValueError("cannot commit cursor from an unhealthy inventory")
    proposed = inventory.get("proposed_cursor")
    if not isinstance(proposed, dict):
        raise ValueError("inventory has no proposed_cursor")
    write_json(cursor_path, proposed)
    return proposed


def _catalog_radars(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    rows = catalog.get("radars")
    if not isinstance(rows, list) or not rows:
        raise ValueError("catalog radars must be a non-empty list")
    result = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not row.get("radar") or not row.get("last_date"):
            raise ValueError(f"catalog radar record {index} is malformed")
        _parse_day(str(row["last_date"]))
        result.append(row)
    return sorted(result, key=lambda item: str(item["radar"]))


def _select_pulse_record(
    *,
    radar: str,
    source_day: date,
    object_prefix: str,
    public_base_url: str,
    bucket: str,
    head: HeadFunction,
) -> dict[str, object] | None:
    records = _select_pulse_records(
        radar=radar,
        source_day=source_day,
        object_prefix=object_prefix,
        public_base_url=public_base_url,
        bucket=bucket,
        head=head,
        pulse_mode="preferred",
    )
    return records[0] if records else None


def _select_pulse_records(
    *,
    radar: str,
    source_day: date,
    object_prefix: str,
    public_base_url: str,
    bucket: str,
    head: HeadFunction,
    pulse_mode: str,
) -> list[dict[str, object]]:
    if pulse_mode not in {"preferred", "both"}:
        raise ValueError("pulse_mode must be preferred or both")
    candidates: dict[str, tuple[str, str, dict[str, object]]] = {}
    for pulse in ("lp", "sp"):
        stamp = _day_stamp(source_day)
        key = f"{object_prefix}/{radar}/{source_day.year}/{stamp}_{pulse}_vpts.csv"
        public_url = f"{public_base_url.rstrip('/')}/{key}"
        metadata = head(public_url)
        if metadata is not None:
            candidates[pulse] = (key, public_url, metadata)
    selected_pulses = (
        [pulse for pulse in ("lp", "sp") if pulse in candidates]
        if pulse_mode == "both"
        else (["lp"] if "lp" in candidates else (["sp"] if "sp" in candidates else []))
    )
    records = []
    for selected_pulse in selected_pulses:
        key, public_url, metadata = candidates[selected_pulse]
        records.append(
            {
                "radar": radar,
                "date": _day_stamp(source_day),
                "pulse": selected_pulse,
                "available_pulses": sorted(candidates),
                "selection_policy": (
                    "all_available_lp_and_sp_separate"
                    if pulse_mode == "both"
                    else VPTS_PULSE_POLICY
                ),
                "key": key,
                "file_format": "csv",
                "size": int(metadata.get("size") or 0),
                "etag": str(metadata.get("etag") or ""),
                "modified_time": metadata.get("modified_time"),
                "content_type": str(metadata.get("content_type") or "text/csv"),
                "source_uri": f"s3://{bucket}/{key}",
                "public_url": public_url,
            }
        )
    return records


def _load_cursor(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_cursor("")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("radars"), dict):
        raise ValueError("cursor must contain a radars object")
    return payload


def _empty_cursor(catalog_url: str) -> dict[str, Any]:
    return {"schema_version": 1, "catalog_url": catalog_url, "radars": {}}


def _cursor_radar(cursor: dict[str, Any], radar: str) -> dict[str, Any]:
    radars = cursor.get("radars")
    if not isinstance(radars, dict):
        return {}
    row = radars.get(radar)
    return dict(row) if isinstance(row, dict) else {}


def _inventory_error(
    *,
    catalog_url: str,
    checked_at: datetime,
    errors: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "ok": False,
        "status": "error",
        "no_change": False,
        "generated_at_utc": _format_datetime(checked_at),
        "catalog_url": catalog_url,
        "record_count": 0,
        "records": [],
        "errors": errors,
    }


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def _optional_day(value: object) -> date | None:
    if value in (None, ""):
        return None
    return _parse_day(str(value))


def _day_stamp(value: date) -> str:
    return value.strftime("%Y%m%d")


def _parse_datetime(value: object) -> datetime:
    if not value:
        raise ValueError("missing generated_at")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return _as_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _key_metadata(key: str) -> dict[str, object]:
    match = VPTS_KEY_RE.match(key)
    if match:
        return {
            "radar": match.group("radar"),
            "date": match.group("date"),
            "pulse": match.group("pulse"),
            "file_format": match.group("format"),
        }
    path = Path(key)
    date_match = DATE_RE.search(path.name)
    return {
        "radar": "",
        "date": date_match.group("date") if date_match else "",
        "pulse": "",
        "file_format": path.suffix.lower().lstrip("."),
    }
