#!/usr/bin/env python3
"""Compare held-out GAMM candidates against the selected UK baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PRIMARY_INTENSITY = (
    ("lp", "mtr_birds_km_h"),
    ("lp", "vid_birds_per_km2"),
    ("sp", "mtr_birds_km_h"),
)
LP_VECTORS = (("lp", "bird_u_ms"), ("lp", "bird_v_ms"))


def read_loro_metrics(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        (metric["pulse"], metric["target"]): metric
        for metric in payload.get("metrics", [])
        if metric.get("validation") == "leave_one_radar_out"
    }


def parse_candidate(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition("=")
    if not separator or not name or not path:
        raise argparse.ArgumentTypeError("candidate must be NAME=METRICS_JSON")
    return name, Path(path)


def compare(
    baseline: dict[tuple[str, str], dict[str, Any]],
    candidate: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for key in sorted(set(baseline) | set(candidate)):
        base = baseline.get(key)
        value = candidate.get(key)
        row: dict[str, Any] = {"pulse": key[0], "target": key[1]}
        if base:
            row.update({f"baseline_{field}": base.get(field) for field in ("r_squared", "rmse", "mae", "bias")})
        if value:
            row.update({f"candidate_{field}": value.get(field) for field in ("r_squared", "rmse", "mae", "bias")})
        if base and value:
            row["delta_r_squared"] = value["r_squared"] - base["r_squared"]
            row["delta_rmse"] = value["rmse"] - base["rmse"]
        rows.append(row)

    intensity_rows = [
        candidate[key]["r_squared"] - baseline[key]["r_squared"]
        for key in PRIMARY_INTENSITY
        if key in baseline and key in candidate
    ]
    vector_rows = [
        candidate[key]["r_squared"] - baseline[key]["r_squared"]
        for key in LP_VECTORS
        if key in baseline and key in candidate
    ]
    intensity_non_regression = bool(intensity_rows) and min(intensity_rows) >= -0.01
    vector_gain = bool(vector_rows) and sum(vector_rows) / len(vector_rows) >= 0.02
    return {
        "metrics": rows,
        "selection_gate": {
            "intensity_non_regression": intensity_non_regression,
            "lp_vector_mean_r_squared_delta": sum(vector_rows) / len(vector_rows) if vector_rows else None,
            "lp_vector_gain": vector_gain,
            "eligible_for_follow_up": intensity_non_regression and vector_gain,
            "rule": "A candidate is eligible only when each primary intensity R2 is within 0.01 of baseline and mean LP vector R2 improves by at least 0.02.",
        },
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# UK GAMM Candidate Comparison",
        "",
        "All figures are leave-one-UK-radar-out results.",
        "",
    ]
    for name, result in payload["candidates"].items():
        if result["status"] != "evaluated":
            lines.extend([f"## {name}", "", f"Metrics unavailable: `{result['metrics_path']}`.", ""])
            continue
        gate = result["selection_gate"]
        lines.extend(
            [
                f"## {name}",
                "",
                f"Eligible for follow-up: **{gate['eligible_for_follow_up']}**.",
                "",
                "| Pulse | Target | Baseline R2 | Candidate R2 | Delta R2 | Delta RMSE |",
                "|---|---|---:|---:|---:|---:|",
            ]
        )
        for row in result["metrics"]:
            lines.append(
                "| {pulse} | {target} | {baseline_r_squared:.4f} | {candidate_r_squared:.4f} | "
                "{delta_r_squared:+.4f} | {delta_rmse:+.4f} |".format(**row)
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", action="append", required=True, type=parse_candidate)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-markdown", required=True, type=Path)
    args = parser.parse_args()

    baseline = read_loro_metrics(args.baseline)
    if not baseline:
        raise SystemExit("baseline contains no leave-one-radar-out metrics")
    candidates: dict[str, Any] = {}
    for name, path in args.candidate:
        if not path.exists():
            candidates[name] = {"status": "missing", "metrics_path": str(path)}
            continue
        candidates[name] = {"status": "evaluated", "metrics_path": str(path), **compare(baseline, read_loro_metrics(path))}

    payload = {"baseline_metrics_path": str(args.baseline), "candidates": candidates}
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    args.output_markdown.write_text(markdown(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
