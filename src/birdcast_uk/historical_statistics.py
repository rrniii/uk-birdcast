"""Historical daily statistics used by the August 2026 publication.

These calculation functions were brought into the release from the archived
vpts_empirical_analysis_20260815.py, retaining the existing local-solar-day,
altitude and LP/SP definitions. Scheduling, caching and completeness gates live
in historical_cycle.py; this module performs no discovery or publication.
"""

from __future__ import annotations

import csv
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

FILE_RE = re.compile(r"^(?P<date>[0-9]{8})_(?P<pulse>lp|sp)_vpts\.csv$")


def parse_datetime(value: str) -> datetime:
    text = (value or "").strip().strip('"')
    if not text:
        raise ValueError("empty datetime")
    if text.endswith("Z"):
        text = text[:-1]
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d%H%M%S", "%Y%m%d%H%M"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    raise ValueError("cannot parse datetime: {}".format(value))


def parse_float(value: object) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().strip('"')
    if not text or text.upper() == "NA" or text.lower() in {"nan", "null", "none"}:
        return None
    try:
        out = float(text)
    except ValueError:
        return None
    return out if math.isfinite(out) else None


def parse_bool(value: object) -> Optional[bool]:
    if value is None:
        return None
    text = str(value).strip().strip('"').lower()
    if text in {"true", "t", "1", "yes", "y"}:
        return True
    if text in {"false", "f", "0", "no", "n"}:
        return False
    return None


def season_for_date(value: datetime) -> str:
    month_day = value.month * 100 + value.day
    if 215 <= month_day <= 615:
        return "spring"
    if 616 <= month_day <= 714:
        return "summer"
    if 715 <= month_day <= 1130:
        return "autumn"
    return "winter"


def solar_elevation_deg(timestamp: datetime, latitude: float, longitude: float) -> float:
    minutes = timestamp.hour * 60 + timestamp.minute + timestamp.second / 60
    day_of_year = timestamp.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (day_of_year - 1 + (minutes / 60 - 12) / 24)
    declination = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    equation_of_time = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    true_solar_minutes = (minutes + equation_of_time + 4 * longitude) % 1440
    hour_angle_deg = true_solar_minutes / 4 - 180
    latitude_rad = math.radians(latitude)
    hour_angle_rad = math.radians(hour_angle_deg)
    elevation = math.asin(
        math.sin(latitude_rad) * math.sin(declination)
        + math.cos(latitude_rad) * math.cos(declination) * math.cos(hour_angle_rad)
    )
    return math.degrees(elevation)


def solar_period(timestamp: datetime, latitude: float, longitude: float) -> str:
    elevation = solar_elevation_deg(timestamp, latitude, longitude)
    if elevation > 0.0:
        return "day"
    if elevation < -6.0:
        return "night"
    return "civil_twilight"


def local_solar_datetime(timestamp: datetime, longitude: float) -> datetime:
    return timestamp + timedelta(minutes=4 * longitude)


def median_step_km(heights: List[float]) -> float:
    unique = sorted(set(h for h in heights if h is not None))
    diffs = [b - a for a, b in zip(unique, unique[1:]) if b > a]
    if not diffs:
        return 0.2
    diffs.sort()
    mid = len(diffs) // 2
    if len(diffs) % 2:
        step_m = diffs[mid]
    else:
        step_m = (diffs[mid - 1] + diffs[mid]) / 2
    if step_m <= 0:
        step_m = 200.0
    return step_m / 1000.0


def empty_accumulator() -> Dict[str, object]:
    return {
        "profile_count": 0,
        "profile_count_positive": 0,
        "row_count": 0,
        "row_count_used": 0,
        "row_count_gap": 0,
        "vid_birds_per_km2": 0.0,
        "vid_birds_per_km2_all_rows": 0.0,
        "eta_integrated": 0.0,
        "weighted_height_numerator": 0.0,
        "weighted_height_denominator": 0.0,
        "ff_sum": 0.0,
        "ff_count": 0,
        "bio_day_profile_count": 0,
        "source_file_count": 0,
    }


def add_profile_to_daily(
    daily: Dict[Tuple[str, int, str, str, str, str], Dict[str, object]],
    radar: str,
    pulse: str,
    timestamp: datetime,
    latitude: float,
    longitude: float,
    source_date: str,
    profile_rows: List[Dict[str, object]],
    metric: str,
    alt_min: float,
    alt_max: float,
    include_gap_primary: bool,
) -> None:
    heights: List[float] = []
    for row in profile_rows:
        h = parse_float(row.get("height"))
        if h is not None and alt_min <= h <= alt_max:
            heights.append(h)
    step_km = median_step_km(heights)
    vid_primary = 0.0
    vid_all = 0.0
    eta_integrated = 0.0
    weighted_height_numerator = 0.0
    weighted_height_denominator = 0.0
    ff_sum = 0.0
    ff_count = 0
    used = 0
    gap_rows = 0
    row_count = 0
    bio_day = None

    for row in profile_rows:
        row_count += 1
        h = parse_float(row.get("height"))
        if h is None or h < alt_min or h > alt_max:
            continue
        value = parse_float(row.get(metric))
        if value is None or value < 0:
            continue
        gap = parse_bool(row.get("gap"))
        if gap:
            gap_rows += 1
        contribution = value * step_km
        vid_all += contribution
        if include_gap_primary or gap is not True:
            vid_primary += contribution
            used += 1
            weighted_height_numerator += h * contribution
            weighted_height_denominator += contribution
        eta = parse_float(row.get("eta"))
        if eta is not None and (include_gap_primary or gap is not True):
            eta_integrated += eta * step_km
        ff = parse_float(row.get("ff"))
        if ff is not None and (include_gap_primary or gap is not True):
            ff_sum += ff
            ff_count += 1
        if bio_day is None:
            bio_day = parse_bool(row.get("day"))

    local_dt = local_solar_datetime(timestamp, longitude)
    local_date = local_dt.date().isoformat()
    year = local_dt.year
    season = season_for_date(local_dt)
    period = solar_period(timestamp, latitude, longitude)
    key = (radar, year, season, period, local_date, pulse)
    acc = daily.setdefault(key, empty_accumulator())
    acc["profile_count"] = int(acc["profile_count"]) + 1
    if vid_primary > 0:
        acc["profile_count_positive"] = int(acc["profile_count_positive"]) + 1
    acc["row_count"] = int(acc["row_count"]) + row_count
    acc["row_count_used"] = int(acc["row_count_used"]) + used
    acc["row_count_gap"] = int(acc["row_count_gap"]) + gap_rows
    acc["vid_birds_per_km2"] = float(acc["vid_birds_per_km2"]) + vid_primary
    acc["vid_birds_per_km2_all_rows"] = float(acc["vid_birds_per_km2_all_rows"]) + vid_all
    acc["eta_integrated"] = float(acc["eta_integrated"]) + eta_integrated
    acc["weighted_height_numerator"] = (
        float(acc["weighted_height_numerator"]) + weighted_height_numerator
    )
    acc["weighted_height_denominator"] = (
        float(acc["weighted_height_denominator"]) + weighted_height_denominator
    )
    acc["ff_sum"] = float(acc["ff_sum"]) + ff_sum
    acc["ff_count"] = int(acc["ff_count"]) + ff_count
    if bio_day is True:
        acc["bio_day_profile_count"] = int(acc["bio_day_profile_count"]) + 1
    # Source date is retained to diagnose UTC/local day movement.
    source_dates = acc.setdefault("source_dates", set())
    source_dates.add(source_date)


def summarize_file(
    path: Path,
    radar: str,
    pulse: str,
    metric: str,
    alt_min: float,
    alt_max: float,
    include_gap_primary: bool,
    daily: Dict[Tuple[str, int, str, str, str, str], Dict[str, object]],
) -> Dict[str, object]:
    source_date = FILE_RE.match(path.name).group("date")  # type: ignore[union-attr]
    profiles: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    row_count = 0
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("missing CSV header")
        required = {"datetime", "height", metric, "radar_latitude", "radar_longitude"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError("missing columns: {}".format(",".join(sorted(missing))))
        for row in reader:
            row_count += 1
            dt_raw = row.get("datetime")
            if dt_raw:
                profiles[dt_raw].append(row)
    profile_count = 0
    for dt_raw, rows in profiles.items():
        if not rows:
            continue
        timestamp = parse_datetime(dt_raw)
        latitude = parse_float(rows[0].get("radar_latitude"))
        longitude = parse_float(rows[0].get("radar_longitude"))
        if latitude is None or longitude is None:
            continue
        add_profile_to_daily(
            daily,
            radar,
            pulse,
            timestamp,
            latitude,
            longitude,
            source_date,
            rows,
            metric,
            alt_min,
            alt_max,
            include_gap_primary,
        )
        profile_count += 1
    return {"rows": row_count, "profiles": profile_count}


def finalize_daily_rows(
    daily: Dict[Tuple[str, int, str, str, str, str], Dict[str, object]],
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for (radar, year, season, period, date, pulse), acc in sorted(daily.items()):
        profile_count = int(acc["profile_count"])
        ff_count = int(acc["ff_count"])
        denom = float(acc["weighted_height_denominator"])
        source_dates = sorted(acc.get("source_dates", set()))
        row = {
            "radar": radar,
            "year": year,
            "season": season,
            "solar_period": period,
            "date": date,
            "pulse": pulse,
            "profile_count": profile_count,
            "profile_count_positive": int(acc["profile_count_positive"]),
            "row_count": int(acc["row_count"]),
            "row_count_used": int(acc["row_count_used"]),
            "row_count_gap": int(acc["row_count_gap"]),
            "vid_birds_per_km2": round(float(acc["vid_birds_per_km2"]), 8),
            "vid_birds_per_km2_all_rows": round(float(acc["vid_birds_per_km2_all_rows"]), 8),
            "eta_integrated": round(float(acc["eta_integrated"]), 8),
            "mean_vid_birds_per_km2_per_profile": round(
                float(acc["vid_birds_per_km2"]) / profile_count, 8
            )
            if profile_count
            else "",
            "mean_weighted_height_m": round(float(acc["weighted_height_numerator"]) / denom, 4)
            if denom > 0
            else "",
            "mean_ff_ms": round(float(acc["ff_sum"]) / ff_count, 4) if ff_count else "",
            "bio_day_profile_fraction": round(int(acc["bio_day_profile_count"]) / profile_count, 6)
            if profile_count
            else "",
            "source_date_count": len(source_dates),
            "source_dates": ";".join(source_dates[:5]) + (";..." if len(source_dates) > 5 else ""),
        }
        rows.append(row)
    return rows


def aggregate_annual(daily_rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, int, str, str, str], Dict[str, object]] = {}
    for row in daily_rows:
        key = (
            str(row["radar"]),
            int(row["year"]),
            str(row["season"]),
            str(row["solar_period"]),
            str(row["pulse"]),
        )
        acc = grouped.setdefault(
            key,
            {
                "radar": key[0],
                "year": key[1],
                "season": key[2],
                "solar_period": key[3],
                "pulse": key[4],
                "day_count": 0,
                "profile_count": 0,
                "row_count_used": 0,
                "row_count_gap": 0,
                "vid_birds_per_km2": 0.0,
                "eta_integrated": 0.0,
                "height_num": 0.0,
                "height_den": 0.0,
            },
        )
        acc["day_count"] = int(acc["day_count"]) + 1
        acc["profile_count"] = int(acc["profile_count"]) + int(row["profile_count"])
        acc["row_count_used"] = int(acc["row_count_used"]) + int(row["row_count_used"])
        acc["row_count_gap"] = int(acc["row_count_gap"]) + int(row["row_count_gap"])
        vid = float(row["vid_birds_per_km2"])
        acc["vid_birds_per_km2"] = float(acc["vid_birds_per_km2"]) + vid
        acc["eta_integrated"] = float(acc["eta_integrated"]) + float(row["eta_integrated"])
        height = parse_float(row.get("mean_weighted_height_m"))
        if height is not None and vid > 0:
            acc["height_num"] = float(acc["height_num"]) + height * vid
            acc["height_den"] = float(acc["height_den"]) + vid

    out: List[Dict[str, object]] = []
    for key, acc in sorted(grouped.items()):
        day_count = int(acc["day_count"])
        profile_count = int(acc["profile_count"])
        height_den = float(acc["height_den"])
        vid = float(acc["vid_birds_per_km2"])
        out.append(
            {
                "radar": acc["radar"],
                "year": acc["year"],
                "season": acc["season"],
                "solar_period": acc["solar_period"],
                "pulse": acc["pulse"],
                "day_count": day_count,
                "profile_count": profile_count,
                "row_count_used": acc["row_count_used"],
                "row_count_gap": acc["row_count_gap"],
                "vid_birds_per_km2": round(vid, 8),
                "eta_integrated": round(float(acc["eta_integrated"]), 8),
                "mean_daily_vid_birds_per_km2": round(vid / day_count, 8) if day_count else "",
                "mean_profile_vid_birds_per_km2": round(vid / profile_count, 8)
                if profile_count
                else "",
                "mean_weighted_height_m": round(float(acc["height_num"]) / height_den, 4)
                if height_den > 0
                else "",
            }
        )
    return out


def aggregate_network(annual_rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[int, str, str, str], Dict[str, object]] = {}
    for row in annual_rows:
        key = (int(row["year"]), str(row["season"]), str(row["solar_period"]), str(row["pulse"]))
        acc = grouped.setdefault(
            key,
            {
                "year": key[0],
                "season": key[1],
                "solar_period": key[2],
                "pulse": key[3],
                "radar_count": 0,
                "day_count": 0,
                "profile_count": 0,
                "vid_birds_per_km2": 0.0,
            },
        )
        acc["radar_count"] = int(acc["radar_count"]) + 1
        acc["day_count"] = int(acc["day_count"]) + int(row["day_count"])
        acc["profile_count"] = int(acc["profile_count"]) + int(row["profile_count"])
        acc["vid_birds_per_km2"] = float(acc["vid_birds_per_km2"]) + float(row["vid_birds_per_km2"])
    out = []
    for key, acc in sorted(grouped.items()):
        day_count = int(acc["day_count"])
        profile_count = int(acc["profile_count"])
        vid = float(acc["vid_birds_per_km2"])
        out.append(
            {
                "year": acc["year"],
                "season": acc["season"],
                "solar_period": acc["solar_period"],
                "pulse": acc["pulse"],
                "radar_count": acc["radar_count"],
                "day_count": day_count,
                "profile_count": profile_count,
                "vid_birds_per_km2": round(vid, 8),
                "mean_daily_vid_birds_per_km2": round(vid / day_count, 8) if day_count else "",
                "mean_profile_vid_birds_per_km2": round(vid / profile_count, 8)
                if profile_count
                else "",
            }
        )
    return out


def threshold_date(cumulative: List[Tuple[str, float]], fraction: float) -> str:
    total = cumulative[-1][1] if cumulative else 0.0
    if total <= 0:
        return ""
    target = total * fraction
    for date, value in cumulative:
        if value >= target:
            return date
    return cumulative[-1][0]


def days_between(start: str, end: str) -> object:
    if not start or not end:
        return ""
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days


def phenology(daily_rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, int, str, str, str], List[Dict[str, object]]] = defaultdict(list)
    for row in daily_rows:
        grouped[
            (
                str(row["radar"]),
                int(row["year"]),
                str(row["season"]),
                str(row["solar_period"]),
                str(row["pulse"]),
            )
        ].append(row)
    out: List[Dict[str, object]] = []
    for (radar, year, season, period, pulse), rows in sorted(grouped.items()):
        cumulative_value = 0.0
        cumulative: List[Tuple[str, float]] = []
        peak: Optional[Dict[str, object]] = None
        for row in sorted(rows, key=lambda item: str(item["date"])):
            value = float(row["vid_birds_per_km2"])
            cumulative_value += value
            cumulative.append((str(row["date"]), cumulative_value))
            if peak is None or value > float(peak["vid_birds_per_km2"]):
                peak = row
        p05 = threshold_date(cumulative, 0.05)
        p10 = threshold_date(cumulative, 0.10)
        p50 = threshold_date(cumulative, 0.50)
        p90 = threshold_date(cumulative, 0.90)
        out.append(
            {
                "radar": radar,
                "year": year,
                "season": season,
                "solar_period": period,
                "pulse": pulse,
                "vid_birds_per_km2": round(cumulative_value, 8),
                "day_count": len(rows),
                "onset_5_date": p05,
                "early_10_date": p10,
                "median_50_date": p50,
                "late_90_date": p90,
                "duration_days_10_90": days_between(p10, p90),
                "peak_date": str(peak["date"]) if peak else "",
                "peak_vid_birds_per_km2": peak["vid_birds_per_km2"] if peak else "",
            }
        )
    return out


def write_csv(
    path: Path, rows: List[Dict[str, object]], fieldnames: Optional[List[str]] = None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
