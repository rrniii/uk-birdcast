#!/usr/bin/env python3
"""Build Europe radar metadata from derived Aloft rows and UK radar metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def load_overrides(path: str | None) -> dict[str, dict[str, object]]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("radars", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("overrides must be a list or contain a radars list")
    return {
        str(row.get("radar") or row.get("slug") or "").lower(): row
        for row in rows
        if isinstance(row, dict)
    }


def build(args: argparse.Namespace) -> dict[str, object]:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("duckdb is required") from exc

    overrides = load_overrides(args.overrides)
    con = duckdb.connect()
    rows = con.execute(
        f"""
        SELECT lower(radar) AS radar,
               avg(latitude) AS latitude,
               avg(longitude) AS longitude,
               count(*) AS hourly_rows
        FROM read_parquet({sql_literal(args.aloft_parquet)},
                          hive_partitioning=true, union_by_name=true)
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        GROUP BY lower(radar)
        ORDER BY radar
        """
    ).fetchall()
    radars: list[dict[str, object]] = []
    for radar, latitude, longitude, hourly_rows in rows:
        override = overrides.get(radar, {})
        prefix = radar[:2].upper()
        radars.append(
            {
                "radar": radar,
                "slug": radar,
                "label": override.get("label") or radar.upper(),
                "latitude": float(override.get("latitude", latitude)),
                "longitude": float(override.get("longitude", longitude)),
                "country": override.get("country") or ("GB" if prefix == "UK" else prefix),
                "network": override.get("network") or "baltrad",
                "source": "aloft-baltrad",
                "max_range_m": float(override.get("max_range_m", args.maximum_support_km * 1000)),
                "hourly_rows": int(hourly_rows),
                "country_source": "override" if override.get("country") else "opera_radar_prefix",
            }
        )

    uk_payload = json.loads(Path(args.uk_radars).read_text(encoding="utf-8"))
    uk_rows = uk_payload.get("radars", uk_payload) if isinstance(uk_payload, dict) else uk_payload
    if not isinstance(uk_rows, list):
        raise ValueError("UK radar metadata must be a list or contain a radars list")
    for row in uk_rows:
        if not isinstance(row, dict) or row.get("latitude") is None or row.get("longitude") is None:
            continue
        radar = str(row.get("radar") or row.get("slug") or "").lower()
        radars.append(
            {
                "radar": radar,
                "slug": radar,
                "label": row.get("label") or radar.replace("-", " ").title(),
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "country": "GB",
                "network": "jasmin-uk",
                "source": "jasmin-uk-sp",
                "max_range_m": float(row.get("max_range_m") or args.maximum_support_km * 1000),
                "country_source": "fixed_uk_archive",
            }
        )

    payload = {
        "schema_version": "birdcast-euro-radars-1.0",
        "raw_aloft_persisted": False,
        "country_convention": "OPERA radar identifier prefix, with explicit overrides",
        "radar_count": len(radars),
        "aloft_radar_count": len(rows),
        "uk_sp_radar_count": len(radars) - len(rows),
        "radars": radars,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--aloft-parquet", required=True, help="DuckDB glob for derived hourly partitions")
    parser.add_argument("--uk-radars", required=True)
    parser.add_argument("--overrides")
    parser.add_argument("--maximum-support-km", type=float, default=250.0)
    parser.add_argument("--output", required=True)
    result = build(parser.parse_args())
    print(json.dumps({key: value for key, value in result.items() if key != "radars"}, indent=2))
