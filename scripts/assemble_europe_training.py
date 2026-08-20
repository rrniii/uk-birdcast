#!/usr/bin/env python3
"""Assemble the Europe GAMM table without materialising raw Aloft VPTS."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory


def radar_csv(source: Path, output: Path) -> None:
    try:
        from pyproj import Transformer
    except ImportError as exc:
        raise RuntimeError("pyproj is required") from exc
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = payload.get("radars") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("radar metadata must be a list or contain radars")
    transform = Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True)
    fields = ["radar", "network", "country", "latitude", "longitude", "easting_m", "northing_m"]
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in rows:
            radar = str(item.get("radar") or item.get("slug") or "").lower()
            latitude = float(item["latitude"])
            longitude = float(item["longitude"])
            easting, northing = transform.transform(longitude, latitude)
            writer.writerow(
                {
                    "radar": radar,
                    "network": item.get("network") or item.get("source") or "baltrad",
                    "country": item.get("country") or "unknown",
                    "latitude": latitude,
                    "longitude": longitude,
                    "easting_m": easting,
                    "northing_m": northing,
                }
            )


def assemble(args: argparse.Namespace) -> None:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("duckdb is required") from exc
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="birdcast-euro-radars-") as temporary:
        metadata_csv = Path(temporary) / "radars.csv"
        radar_csv(Path(args.radar_metadata), metadata_csv)
        con = duckdb.connect()
        con.execute("SET preserve_insertion_order=false")
        metadata_path = sql_literal(str(metadata_csv))
        era5_path = sql_literal(args.era5_parquet)
        aloft_path = sql_literal(args.aloft_parquet)
        uk_path = sql_literal(args.uk_training_csv)
        con.execute(
            f"CREATE VIEW radar_meta AS SELECT * FROM read_csv_auto({metadata_path}, header=true)"
        )
        cohort = json.loads(Path(args.cohort).read_text(encoding="utf-8"))
        entries = cohort.get("entries") if isinstance(cohort, dict) else None
        if not isinstance(entries, list):
            raise ValueError("Europe cohort has no entries")
        cohort_csv = Path(temporary) / "cohort.csv"
        with cohort_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["radar", "cohort_role"])
            writer.writeheader()
            for item in entries:
                writer.writerow({"radar": str(item["radar"]).lower(), "cohort_role": item["role"]})
        con.execute(
            f"CREATE VIEW cohort AS SELECT * FROM read_csv_auto({sql_literal(str(cohort_csv))}, header=true)"
        )
        con.execute(
            f"""
            CREATE VIEW era5 AS
            SELECT
              * EXCLUDE (time_utc),
              strftime(CAST(time_utc AS TIMESTAMPTZ) AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ') AS time_utc
            FROM read_parquet({era5_path}, hive_partitioning=true, union_by_name=true)
            """
        )
        con.execute(
            f"""
            CREATE VIEW aloft AS
            SELECT
              lower(a.radar) AS radar,
              'aloft' AS pulse,
              'aloft-baltrad' AS source,
              m.network,
              m.country,
              strftime(CAST(a.time_utc AS TIMESTAMPTZ) AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ') AS time_utc,
              m.latitude,
              m.longitude,
              m.easting_m,
              m.northing_m,
              a.mean_mtr_birds_km_h AS mtr_birds_km_h,
              a.mean_vid_birds_per_km2 AS vid_birds_per_km2,
              a.bird_u_ms,
              a.bird_v_ms,
              a.profile_count,
              a.rain_suspect_fraction,
              c.cohort_role
            FROM read_parquet({aloft_path}, hive_partitioning=true, union_by_name=true) a
            JOIN radar_meta m ON lower(a.radar)=lower(m.radar)
            JOIN cohort c ON lower(a.radar)=lower(c.radar)
            """
        )
        con.execute(
            """
            CREATE VIEW aloft_joined AS
            SELECT a.*, e.* EXCLUDE (radar, time_utc)
            FROM aloft a
            JOIN era5 e ON lower(a.radar)=lower(e.radar) AND a.time_utc=e.time_utc
            """
        )
        con.execute(
            f"""
            CREATE VIEW uk_sp AS
            SELECT
              lower(radar) AS radar,
              'sp' AS pulse,
              'jasmin-uk-sp' AS source,
              'jasmin-uk' AS network,
              'GBR' AS country,
              * EXCLUDE (radar, pulse, time_utc),
              strftime(CAST(time_utc AS TIMESTAMPTZ) AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ') AS time_utc
            FROM read_csv_auto({uk_path}, header=true, union_by_name=true)
            WHERE lower(pulse)='sp'
            """
        )
        aloft_columns = {row[0] for row in con.execute("DESCRIBE aloft_joined").fetchall()}
        uk_columns = {row[0] for row in con.execute("DESCRIBE uk_sp").fetchall()}
        aloft_only = bool(getattr(args, "aloft_only", False))
        columns = sorted(
            aloft_columns - {"cohort_role"}
            if aloft_only
            else (aloft_columns - {"cohort_role"}) & uk_columns
        )
        preferred = [
            "radar",
            "pulse",
            "source",
            "network",
            "country",
            "time_utc",
            "latitude",
            "longitude",
            "easting_m",
            "northing_m",
            "mtr_birds_km_h",
            "vid_birds_per_km2",
            "bird_u_ms",
            "bird_v_ms",
            "profile_count",
            "rain_suspect_fraction",
        ]
        ordered = [name for name in preferred if name in columns] + sorted(
            set(columns) - set(preferred)
        )
        select = ", ".join(f'"{name}"' for name in ordered)
        destination = str(output).replace("'", "''")
        training_query = (
            f"SELECT {select} FROM aloft_joined WHERE cohort_role='training'"
            if aloft_only
            else f"SELECT {select} FROM aloft_joined WHERE cohort_role='training' UNION ALL BY NAME SELECT {select} FROM uk_sp"
        )
        con.execute(f"COPY ({training_query}) TO '{destination}' (HEADER, DELIMITER ',')")
        validation_counts = []
        if args.validation_output:
            validation_output = Path(args.validation_output)
            validation_output.parent.mkdir(parents=True, exist_ok=True)
            validation_destination = str(validation_output).replace("'", "''")
            con.execute(
                f"""
                COPY (
                  SELECT {select}
                  FROM aloft_joined
                  WHERE cohort_role='transfer-validation'
                ) TO '{validation_destination}' (HEADER, DELIMITER ',')
                """
            )
            validation_counts = con.execute(
                "SELECT count(*), count(DISTINCT radar) FROM aloft_joined "
                "WHERE cohort_role='transfer-validation'"
            ).fetchone()
        counts = con.execute(
            """
            SELECT source, count(*) AS rows, count(DISTINCT radar) AS radars
            FROM read_csv_auto(?, header=true)
            GROUP BY source ORDER BY source
            """,
            [str(output)],
        ).fetchall()
    manifest = {
        "schema_version": "birdcast-euro-training-table-1.0",
        "training_csv": str(output),
        "raw_aloft_persisted": False,
        "aloft_hourly_parquet": args.aloft_parquet,
        "uk_training_csv": args.uk_training_csv,
        "era5_parquet": args.era5_parquet,
        "source_counts": [
            {"source": source, "rows": rows, "radars": radars} for source, rows, radars in counts
        ],
        "transfer_validation_csv": args.validation_output,
        "transfer_validation_rows": validation_counts[0] if validation_counts else 0,
        "transfer_validation_radars": validation_counts[1] if validation_counts else 0,
        "filters": {
            "aloft_source": "baltrad",
            "uk_pulse": "sp",
            "aloft_only": aloft_only,
            "phenology": "none",
            "daylight": "none",
        },
    }
    output.with_suffix(output.suffix + ".manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--aloft-parquet", required=True, help="DuckDB glob for derived hourly partitions"
    )
    parser.add_argument("--uk-training-csv", required=True)
    parser.add_argument(
        "--era5-parquet", required=True, help="DuckDB glob for European site ERA5 features"
    )
    parser.add_argument("--radar-metadata", required=True)
    parser.add_argument("--cohort", required=True)
    parser.add_argument("--aloft-only", action="store_true")
    parser.add_argument("--validation-output")
    parser.add_argument("--output", required=True)
    assemble(parser.parse_args())
