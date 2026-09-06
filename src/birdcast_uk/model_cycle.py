"""Restartable daily application of the frozen selected model to ERA5/ERA5T.

Run on LOTUS. No fitting, radar-source changes, forecast requests or forecast
publication occur here. Each complete UTC day is an independent transaction:
validate inputs and LP/SP grids, upload immutable assets, GET/hash them, compare
public latest with the saved base, then promote and verify only gam-era5.json.
Interrupted catch-up resumes from public latest, never an optimistic checkpoint.
"""

from __future__ import annotations

import argparse
import copy
import csv
import fcntl
import json
import math
import os
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import mkdtemp
from urllib.request import Request, urlopen
from uuid import uuid4

from . import era5
from .config import DEFAULT_PUBLIC_BASE_URL, OBJECT_PREFIX
from .historical_cycle import verify_public_plan
from .publication import (
    _validate_selected_model_manifest,
    build_model_extension_plan,
    write_sync_commands,
)
from .reanalysis import _stream_wide_daily_assets
from .selected_model import (
    COMPONENT_MANIFEST_SHA256,
    QUALIFIED_FIRST_DAY,
    QUALIFIED_LAST_DAY,
    RETROSPECTIVE_LAG_DAYS,
    SELECTED_GRID_FIELDS,
    TARGETS,
    TRAINING_CONTRACT_SHA256,
    file_sha256,
    validate_component_manifest,
)
from .static_artifacts import utc_now, write_json


def fetch_public(url: str) -> dict:
    """Bypass freshness caches when checking the publication precondition."""

    with urlopen(Request(url, headers={"Cache-Control": "no-cache"}), timeout=90) as response:
        return json.load(response)


def completed_target(now: datetime, lag_days: int = RETROSPECTIVE_LAG_DAYS) -> date:
    if now.tzinfo is None or lag_days < 5:
        raise ValueError("retrospective ERA5 requires an aware clock and at least five days lag")
    return now.astimezone(timezone.utc).date() - timedelta(days=lag_days)


def validate_grid_day(path: Path, day: date, coordinates: set[tuple[float, float]]) -> None:
    """Require every predictor and the same physical grid in all 24 UTC hours."""

    frames: dict[int, set[tuple[float, float]]] = {}
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        if len(header) != len(set(header)) or not SELECTED_GRID_FIELDS.issubset(header):
            raise ValueError(
                "daily grid is missing selected-model predictors or has duplicate fields"
            )
        for row in reader:
            stamp = datetime.fromisoformat(row["time_utc"].replace("Z", "+00:00"))
            if stamp.tzinfo is not None:
                stamp = stamp.astimezone(timezone.utc)
            if stamp.date() != day or stamp.minute or stamp.second or stamp.microsecond:
                raise ValueError("daily grid contains off-day or non-hourly timestamps")
            values = {key: float(row[key]) for key in SELECTED_GRID_FIELDS - {"time_utc"}}
            if not all(math.isfinite(value) for value in values.values()):
                raise ValueError("daily grid contains non-finite selected-model predictors")
            if not 0 <= values["support"] <= 1:
                raise ValueError("daily grid support is outside 0-1")
            point = values["longitude"], values["latitude"]
            frame = frames.setdefault(stamp.hour, set())
            if point in frame:
                raise ValueError("daily grid contains duplicate coordinates")
            frame.add(point)
    if set(frames) != set(range(24)) or any(points != coordinates for points in frames.values()):
        raise ValueError("daily grid differs from the published grid or lacks complete UTC hours")


def frame_coordinates(payload: dict, day: date, pulse: str) -> set[tuple[float, float]]:
    """Check daily public shape, values and uncertainty before comparison/promotion."""

    if payload.get("pulse") != pulse or payload.get("date_utc") != day.isoformat():
        raise ValueError("daily prediction has the wrong pulse or date")
    frames = payload.get("frames", [])
    expected = [f"{day.isoformat()}T{hour:02d}:00:00Z" for hour in range(24)]
    if [frame.get("time_utc") for frame in frames] != expected:
        raise ValueError("daily prediction lacks canonical 24-hour coverage")
    reference = None
    for frame in frames:
        cells = frame.get("cells", [])
        points = {(float(cell["longitude"]), float(cell["latitude"])) for cell in cells}
        if (
            not points
            or len(points) != len(cells)
            or any(not math.isfinite(value) for point in points for value in point)
        ):
            raise ValueError("daily prediction has invalid or duplicate coordinates")
        if reference is not None and points != reference:
            raise ValueError("daily prediction grid changes within the day")
        reference = points
        for cell in cells:
            values = [cell.get(target) for target in TARGETS]
            errors = [cell.get("uncertainty_by_target", {}).get(target) for target in TARGETS]
            if any(value is None or not math.isfinite(float(value)) for value in values + errors):
                raise ValueError("daily prediction has non-finite predictions or uncertainty")
            if any(float(value) < 0 for value in errors + values[:2]):
                raise ValueError("daily prediction has negative intensity or uncertainty")
            support = cell.get("support")
            if support is None or not 0 <= float(support) <= 1:
                raise ValueError("daily prediction has invalid support")
    return reference or set()


def stage_extension(
    *,
    previous: dict,
    day: date,
    predictions: Path,
    output_root: Path,
    coordinates: set[tuple[float, float]],
    provenance: dict,
) -> dict:
    """Preserve the evidence archive and append exactly one independently checked day."""

    _validate_selected_model_manifest(previous)
    if day != date.fromisoformat(previous["latest_time_utc"][:10]) + timedelta(days=1):
        raise ValueError("model extension must append the next unpublished day")
    relative = Path("archive/reanalysis/gam-era5/updates") / uuid4().hex
    archive = output_root / relative
    archive.mkdir(parents=True, exist_ok=False)
    current = copy.deepcopy(previous)
    for pulse in ("lp", "sp"):
        assets, grid, first, last, _ = _stream_wide_daily_assets(
            predictions / f"predictions_wide_{pulse}.csv",
            archive,
            pulse=pulse,
            model_family="gamm",
        )
        if (
            sorted(assets) != [day.isoformat()]
            or grid != previous["grid"]
            or first != f"{day.isoformat()}T00:00:00Z"
            or last != f"{day.isoformat()}T23:00:00Z"
        ):
            raise ValueError("new predictions differ from the published grid or requested day")
        asset = assets[day.isoformat()]
        points = frame_coordinates(json.loads((archive / asset).read_text()), day, pulse)
        if points != coordinates:
            raise ValueError("new predictions change the published physical grid")
        current["assets"][pulse][day.isoformat()] = (relative / asset).as_posix()
    current["generated_at_utc"] = utc_now()
    current["latest_time_utc"] = f"{day.isoformat()}T23:00:00Z"
    current["rolling_update"] = {
        "policy": "daily_frozen_model_retrospective",
        "evidence_window": [QUALIFIED_FIRST_DAY.isoformat(), QUALIFIED_LAST_DAY.isoformat()],
        "weather_status": "ERA5_or_preliminary_ERA5T_as_retrieved",
        "source": (relative / "source.json").as_posix(),
        "interpretation": "Retrospective application of the frozen selected model, not a forecast. "
        "Dates beyond the evidence window are not newly independently validated. "
        "Recent ERA5T weather may be revised by ECMWF; published daily snapshots are retained.",
    }
    write_json(output_root / "previous-model.json", previous)
    write_json(
        archive / "source.json",
        {
            **provenance,
            "date_utc": day.isoformat(),
            "generated_at_utc": utc_now(),
            "previous_manifest_sha256": file_sha256(output_root / "previous-model.json"),
            "component_provenance": previous["component_provenance"],
            "predictions_sha256": {
                pulse: file_sha256(predictions / f"predictions_wide_{pulse}.csv")
                for pulse in ("lp", "sp")
            },
            "checks": {
                "24_complete_hours": True,
                "unchanged_grid": True,
                "finite_inputs_outputs": True,
                "reviewed_model_hashes": True,
            },
        },
    )
    write_json(output_root / "latest/gam-era5.json", current)
    return current


def assert_public_base(base: str, previous: dict) -> None:
    if fetch_public(f"{base}/latest/gam-era5.json") != previous:
        raise RuntimeError("public model manifest changed during update; refusing promotion")
    forecast = fetch_public(f"{base}/latest/forecast.json")
    if (
        forecast.get("mode") != "disabled"
        or forecast.get("data_available") is not False
        or forecast.get("valid_times_utc") not in (None, [])
        or forecast.get("assets", {}).get("frames") not in (None, [])
    ):
        raise RuntimeError("forecast fail-closed contract is not satisfied")


def publish_extension(args: argparse.Namespace, root: Path, previous: dict) -> dict:
    """Upload immutable assets before a compare-and-verify latest promotion.

    The state lock and Slurm singleton enforce one production writer. Rechecking
    public latest also detects out-of-band writers; object storage offers no
    cross-object transaction, so assets always precede the single mutable pointer.
    """

    plan_path = root / "publication-plan.json"
    plan = build_model_extension_plan(root, plan_path, object_prefix=args.object_prefix)
    base = f"{args.public_base_url.rstrip('/')}/{args.object_prefix.strip('/')}"
    for phase in ("assets", "manifests"):
        assert_public_base(base, previous)
        script = root / f"publish-{phase}.sh"
        write_sync_commands(
            plan_path,
            script,
            bucket=args.bucket,
            endpoint_url="",
            client="s3cmd",
            s3cmd_config=str(args.s3cmd_config),
            phase=phase,
        )
        subprocess.run(["/bin/sh", str(script)], check=True)
        verified = (
            plan
            if phase == "manifests"
            else {
                **plan,
                "objects": [obj for obj in plan["objects"] if "/latest/" not in obj["key"]],
            }
        )
        verify_public_plan(verified, args.public_base_url)
    return plan


def run_cycle(args: argparse.Namespace) -> dict:
    target = completed_target(datetime.now(timezone.utc), args.lag_days)
    base = f"{args.public_base_url.rstrip('/')}/{args.object_prefix.strip('/')}"
    previous = fetch_public(f"{base}/latest/gam-era5.json")
    _validate_selected_model_manifest(previous)
    assert_public_base(base, previous)
    last_day = date.fromisoformat(previous["latest_time_utc"][:10])
    if last_day > target:
        raise ValueError("published model is ahead of the configured completed ERA5 window")
    status = {
        "state": "no_change",
        "checked_at_utc": utc_now(),
        "target_through": target.isoformat(),
        "published_through": last_day.isoformat(),
        "release_sha": args.release_sha,
        "published_days": 0,
    }
    if last_day == target:
        # Read and verify both public tail assets even on a no-change run.
        for pulse in ("lp", "sp"):
            frame_coordinates(
                fetch_public(f"{base}/{previous['assets'][pulse][last_day.isoformat()]}"),
                last_day,
                pulse,
            )
        return status
    validate_component_manifest(args.component_manifest, verify_model_files=True)
    if file_sha256(args.training_table) != TRAINING_CONTRACT_SHA256:
        raise ValueError("training support contract is not the selected radar-range reference")
    if not era5.cds_readiness()["ok"]:
        raise RuntimeError("ERA5 credential/backend readiness failed")
    coordinates = None
    for pulse in ("lp", "sp"):
        points = frame_coordinates(
            fetch_public(f"{base}/{previous['assets'][pulse][last_day.isoformat()]}"),
            last_day,
            pulse,
        )
        if coordinates is not None and coordinates != points:
            raise ValueError("published LP and SP grids differ")
        coordinates = points
    assert coordinates
    inputs = {
        "training_sha256": file_sha256(args.training_table),
        "radars_sha256": file_sha256(args.radars),
        "release_sha": args.release_sha,
    }
    raw = args.state_root / "raw"
    features = args.state_root / "site-features"
    for offset in range(1, min(args.max_days, (target - last_day).days) + 1):
        day = last_day + timedelta(days=offset)
        stamp = day.strftime("%Y%m%d")
        print(
            json.dumps({"phase": "model_day", "day": day.isoformat(), "at": utc_now()}), flush=True
        )
        write_json(
            args.state_root / "cycle-status.json",
            {
                **status,
                "state": "running",
                "processing_day": day.isoformat(),
                "checked_at_utc": utc_now(),
            },
        )
        single = raw / f"era5_single_levels_{stamp}_uk.nc"
        pressure = raw / f"era5_pressure_levels_{stamp}_uk.nc"
        feature = features / f"era5_site_features_{stamp}.json"
        if not all(path.is_file() for path in (single, pressure, feature)):
            era5.build_period(
                start_day=day.isoformat(),
                end_day=day.isoformat(),
                raw_dir=raw,
                feature_dir=features,
                radars_path=args.radars,
                download=True,
            )
        validation = era5.validate_day(
            day=day.isoformat(), raw_dir=raw, feature_output=feature, radars_path=args.radars
        )
        if not validation["ok"]:
            raise ValueError(f"ERA5 day validation failed: {validation['errors']}")
        run_dir = Path(mkdtemp(prefix=f"{stamp}-", dir=args.state_root / "runs"))
        grid = run_dir / "grid.csv"
        era5.extract_grid_features(
            single_levels=single,
            pressure_levels=pressure,
            radars_path=args.radars,
            output=grid,
            training_table=args.training_table,
            restrict_to_training_window=False,
        )
        validate_grid_day(grid, day, coordinates)
        provenance = {
            **inputs,
            "era5_sha256": {
                "single_levels": file_sha256(single),
                "pressure_levels": file_sha256(pressure),
            },
            "grid_sha256": file_sha256(grid),
        }
        predictions = run_dir / "predictions"
        environment = {
            **os.environ,
            "BIRDCAST_UK_EXPECTED_COMPONENT_MANIFEST_SHA256": COMPONENT_MANIFEST_SHA256,
        }
        subprocess.run(
            [
                args.rscript,
                str(args.predict_script),
                str(args.component_manifest),
                str(grid),
                str(predictions),
            ],
            env=environment,
            check=True,
        )
        if (
            predictions / "component-manifest.sha256"
        ).read_text().strip() != COMPONENT_MANIFEST_SHA256:
            raise ValueError("prediction authority sidecar mismatch")
        if inputs != {
            "training_sha256": file_sha256(args.training_table),
            "radars_sha256": file_sha256(args.radars),
            "release_sha": args.release_sha,
        }:
            raise ValueError("training reference or radar metadata changed during inference")
        root = run_dir / "public"
        current = stage_extension(
            previous=previous,
            day=day,
            predictions=predictions,
            output_root=root,
            coordinates=coordinates,
            provenance=provenance,
        )
        if not args.publish:
            build_model_extension_plan(
                root, root / "publication-plan.json", object_prefix=args.object_prefix
            )
            return {**status, "state": "validated_not_published", "run_dir": str(run_dir)}
        publish_extension(args, root, previous)
        previous = current
        status.update(
            state="published",
            published_through=day.isoformat(),
            published_days=offset,
            checked_at_utc=utc_now(),
            run_dir=str(run_dir),
        )
        write_json(args.state_root / "published.json", status)
        write_json(args.state_root / "cycle-status.json", status)
    status["caught_up"] = status["published_through"] == target.isoformat()
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("state-root", "radars", "training-table", "component-manifest", "predict-script"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--public-base-url", default=DEFAULT_PUBLIC_BASE_URL)
    parser.add_argument("--object-prefix", default=OBJECT_PREFIX)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--s3cmd-config", type=Path, required=True)
    parser.add_argument("--rscript", default="Rscript")
    parser.add_argument("--lag-days", type=int, default=RETROSPECTIVE_LAG_DAYS)
    parser.add_argument("--max-days", type=int, default=60)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.max_days <= 90 or not 5 <= args.lag_days <= 14:
        parser.error("max-days must be 1-90 and lag-days must be 5-14")
    os.umask(0o077)
    (args.state_root / "runs").mkdir(parents=True, exist_ok=True)
    with (args.state_root / "cycle.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Model cycle is already running")
            return 0
        try:
            result = run_cycle(args)
        except Exception as exc:
            write_json(
                args.state_root / "cycle-status.json",
                {
                    "state": "failed",
                    "checked_at_utc": utc_now(),
                    "release_sha": args.release_sha,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise
        write_json(args.state_root / "cycle-status.json", result)
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
