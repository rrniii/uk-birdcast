"""Command line tools for UK BirdCast static artifacts and data flows."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .bto import write_request_template, write_validation_status
from .config import (
    DEFAULT_BUCKET,
    DEFAULT_INTERNAL_ENDPOINT,
    DEFAULT_PUBLIC_BASE_URL,
    OBJECT_PREFIX,
    UKMO_PVOL_CATALOG_URL,
    UKMO_VPTS_CATALOG_URL,
    VPTS_BOOTSTRAP_LOOKBACK_DAYS,
    VPTS_MAX_CATALOG_AGE_HOURS,
    VPTS_MAX_INCREMENT_DAYS,
)
from .era5 import build_day, download_request, extract_site_features, extract_zip_archive, write_request
from .joined import join_observed_to_era5
from .observed import build_observed_products
from .publication import build_publication_plan, write_sync_commands
from .radars import radars_from_pvol_catalog, write_radars
from .static_artifacts import build_static_artifacts, install_static_site
from .vpts import build_catalog_inventory, validate_manifest


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

    era5_build_day = era5_sub.add_parser("build-day")
    era5_build_day.add_argument("--day", required=True)
    era5_build_day.add_argument("--raw-dir", required=True)
    era5_build_day.add_argument("--feature-output", required=True)
    era5_build_day.add_argument("--radars")
    era5_build_day.add_argument("--download", action="store_true")
    era5_build_day.add_argument("--overwrite", action="store_true")
    era5_build_day.set_defaults(func=cmd_era5_build_day)

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

    features_parser = subparsers.add_parser("features")
    features_sub = features_parser.add_subparsers(required=True)
    features_join = features_sub.add_parser("join-era5")
    features_join.add_argument("--observed-hourly", required=True)
    features_join.add_argument("--era5-dir", required=True)
    features_join.add_argument("--output", required=True)
    features_join.set_defaults(func=cmd_join_era5)

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
    publish_script.set_defaults(func=cmd_publish_script)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
