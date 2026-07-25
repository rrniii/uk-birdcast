#!/usr/bin/env python3
"""Audit public Aloft VP HDF5 structure without persisting raw source files.

The Aloft BALTRAD ``hdf5`` prefix currently contains VP files, not PVOLs. This
tool samples published VP objects in memory and writes only derived schema,
metadata and numeric-summary evidence. It is intended to determine whether
apparent MTR scale outliers can be explained by incompatible VP structures
before making any claims about a GAMM or the biological signal.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import urlopen
from xml.etree import ElementTree


PUBLIC_BASE = "https://aloftdata.s3-eu-west-1.amazonaws.com"
DEFAULT_RADARS = ("dksam", "robuc", "rocra", "romed", "rotim")
DEFAULT_DAYS = ("20260115", "20260415", "20260715")
SELECTED_ATTRIBUTES = {
    "product", "object", "version", "quantity", "gain", "offset",
    "nodata", "undetect", "height", "height_min", "height_max",
    "levels", "interval", "rscale", "nbins", "startdate", "starttime",
    "enddate", "endtime", "date", "time", "source", "radar",
}
DYNAMIC_ATTRIBUTES = {"date", "time", "startdate", "starttime", "enddate", "endtime"}


def normalise_attribute(value: Any) -> Any:
    """Return a JSON-safe, bounded representation of an HDF5 attribute."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, list):
        return [normalise_attribute(item) for item in value[:64]]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def selected_attributes(attrs: Any, *, include_dynamic: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in attrs.keys():
        clean = str(key).lower()
        if clean in SELECTED_ATTRIBUTES and (include_dynamic or clean not in DYNAMIC_ATTRIBUTES):
            result[clean] = normalise_attribute(attrs[key])
    return result


def s3_day_objects(*, public_base: str, radar: str, day: str, timeout_seconds: float) -> list[str]:
    if len(day) != 8 or not day.isdigit():
        raise ValueError(f"day must be YYYYMMDD, got {day!r}")
    prefix = f"baltrad/hdf5/{radar.lower()}/{day[:4]}/{day[4:6]}/{day[6:8]}/"
    query = urlencode({"list-type": "2", "prefix": prefix, "max-keys": "1000"})
    with urlopen(f"{public_base.rstrip('/')}/?{query}", timeout=timeout_seconds) as response:
        root = ElementTree.fromstring(response.read())
    namespace = "{http://s3.amazonaws.com/doc/2006-03-01/}"
    return [item.text for item in root.findall(f".//{namespace}Key") if item.text and item.text.endswith(".h5")]


def sample_keys(keys: list[str], count: int) -> list[str]:
    if count < 1:
        raise ValueError("sample count must be positive")
    if len(keys) <= count:
        return keys
    indices = {round(index * (len(keys) - 1) / (count - 1)) for index in range(count)} if count > 1 else {len(keys) // 2}
    return [keys[index] for index in sorted(indices)]


def numeric_summary(dataset: Any) -> dict[str, Any] | None:
    """Calculate statistics in memory and discard the raw VP numeric array."""
    try:
        import numpy as np

        values = np.asarray(dataset[()])
    except (TypeError, ValueError, OSError):
        return None
    if values.dtype.kind not in {"i", "u", "f"}:
        return None
    finite = values[np.isfinite(values)] if values.dtype.kind == "f" else values.reshape(-1)
    if not finite.size:
        return {"finite_count": 0, "min": None, "max": None, "mean": None}
    return {
        "finite_count": int(finite.size),
        "min": float(np.min(finite)),
        "max": float(np.max(finite)),
        "mean": float(np.mean(finite)),
    }


def inspect_hdf5(payload: bytes) -> dict[str, Any]:
    """Return only structural and summary information for one in-memory VP."""
    try:
        import h5py
    except ImportError as error:  # pragma: no cover - deployment dependency
        raise RuntimeError("h5py is required for VP structure auditing") from error

    groups: list[dict[str, Any]] = []
    datasets: list[dict[str, Any]] = []
    with h5py.File(io.BytesIO(payload), "r") as handle:
        groups.append({"path": "/", "attribute_keys": sorted(map(str, handle.attrs.keys())), "metadata": selected_attributes(handle.attrs)})

        def visitor(path: str, node: Any) -> None:
            if isinstance(node, h5py.Group):
                groups.append({"path": f"/{path}", "attribute_keys": sorted(map(str, node.attrs.keys())), "metadata": selected_attributes(node.attrs)})
                return
            record = {
                "path": f"/{path}",
                "shape": list(node.shape),
                "dtype": str(node.dtype),
                "attribute_keys": sorted(map(str, node.attrs.keys())),
                "metadata": selected_attributes(node.attrs),
            }
            summary = numeric_summary(node)
            if summary is not None:
                record["numeric_summary"] = summary
            datasets.append(record)

        handle.visititems(visitor)

    fingerprint = {
        "groups": [{"path": group["path"], "attribute_keys": group["attribute_keys"], "metadata": selected_attributes(group["metadata"], include_dynamic=False)} for group in groups],
        "datasets": [{"path": dataset["path"], "shape": dataset["shape"], "dtype": dataset["dtype"], "attribute_keys": dataset["attribute_keys"], "metadata": selected_attributes(dataset["metadata"], include_dynamic=False)} for dataset in datasets],
    }
    return {
        "schema_fingerprint_sha256": hashlib.sha256(json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "groups": groups,
        "datasets": datasets,
        "quantity_values": sorted({str(record["metadata"]["quantity"]) for record in [*groups, *datasets] if record["metadata"].get("quantity") is not None}),
        "altitude_metadata": [
            {"path": record["path"], "metadata": {key: value for key, value in record["metadata"].items() if key in {"height", "height_min", "height_max", "levels", "interval", "nbins", "rscale"}}}
            for record in [*groups, *datasets]
            if any(key in record["metadata"] for key in {"height", "height_min", "height_max", "levels", "interval", "nbins", "rscale"})
        ],
    }


def fetch_and_inspect(*, public_base: str, key: str, timeout_seconds: float) -> dict[str, Any]:
    url = f"{public_base.rstrip('/')}/{key}"
    with urlopen(url, timeout=timeout_seconds) as response:
        payload = response.read()
        headers = response.headers
    result = inspect_hdf5(payload)
    return {
        "url": url,
        "key": key,
        "content_length": len(payload),
        "etag": headers.get("ETag"),
        "last_modified": headers.get("Last-Modified"),
        "sha256": hashlib.sha256(payload).hexdigest(),
        **result,
    }


def summarise_radar(samples: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(samples)
    return {
        "sample_count": len(rows),
        "schema_fingerprint_count": len({row["schema_fingerprint_sha256"] for row in rows}),
        "schema_fingerprints": sorted({row["schema_fingerprint_sha256"] for row in rows}),
        "quantity_values": sorted({value for row in rows for value in row["quantity_values"]}),
        "median_content_length_bytes": median([row["content_length"] for row in rows]) if rows else None,
        "altitude_metadata": [row["altitude_metadata"] for row in rows],
    }


def audit(*, radars: Iterable[str], days: Iterable[str], samples_per_day: int, public_base: str, timeout_seconds: float) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for radar in sorted({value.lower() for value in radars}):
        samples: list[dict[str, Any]] = []
        unavailable: list[dict[str, str]] = []
        for day in days:
            keys = s3_day_objects(public_base=public_base, radar=radar, day=day, timeout_seconds=timeout_seconds)
            if not keys:
                unavailable.append({"day": day, "reason": "no_public_vp_objects"})
                continue
            for key in sample_keys(keys, samples_per_day):
                samples.append(fetch_and_inspect(public_base=public_base, key=key, timeout_seconds=timeout_seconds))
        results[radar] = {"summary": summarise_radar(samples), "samples": samples, "unavailable": unavailable}
    return {
        "schema_version": "birdcast-euro-aloft-vp-structure-audit-1.0",
        "purpose": "Compare public VP schema and metadata at MTR scale outlier radars before modelling.",
        "raw_source_persisted": False,
        "source": "Aloft BALTRAD public hdf5 VP objects",
        "product_type": "VP, not PVOL",
        "radars": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--radar", action="append", default=[], help="Aloft radar identifier; repeatable")
    parser.add_argument("--day", action="append", default=[], help="UTC day YYYYMMDD; repeatable")
    parser.add_argument("--samples-per-day", type=int, default=3)
    parser.add_argument("--public-base", default=PUBLIC_BASE)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(
        radars=args.radar or list(DEFAULT_RADARS),
        days=args.day or list(DEFAULT_DAYS),
        samples_per_day=args.samples_per_day,
        public_base=args.public_base,
        timeout_seconds=args.timeout_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "radar_count": len(report["radars"]), "raw_source_persisted": False}, indent=2))


if __name__ == "__main__":
    main()
