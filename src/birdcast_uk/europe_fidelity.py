"""Fail-closed source and training-contract checks for the Europe reanalysis.

These checks deliberately re-stream advertised Aloft VPTS objects.  They
compare the compact hourly derivatives with a fresh reconstruction while
retaining only an audit report; raw source profiles are never written.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError

from .archive import VptsObject
from .config import ALOFT_PUBLIC_BASE_URL
from .europe import OpenUrl, stream_aloft_hourly
from .static_artifacts import utc_now, write_json


def verify_aloft_chunk(
    chunk: dict[str, Any],
    *,
    hourly_root: Path,
    public_base_url: str = ALOFT_PUBLIC_BASE_URL,
    opener: OpenUrl | None = None,
    release_id: str = "unversioned",
    retry_attempts: int = 3,
    retry_delay_seconds: float = 0.5,
) -> dict[str, Any]:
    """Reconstruct a derived radar-month partition and reject any difference."""

    if retry_attempts < 1:
        raise ValueError("retry_attempts must be at least one")

    source = str(chunk["source"])
    radar = str(chunk["radar"])
    year = str(chunk["year"])
    month = str(chunk["month"])
    derived = hourly_root / f"source={source}" / f"year={year}" / f"month={month}" / f"{radar}.parquet"
    manifest_path = derived.with_suffix(derived.suffix + ".manifest.json")
    if not derived.is_file() or not manifest_path.is_file():
        raise ValueError(f"missing derived hourly partition or provenance manifest for {source}/{radar}/{year}-{month}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "complete" or manifest.get("raw_source_persisted") is not False:
        raise ValueError("derived partition does not declare a complete raw-free stream")
    if manifest.get("release_id") != release_id:
        raise ValueError("derived partition was not produced by the declared Europe release")

    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeError("pyarrow is required for Europe fidelity checks") from exc
    # Read the file directly: its Hive partition directories intentionally use
    # the same names as provenance columns (for example ``source=baltrad``).
    # Dataset inference would attempt to merge those two schemas.
    persisted = pq.ParquetFile(derived).read().to_pylist()
    persisted_by_day = _rows_by_day(persisted)
    audit_by_day = {str(item["day"]): item for item in manifest.get("audits", [])}
    expected_days = [str(day) for day in chunk.get("days", [])]
    if set(persisted_by_day) - set(expected_days) or set(audit_by_day) != set(expected_days):
        raise ValueError("partition source days do not exactly match its locked chunk")

    checked_rows = 0
    for day in expected_days:
        obj = VptsObject(
            source=source,
            radar=radar,
            day=day,
            url=f"{public_base_url.rstrip('/')}/{source}/daily/{radar}/{year}/{radar}_vpts_{day}.csv",
        )
        kwargs = {"opener": opener} if opener is not None else {}
        for attempt in range(retry_attempts):
            try:
                reconstructed, audit = stream_aloft_hourly(obj, **kwargs)
                break
            except HTTPError as error:
                # Aloft can briefly throttle or return a gateway error under
                # parallel audit load.  Retrying those responses preserves the
                # source check; all semantic HTTP errors still fail closed.
                if error.code not in {408, 429, 500, 501, 502, 503, 504}:
                    raise
                if attempt + 1 == retry_attempts:
                    raise RuntimeError(
                        f"Aloft VPTS fidelity re-stream failed after {retry_attempts} attempts for {obj.url}"
                    ) from error
                time.sleep(retry_delay_seconds * (attempt + 1))
            except (TimeoutError, URLError, OSError) as error:
                if attempt + 1 == retry_attempts:
                    raise RuntimeError(
                        f"Aloft VPTS fidelity re-stream failed after {retry_attempts} attempts for {obj.url}"
                    ) from error
                time.sleep(retry_delay_seconds * (attempt + 1))
        for row in reconstructed:
            row["cohort_role"] = chunk.get("role")
        _assert_row_sets_equal(persisted_by_day.get(day, []), reconstructed, day)
        _assert_audit_equal(audit_by_day[day], audit.to_dict(), day)
        checked_rows += len(reconstructed)

    return {
        "schema_version": "birdcast-euro-fidelity-chunk-1.0",
        "status": "passed",
        "generated_at_utc": utc_now(),
        "raw_source_persisted": False,
        "release_id": release_id,
        "chunk": {key: chunk[key] for key in ("source", "radar", "year", "month", "role")},
        "source_day_count": len(expected_days),
        "hourly_row_count": checked_rows,
        "derived_hourly_path": str(derived),
    }


def verify_training_input_policy(
    training_csv: Path,
    *,
    cohort_json: Path,
    required_predictors: list[str],
    transfer_csv: Path | None = None,
) -> dict[str, Any]:
    """Reject hidden source, pulse, cohort and predictor-policy violations."""

    cohort = json.loads(cohort_json.read_text(encoding="utf-8"))
    roles = {
        str(entry["radar"]).lower(): str(entry["role"])
        for entry in cohort.get("entries", [])
        if str(entry.get("source")) == "baltrad"
    }
    training = _read_csv(training_csv)
    if not training:
        raise ValueError("Europe training table is empty")
    columns = set(training[0])
    missing_predictors = set(required_predictors) - columns
    if missing_predictors:
        raise ValueError(f"Europe training table is missing ERA5 predictors: {', '.join(sorted(missing_predictors))}")
    training_aloft_radars: set[str] = set()
    for row in training:
        source = str(row.get("source") or "")
        radar = str(row.get("radar") or "").lower()
        pulse = str(row.get("pulse") or "").lower()
        if source not in {"aloft-baltrad", "jasmin-uk-sp"}:
            raise ValueError(f"unapproved Europe training source: {source}")
        if source == "jasmin-uk-sp" and pulse != "sp":
            raise ValueError("UK training data must be SP only")
        if source == "aloft-baltrad":
            if pulse != "aloft" or roles.get(radar) != "training":
                raise ValueError(f"Aloft radar {radar} is not permitted in the training cohort")
            training_aloft_radars.add(radar)
    transfer_radars = {radar for radar, role in roles.items() if role == "transfer-validation"}
    if training_aloft_radars & transfer_radars:
        raise ValueError("transfer-validation Aloft radars leaked into the training table")

    transfer_rows = _read_csv(transfer_csv) if transfer_csv else []
    for row in transfer_rows:
        radar = str(row.get("radar") or "").lower()
        if str(row.get("source") or "") != "aloft-baltrad" or roles.get(radar) != "transfer-validation":
            raise ValueError(f"transfer-validation table contains non-transfer radar {radar}")

    return {
        "schema_version": "birdcast-euro-fidelity-training-1.0",
        "status": "passed",
        "generated_at_utc": utc_now(),
        "training_rows": len(training),
        "training_aloft_radar_count": len(training_aloft_radars),
        "transfer_rows": len(transfer_rows),
        "transfer_radar_count": len({str(row.get('radar') or '').lower() for row in transfer_rows}),
        "required_predictor_count": len(required_predictors),
        "raw_source_persisted": False,
    }


def write_fidelity_report(payload: dict[str, Any], output: Path) -> dict[str, Any]:
    write_json(output, payload)
    return payload


def _rows_by_day(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        day = str(row.get("source_day") or "")
        if not day:
            raise ValueError("derived row has no source_day")
        grouped.setdefault(day, []).append(row)
    return grouped


def _assert_row_sets_equal(actual: list[dict[str, Any]], expected: list[dict[str, Any]], day: str) -> None:
    normal_actual = sorted((_normalise_row(row) for row in actual), key=_row_key)
    normal_expected = sorted((_normalise_row(row) for row in expected), key=_row_key)
    if normal_actual != normal_expected:
        raise ValueError(f"hourly reconstruction mismatch for Aloft source day {day}")


def _normalise_row(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, float):
            result[key] = None if math.isnan(value) else round(value, 10)
        else:
            result[key] = value
    return result


def _row_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (str(row.get("source_day")), str(row.get("time_utc")), str(row.get("radar")))


def _assert_audit_equal(actual: dict[str, Any], expected: dict[str, Any], day: str) -> None:
    for key in ("source", "radar", "day", "url", "bytes_read", "row_count", "profile_count", "hourly_row_count", "sha256", "availability", "unavailable_reason"):
        if actual.get(key) != expected.get(key):
            raise ValueError(f"source audit mismatch for {day}: {key}")


def _read_csv(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
