"""A restartable, read-only VPTS analysis and manifest-last publication cycle.

Run on LOTUS, never on a login, cron or web host. A private SQLite cache holds
per-source sufficient statistics, not raw radar data. File size, mtime and the
calculation-code hash invalidate entries; newly read files are SHA-256 recorded
and checked for concurrent changes. Publication/checkpointing is separate from
cache commits, so an interrupted run cannot mark unpublished data as complete.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import mkdtemp
from urllib.request import Request, urlopen

from . import historical_statistics as statistics
from .freshness import catalog_window
from .historical import build_historical_products
from .publication import build_publication_plan, write_sync_commands
from .radars import load_radars
from .static_artifacts import build_static_artifacts, utc_now, write_json
from .vpts import fetch_json, head_public_object


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _signature(path: Path) -> tuple[int, int]:
    info = path.stat()
    return info.st_size, info.st_mtime_ns


def discover_sources(root: Path, radars: set[str], end: date) -> list[tuple]:
    """Enumerate only the named read-only local CSV archive, with a fixed end."""

    root = root / "single-site" if (root / "single-site").is_dir() else root
    records = []
    for radar in sorted(radars):
        if not re.fullmatch(r"[a-z0-9-]+", radar) or not (root / radar).is_dir():
            raise ValueError(f"Configured radar archive is missing or unsafe: {radar}")
        for year_dir in sorted((root / radar).iterdir()):
            if not year_dir.is_dir() or not re.fullmatch(r"[0-9]{4}", year_dir.name):
                continue
            if int(year_dir.name) > end.year:
                continue
            for path in sorted(year_dir.glob("*_vpts.csv")):
                match = statistics.FILE_RE.fullmatch(path.name)
                if not match:
                    continue
                source_day = datetime.strptime(match["date"], "%Y%m%d").date()
                if source_day.year != int(year_dir.name):
                    raise ValueError(f"Source date does not match its year directory: {path}")
                if source_day <= end:
                    records.append(
                        (
                            str(path),
                            radar,
                            match["pulse"],
                            source_day.isoformat(),
                            *_signature(path),
                        )
                    )
    if not records:
        raise ValueError("No VPTS source files were found")
    return records


def missing_source_days(
    records: list[tuple], radars: set[str], start: date, end: date
) -> list[dict]:
    """Identify retrospective gaps explicitly, without synthesising zero rows."""

    present = {(record[1], record[2], record[3]) for record in records}
    missing = []
    while start <= end:
        missing.extend(
            {"radar": radar, "pulse": pulse, "date": start.isoformat()}
            for radar in sorted(radars)
            for pulse in ("lp", "sp")
            if (radar, pulse, start.isoformat()) not in present
        )
        start += timedelta(days=1)
    return missing


def _summarize(record: tuple) -> tuple:
    path_text, radar, pulse, day, size, mtime = record
    path = Path(path_text)
    before = _sha256(path)
    daily = {}
    counts = statistics.summarize_file(path, radar, pulse, "dens", 200, 4000, False, daily)
    if _signature(path) != (size, mtime) or _sha256(path) != before:
        raise RuntimeError(f"Source changed while being read: {path}")
    for accumulator in daily.values():
        accumulator["source_dates"] = sorted(accumulator.get("source_dates", []))
    payload = {
        "sha256": before,
        "counts": counts,
        "daily": [[list(key), value] for key, value in sorted(daily.items())],
    }
    return path_text, size, mtime, json.dumps(payload, separators=(",", ":"), allow_nan=False)


def build_analysis(
    *,
    input_root: Path,
    cache_path: Path,
    output_dir: Path,
    radars: set[str],
    source_end: date,
    published_end: date,
    previous: dict,
    workers: int = 8,
    gap_check=None,
) -> dict:
    """Reconcile the full archive using cached, independently replaceable files."""

    records = discover_sources(input_root, radars, source_end)
    previous_end = date.fromisoformat(previous["latest_date"])
    # Keep recent outages visible even after publication advances past them.
    # A late source arrival changes the full-archive fingerprint and is rebuilt
    # automatically; absent observations are never replaced by measured zeros.
    gap_start = max(
        date.fromisoformat(previous["first_date"]),
        min(previous_end, published_end, source_end - timedelta(days=14)),
    )
    gaps = missing_source_days(records, radars, gap_start, source_end)
    boundary_gaps = [
        row for row in gaps if row["date"] >= (published_end - timedelta(days=1)).isoformat()
    ]
    if boundary_gaps:
        raise ValueError(f"Incomplete UTC boundaries for latest local day: {boundary_gaps[:8]}")
    if gap_check is not None:
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(gap_check, gaps))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    calculation_hash = _sha256(Path(statistics.__file__))
    with sqlite3.connect(cache_path) as cache:
        cache.execute(
            "CREATE TABLE IF NOT EXISTS files "
            "(path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, "
            "calculation TEXT, payload TEXT)"
        )
        known = {
            row[0]: row[1:]
            for row in cache.execute("SELECT path, size, mtime, calculation FROM files")
        }
        changed = [
            row for row in records if known.get(row[0]) != (row[4], row[5], calculation_hash)
        ]
        print(
            json.dumps(
                {
                    "phase": "analysis",
                    "source_files": len(records),
                    "changed_files": len(changed),
                    "workers": workers,
                }
            ),
            flush=True,
        )
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for index, result in enumerate(executor.map(_summarize, changed, chunksize=8), 1):
                path, size, mtime, payload = result
                cache.execute(
                    "INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?)",
                    (path, size, mtime, calculation_hash, payload),
                )
                if index % 250 == 0:
                    cache.commit()
                    print(
                        json.dumps(
                            {
                                "phase": "cache",
                                "processed": index,
                                "of": len(changed),
                                "at": utc_now(),
                            }
                        ),
                        flush=True,
                    )
        cache.commit()

        daily, coverage = {}, {}
        fingerprint = hashlib.sha256(calculation_hash.encode())
        for path, radar, pulse, day, size, mtime in records:
            if _signature(Path(path)) != (size, mtime):
                raise RuntimeError(f"Source snapshot changed during analysis: {path}")
            payload = json.loads(
                cache.execute("SELECT payload FROM files WHERE path=?", (path,)).fetchone()[0]
            )
            fingerprint.update(json.dumps([radar, pulse, day, payload["sha256"]]).encode())
            for key, values in payload["daily"]:
                if key[4] > published_end.isoformat():
                    continue  # Withhold the trailing incomplete local solar day.
                accumulator = daily.setdefault(tuple(key), statistics.empty_accumulator())
                for field, value in values.items():
                    if field == "source_dates":
                        accumulator.setdefault(field, set()).update(value)
                    else:
                        accumulator[field] += value
            group = radar, int(day[:4]), pulse
            row = coverage.setdefault(
                group,
                {
                    "radar": radar,
                    "year": group[1],
                    "pulse": pulse,
                    "file_count": 0,
                    "row_count": 0,
                    "profile_count": 0,
                    "failed_file_count": 0,
                    "first_date": day.replace("-", ""),
                    "last_date": day.replace("-", ""),
                },
            )
            row["file_count"] += 1
            row["row_count"] += payload["counts"]["rows"]
            row["profile_count"] += payload["counts"]["profiles"]
            row["last_date"] = max(row["last_date"], day.replace("-", ""))

    daily_rows = statistics.finalize_daily_rows(daily)
    if not daily_rows or max(row["date"] for row in daily_rows) != published_end.isoformat():
        raise ValueError("Analysis does not reach the declared complete local date")
    # A file containing only missing/gap data cannot qualify a radar as observed.
    available = {
        (row["radar"], row["pulse"], row["date"]) for row in daily_rows if row["row_count_used"] > 0
    }
    for radar in radars:
        for pulse in ("lp", "sp"):
            if (radar, pulse, published_end.isoformat()) not in available:
                raise ValueError(f"No usable observations for {radar}/{pulse}/{published_end}")
    usable_rows = [row for row in daily_rows if row["row_count_used"] > 0]
    annual = statistics.aggregate_annual(usable_rows)
    network = statistics.aggregate_network(annual)
    phenology = statistics.phenology(usable_rows)
    # Preserve coverage counts, but never present an entirely missing/gap-only
    # solar period as measured zero passage. Real measured zeros remain zeros.
    for row in daily_rows:
        if row["row_count_used"] == 0:
            for field in (
                "vid_birds_per_km2",
                "mean_vid_birds_per_km2_per_profile",
                "eta_integrated",
                "mean_weighted_height_m",
                "mean_ff_ms",
            ):
                row[field] = ""
    coverage_rows = [row for _, row in sorted(coverage.items())]
    summary = {
        "finished_at": utc_now(),
        "failure_count": 0,
        "metric": "dens",
        "alt_min_m": 200,
        "alt_max_m": 4000,
        "trend_end_year": 2025,
        "primary_gap_handling": "exclude_gap_rows",
        "inclusive_end_date": source_end.strftime("%Y%m%d"),
        "complete_local_date_through": published_end.isoformat(),
        "input_snapshot_sha256": fingerprint.hexdigest(),
        "calculation_sha256": calculation_hash,
        "files_seen": len(records),
        "changed_files": len(changed),
        "rows_seen": sum(row["row_count"] for row in coverage_rows),
        "profiles_seen": sum(row["profile_count"] for row in coverage_rows),
        "daily_rows": len(daily_rows),
        "catch_up_missing_source_days": gaps,
    }
    for field in ("files_seen", "profiles_seen", "rows_seen"):
        if summary[field] < previous.get("source", {}).get(field, 0):
            raise ValueError(f"Source coverage regressed relative to publication: {field}")
    if min(row["date"] for row in daily_rows) > previous["first_date"]:
        raise ValueError("Historical start date regressed")
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (
        ("daily_totals.csv", daily_rows),
        ("network_annual_seasonal_totals.csv", network),
        ("phenology.csv", phenology),
        ("coverage.csv", coverage_rows),
    ):
        statistics.write_csv(output_dir / name, rows)
    write_json(output_dir / "analysis_summary.json", summary)
    write_json(
        output_dir / "source-inventory.json",
        {
            "input_snapshot_sha256": fingerprint.hexdigest(),
            "calculation_sha256": calculation_hash,
            "records": [list(record) for record in records],
        },
    )
    return summary


def verify_public_plan(plan: dict, public_base_url: str) -> None:
    """Independently GET/hash every published object before recording success."""

    def verify(record: dict) -> None:
        url = f"{public_base_url.rstrip('/')}/{record['key']}"
        request = Request(url, headers={"Cache-Control": "no-cache"})
        digest, size = hashlib.sha256(), 0
        with urlopen(request, timeout=90) as response:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        if size != record["size"] or digest.hexdigest() != record["sha256"]:
            raise ValueError(f"Public object verification failed: {record['key']}")

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(verify, plan["objects"]))


def run_cycle(args: argparse.Namespace) -> dict:
    base = f"{args.public_base_url.rstrip('/')}/{args.object_prefix.strip('/')}"
    previous = fetch_json(f"{base}/latest/historical.json")
    radars = {radar.slug for radar in load_radars(args.radars)}
    catalog = fetch_json(args.catalog_url)
    now = datetime.now(timezone.utc)
    generated, source_end = catalog_window(catalog, radars, now)
    if now - generated > timedelta(hours=72):
        raise ValueError("Source catalogue is stale; publication is withheld")
    published_end = source_end - timedelta(days=1)
    if published_end < date.fromisoformat(previous["latest_date"]):
        raise ValueError("Source window would move the published end backwards")
    run_dir = Path(
        mkdtemp(prefix=f"{now.strftime('%Y%m%dT%H%M%SZ')}-", dir=args.state_root / "runs")
    )
    write_json(run_dir / "catalog.json", catalog)
    write_json(run_dir / "previous-historical.json", previous)

    def verify_gap(row: dict) -> None:
        stamp = row["date"].replace("-", "")
        url = (
            f"{args.public_base_url.rstrip('/')}/ukmo-nimrod/vpts/current_ci_le4/"
            f"{row['radar']}/{stamp[:4]}/{stamp}_{row['pulse']}_vpts.csv"
        )
        if head_public_object(url) is not None:
            raise ValueError(f"Local archive is missing an available public source: {url}")

    summary = build_analysis(
        input_root=args.input_root,
        cache_path=args.state_root / "analysis.sqlite3",
        output_dir=run_dir / "analysis",
        radars=radars,
        source_end=source_end,
        published_end=published_end,
        previous=previous,
        workers=args.workers,
        gap_check=verify_gap,
    )
    checkpoint_path = args.state_root / "published.json"
    checkpoint = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {}
    if (
        checkpoint.get("input_snapshot_sha256") == summary["input_snapshot_sha256"]
        and checkpoint.get("release_id") == previous.get("release_id")
        and previous["latest_date"] == published_end.isoformat()
    ):
        result = {
            **checkpoint,
            "state": "no_change",
            "run_dir": str(run_dir),
            "checked_at_utc": utc_now(),
        }
        write_json(run_dir / "result.json", result)
        return result
    public = run_dir / "public"
    build_static_artifacts(
        public,
        public_base_url=args.public_base_url,
        object_prefix=args.object_prefix,
        radars_path=args.radars,
    )
    built = build_historical_products(
        run_dir / "analysis",
        public,
        radars_path=args.radars,
        boundary_source=args.boundary,
        expected_latest_date=published_end.isoformat(),
    )
    plan_path = run_dir / "publication-plan.json"
    plan = build_publication_plan(
        public, plan_path, object_prefix=args.object_prefix, products=("historical",)
    )
    result = {
        "state": "prepared",
        "run_dir": str(run_dir),
        "release_id": built["release_id"],
        "published_through": published_end.isoformat(),
        "source_end": source_end.isoformat(),
        "input_snapshot_sha256": summary["input_snapshot_sha256"],
        "release_sha": os.environ.get("BIRDCAST_UK_RELEASE_SHA"),
        "object_count": plan["object_count"],
        "finished_at_utc": utc_now(),
    }
    if args.publish:
        # Compare-and-check before promotion; this job owns only historical+the
        # disabled forecast tombstone, never the selected model or source archive.
        if fetch_json(f"{base}/latest/historical.json") != previous:
            raise RuntimeError("Historical publication changed concurrently; refusing promotion")
        script = run_dir / "publish.sh"
        write_sync_commands(
            plan_path,
            script,
            bucket=args.bucket,
            endpoint_url="",
            client="s3cmd",
            s3cmd_config=str(args.s3cmd_config),
            phase="assets",
        )
        subprocess.run(["/bin/sh", str(script)], check=True)
        immutable = {
            **plan,
            "objects": [row for row in plan["objects"] if "/latest/" not in row["key"]],
        }
        verify_public_plan(immutable, args.public_base_url)
        if fetch_json(f"{base}/latest/historical.json") != previous:
            raise RuntimeError("Historical publication changed during upload; refusing promotion")
        write_sync_commands(
            plan_path,
            script,
            bucket=args.bucket,
            endpoint_url="",
            client="s3cmd",
            s3cmd_config=str(args.s3cmd_config),
            phase="manifests",
        )
        subprocess.run(["/bin/sh", str(script)], check=True)
        verify_public_plan(plan, args.public_base_url)
        result.update(state="published", finished_at_utc=utc_now())
        write_json(checkpoint_path, result)
    write_json(run_dir / "result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input-root", "state-root", "radars", "boundary", "s3cmd-config"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in ("catalog-url", "public-base-url", "object-prefix", "bucket"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        parser.error("workers must be between 1 and 16")
    if args.state_root.resolve().is_relative_to(args.input_root.resolve()):
        parser.error("private state must not be placed inside the read-only radar archive")
    args.state_root.mkdir(parents=True, exist_ok=True)
    args.state_root.chmod(0o700)
    (args.state_root / "runs").mkdir(exist_ok=True)
    with (args.state_root / "cycle.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"state":"already_running"}')
            return 0
        try:
            result = run_cycle(args)
        except Exception as exc:
            write_json(
                args.state_root / "cycle-status.json",
                {
                    "state": "failed",
                    "checked_at_utc": utc_now(),
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise
        write_json(args.state_root / "cycle-status.json", result)
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
