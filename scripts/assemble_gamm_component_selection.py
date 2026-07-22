#!/usr/bin/env python3
"""Write a validated, per-pulse selection manifest for compatible GAMM runs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SP_VECTOR_TARGETS = ("bird_u_ms", "bird_v_ms")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def metric_index(payload: dict[str, Any], validation: str) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(item["pulse"]), str(item["target"])): item
        for item in payload["metrics"]
        if item.get("validation") == validation
    }


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def component(
    run_dir: Path,
    pulse: str,
    target: str,
    metrics: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    model = run_dir / f"gamm_{pulse}_{target}.rds"
    if not model.is_file():
        raise SystemExit(f"missing selected GAMM artifact: {model}")
    metric_key = (pulse, target)
    if metric_key not in metrics:
        raise SystemExit(f"missing leave-one-radar-out metric for {pulse}/{target}")
    return {
        "model_rds": str(model),
        "sha256": fingerprint(model),
        "leave_one_radar_out": {
            field: metrics[metric_key][field] for field in ("r_squared", "rmse", "mae", "bias")
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--sp-vector-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    baseline_metrics = load_json(args.baseline_dir / "metrics.json")
    vector_metrics = load_json(args.sp_vector_dir / "metrics.json")
    baseline_loro = metric_index(baseline_metrics, "leave_one_radar_out")
    vector_loro = metric_index(vector_metrics, "leave_one_radar_out")
    baseline_time = metric_index(baseline_metrics, "blocked_time")
    vector_time = metric_index(vector_metrics, "blocked_time")

    for target in SP_VECTOR_TARGETS:
        key = ("sp", target)
        if vector_loro[key]["r_squared"] <= baseline_loro[key]["r_squared"]:
            raise SystemExit(f"SP {target} does not improve leave-one-radar-out R2")
        if vector_time[key]["r_squared"] <= baseline_time[key]["r_squared"]:
            raise SystemExit(f"SP {target} does not improve blocked-time R2")

    components: dict[str, dict[str, dict[str, Any]]] = {"lp": {}, "sp": {}}
    for target in ("mtr_birds_km_h", "vid_birds_per_km2", "bird_u_ms", "bird_v_ms"):
        components["lp"][target] = component(args.baseline_dir, "lp", target, baseline_loro)
    for target in ("mtr_birds_km_h", "vid_birds_per_km2"):
        components["sp"][target] = component(args.baseline_dir, "sp", target, baseline_loro)
    for target in SP_VECTOR_TARGETS:
        selected = component(args.sp_vector_dir, "sp", target, vector_loro)
        key = ("sp", target)
        selected["blocked_time"] = {
            field: vector_time[key][field] for field in ("r_squared", "rmse", "mae", "bias")
        }
        selected["baseline_leave_one_radar_out_r_squared"] = baseline_loro[key]["r_squared"]
        selected["baseline_blocked_time_r_squared"] = baseline_time[key]["r_squared"]
        components["sp"][target] = selected

    manifest = {
        "schema_version": "uk-gamm-component-selection-v1",
        "selection_id": "uk-gamm-heldout-v2-sp-vector-925",
        "selection_policy": (
            "Use the selected 850-hPa GAMM for all intensity products and LP vectors. "
            "Use the 925-hPa wind-interaction GAMM only for SP u/v when it improves "
            "both leave-one-UK-radar-out and blocked-time R2."
        ),
        "components": components,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
