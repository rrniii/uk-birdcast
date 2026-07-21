#!/usr/bin/env python3
"""Run a full-period, external Aloft VPTS validation against GAMM predictions."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from birdcast_uk.external_validation import evaluate_external_vpts, hourly_vpts_observations


SITES = {
    "frpla": {"name": "Plabennec", "latitude": 48.4609, "longitude": -4.4298, "grid": [48.5, -4.5], "grid_distance_km": 6.7},
    "bejab": {"name": "Jabbeke", "latitude": 51.1919, "longitude": 3.0641, "grid": [51.25, 3.0], "grid_distance_km": 7.8},
    "frcae": {"name": "Falaise", "latitude": 48.9272, "longitude": -0.1496, "grid": [49.0, -0.25], "grid_distance_km": 10.9},
    "frtre": {"name": "Treillieres", "latitude": 47.3374, "longitude": -1.6563, "grid": [47.25, -1.75], "grid_distance_km": 12.0},
    "iesha": {"name": "Shannon", "latitude": 52.6928, "longitude": -8.9201, "grid": [52.75, -8.75], "grid_distance_km": 13.1},
    "frabb": {"name": "Abbeville", "latitude": 50.1360, "longitude": 1.8347, "grid": [50.25, 1.75], "grid_distance_km": 14.0},
    "frave": {"name": "Avesnes", "latitude": 50.1283, "longitude": 3.8118, "grid": [50.25, 3.75], "grid_distance_km": 14.2},
}


def normal_time(value: str) -> str:
    text = str(value).replace(".000000000", "")
    return text if text.endswith("Z") else f"{text}Z"


def load_predictions(path: Path) -> dict[tuple[str, float, float], dict[str, str]]:
    with path.open(newline="") as handle:
        return {
            (normal_time(row["time_utc"]), float(row["latitude"]), float(row["longitude"])): row
            for row in csv.DictReader(handle)
        }


def matched(site_id: str, observations, predictions):
    latitude, longitude = SITES[site_id]["grid"]
    return [
        observation
        for observation in observations
        if (normal_time(observation["time_utc"]), latitude, longitude) in predictions
    ], [
        predictions[(normal_time(observation["time_utc"]), latitude, longitude)]
        for observation in observations
        if (normal_time(observation["time_utc"]), latitude, longitude) in predictions
    ]


def evaluate(vpts_dir: Path, prediction_csv: Path, pulse: str, site_ids: set[str]) -> dict:
    predictions = load_predictions(prediction_csv)
    per_site = {site: {"observations": [], "predictions": [], "files": 0, "valid_hours": 0} for site in site_ids}
    for path in sorted(vpts_dir.glob("*_vpts_*.csv")):
        site_id = path.name[:5]
        if site_id not in site_ids:
            continue
        with path.open(newline="") as handle:
            observations = hourly_vpts_observations(csv.DictReader(handle))
        selected_observations, selected_predictions = matched(site_id, observations, predictions)
        site = per_site[site_id]
        site["files"] += 1
        site["valid_hours"] += len(observations)
        site["observations"].extend(selected_observations)
        site["predictions"].extend(selected_predictions)

    reports = {}
    all_observations, all_predictions = [], []
    for site_id, result in per_site.items():
        report = evaluate_external_vpts(
            observations=result["observations"],
            predictions=result["predictions"],
            site={"radar": site_id, "source": "Aloft BALTRAD", **SITES[site_id]},
            model={"family": "gamm", "pulse": pulse, "radar_random_effect_excluded": True},
        )
        report["source_file_count"] = result["files"]
        report["valid_hour_count_before_prediction_match"] = result["valid_hours"]
        reports[site_id] = report
        # The reusable evaluator indexes profiles by time. Prefix with the
        # radar code here so a pooled report does not collapse same-hour
        # observations from different independent radars.
        for observation, prediction in zip(result["observations"], result["predictions"]):
            marker = f"{site_id}|{normal_time(observation['time_utc'])}"
            all_observations.append({**observation, "time_utc": marker})
            all_predictions.append({**prediction, "time_utc": marker})
    pooled = evaluate_external_vpts(
        observations=all_observations,
        predictions=all_predictions,
        site={"radars": sorted(SITES), "source": "Aloft BALTRAD"},
        model={"family": "gamm", "pulse": pulse, "radar_random_effect_excluded": True},
    )
    return {"pulse": pulse, "pooled": pooled, "sites": reports}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vpts-dir", type=Path, required=True)
    parser.add_argument("--lp-predictions", type=Path, required=True)
    parser.add_argument("--sp-predictions", type=Path, required=True)
    parser.add_argument("--expected-file-count", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--site", action="append", choices=sorted(SITES))
    parser.add_argument("--pulse", action="append", choices=["lp", "sp"])
    args = parser.parse_args()

    available = len(list(args.vpts_dir.glob("*_vpts_*.csv")))
    site_ids = set(args.site or SITES)
    pulses = args.pulse or ["lp", "sp"]
    prediction_paths = {"lp": args.lp_predictions, "sp": args.sp_predictions}
    output = {
        "schema_version": "birdcast-uk-aloft-external-evaluation-1.0",
        "period": ["2025-07-14", "2026-07-13"],
        "source": "Aloft BALTRAD daily VPTS",
        "expected_vpts_file_count": args.expected_file_count,
        "available_vpts_file_count": available,
        "unavailable_vpts_file_count": args.expected_file_count - available,
        "pulse_policy": "Aloft daily VPTS do not carry a UK LP/SP label; both UK GAMM pulse fits are reported.",
        "selected_sites": sorted(site_ids),
        "evaluations": {pulse: evaluate(args.vpts_dir, prediction_paths[pulse], pulse, site_ids) for pulse in pulses},
    }
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "available_vpts_file_count": available}, indent=2))


if __name__ == "__main__":
    main()
