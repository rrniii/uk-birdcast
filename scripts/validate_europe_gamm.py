#!/usr/bin/env python3
"""Apply preregistered, site-level release gates to a Europe GAMM run."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from statistics import median


INTENSITY_TARGETS = {"mtr_birds_km_h", "vid_birds_per_km2"}


def finite(values):
    return [float(value) for value in values if value is not None and math.isfinite(float(value))]


def circular_error(u_obs: float, v_obs: float, u_pred: float, v_pred: float) -> float:
    observed = math.degrees(math.atan2(u_obs, v_obs)) % 360
    predicted = math.degrees(math.atan2(u_pred, v_pred)) % 360
    return abs((predicted - observed + 180) % 360 - 180)


def direction_summary(path: Path) -> dict[str, object]:
    paired: dict[tuple[str, str], dict[str, tuple[float, float]]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["target"] not in {"bird_u_ms", "bird_v_ms"}:
                continue
            key = (row["radar"], row["row_id"])
            paired.setdefault(key, {})[row["target"]] = (
                float(row["observed"]),
                float(row["predicted"]),
            )
    site_errors: dict[str, list[float]] = {}
    for (radar, _), values in paired.items():
        if set(values) != {"bird_u_ms", "bird_v_ms"}:
            continue
        u_obs, u_pred = values["bird_u_ms"]
        v_obs, v_pred = values["bird_v_ms"]
        site_errors.setdefault(radar, []).append(circular_error(u_obs, v_obs, u_pred, v_pred))
    medians = {radar: median(values) for radar, values in site_errors.items() if values}
    return {
        "matched_vector_rows": sum(len(values) for values in site_errors.values()),
        "site_count": len(medians),
        "median_site_direction_error_deg": median(medians.values()) if medians else None,
        "site_median_direction_error_deg": medians,
    }


def validate(metrics_path: Path, output: Path) -> dict[str, object]:
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    folds = [
        row for row in metrics.get("folds", [])
        if row.get("validation") == "leave_one_radar_out" and row.get("target") in INTENSITY_TARGETS
    ]
    log_skill = finite(row.get("log1p_r_squared") for row in folds)
    f1 = finite(row.get("top_decile_f1") for row in folds)
    direction_path = metrics.get("heldout_radar_vectors")
    direction = (
        direction_summary(Path(direction_path))
        if direction_path and Path(direction_path).is_file()
        else {"matched_vector_rows": 0, "site_count": 0, "median_site_direction_error_deg": None}
    )
    direction_error = direction["median_site_direction_error_deg"]
    gates = {
        "median_site_log1p_skill_positive": bool(log_skill and median(log_skill) > 0),
        "positive_site_fraction_at_least_0_75": bool(
            log_skill and sum(value > 0 for value in log_skill) / len(log_skill) >= 0.75
        ),
        "median_top_decile_f1_at_least_0_50": bool(f1 and median(f1) >= 0.50),
        "median_direction_error_at_most_30_deg": bool(
            direction_error is not None and float(direction_error) <= 30
        ),
    }
    passed = all(gates.values())
    payload = {
        "schema_version": "birdcast-euro-validation-1.0",
        "model_id": metrics.get("model_id"),
        "external_validation_status": "passed" if passed else "research_preview",
        "release_passed": passed,
        "site_equal_metrics": {
            "intensity_fold_count": len(folds),
            "median_log1p_r_squared": median(log_skill) if log_skill else None,
            "positive_log1p_skill_fraction": (
                sum(value > 0 for value in log_skill) / len(log_skill) if log_skill else None
            ),
            "median_top_decile_f1": median(f1) if f1 else None,
            **direction,
        },
        "gates": gates,
        "pooled_metrics_may_not_override_site_gates": True,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args()
    result = validate(Path(args.metrics), Path(args.output))
    print(json.dumps(result, indent=2))
    raise SystemExit(1 if args.require_pass and not result["release_passed"] else 0)
