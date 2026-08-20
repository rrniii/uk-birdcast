"""Command line tools for UK BirdCast static artifacts and data flows."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .archive import (
    VptsObject,
    aloft_daily_objects,
    build_comparison_index,
    build_crosswalk,
    compare_vpts_profiles,
    load_vpts_rows,
    write_comparison_report,
)
from .bto import validate_aggregates, write_request_template, write_validation_status
from .coastal import build_coastal_relative_flow, install_coastal_static_site
from .config import (
    ALOFT_COVERAGE_URL,
    ALOFT_PUBLIC_BASE_URL,
    ALOFT_SOURCES,
    DEFAULT_BUCKET,
    DEFAULT_INTERNAL_ENDPOINT,
    DEFAULT_PUBLIC_BASE_URL,
    EUROPE_ERA5_AREA,
    EUROPE_MIN_TRAINING_DAYS,
    EUROPE_MIN_TRANSFER_DAYS,
    FORECAST_ENSEMBLE_SIZE,
    OBJECT_PREFIX,
    UK_PVOL_MAX_RANGE_M,
    UKMO_PVOL_CATALOG_URL,
    UKMO_VPTS_CATALOG_URL,
    VPTS_BOOTSTRAP_LOOKBACK_DAYS,
    VPTS_MAX_CATALOG_AGE_HOURS,
    VPTS_MAX_INCREMENT_DAYS,
)
from .ecmwf import archive_cycle
from .era5 import (
    build_day,
    build_period,
    cds_readiness,
    download_request,
    extract_grid_features,
    extract_site_features,
    extract_zip_archive,
    validate_day,
    write_request,
)
from .europe import (
    build_aloft_cohort,
    build_europe_manifest,
    install_europe_static_site,
    iter_aloft_coverage,
    publish_europe_prediction_partitions,
    publish_europe_predictions,
    read_jsonl_record,
    stream_aloft_chunk,
    stream_aloft_hourly,
    write_aloft_chunk_manifest,
    write_aloft_cohort,
    write_hourly_parquet,
)
from .europe_fidelity import (
    verify_aloft_chunk,
    verify_training_input_policy,
    write_fidelity_report,
)
from .external_validation import validate_external_vpts_csv
from .forecast import build_forecast
from .historical import NATURAL_EARTH_10M_COUNTRIES_URL, build_historical_products, write_boundary
from .joined import join_observed_to_era5
from .observed import build_hourly_observations, build_observed_products
from .publication import (
    build_publication_plan,
    validate_publication_plan,
    validate_release,
    write_sync_commands,
)
from .radars import radars_from_pvol_catalog, write_radars
from .reanalysis import (
    compare_models,
    prepare_training_table,
    publish_wide_reanalysis,
    write_model_spec,
)
from .static_artifacts import build_static_artifacts, install_static_site, write_json
from .vpts import build_catalog_inventory, build_historical_inventory, validate_manifest


def cmd_europe_cohort(args: argparse.Namespace) -> int:
    entries = build_aloft_cohort(
        iter_aloft_coverage(
            coverage_url=args.coverage_url,
            public_base_url=args.public_base_url,
            source="baltrad",
        ),
        minimum_training_days=args.minimum_training_days,
        minimum_transfer_days=args.minimum_transfer_days,
    )
    payload = write_aloft_cohort(entries, Path(args.output))
    print(
        json.dumps(
            {key: value for key, value in payload.items() if key != "entries"},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def cmd_europe_stream_day(args: argparse.Namespace) -> int:
    day = args.day.replace("-", "")
    obj = VptsObject(
        source="baltrad",
        radar=args.radar.lower(),
        day=day,
        url=(
            f"{args.public_base_url.rstrip('/')}/baltrad/daily/{args.radar.lower()}/"
            f"{day[:4]}/{args.radar.lower()}_vpts_{day}.csv"
        ),
    )
    rows, audit = stream_aloft_hourly(obj)
    result = write_hourly_parquet(rows, Path(args.output), audit=audit)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_europe_chunk_manifest(args: argparse.Namespace) -> int:
    cohort = json.loads(Path(args.cohort).read_text(encoding="utf-8"))
    result = write_aloft_chunk_manifest(
        iter_aloft_coverage(
            coverage_url=args.coverage_url,
            public_base_url=args.public_base_url,
            source="baltrad",
        ),
        cohort=cohort,
        output=Path(args.output),
        start_day=args.start_day,
        end_day=args.end_day,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_europe_stream_chunk(args: argparse.Namespace) -> int:
    chunk = read_jsonl_record(Path(args.manifest), args.index)
    result = stream_aloft_chunk(
        chunk,
        output_root=Path(args.output_root),
        public_base_url=args.public_base_url,
        release_id=args.release_id,
    )
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "audits"},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def cmd_europe_verify_chunk(args: argparse.Namespace) -> int:
    chunk = read_jsonl_record(Path(args.manifest), args.index)
    payload = verify_aloft_chunk(
        chunk,
        hourly_root=Path(args.hourly_root),
        public_base_url=args.public_base_url,
        release_id=args.release_id,
    )
    write_fidelity_report(payload, Path(args.output))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_europe_verify_training(args: argparse.Namespace) -> int:
    spec = json.loads(Path(args.model_spec).read_text(encoding="utf-8"))
    payload = verify_training_input_policy(
        Path(args.training_csv),
        cohort_json=Path(args.cohort),
        required_predictors=[str(item) for item in spec.get("predictors", [])],
        transfer_csv=Path(args.transfer_csv) if args.transfer_csv else None,
    )
    write_fidelity_report(payload, Path(args.output))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_europe_manifest(args: argparse.Namespace) -> int:
    payload = build_europe_manifest(
        model_id=args.model_id,
        first_time_utc=args.first_time_utc,
        latest_time_utc=args.latest_time_utc,
        aloft_radar_count=args.aloft_radar_count,
        uk_sp_radar_count=args.uk_sp_radar_count,
        grid_asset=args.grid_asset,
        daily_asset_template=args.daily_asset_template,
        validation_url=args.validation_url,
        output=Path(args.output),
        release_status=args.release_status,
        radar_asset=args.radar_asset,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_europe_static_install(args: argparse.Namespace) -> int:
    result = install_europe_static_site(Path(args.site_root))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_coastal_build(args: argparse.Namespace) -> int:
    payload = build_coastal_relative_flow(
        training_csv=Path(args.training_csv),
        cohort_json=Path(args.cohort),
        output_root=Path(args.output_root),
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_coastal_static_install(args: argparse.Namespace) -> int:
    result = install_coastal_static_site(Path(args.site_root))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_europe_publish(args: argparse.Namespace) -> int:
    publish = (
        publish_europe_prediction_partitions
        if args.predictions_root
        else publish_europe_predictions
    )
    common = dict(
        output_root=Path(args.output_root),
        model_id=args.model_id,
        aloft_radar_count=args.aloft_radar_count,
        uk_sp_radar_count=args.uk_sp_radar_count,
        validation_url=args.validation_url,
        radars_json=Path(args.radars) if args.radars else None,
        release_status=args.release_status,
    )
    result = publish(
        **(
            {"predictions_root": Path(args.predictions_root)}
            if args.predictions_root
            else {"predictions_csv": Path(args.predictions)}
        ),
        **common,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_static_build(args: argparse.Namespace) -> int:
    radars = args.radars or os.environ.get("BIRDCAST_UK_RADARS_FILE") or ""
    result = build_static_artifacts(
        Path(args.output_dir),
        public_base_url=args.public_base_url,
        object_prefix=args.object_prefix,
        radars_path=Path(radars) if radars else None,
        forecast_enabled=args.forecast_enabled,
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
        radars_path=Path(args.radars),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def cmd_era5_build_period(args: argparse.Namespace) -> int:
    result = build_period(
        start_day=args.start_day,
        end_day=args.end_day,
        raw_dir=Path(args.raw_dir),
        feature_dir=Path(args.feature_dir),
        radars_path=Path(args.radars) if args.radars else None,
        area=EUROPE_ERA5_AREA if args.domain == "europe" else None,
        download=args.download,
        overwrite=args.overwrite,
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
        expected_latest_date=args.expected_latest_date,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_historical_boundary(args: argparse.Namespace) -> int:
    result = write_boundary(Path(args.output), boundary_source=args.boundary_source)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_radars_from_pvol(args: argparse.Namespace) -> int:
    radars = radars_from_pvol_catalog(
        args.input,
        default_max_range_m=args.default_max_range_m,
    )
    payload = write_radars(Path(args.output), radars, source=args.input)
    print(
        json.dumps(
            {"wrote": args.output, "radar_count": len(payload["radars"])}, indent=2, sort_keys=True
        )
    )
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


def cmd_archive_aloft_coverage(args: argparse.Namespace) -> int:
    objects = aloft_daily_objects(
        radar=args.radar,
        start_day=args.start_day,
        end_day=args.end_day,
        source=args.source,
        coverage_url=args.coverage_url,
        public_base_url=args.public_base_url,
    )
    payload = {
        "schema_version": "birdcast-uk-aloft-coverage-1.0",
        "source": args.source,
        "radar": args.radar.lower(),
        "start_day": args.start_day,
        "end_day": args.end_day,
        "object_count": len(objects),
        "objects": [obj.to_dict() for obj in objects],
        "source_policy": "Existing Aloft VPTS objects are catalogued read-only.",
    }
    write_json(Path(args.output), payload)
    print(
        json.dumps({"wrote": args.output, "object_count": len(objects)}, indent=2, sort_keys=True)
    )
    return 0


def cmd_archive_compare(args: argparse.Namespace) -> int:
    uk = VptsObject(
        source="jasmin-uk",
        radar=args.uk_radar.lower(),
        day=args.day.replace("-", ""),
        url=args.uk_url,
        pulse=args.pulse,
    )
    aloft = VptsObject(
        source=args.aloft_source,
        radar=args.aloft_radar.lower(),
        day=args.day.replace("-", ""),
        url=args.aloft_url,
    )
    report = compare_vpts_profiles(
        load_vpts_rows(uk),
        load_vpts_rows(aloft),
        requested=args.datetime,
        max_time_offset_seconds=args.max_time_offset_seconds,
    )
    result = write_comparison_report(report, Path(args.output))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_archive_crosswalk(args: argparse.Namespace) -> int:
    radar_payload = json.loads(Path(args.uk_radars).read_text(encoding="utf-8"))
    uk_radars = (
        radar_payload.get("radars", radar_payload)
        if isinstance(radar_payload, dict)
        else radar_payload
    )
    mapping_payload = json.loads(Path(args.mappings).read_text(encoding="utf-8"))
    mappings = (
        mapping_payload.get("mappings", mapping_payload)
        if isinstance(mapping_payload, dict)
        else mapping_payload
    )
    if not isinstance(uk_radars, list) or not isinstance(mappings, list):
        raise ValueError("UK radar and mapping files must each contain a list")
    payload = build_crosswalk(uk_radars, mappings)
    write_json(Path(args.output), payload)
    print(
        json.dumps(
            {"wrote": args.output, "entry_count": payload["entry_count"]}, indent=2, sort_keys=True
        )
    )
    return 0


def cmd_archive_comparison_index(args: argparse.Namespace) -> int:
    crosswalk = json.loads(Path(args.crosswalk).read_text(encoding="utf-8"))
    if not isinstance(crosswalk, dict):
        raise ValueError("Crosswalk must be a JSON object")
    reports: list[dict[str, object]] = []
    report_directory = Path(args.reports_dir)
    if report_directory.exists():
        for path in sorted(report_directory.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                reports.append(payload)
    payload = build_comparison_index(crosswalk, reports)
    write_json(Path(args.output), payload)
    print(
        json.dumps(
            {
                "wrote": args.output,
                "entry_count": payload["entry_count"],
                "report_count": payload["report_count"],
                "status": payload["status"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


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
    payload = write_validation_status(
        Path(args.output), data_available=args.data_available, status=args.status
    )
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
        extra_era5_features=tuple(args.extra_era5_feature),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_reanalysis_spec(args: argparse.Namespace) -> int:
    result = write_model_spec(
        Path(args.output),
        table=Path(args.table),
        model_family=args.model_family,
        gamm_options_path=Path(args.gamm_options) if args.gamm_options else None,
    )
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


def cmd_reanalysis_publish_wide(args: argparse.Namespace) -> int:
    result = publish_wide_reanalysis(
        lp_csv=Path(args.lp_csv),
        sp_csv=Path(args.sp_csv),
        comparison=Path(args.comparison),
        output_root=Path(args.output_root),
        model_family=args.model_family,
        component_manifest=Path(args.component_manifest),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_reanalysis_validate_external(args: argparse.Namespace) -> int:
    result = validate_external_vpts_csv(
        vpts_csv=Path(args.vpts_csv),
        predictions_csv=Path(args.predictions_csv),
        output=Path(args.output),
        site=json.loads(args.site_json),
        model=json.loads(args.model_json),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_publish_plan(args: argparse.Namespace) -> int:
    result = build_publication_plan(
        Path(args.source_dir),
        Path(args.output),
        object_prefix=args.object_prefix,
        products=tuple(args.product),
    )
    print(
        json.dumps(
            {"wrote": args.output, "object_count": result["object_count"]}, indent=2, sort_keys=True
        )
    )
    return 0


def cmd_publish_validate(args: argparse.Namespace) -> int:
    result = validate_release(
        Path(args.source_dir),
        required_products=tuple(args.require),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
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


def cmd_publish_validate_plan(args: argparse.Namespace) -> int:
    result = validate_publication_plan(Path(args.plan))
    print(json.dumps({"plan": args.plan, "object_count": result["object_count"]}, indent=2))
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
    static_build.add_argument(
        "--forecast-enabled",
        action="store_true",
        help="Preserve an existing forecast manifest only after its production contract is approved.",
    )
    static_build.set_defaults(func=cmd_static_build)
    static_install = static_sub.add_parser("install-site")
    static_install.add_argument("--artifact-root", required=True)
    static_install.add_argument("--site-root", required=True)
    static_install.add_argument("--data-base-url", default="/birdcast-uk/data")
    static_install.add_argument("--object-prefix", default=OBJECT_PREFIX)
    static_install.set_defaults(func=cmd_static_install)

    europe_parser = subparsers.add_parser("europe")
    europe_sub = europe_parser.add_subparsers(required=True)
    europe_cohort = europe_sub.add_parser("cohort")
    europe_cohort.add_argument("--coverage-url", default=ALOFT_COVERAGE_URL)
    europe_cohort.add_argument("--public-base-url", default=ALOFT_PUBLIC_BASE_URL)
    europe_cohort.add_argument(
        "--minimum-training-days", type=int, default=EUROPE_MIN_TRAINING_DAYS
    )
    europe_cohort.add_argument(
        "--minimum-transfer-days", type=int, default=EUROPE_MIN_TRANSFER_DAYS
    )
    europe_cohort.add_argument("--output", required=True)
    europe_cohort.set_defaults(func=cmd_europe_cohort)
    europe_stream = europe_sub.add_parser("stream-day")
    europe_stream.add_argument("--radar", required=True)
    europe_stream.add_argument("--day", required=True)
    europe_stream.add_argument("--public-base-url", default=ALOFT_PUBLIC_BASE_URL)
    europe_stream.add_argument("--output", required=True)
    europe_stream.set_defaults(func=cmd_europe_stream_day)
    europe_chunks = europe_sub.add_parser("chunk-manifest")
    europe_chunks.add_argument("--cohort", required=True)
    europe_chunks.add_argument("--coverage-url", default=ALOFT_COVERAGE_URL)
    europe_chunks.add_argument("--public-base-url", default=ALOFT_PUBLIC_BASE_URL)
    europe_chunks.add_argument("--start-day")
    europe_chunks.add_argument("--end-day")
    europe_chunks.add_argument("--output", required=True)
    europe_chunks.set_defaults(func=cmd_europe_chunk_manifest)
    europe_chunk = europe_sub.add_parser("stream-chunk")
    europe_chunk.add_argument("--manifest", required=True)
    europe_chunk.add_argument("--index", required=True, type=int)
    europe_chunk.add_argument("--output-root", required=True)
    europe_chunk.add_argument("--public-base-url", default=ALOFT_PUBLIC_BASE_URL)
    europe_chunk.add_argument("--release-id", default="unversioned")
    europe_chunk.set_defaults(func=cmd_europe_stream_chunk)
    europe_verify_chunk = europe_sub.add_parser("verify-chunk")
    europe_verify_chunk.add_argument("--manifest", required=True)
    europe_verify_chunk.add_argument("--index", required=True, type=int)
    europe_verify_chunk.add_argument("--hourly-root", required=True)
    europe_verify_chunk.add_argument("--public-base-url", default=ALOFT_PUBLIC_BASE_URL)
    europe_verify_chunk.add_argument("--release-id", default="unversioned")
    europe_verify_chunk.add_argument("--output", required=True)
    europe_verify_chunk.set_defaults(func=cmd_europe_verify_chunk)
    europe_verify_training = europe_sub.add_parser("verify-training")
    europe_verify_training.add_argument("--training-csv", required=True)
    europe_verify_training.add_argument("--transfer-csv")
    europe_verify_training.add_argument("--cohort", required=True)
    europe_verify_training.add_argument("--model-spec", required=True)
    europe_verify_training.add_argument("--output", required=True)
    europe_verify_training.set_defaults(func=cmd_europe_verify_training)
    europe_manifest = europe_sub.add_parser("manifest")
    europe_manifest.add_argument("--model-id", required=True)
    europe_manifest.add_argument("--first-time-utc", required=True)
    europe_manifest.add_argument("--latest-time-utc", required=True)
    europe_manifest.add_argument("--aloft-radar-count", required=True, type=int)
    europe_manifest.add_argument("--uk-sp-radar-count", required=True, type=int)
    europe_manifest.add_argument("--grid-asset", required=True)
    europe_manifest.add_argument("--daily-asset-template", required=True)
    europe_manifest.add_argument("--validation-url", required=True)
    europe_manifest.add_argument("--radar-asset")
    europe_manifest.add_argument("--release-status", default="research-preview")
    europe_manifest.add_argument("--output", required=True)
    europe_manifest.set_defaults(func=cmd_europe_manifest)
    europe_static = europe_sub.add_parser("install-site")
    europe_static.add_argument("--site-root", required=True)
    europe_static.set_defaults(func=cmd_europe_static_install)
    europe_publish = europe_sub.add_parser("publish")
    prediction_input = europe_publish.add_mutually_exclusive_group(required=True)
    prediction_input.add_argument("--predictions")
    prediction_input.add_argument("--predictions-root")
    europe_publish.add_argument("--output-root", required=True)
    europe_publish.add_argument("--model-id", required=True)
    europe_publish.add_argument("--aloft-radar-count", required=True, type=int)
    europe_publish.add_argument("--uk-sp-radar-count", required=True, type=int)
    europe_publish.add_argument("--validation-url", required=True)
    europe_publish.add_argument("--radars")
    europe_publish.add_argument("--release-status", default="research-preview")
    europe_publish.set_defaults(func=cmd_europe_publish)

    coastal_parser = subparsers.add_parser("coastal")
    coastal_sub = coastal_parser.add_subparsers(required=True)
    coastal_build = coastal_sub.add_parser("build-relative-flow")
    coastal_build.add_argument("--training-csv", required=True)
    coastal_build.add_argument("--cohort", required=True)
    coastal_build.add_argument("--output-root", required=True)
    coastal_build.set_defaults(func=cmd_coastal_build)
    coastal_static = coastal_sub.add_parser("install-site")
    coastal_static.add_argument("--site-root", required=True)
    coastal_static.set_defaults(func=cmd_coastal_static_install)

    radars_parser = subparsers.add_parser("radars")
    radars_sub = radars_parser.add_subparsers(required=True)
    radars_pvol = radars_sub.add_parser("from-pvol-catalog")
    radars_pvol.add_argument("--input", default=UKMO_PVOL_CATALOG_URL)
    radars_pvol.add_argument("--output", required=True)
    radars_pvol.add_argument(
        "--default-max-range-m",
        type=float,
        default=UK_PVOL_MAX_RANGE_M,
        help="ODIM-derived network range used when the aggregate catalogue omits max_range_m",
    )
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
    era5_validate_day.add_argument("--radars", required=True)
    era5_validate_day.set_defaults(func=cmd_era5_validate_day)
    era5_build_period = era5_sub.add_parser("build-period")
    era5_build_period.add_argument("--start-day", required=True)
    era5_build_period.add_argument("--end-day", required=True)
    era5_build_period.add_argument("--raw-dir", required=True)
    era5_build_period.add_argument("--feature-dir", required=True)
    era5_build_period.add_argument("--radars")
    era5_build_period.add_argument("--domain", choices=("uk", "europe"), default="uk")
    era5_build_period.add_argument("--download", action="store_true")
    era5_build_period.add_argument("--overwrite", action="store_true")
    era5_build_period.set_defaults(func=cmd_era5_build_period)

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
    historical_build.add_argument("--expected-latest-date")
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

    archive_parser = subparsers.add_parser("archive")
    archive_sub = archive_parser.add_subparsers(required=True)
    aloft_coverage = archive_sub.add_parser("aloft-coverage")
    aloft_coverage.add_argument("--radar", required=True)
    aloft_coverage.add_argument("--start-day", required=True)
    aloft_coverage.add_argument("--end-day", required=True)
    aloft_coverage.add_argument("--source", choices=ALOFT_SOURCES, default="baltrad")
    aloft_coverage.add_argument("--coverage-url", default=ALOFT_COVERAGE_URL)
    aloft_coverage.add_argument("--public-base-url", default=ALOFT_PUBLIC_BASE_URL)
    aloft_coverage.add_argument("--output", required=True)
    aloft_coverage.set_defaults(func=cmd_archive_aloft_coverage)

    archive_compare = archive_sub.add_parser("compare")
    archive_compare.add_argument("--uk-radar", required=True)
    archive_compare.add_argument("--uk-url", required=True)
    archive_compare.add_argument("--pulse", choices=["lp", "sp"], required=True)
    archive_compare.add_argument("--aloft-radar", required=True)
    archive_compare.add_argument("--aloft-source", choices=ALOFT_SOURCES, default="baltrad")
    archive_compare.add_argument("--aloft-url", required=True)
    archive_compare.add_argument("--day", required=True)
    archive_compare.add_argument("--datetime", required=True)
    archive_compare.add_argument("--max-time-offset-seconds", type=float, default=300.0)
    archive_compare.add_argument("--output", required=True)
    archive_compare.set_defaults(func=cmd_archive_compare)

    archive_crosswalk = archive_sub.add_parser("crosswalk")
    archive_crosswalk.add_argument("--uk-radars", required=True)
    archive_crosswalk.add_argument("--mappings", required=True)
    archive_crosswalk.add_argument("--output", required=True)
    archive_crosswalk.set_defaults(func=cmd_archive_crosswalk)

    archive_index = archive_sub.add_parser("comparison-index")
    archive_index.add_argument("--crosswalk", required=True)
    archive_index.add_argument("--reports-dir", required=True)
    archive_index.add_argument("--output", required=True)
    archive_index.set_defaults(func=cmd_archive_comparison_index)

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
    reanalysis_prepare.add_argument(
        "--extra-era5-feature",
        action="append",
        default=[],
        help="Opt-in ERA5 predictor requiring complete coverage, for example u_925_ms.",
    )
    reanalysis_prepare.set_defaults(func=cmd_reanalysis_prepare)
    reanalysis_spec = reanalysis_sub.add_parser("spec")
    reanalysis_spec.add_argument("--table", required=True)
    reanalysis_spec.add_argument("--model-family", choices=["gamm", "xgboost"], required=True)
    reanalysis_spec.add_argument("--gamm-options")
    reanalysis_spec.add_argument("--output", required=True)
    reanalysis_spec.set_defaults(func=cmd_reanalysis_spec)
    reanalysis_compare = reanalysis_sub.add_parser("compare")
    reanalysis_compare.add_argument("--gamm-metrics", required=True)
    reanalysis_compare.add_argument("--xgboost-metrics", required=True)
    reanalysis_compare.add_argument("--output", required=True)
    reanalysis_compare.set_defaults(func=cmd_reanalysis_compare)
    reanalysis_publish_wide = reanalysis_sub.add_parser("publish-wide")
    reanalysis_publish_wide.add_argument("--lp-csv", required=True)
    reanalysis_publish_wide.add_argument("--sp-csv", required=True)
    reanalysis_publish_wide.add_argument("--comparison", required=True)
    reanalysis_publish_wide.add_argument("--output-root", required=True)
    reanalysis_publish_wide.add_argument(
        "--model-family", choices=["gamm", "xgboost"], required=True
    )
    reanalysis_publish_wide.add_argument("--component-manifest", required=True)
    reanalysis_publish_wide.set_defaults(func=cmd_reanalysis_publish_wide)
    reanalysis_external = reanalysis_sub.add_parser("validate-external")
    reanalysis_external.add_argument("--vpts-csv", required=True)
    reanalysis_external.add_argument("--predictions-csv", required=True)
    reanalysis_external.add_argument("--site-json", required=True)
    reanalysis_external.add_argument("--model-json", required=True)
    reanalysis_external.add_argument("--output", required=True)
    reanalysis_external.set_defaults(func=cmd_reanalysis_validate_external)

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
    publish_validate = publish_sub.add_parser("validate")
    publish_validate.add_argument("--source-dir", required=True)
    publish_validate.add_argument(
        "--require",
        action="append",
        default=[],
        choices=["historical", "gam-era5"],
        required=True,
    )
    publish_validate.set_defaults(func=cmd_publish_validate)
    publish_plan = publish_sub.add_parser("plan")
    publish_plan.add_argument("--source-dir", required=True)
    publish_plan.add_argument("--output", required=True)
    publish_plan.add_argument("--object-prefix", default=OBJECT_PREFIX)
    publish_plan.add_argument(
        "--product",
        action="append",
        choices=["historical", "gam-era5"],
        required=True,
        help="Data-bearing product to include; repeat for a multi-product release.",
    )
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
    publish_validate_plan = publish_sub.add_parser("validate-plan")
    publish_validate_plan.add_argument("--plan", required=True)
    publish_validate_plan.set_defaults(func=cmd_publish_validate_plan)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
