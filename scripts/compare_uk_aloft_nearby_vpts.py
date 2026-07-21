#!/usr/bin/env python3
"""Compare existing, nearby UK and Aloft VPTS profile structure.

This is deliberately a product-structure diagnostic, not a co-located
instrument validation.  It reads daily VPTS files from both archives, matches
profiles at a 15-minute cadence, and writes only compact statistics.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from statistics import mean, median
from urllib.request import urlopen

from birdcast_uk.archive import uk_vpts_object
from birdcast_uk.static_artifacts import utc_now


# These are the three closest available UK--Aloft pairings.  Their separation
# means results expose regional/product-scale differences, not same-radar bias.
PAIRS = {
    "frcae_jersey": {
        "aloft_radar": "frcae", "uk_radar": "jersey", "distance_km": 153.7,
        "aloft_label": "Falaise", "uk_label": "Jersey",
    },
    "frabb_thurnham": {
        "aloft_radar": "frabb", "uk_radar": "thurnham", "distance_km": 155.3,
        "aloft_label": "Abbeville", "uk_label": "Thurnham",
    },
    "bejab_thurnham": {
        "aloft_radar": "bejab", "uk_radar": "thurnham", "distance_km": 171.6,
        "aloft_label": "Jabbeke", "uk_label": "Thurnham",
    },
}
VARIABLES = ("dens", "eta", "dbz", "u", "v", "ff")
ALTITUDE_MIN_M = 200.0
ALTITUDE_MAX_M = 4000.0


def finite(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_time(value: str) -> datetime:
    result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def fetch_to_cache(url: str, destination: Path) -> bool:
    """Fetch an existing public object once; cache known unavailable days too."""
    if destination.exists() and destination.stat().st_size:
        return True
    missing = destination.with_suffix(destination.suffix + ".missing")
    if missing.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urlopen(url, timeout=90) as response:
            destination.write_bytes(response.read())
    except Exception as error:
        # The marker avoids repeated network calls for a documented gap while
        # preserving the original VPTS archive as read-only.
        missing.write_text(f"{type(error).__name__}: {error}\n", encoding="utf-8")
        return False
    return True


def load_profiles(path: Path) -> dict[datetime, dict[float, dict[str, str]]]:
    profiles: dict[datetime, dict[float, dict[str, str]]] = defaultdict(dict)
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            height = finite(row.get("height"))
            if height is None or not ALTITUDE_MIN_M <= height <= ALTITUDE_MAX_M:
                continue
            try:
                profiles[parse_time(str(row["datetime"]))][height] = row
            except (KeyError, ValueError):
                continue
    return dict(profiles)


def nearest_profile(profiles: dict[datetime, dict[float, dict[str, str]]], target: datetime) -> tuple[datetime, dict[float, dict[str, str]]]:
    selected = min(profiles, key=lambda value: abs((value - target).total_seconds()))
    return selected, profiles[selected]


def correlation(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 2:
        return None
    left, right = zip(*pairs)
    left_mean, right_mean = mean(left), mean(right)
    denominator = math.sqrt(sum((value - left_mean) ** 2 for value in left) * sum((value - right_mean) ** 2 for value in right))
    if not denominator:
        return None
    return sum((a - left_mean) * (b - right_mean) for a, b in pairs) / denominator


def statistics(pairs: list[tuple[float, float]], *, log_ratio: bool = False) -> dict[str, float | int | None]:
    if not pairs:
        return {"count": 0, "uk_mean": None, "aloft_mean": None, "bias": None, "rmse": None, "correlation": None, "median_uk_to_aloft_ratio": None}
    uk, aloft = zip(*pairs)
    residuals = [a - b for a, b in pairs]
    ratio = [a / b for a, b in pairs if b > 0 and a > 0]
    return {
        "count": len(pairs),
        "uk_mean": mean(uk),
        "aloft_mean": mean(aloft),
        "bias": mean(residuals),
        "rmse": math.sqrt(mean(value * value for value in residuals)),
        "correlation": correlation(pairs),
        "median_uk_to_aloft_ratio": median(ratio) if ratio else None,
        "median_log10_uk_to_aloft_ratio": median([math.log10(value) for value in ratio]) if log_ratio and ratio else None,
    }


def compare_pair(pair_id: str, pair: dict[str, object], aloft_dir: Path, uk_cache_dir: Path) -> dict[str, object]:
    raw: dict[str, list[tuple[float, float]]] = {name: [] for name in VARIABLES}
    matched_profiles = matched_altitude_rows = downloaded = unavailable = 0
    offsets: list[float] = []
    for aloft_path in sorted(aloft_dir.glob(f"{pair['aloft_radar']}_vpts_*.csv")):
        day = aloft_path.stem.rsplit("_", 1)[-1]
        uk_object = uk_vpts_object(radar=str(pair["uk_radar"]), day=day, pulse="lp")
        uk_path = uk_cache_dir / str(pair["uk_radar"]) / f"{day}_lp_vpts.csv"
        try:
            before = uk_path.exists()
            available = fetch_to_cache(uk_object.url, uk_path)
            downloaded += int(available and not before)
        except Exception:
            unavailable += 1
            continue
        if not available:
            unavailable += 1
            continue
        aloft_profiles, uk_profiles = load_profiles(aloft_path), load_profiles(uk_path)
        if not aloft_profiles or not uk_profiles:
            continue
        for aloft_time, aloft_rows in aloft_profiles.items():
            uk_time, uk_rows = nearest_profile(uk_profiles, aloft_time)
            offset = abs((uk_time - aloft_time).total_seconds())
            if offset > 300:
                continue
            common_heights = aloft_rows.keys() & uk_rows.keys()
            if not common_heights:
                continue
            matched_profiles += 1
            matched_altitude_rows += len(common_heights)
            offsets.append(offset)
            for height in common_heights:
                for variable in VARIABLES:
                    uk_value, aloft_value = finite(uk_rows[height].get(variable)), finite(aloft_rows[height].get(variable))
                    if uk_value is not None and aloft_value is not None:
                        raw[variable].append((uk_value, aloft_value))
    return {
        "pair_id": pair_id,
        **pair,
        "comparison_class": "nearby_radar_profile_structure",
        "uk_pulse": "lp",
        "altitude_band_m": [ALTITUDE_MIN_M, ALTITUDE_MAX_M],
        "matched_profile_count": matched_profiles,
        "matched_altitude_row_count": matched_altitude_rows,
        "median_profile_time_offset_seconds": median(offsets) if offsets else None,
        "downloaded_uk_file_count": downloaded,
        "unavailable_uk_file_count": unavailable,
        "variables": {name: statistics(values, log_ratio=name == "dens") for name, values in raw.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aloft-vpts-dir", type=Path, required=True)
    parser.add_argument("--uk-cache-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pair", action="append", choices=sorted(PAIRS))
    args = parser.parse_args()
    selected = args.pair or sorted(PAIRS)
    results = [compare_pair(pair_id, PAIRS[pair_id], args.aloft_vpts_dir, args.uk_cache_dir) for pair_id in selected]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "schema_version": "birdcast-uk-nearby-vpts-structure-1.0",
        "generated_at_utc": utc_now(),
        "purpose": "Compare raw VPTS profile structure before modelling; no VP, VPTS, or PVOL products are created.",
        "interpretation": "Sites are 154-172 km apart. Differences combine biological spatial variation with archive/product processing differences and must not be treated as co-located instrument bias.",
        "pair_count": len(results),
        "pairs": results,
    }, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "pair_count": len(results)}, indent=2))


if __name__ == "__main__":
    main()
