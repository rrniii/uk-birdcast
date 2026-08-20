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
import csv
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
    "product",
    "object",
    "version",
    "quantity",
    "gain",
    "offset",
    "nodata",
    "undetect",
    "height",
    "height_min",
    "height_max",
    "levels",
    "interval",
    "rscale",
    "nbins",
    "startdate",
    "starttime",
    "enddate",
    "endtime",
    "date",
    "time",
    "source",
    "radar",
}
DYNAMIC_ATTRIBUTES = {"date", "time", "startdate", "starttime", "enddate", "endtime"}
VP_TO_VPTS_FIELDS = {
    "HGHT": "height",
    "u": "u",
    "v": "v",
    "w": "w",
    "ff": "ff",
    "dd": "dd",
    "sd_vvp": "sd_vvp",
    "gap": "gap",
    "eta": "eta",
    "dens": "dens",
    "dbz": "dbz",
    "DBZH": "dbz_all",
    "n": "n",
    "n_dbz": "n_dbz",
    "n_all": "n_all",
    "n_dbz_all": "n_dbz_all",
}


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
    return [
        item.text
        for item in root.findall(f".//{namespace}Key")
        if item.text and item.text.endswith(".h5")
    ]


def sample_keys(keys: list[str], count: int) -> list[str]:
    if count < 1:
        raise ValueError("sample count must be positive")
    if len(keys) <= count:
        return keys
    indices = (
        {round(index * (len(keys) - 1) / (count - 1)) for index in range(count)}
        if count > 1
        else {len(keys) // 2}
    )
    return [keys[index] for index in sorted(indices)]


def numeric_summary(
    dataset: Any,
    *,
    nodata: float | int | None = None,
    undetect: float | int | None = None,
) -> dict[str, Any] | None:
    """Calculate statistics in memory and discard the raw VP numeric array."""
    try:
        import numpy as np

        values = np.asarray(dataset[()])
    except (TypeError, ValueError, OSError):
        return None
    if values.dtype.kind not in {"i", "u", "f"}:
        return None
    flat = values.reshape(-1)
    finite = flat[np.isfinite(flat)] if values.dtype.kind == "f" else flat
    for invalid in (nodata, undetect):
        if invalid is not None:
            finite = finite[finite != invalid]
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
    dataset_nodes: list[tuple[dict[str, Any], Any]] = []
    with h5py.File(io.BytesIO(payload), "r") as handle:
        groups.append(
            {
                "path": "/",
                "attribute_keys": sorted(map(str, handle.attrs.keys())),
                "metadata": selected_attributes(handle.attrs),
            }
        )

        def visitor(path: str, node: Any) -> None:
            if isinstance(node, h5py.Group):
                groups.append(
                    {
                        "path": f"/{path}",
                        "attribute_keys": sorted(map(str, node.attrs.keys())),
                        "metadata": selected_attributes(node.attrs),
                    }
                )
                return
            record = {
                "path": f"/{path}",
                "shape": list(node.shape),
                "dtype": str(node.dtype),
                "attribute_keys": sorted(map(str, node.attrs.keys())),
                "metadata": selected_attributes(node.attrs),
            }
            datasets.append(record)
            dataset_nodes.append((record, node))

        handle.visititems(visitor)
        quantity_metadata = {
            group["path"].removesuffix("/what"): group["metadata"]
            for group in groups
            if group["path"].endswith("/what") and group["metadata"].get("quantity") is not None
        }
        for record, node in dataset_nodes:
            metadata = quantity_metadata.get(record["path"].rsplit("/", 1)[0], {})
            record["quantity"] = metadata.get("quantity")
            record["quantity_metadata"] = metadata
            summary = numeric_summary(
                node,
                nodata=metadata.get("nodata"),
                undetect=metadata.get("undetect"),
            )
            if summary is not None:
                record["numeric_summary"] = summary

    fingerprint = {
        "groups": [
            {
                "path": group["path"],
                "attribute_keys": group["attribute_keys"],
                "metadata": selected_attributes(group["metadata"], include_dynamic=False),
            }
            for group in groups
        ],
        "datasets": [
            {
                "path": dataset["path"],
                "shape": dataset["shape"],
                "dtype": dataset["dtype"],
                "attribute_keys": dataset["attribute_keys"],
                "metadata": selected_attributes(dataset["metadata"], include_dynamic=False),
            }
            for dataset in datasets
        ],
    }
    return {
        "schema_fingerprint_sha256": hashlib.sha256(
            json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "groups": groups,
        "datasets": datasets,
        "quantity_values": sorted(
            {
                str(record["metadata"]["quantity"])
                for record in [*groups, *datasets]
                if record["metadata"].get("quantity") is not None
            }
        ),
        "altitude_metadata": [
            {
                "path": record["path"],
                "metadata": {
                    key: value
                    for key, value in record["metadata"].items()
                    if key
                    in {
                        "height",
                        "height_min",
                        "height_max",
                        "levels",
                        "interval",
                        "nbins",
                        "rscale",
                    }
                },
            }
            for record in [*groups, *datasets]
            if any(
                key in record["metadata"]
                for key in {
                    "height",
                    "height_min",
                    "height_max",
                    "levels",
                    "interval",
                    "nbins",
                    "rscale",
                }
            )
        ],
        "quantity_summaries": {
            str(record["quantity"]): record.get("numeric_summary")
            for record in datasets
            if record.get("quantity") is not None and record.get("numeric_summary") is not None
        },
    }


def fetch_and_inspect(
    *,
    public_base: str,
    key: str,
    timeout_seconds: float,
    vpts_rows: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    url = f"{public_base.rstrip('/')}/{key}"
    with urlopen(url, timeout=timeout_seconds) as response:
        payload = response.read()
        headers = response.headers
    result = inspect_hdf5(payload)
    vpts_comparison = compare_vp_to_vpts(payload, vpts_rows) if vpts_rows is not None else None
    return {
        "url": url,
        "key": key,
        "content_length": len(payload),
        "etag": headers.get("ETag"),
        "last_modified": headers.get("Last-Modified"),
        "sha256": hashlib.sha256(payload).hexdigest(),
        **result,
        **({"vpts_reconstruction": vpts_comparison} if vpts_comparison is not None else {}),
    }


def vpts_url(*, public_base: str, radar: str, day: str) -> str:
    return f"{public_base.rstrip('/')}/baltrad/daily/{radar.lower()}/{day[:4]}/{radar.lower()}_vpts_{day}.csv"


def vpts_rows_by_source(
    *, public_base: str, radar: str, day: str, timeout_seconds: float
) -> tuple[str, dict[str, list[dict[str, str]]]]:
    """Stream one daily VPTS CSV and index it by immutable source VP filename."""
    url = vpts_url(public_base=public_base, radar=radar, day=day)
    with urlopen(url, timeout=timeout_seconds) as response:
        data = response.read()
    rows: dict[str, list[dict[str, str]]] = {}
    for row in csv.DictReader(io.StringIO(data.decode("utf-8"))):
        source_file = row.get("source_file")
        if source_file:
            rows.setdefault(source_file, []).append(row)
    return url, rows


def float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def vp_value(value: Any, metadata: dict[str, Any]) -> float | None:
    parsed = float_or_none(value)
    if parsed is None or parsed in {
        float_or_none(metadata.get("nodata")),
        float_or_none(metadata.get("undetect")),
    }:
        return None
    return parsed * float(metadata.get("gain", 1.0)) + float(metadata.get("offset", 0.0))


def vpts_value(value: str | None, *, field: str) -> float | None:
    if field == "gap":
        if value == "TRUE":
            return 1.0
        if value == "FALSE":
            return 0.0
    return float_or_none(value)


def compare_vp_to_vpts(payload: bytes, rows: list[dict[str, str]] | None) -> dict[str, Any]:
    """Compare one in-memory VP with VPTS rows that name it as source_file.

    The report contains only mismatch counts and numerical error summaries; raw
    VP/VPTS values are released as soon as this function returns.
    """
    if not rows:
        return {"status": "missing_source_rows", "source_row_count": 0}
    try:
        import h5py
        import numpy as np
    except ImportError as error:  # pragma: no cover - deployment dependency
        raise RuntimeError(
            "h5py and numpy are required for VP/VPTS reconstruction auditing"
        ) from error

    profiles: dict[str, tuple[dict[str, Any], Any]] = {}
    with h5py.File(io.BytesIO(payload), "r") as handle:

        def visitor(path: str, node: Any) -> None:
            if not isinstance(node, h5py.Dataset) or not path.endswith("/data"):
                return
            parent = node.parent
            what = parent.get("what")
            if what is None or "quantity" not in what.attrs:
                return
            quantity = str(normalise_attribute(what.attrs["quantity"]))
            profiles[quantity] = (selected_attributes(what.attrs), np.asarray(node[()]).reshape(-1))

        handle.visititems(visitor)

    heights = profiles.get("HGHT")
    if heights is None:
        return {"status": "missing_hght", "source_row_count": len(rows)}
    height_metadata, height_values = heights
    indexed_rows = {
        round(value, 6): row
        for row in rows
        if (value := float_or_none(row.get("height"))) is not None
    }
    comparison: dict[str, dict[str, Any]] = {}
    matched_heights = 0
    for raw_height in height_values:
        height = vp_value(raw_height, height_metadata)
        if height is not None and round(height, 6) in indexed_rows:
            matched_heights += 1

    for quantity, vpts_field in VP_TO_VPTS_FIELDS.items():
        profile = profiles.get(quantity)
        if profile is None:
            continue
        metadata, values = profile
        pairs: list[float] = []
        matched_missing = 0
        mismatched_missing = 0
        for index, raw_height in enumerate(height_values):
            height = vp_value(raw_height, height_metadata)
            row = indexed_rows.get(round(height, 6)) if height is not None else None
            if row is None or index >= len(values):
                continue
            expected = vp_value(values[index], metadata)
            observed = vpts_value(row.get(vpts_field), field=vpts_field)
            if expected is None and observed is None:
                matched_missing += 1
            elif expected is None or observed is None:
                mismatched_missing += 1
            else:
                pairs.append(abs(expected - observed))
        comparison[quantity] = {
            "vpts_field": vpts_field,
            "matched_value_count": len(pairs),
            "matched_missing_count": matched_missing,
            "mismatched_missing_count": mismatched_missing,
            "mean_absolute_difference": mean(pairs) if pairs else None,
            "max_absolute_difference": max(pairs) if pairs else None,
            "within_1e-6_count": sum(delta <= 1e-6 for delta in pairs),
        }
    return {
        "status": "compared",
        "source_row_count": len(rows),
        "matched_height_count": matched_heights,
        "comparison": comparison,
    }


def summarise_radar(samples: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(samples)
    quantity_stats: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        for quantity, summary in row.get("quantity_summaries", {}).items():
            if summary and int(summary.get("finite_count") or 0) > 0:
                quantity_stats.setdefault(quantity, []).append(summary)
    quantity_summary = {
        quantity: {
            "sample_count": len(values),
            "median_finite_count": median([int(value["finite_count"]) for value in values]),
            "median_mean": median([float(value["mean"]) for value in values]),
            "median_min": median([float(value["min"]) for value in values]),
            "median_max": median([float(value["max"]) for value in values]),
        }
        for quantity, values in sorted(quantity_stats.items())
    }
    reconstruction_stats: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        reconstruction = row.get("vpts_reconstruction", {})
        if reconstruction.get("status") != "compared":
            continue
        for quantity, metric in reconstruction.get("comparison", {}).items():
            reconstruction_stats.setdefault(quantity, []).append(metric)
    reconstruction_summary = {
        quantity: {
            "profile_count": len(metrics),
            "matched_value_count": sum(int(metric["matched_value_count"]) for metric in metrics),
            "matched_missing_count": sum(
                int(metric["matched_missing_count"]) for metric in metrics
            ),
            "mismatched_missing_count": sum(
                int(metric["mismatched_missing_count"]) for metric in metrics
            ),
            "max_absolute_difference": max(
                (
                    float(metric["max_absolute_difference"])
                    for metric in metrics
                    if metric["max_absolute_difference"] is not None
                ),
                default=None,
            ),
            "within_1e-6_count": sum(int(metric["within_1e-6_count"]) for metric in metrics),
        }
        for quantity, metrics in sorted(reconstruction_stats.items())
    }
    return {
        "sample_count": len(rows),
        "schema_fingerprint_count": len({row["schema_fingerprint_sha256"] for row in rows}),
        "schema_fingerprints": sorted({row["schema_fingerprint_sha256"] for row in rows}),
        "quantity_values": sorted({value for row in rows for value in row["quantity_values"]}),
        "median_content_length_bytes": median([row["content_length"] for row in rows])
        if rows
        else None,
        "quantity_summary": quantity_summary,
        "vpts_reconstruction_summary": reconstruction_summary,
        "altitude_metadata": [row["altitude_metadata"] for row in rows],
    }


def audit(
    *,
    radars: Iterable[str],
    days: Iterable[str],
    samples_per_day: int,
    public_base: str,
    timeout_seconds: float,
    compare_vpts: bool,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for radar in sorted({value.lower() for value in radars}):
        samples: list[dict[str, Any]] = []
        unavailable: list[dict[str, str]] = []
        for day in days:
            keys = s3_day_objects(
                public_base=public_base, radar=radar, day=day, timeout_seconds=timeout_seconds
            )
            if not keys:
                unavailable.append({"day": day, "reason": "no_public_vp_objects"})
                continue
            vpts_rows: dict[str, list[dict[str, str]]] | None = None
            if compare_vpts:
                try:
                    _, vpts_rows = vpts_rows_by_source(
                        public_base=public_base,
                        radar=radar,
                        day=day,
                        timeout_seconds=timeout_seconds,
                    )
                except OSError as error:
                    unavailable.append(
                        {"day": day, "reason": f"vpts_unavailable:{error.__class__.__name__}"}
                    )
            for key in sample_keys(keys, samples_per_day):
                source_file = key.rsplit("/", 1)[-1]
                samples.append(
                    fetch_and_inspect(
                        public_base=public_base,
                        key=key,
                        timeout_seconds=timeout_seconds,
                        vpts_rows=vpts_rows.get(source_file, []) if vpts_rows is not None else None,
                    )
                )
        results[radar] = {
            "summary": summarise_radar(samples),
            "samples": samples,
            "unavailable": unavailable,
        }
    return {
        "schema_version": "birdcast-euro-aloft-vp-structure-audit-1.1",
        "purpose": "Compare public VP schema and metadata at MTR scale outlier radars before modelling.",
        "raw_source_persisted": False,
        "source": "Aloft BALTRAD public hdf5 VP objects",
        "product_type": "VP, not PVOL",
        "radars": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--radar", action="append", default=[], help="Aloft radar identifier; repeatable"
    )
    parser.add_argument("--day", action="append", default=[], help="UTC day YYYYMMDD; repeatable")
    parser.add_argument("--samples-per-day", type=int, default=3)
    parser.add_argument("--public-base", default=PUBLIC_BASE)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument(
        "--compare-vpts",
        action="store_true",
        help="Compare sampled VP profiles with same-source daily VPTS rows",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(
        radars=args.radar or list(DEFAULT_RADARS),
        days=args.day or list(DEFAULT_DAYS),
        samples_per_day=args.samples_per_day,
        public_base=args.public_base,
        timeout_seconds=args.timeout_seconds,
        compare_vpts=args.compare_vpts,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "radar_count": len(report["radars"]),
                "raw_source_persisted": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
