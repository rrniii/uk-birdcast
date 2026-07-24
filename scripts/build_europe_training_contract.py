#!/usr/bin/env python3
"""Freeze Europe GAMM predictor availability, ranges and temporal support."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path


def build(training_csv: Path, model_spec: Path, output: Path) -> dict[str, object]:
    spec = json.loads(model_spec.read_text(encoding="utf-8"))
    predictors = [str(name) for name in spec.get("predictors", [])]
    if not predictors:
        raise ValueError("model spec has no predictors")
    with training_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("Europe training table is empty")
    columns = set(rows[0])
    missing_columns = sorted(set(predictors) - columns)
    if missing_columns:
        raise ValueError(f"Europe training table lacks model predictors: {', '.join(missing_columns)}")
    values: dict[str, list[float]] = {name: [] for name in predictors}
    times = []
    for row in rows:
        time = str(row.get("time_utc") or "")
        try:
            times.append(datetime.fromisoformat(time.replace("Z", "+00:00")).date())
        except ValueError as exc:
            raise ValueError(f"invalid training UTC timestamp: {time}") from exc
        for name in predictors:
            try:
                value = float(row[name])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid predictor {name} in Europe training table") from exc
            if not math.isfinite(value):
                raise ValueError(f"non-finite predictor {name} in Europe training table")
            values[name].append(value)
    payload = {
        "schema_version": "birdcast-euro-training-contract-1.0",
        "status": "passed",
        "raw_source_persisted": False,
        "training_csv": str(training_csv),
        "model_spec": str(model_spec),
        "model_spec_sha256": hashlib.sha256(model_spec.read_bytes()).hexdigest(),
        "row_count": len(rows),
        "radar_count": len({str(row.get("radar") or "") for row in rows}),
        "first_day_utc": min(times).isoformat(),
        "latest_complete_day_utc": max(times).isoformat(),
        "feature_ranges": {name: _percentile_range(items) for name, items in values.items()},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def _percentile_range(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "lower": ordered[max(0, int(len(ordered) * .01) - 1)],
        "upper": ordered[min(len(ordered) - 1, int(len(ordered) * .99))],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--model-spec", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(build(Path(args.training_csv), Path(args.model_spec), Path(args.output)), indent=2))
