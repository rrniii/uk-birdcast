"""Command line tools for UK BirdCast static artifacts and data flows."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .bto import validate_aggregates, write_request_template, write_validation_status
from .config import (
    DEFAULT_BUCKET,
    DEFAULT_INTERNAL_ENDPOINT,
    DEFAULT_PUBLIC_BASE_URL,
    FORECAST_ENSEMBLE_SIZE,
    OBJECT_PREFIX,
    UKMO_PVOL_CATALOG_URL,
    UKMO_VPTS_CATALOG_URL,
    VPTS_BOOTSTRAP_LOOKBACK_DAYS,
    VPTS_MAX_CATALOG_AGE_HOURS,
    VPTS_MAX_INCREMENT_DAYS,
)
from .era5 import build_day, cds_readiness, download_request, extract_grid_features, extract_site_features, extract_zip_archive, validate_day, write_request
from .ecmwf import archive_cycle
from .forecast import build_forecast
from .historical import NATURAL_EARTH_10M_COUNTRIES_URL, build_historical_products, write_boundary
from .joined import join_observed_to_era5
from .observed import build_hourly_observations, build_observed_products
from .publication import build_publication_plan, write_sync_commands
from .radars import radars_from_pvol_catalog, write_radars
from .reanalysis import build_prediction_frames, compare_models, prepare_training_table, publish_reanalysis, publish_wide_reanalysis, write_model_spec
from .static_artifacts import build_static_artifacts, install_static_site
from .vpts import build_catalog_inventory, build_historical_inventory, validate_manifest


def cmd_static_build(args: argparse.Namespace) -> int:
    radars = args.radars or os.environ.get("BIRDCAST_UK_RADARS_FILE") or ""
    result = build_static_artifacts(
        Path(args.output_dir),
        public_base_url=args.public_base_url,
        object_prefix=args.object_prefix,
        radars_path=Path(radars) if radars else None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_static_install(args: argparse.Namespace) -> int:
    result = install_static_site(
        Path(args.artifact_root),
        Path(args.site_root),
        data_base_url=args.data_base_url,
        object_prefix=args.object_prefix,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_era5_request(args: argparse.Namespace) -> int:
    request = write_request(
        args.day,
        args.kind,
        Path(args.output_file),
        Path(args.request_json),
    )
    print(json.dumps(request.to_dict(), indent=2, sort_keys=True))
    return 0


def cmd_era5_download(args: argparse.Namespace) -> int:
    result = download_request(Path(args.request_json), overwrite=args.overwrite)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_era5_readiness(args: argparse.Namespace) -> int:
    result = cds_readiness(Path(args.credentials) if args.credentials else None)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def cmd_era5_extract_zip(args: argparse.Namespace) -> int:
    result = extract_zip_archive(Path(args.archive), Path(args.output_dir))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_era5_features(args: argparse.Namespace) -> int:
    result = extract_site_features(
        single_levels=Path(args.single_levels) if args.single_levels else None,
        pressure_levels=Path(args.pressure_levels) if args.pressure_levels else None,
        radars_path=Path(args.radars) if args.radars else None,
        output=Path(args.output),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_era5_grid_features(args: argparse.Namespace) -> int:
    result = extract_grid_features(
        single_levels=Path(args.single_levels) if args.single_levels else None,
        pressure_levels=Path(args.pressure_levels) if args.pressure_levels else None,
        radars_path=Path(args.radars) if args.radars else None,
        training_table=Path(args.training_table) if args.training_table else None,
        boundary_path=Path(args.boundary) if args.boundary else None,
        output=Path(args.output),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_era5_build_day(args: argparse.Namespace) -> int:
    result = build_day(
        day=args.day,
        raw_dir=Path(args.raw_dir),
        feature_output=Path(args.feature_output),
        radars_path=Path(args.radars) if args.radars else None,
        download=args.download,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def cmd_era5_validate_day(args: argparse.Namespace) -> int:
    result = validate_day(
        day=args.day,
        raw_dir=Path(args.raw_dir),
        feature_output=Path(args.feature_output),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def cmd_ecmwf_archive(args: argparse.Namespace) -> int:
    result = archive_cycle(
        Path(args.output_root),
        cycle=args.cycle,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "complete" else 1


def cmd_forecast_build(args: argparse.Namespace) -> int:
    result = build_forecast(
        observed_hourly=Path(args.observed_hourly),
        radars_path=Path(args.radars),
        output_root=Path(args.output_root),
        ecmwf_manifest=Path(args.ecmwf_manifest) if args.ecmwf_manifest else None,
        analysis_time=args.analysis_time,
        members=args.members,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_historical_build(args: argparse.Namespace) -> int:
    result = build_historical_products(
        Path(args.source_dir),
        Path(args.output_root),
        radars_path=Path(args.radars),
        boundary_source=args.boundary_source,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_historical_boundary(args: argparse.Namespace) -> int:
    result = write_boundary(Path(args.output), boundary_source=args.boundary_source)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_radars_from_pvol(args: argparse.Namespace) -> int:
    radars = radars_from_pvol_catalog(args.input)
    payload = write_radars(Path(args.output), radars, source=args.input)
    print(json.dumps({"wrote": args.output, "radar_count": len(payload["radars"])}, indent=2, sort_keys=True))
    return 0


def cmd_vpts_validate(args: argparse.Namespace) -> int:
    result = validate_manifest(Path(args.input), Path(args.output) if args.output else None)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def cmd_vpts_inventory(args: argparse.Namespace) -> int:
    payload = build_catalog_inventory(
        output=Path(args.output),
        cursor_path=Path(args.cursor),
        catalog_url=args.catalog_url,
        public_base_url=args.public_base_url,
        bucket=args.bucket,
        bootstrap_lookback_days=args.bootstrap_lookback_days,
        max_increment_days=args.max_increment_days,
        max_catalog_age_hours=args.max_catalog_age_hours,
    )
    print(
        json.dumps(
            {
                "wrote": args.output,
                "ok": payload["ok"],
                "status": payload["status"],
                "record_count": payload["record_count"],
                "changed_radar_count": payload.get("changed_radar_count", 0),
                "errors": payload.get("errors", []),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if payload["ok"] else 1


def cmd_vpts_historical_inventory(args: argparse.Namespace) -> int:
    payload = build_historical_inventory(
        output=Path(args.output),
        catalog_url=args.catalog_url,
        public_base_url=args.public_base_url,
        bucket=args.bucket,
        days=args.days,
        end_date=args.end_date,
        max_workers=args.max_workers,
        max_catalog_age_hours=args.max_catalog_age_hours,
    )
    print(
        json.dumps(
            {
                "wrote": args.output,
                "ok": payload["ok"],
                "status": payload["status"],
                "record_count": payload["record_count"],
                "window": payload.get("window"),
                "errors": payload.get("errors", []),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if payload["ok"] else 1


def cmd_observed_build(args: argparse.Namespace) -> int:
    result = build_observed_products(
        input_path=Path(args.input),
        output_dir=Path(args.output_dir),
        radars_path=Path(args.radars) if args.radars else None,
        input_kind=args.input_kind,
        max_files=args.max_files,
        cursor_path=Path(args.cursor) if args.cursor else None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def cmd_observed_hourly(args: argparse.Namespace) -> int:
    result = build_hourly_observations(
        inventory_path=Path(args.input),
        output=Path(args.output),
        max_files=args.max_files,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_join_era5(args: argparse.Namespace) -> int:
    result = join_observed_to_era5(
        observed_hourly=Path(args.observed_hourly),
        era5_dir=Path(args.era5_dir),
        output=Path(args.output),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def cmd_bto_template(args: argparse.Namespace) -> int:
    write_request_template(Path(args.output))
    print(json.dumps({"wrote": args.output}, indent=2, sort_keys=True))
    return 0


def cmd_bto_status(args: argparse.Namespace) -> int:
    payload = write_validation_status(Path(args.output), data_available=args.data_available, status=args.status)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_bto_validate(args: argparse.Namespace) -> int:
    payload = validate_aggregates(Path(args.bto_csv), Path(args.radar_csv), Path(args.output))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_reanalysis_prepare(args: argparse.Namespace) -> int:
    result = prepare_training_table(
        joined_features=Path(args.joined_features),
        output=Path(args.output),
        window_days=args.window_days,
        min_profiles_per_hour=args.min_profiles_per_hour,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_reanalysis_spec(args: argparse.Namespace) -> int:
    result = write_model_spec(Path(args.output), table=Path(args.table), model_family=args.model_family)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_reanalysis_compare(args: argparse.Namespace) -> int:
    result = compare_models(
        gamm_metrics=Path(args.gamm_metrics),
        xgboost_metrics=Path(args.xgboost_metrics),
        output=Path(args.output),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_reanalysis_publish(args: argparse.Namespace) -> int:
    result = publish_reanalysis(
        predictions=Path(args.predictions),
        comparison=Path(args.comparison),
        output_root=Path(args.output_root),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_reanalysis_publish_wide(args: argparse.Namespace) -> int:
    result = publish_wide_reanalysis(
        lp_csv=Path(args.lp_csv),
        sp_csv=Path(args.sp_csv),
        comparison=Path(args.comparison),
        output_root=Path(args.output_root),
        model_family=args.model_family,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_reanalysis_frames(args: argparse.Namespace) -> int:
    result = build_prediction_frames(
        predictions_csv=Path(args.predictions_csv),
        output=Path(args.output),
        model_family=args.model_family,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_publish_plan(args: argparse.Namespace) -> int:
    result = build_publication_plan(Path(args.source_dir), Path(args.output), object_prefix=args.object_prefix)
    print(json.dumps({"wrote": args.output, "object_count": result["object_count"]}, indent=2, sort_keys=True))
    return 0


def cmd_publish_script(args: argparse.Namespace) -> int:
    write_sync_commands(
        Path(args.plan),
        Path(args.output),
        bucket=args.bucket,
        endpoint_url=args.endpoint_url,
        profile=args.profile,
        client=args.client,
        s3cmd_config=args.s3cmd_config,
    )
    print(json.dumps({"wrote": args.output}, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="birdcast-uk")
    subparsers = parser.add_subparsers(required=True)

    static_parser = subparsers.add_parser("static")
    static_sub = static_parser.add_subparsers(required=True)
    static_build = static_sub.add_parser("build")
    static_build.add_argument("--output-dir", required=True)
    static_build.add_argument("--public-base-url", default=DEFAULT_PUBLIC_BASE_URL)
    static_build.add_argument("--object-prefix", default=OBJECT_PREFIX)
    static_build.add_argument("--radars")
    static_build.set_defaults(func=cmd_static_build)
    static_install = static_sub.add_parser("install-site")
    static_install.add_argument("--artifact-root", required=True)
    static_install.add_argument("--site-root", required=True)
    static_install.add_argument("--data-base-url", default="/birdcast-uk/data")
    static_install.add_argument("--object-prefix", default=OBJECT_PREFIX)
    static_install.set_defaults(func=cmd_static_install)

    radars_parser = subparsers.add_parser("radars")
    radars_sub = radars_parser.add_subparsers(required=True)
    radars_pvol = radars_sub.add_parser("from-pvol-catalog")
    radars_pvol.add_argument("--input", default=UKMO_PVOL_CATALOG_URL)
    radars_pvol.add_argument("--output", required=True)
    radars_pvol.set_defaults(func=cmd_radars_from_pvol)

    era5_parser = subparsers.add_parser("era5")
    era5_sub = era5_parser.add_subparsers(required=True)
    era5_request = era5_sub.add_parser("request")
    era5_request.add_argument("--day", required=True, help="YYYY-MM-DD")
    era5_request.add_argument("--kind", choices=["single-levels", "pressure-levels"], required=True)
    era5_request.add_argument("--output-file", required=True)
    era5_request.add_argument("--request-json", required=True)
    era5_request.set_defaults(func=cmd_era5_request)

    era5_download = era5_sub.add_parser("download")
    era5_download.add_argument("--request-json", required=True)
    era5_download.add_argument("--overwrite", action="store_true")
    era5_download.set_defaults(func=cmd_era5_download)
    era5_readiness = era5_sub.add_parser("readiness")
    era5_readiness.add_argument("--credentials")
    era5_readiness.set_defaults(func=cmd_era5_readiness)

    era5_extract_zip = era5_sub.add_parser("extract-zip")
    era5_extract_zip.add_argument("--archive", required=True)
    era5_extract_zip.add_argument("--output-dir", required=True)
    era5_extract_zip.set_defaults(func=cmd_era5_extract_zip)

    era5_features = era5_sub.add_parser("features")
    era5_features.add_argument("--single-levels")
    era5_features.add_argument("--pressure-levels")
    era5_features.add_argument("--radars")
    era5_features.add_argument("--output", required=True)
    era5_features.set_defaults(func=cmd_era5_features)
    era5_grid_features = era5_sub.add_parser("grid-features")
    era5_grid_features.add_argument("--single-levels")
    era5_grid_features.add_argument("--pressure-levels")
    era5_grid_features.add_argument("--radars")
    era5_grid_features.add_argument("--training-table")
    era5_grid_features.add_argument("--boundary")
    era5_grid_features.add_argument("--output", required=True)
    era5_grid_features.set_defaults(func=cmd_era5_grid_features)

    era5_build_day = era5_sub.add_parser("build-day")
    era5_build_day.add_argument("--day", required=True)
    era5_build_day.add_argument("--raw-dir", required=True)
    era5_build_day.add_argument("--feature-output", required=True)
    era5_build_day.add_argument("--radars")
    era5_build_day.add_argument("--download", action="store_true")
    era5_build_day.add_argument("--overwrite", action="store_true")
    era5_build_day.set_defaults(func=cmd_era5_build_day)
    era5_validate_day = era5_sub.add_parser("validate-day")
    era5_validate_day.add_argument("--day", required=True)
    era5_validate_day.add_argument("--raw-dir", required=True)
    era5_validate_day.add_argument("--feature-output", required=True)
    era5_validate_day.set_defaults(func=cmd_era5_validate_day)

    ecmwf_parser = subparsers.add_parser("ecmwf")
    ecmwf_sub = ecmwf_parser.add_subparsers(required=True)
    ecmwf_archive = ecmwf_sub.add_parser("archive-cycle")
    ecmwf_archive.add_argument("--output-root", required=True)
    ecmwf_archive.add_argument("--cycle")
    ecmwf_archive.add_argument("--overwrite", action="store_true")
    ecmwf_archive.set_defaults(func=cmd_ecmwf_archive)

    forecast_parser = subparsers.add_parser("forecast")
    forecast_sub = forecast_parser.add_subparsers(required=True)
    forecast_build = forecast_sub.add_parser("build")
    forecast_build.add_argument("--observed-hourly", required=True)
    forecast_build.add_argument("--radars", required=True)
    forecast_build.add_argument("--output-root", required=True)
    forecast_build.add_argument("--ecmwf-manifest")
    forecast_build.add_argument("--analysis-time")
    forecast_build.add_argument("--members", type=int, default=FORECAST_ENSEMBLE_SIZE)
    forecast_build.set_defaults(func=cmd_forecast_build)

    historical_parser = subparsers.add_parser("historical")
    historical_sub = historical_parser.add_subparsers(required=True)
    historical_build = historical_sub.add_parser("build")
    historical_build.add_argument("--source-dir", required=True)
    historical_build.add_argument("--output-root", required=True)
    historical_build.add_argument("--radars", required=True)
    historical_build.add_argument("--boundary-source", default=NATURAL_EARTH_10M_COUNTRIES_URL)
    historical_build.set_defaults(func=cmd_historical_build)
    historical_boundary = historical_sub.add_parser("boundary")
    historical_boundary.add_argument("--output", required=True)
    historical_boundary.add_argument("--boundary-source", default=NATURAL_EARTH_10M_COUNTRIES_URL)
    historical_boundary.set_defaults(func=cmd_historical_boundary)

    vpts_parser = subparsers.add_parser("vpts")
    vpts_sub = vpts_parser.add_subparsers(required=True)
    vpts_inventory = vpts_sub.add_parser("inventory")
    vpts_inventory.add_argument("--output", required=True)
    vpts_inventory.add_argument("--cursor", required=True)
    vpts_inventory.add_argument("--catalog-url", default=UKMO_VPTS_CATALOG_URL)
    vpts_inventory.add_argument("--bucket", default=DEFAULT_BUCKET)
    vpts_inventory.add_argument("--public-base-url", default=DEFAULT_PUBLIC_BASE_URL)
    vpts_inventory.add_argument(
        "--bootstrap-lookback-days",
        type=int,
        default=VPTS_BOOTSTRAP_LOOKBACK_DAYS,
    )
    vpts_inventory.add_argument(
        "--max-increment-days",
        type=int,
        default=VPTS_MAX_INCREMENT_DAYS,
    )
    vpts_inventory.add_argument(
        "--max-catalog-age-hours",
        type=float,
        default=VPTS_MAX_CATALOG_AGE_HOURS,
    )
    vpts_inventory.set_defaults(func=cmd_vpts_inventory)

    vpts_historical_inventory = vpts_sub.add_parser("historical-inventory")
    vpts_historical_inventory.add_argument("--output", required=True)
    vpts_historical_inventory.add_argument("--catalog-url", default=UKMO_VPTS_CATALOG_URL)
    vpts_historical_inventory.add_argument("--bucket", default=DEFAULT_BUCKET)
    vpts_historical_inventory.add_argument("--public-base-url", default=DEFAULT_PUBLIC_BASE_URL)
    vpts_historical_inventory.add_argument("--days", type=int, default=365)
    vpts_historical_inventory.add_argument("--end-date")
    vpts_historical_inventory.add_argument("--max-workers", type=int, default=16)
    vpts_historical_inventory.add_argument(
        "--max-catalog-age-hours",
        type=float,
        default=VPTS_MAX_CATALOG_AGE_HOURS,
    )
    vpts_historical_inventory.set_defaults(func=cmd_vpts_historical_inventory)

    vpts_validate = vpts_sub.add_parser("validate-manifest")
    vpts_validate.add_argument("--input", required=True)
    vpts_validate.add_argument("--output")
    vpts_validate.set_defaults(func=cmd_vpts_validate)

    observed_parser = subparsers.add_parser("observed")
    observed_sub = observed_parser.add_subparsers(required=True)
    observed_build = observed_sub.add_parser("build")
    observed_build.add_argument("--input", required=True)
    observed_build.add_argument("--output-dir", required=True)
    observed_build.add_argument("--radars")
    observed_build.add_argument("--input-kind", choices=["records", "inventory"], default="records")
    observed_build.add_argument("--max-files", type=int)
    observed_build.add_argument("--cursor")
    observed_build.set_defaults(func=cmd_observed_build)
    observed_hourly = observed_sub.add_parser("hourly")
    observed_hourly.add_argument("--input", required=True)
    observed_hourly.add_argument("--output", required=True)
    observed_hourly.add_argument("--max-files", type=int)
    observed_hourly.set_defaults(func=cmd_observed_hourly)

    features_parser = subparsers.add_parser("features")
    features_sub = features_parser.add_subparsers(required=True)
    features_join = features_sub.add_parser("join-era5")
    features_join.add_argument("--observed-hourly", required=True)
    features_join.add_argument("--era5-dir", required=True)
    features_join.add_argument("--output", required=True)
    features_join.set_defaults(func=cmd_join_era5)

    reanalysis_parser = subparsers.add_parser("reanalysis")
    reanalysis_sub = reanalysis_parser.add_subparsers(required=True)
    reanalysis_prepare = reanalysis_sub.add_parser("prepare")
    reanalysis_prepare.add_argument("--joined-features", required=True)
    reanalysis_prepare.add_argument("--output", required=True)
    reanalysis_prepare.add_argument("--window-days", type=int, default=365)
    reanalysis_prepare.add_argument("--min-profiles-per-hour", type=int, default=3)
    reanalysis_prepare.set_defaults(func=cmd_reanalysis_prepare)
    reanalysis_spec = reanalysis_sub.add_parser("spec")
    reanalysis_spec.add_argument("--table", required=True)
    reanalysis_spec.add_argument("--model-family", choices=["gamm", "xgboost"], required=True)
    reanalysis_spec.add_argument("--output", required=True)
    reanalysis_spec.set_defaults(func=cmd_reanalysis_spec)
    reanalysis_compare = reanalysis_sub.add_parser("compare")
    reanalysis_compare.add_argument("--gamm-metrics", required=True)
    reanalysis_compare.add_argument("--xgboost-metrics", required=True)
    reanalysis_compare.add_argument("--output", required=True)
    reanalysis_compare.set_defaults(func=cmd_reanalysis_compare)
    reanalysis_publish = reanalysis_sub.add_parser("publish")
    reanalysis_publish.add_argument("--predictions", required=True)
    reanalysis_publish.add_argument("--comparison", required=True)
    reanalysis_publish.add_argument("--output-root", required=True)
    reanalysis_publish.set_defaults(func=cmd_reanalysis_publish)
    reanalysis_publish_wide = reanalysis_sub.add_parser("publish-wide")
    reanalysis_publish_wide.add_argument("--lp-csv", required=True)
    reanalysis_publish_wide.add_argument("--sp-csv", required=True)
    reanalysis_publish_wide.add_argument("--comparison", required=True)
    reanalysis_publish_wide.add_argument("--output-root", required=True)
    reanalysis_publish_wide.add_argument("--model-family", choices=["gamm", "xgboost"], required=True)
    reanalysis_publish_wide.set_defaults(func=cmd_reanalysis_publish_wide)
    reanalysis_frames = reanalysis_sub.add_parser("frames")
    reanalysis_frames.add_argument("--predictions-csv", required=True)
    reanalysis_frames.add_argument("--model-family", choices=["gamm", "xgboost"], required=True)
    reanalysis_frames.add_argument("--output", required=True)
    reanalysis_frames.set_defaults(func=cmd_reanalysis_frames)

    bto_parser = subparsers.add_parser("bto")
    bto_sub = bto_parser.add_subparsers(required=True)
    bto_template = bto_sub.add_parser("request-template")
    bto_template.add_argument("--output", required=True)
    bto_template.set_defaults(func=cmd_bto_template)
    bto_status = bto_sub.add_parser("validation-status")
    bto_status.add_argument("--output", required=True)
    bto_status.add_argument("--status", default="request_pending")
    bto_status.add_argument("--data-available", action="store_true")
    bto_status.set_defaults(func=cmd_bto_status)
    bto_validate = bto_sub.add_parser("validate")
    bto_validate.add_argument("--bto-csv", required=True)
    bto_validate.add_argument("--radar-csv", required=True)
    bto_validate.add_argument("--output", required=True)
    bto_validate.set_defaults(func=cmd_bto_validate)

    publish_parser = subparsers.add_parser("publish")
    publish_sub = publish_parser.add_subparsers(required=True)
    publish_plan = publish_sub.add_parser("plan")
    publish_plan.add_argument("--source-dir", required=True)
    publish_plan.add_argument("--output", required=True)
    publish_plan.add_argument("--object-prefix", default=OBJECT_PREFIX)
    publish_plan.set_defaults(func=cmd_publish_plan)
    publish_script = publish_sub.add_parser("sync-script")
    publish_script.add_argument("--plan", required=True)
    publish_script.add_argument("--output", required=True)
    publish_script.add_argument("--bucket", default=DEFAULT_BUCKET)
    publish_script.add_argument("--endpoint-url", default=DEFAULT_INTERNAL_ENDPOINT)
    publish_script.add_argument("--profile")
    publish_script.add_argument("--client", choices=["aws", "s3cmd"], default="aws")
    publish_script.add_argument("--s3cmd-config")
    publish_script.set_defaults(func=cmd_publish_script)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
