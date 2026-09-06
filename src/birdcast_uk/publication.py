"""Object-store publication planning for static BirdCast UK artifacts."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile

from .config import OBJECT_PREFIX
from .selected_model import (
    COMPONENT_MANIFEST_SHA256,
    COMPONENT_SHA256,
    QUALIFIED_DAY_COUNT,
    QUALIFIED_FIRST_DAY,
    QUALIFIED_LAST_DAY,
    SELECTION_ID,
    qualified_dates,
)
from .static_artifacts import utc_now, write_json

HISTORICAL_BASELINE_DATE = "2026-08-15"
SELECTED_MODEL_ID = SELECTION_ID
MODEL_SCHEMA_VERSION = "live-uk-bird-maps-gam-era5-1.2"
MODEL_PULSES = {"lp", "sp"}
MODEL_TARGETS = {
    "mtr_birds_km_h",
    "vid_birds_per_km2",
    "bird_u_ms",
    "bird_v_ms",
}
PUBLICATION_PRODUCTS = frozenset({"historical", "gam-era5"})


@dataclass(frozen=True)
class PublicationObject:
    source: str
    key: str
    size: int
    sha256: str
    content_type: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_publication_plan(
    source_dir: Path,
    output: Path,
    *,
    object_prefix: str = OBJECT_PREFIX,
    products: tuple[str, ...],
) -> dict[str, object]:
    """Plan an exact release from product manifests and referenced assets.

    Model runs share a JASMIN filesystem with the compact public artifacts.
    Recursively walking that root previously exposed private predictions and
    experiments, so publication is now deliberately manifest-scoped.
    """

    if not products:
        raise ValueError("at least one product manifest is required for publication")
    unsupported = set(products) - PUBLICATION_PRODUCTS
    if unsupported:
        raise ValueError(f"unsupported publication products: {', '.join(sorted(unsupported))}")
    if len(set(products)) != len(products):
        raise ValueError("publication products must be unique")
    prefix = _normalise_object_prefix(object_prefix)
    validate_release(source_dir, required_products=products)
    paths = _product_paths(source_dir, products)
    root = source_dir.resolve()
    objects = []
    for path in paths:
        relative = path.relative_to(root)
        key = "/".join([prefix, *relative.parts])
        objects.append(
            PublicationObject(
                source=str(path),
                key=key,
                size=path.stat().st_size,
                sha256=_sha256(path),
                content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            ).to_dict()
        )
    payload = {
        "generated_at_utc": utc_now(),
        "products": list(products),
        "object_prefix": prefix,
        "source_dir": str(root),
        "object_count": len(objects),
        "objects": objects,
    }
    write_json(output, payload)
    return payload


def validate_release(
    source_dir: Path,
    *,
    required_products: tuple[str, ...] = ("historical", "gam-era5"),
) -> dict[str, object]:
    """Fail closed when a release would publish placeholder or dangling manifests."""

    if not required_products:
        raise ValueError("at least one product manifest is required for validation")
    if len(set(required_products)) != len(required_products):
        raise ValueError("release validation products must be unique")
    checked_assets = 0
    products = {}
    for product in required_products:
        if product not in PUBLICATION_PRODUCTS:
            raise ValueError(f"unsupported publication product: {product}")
        manifest_path = source_dir / "latest" / f"{product}.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"required release manifest is missing: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("data_available") is not True:
            raise ValueError(f"required product {product} is not data-bearing")
        if product == "historical":
            _validate_historical_manifest(source_dir, manifest)
        elif product == "gam-era5":
            _validate_selected_model_manifest(manifest)
        missing = []
        asset_paths = list(_manifest_asset_paths(manifest))
        for asset in asset_paths:
            if asset.startswith(("http://", "https://")):
                raise ValueError(f"public product {product} may not reference an external asset")
            try:
                _resolve_public_asset(source_dir, asset)
            except (FileNotFoundError, ValueError):
                missing.append(asset)
        if missing:
            sample = ", ".join(missing[:5])
            raise FileNotFoundError(
                f"required product {product} references {len(missing)} missing assets: {sample}"
            )
        checked_assets += len(asset_paths)
        products[product] = {
            "manifest": str(manifest_path),
            "schema_version": manifest.get("schema_version"),
            "asset_count": len(asset_paths),
        }
    _validate_forecast_tombstone(source_dir)
    return {
        "ok": True,
        "source_dir": str(source_dir),
        "required_products": list(required_products),
        "checked_asset_count": checked_assets,
        "products": products,
    }


def _asset_paths(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _asset_paths(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _asset_paths(nested)


def _manifest_asset_paths(manifest: dict[str, object]):
    """Yield all local/public references that travel with a product manifest."""

    yield from _asset_paths(manifest.get("assets"))
    for key in ("comparison", "source", "validation_url"):
        value = manifest.get(key)
        if isinstance(value, str) and value:
            yield value


def _product_paths(source_dir: Path, products: tuple[str, ...]) -> list[Path]:
    root = source_dir.resolve()
    selected: set[Path] = set()
    selected.add(_resolve_public_asset(root, "latest/forecast.json"))
    for product in products:
        manifest_path = _resolve_public_asset(root, f"latest/{product}.json")
        selected.add(manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for asset in _manifest_asset_paths(manifest):
            if asset.startswith(("http://", "https://")):
                continue
            selected.add(_resolve_public_asset(root, asset))
    return sorted(selected, key=lambda path: path.relative_to(root).as_posix())


def _validate_forecast_tombstone(source_dir: Path) -> None:
    path = source_dir / "latest" / "forecast.json"
    if not path.is_file():
        raise FileNotFoundError(f"disabled forecast tombstone is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assets = payload.get("assets")
    if (
        payload.get("data_available") is not False
        or payload.get("mode") != "disabled"
        or payload.get("valid_times_utc") not in (None, [])
        or not isinstance(assets, dict)
        or assets.get("frames") not in (None, [])
    ):
        raise ValueError("forecast tombstone must explicitly disable an empty forecast")


def _validate_historical_manifest(source_dir: Path, manifest: dict[str, object]) -> None:
    """Validate the immutable catch-up release before its latest pointer moves."""

    if manifest.get("schema_version") != "live-uk-bird-maps-historical-1.2":
        raise ValueError("historical product has an unsupported schema")
    release_id = manifest.get("release_id")
    if (
        not isinstance(release_id, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", release_id) is None
    ):
        raise ValueError("historical product has an invalid release id")
    try:
        first_date = date.fromisoformat(str(manifest.get("first_date") or ""))
        latest_date = date.fromisoformat(str(manifest.get("latest_date") or ""))
    except ValueError as exc:
        raise ValueError(
            "historical product does not reach the required baseline "
            f"{HISTORICAL_BASELINE_DATE}: invalid date coverage"
        ) from exc
    if latest_date < date.fromisoformat(HISTORICAL_BASELINE_DATE):
        raise ValueError(
            "historical product does not reach the required baseline "
            f"{HISTORICAL_BASELINE_DATE}: {latest_date.isoformat()}"
        )
    if first_date > latest_date:
        raise ValueError("historical product date coverage is reversed")
    expected_years = list(range(first_date.year, latest_date.year + 1))
    if manifest.get("years") != expected_years:
        raise ValueError("historical product years do not match its date coverage")

    source = manifest.get("source")
    if not isinstance(source, dict) or source.get("failure_count") != 0:
        raise ValueError("historical product source must report zero failures")
    for field in ("files_seen", "profiles_seen", "rows_seen"):
        value = source.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"historical product source has invalid {field}")

    radars = manifest.get("radars")
    coverage = manifest.get("radar_coverage")
    if not isinstance(radars, list) or not radars or not isinstance(coverage, list):
        raise ValueError("historical product requires radar metadata and coverage")
    radar_slugs = {str(record.get("slug") or "") for record in radars if isinstance(record, dict)}
    coverage_slugs = {
        str(record.get("radar") or "") for record in coverage if isinstance(record, dict)
    }
    if (
        "" in radar_slugs
        or radar_slugs != coverage_slugs
        or len(radars) != len(radar_slugs)
        or len(coverage) != len(coverage_slugs)
    ):
        raise ValueError("historical product radar coverage is incomplete")

    assets = manifest.get("assets")
    if not isinstance(assets, dict):
        raise ValueError("historical product has no assets")
    expected_asset_keys = {
        "daily_by_year",
        "annual",
        "phenology",
        "coverage",
        "boundary",
        "plots",
        "release_manifest",
    }
    if set(assets) != expected_asset_keys:
        raise ValueError("historical product contains unexpected asset keys")
    if manifest.get("comparison") is not None or manifest.get("validation_url") is not None:
        raise ValueError("historical product contains an unexpected top-level asset reference")
    daily = assets.get("daily_by_year")
    if not isinstance(daily, dict) or set(daily) != {str(year) for year in expected_years}:
        raise ValueError("historical daily assets do not match its year coverage")
    release_prefix = f"archive/historical/{release_id}/"
    asset_paths = list(_asset_paths(assets))
    if not asset_paths or any(not path.startswith(release_prefix) for path in asset_paths):
        raise ValueError("historical assets must stay inside the immutable release")
    expected_manifest = f"{release_prefix}manifest.json"
    if assets.get("release_manifest") != expected_manifest:
        raise ValueError("historical release manifest path is invalid")
    archive_manifest_path = _resolve_public_asset(source_dir, expected_manifest)
    archive_manifest = json.loads(archive_manifest_path.read_text(encoding="utf-8"))
    if archive_manifest != manifest:
        raise ValueError("historical archive and latest manifests differ")


def _validate_selected_model_manifest(manifest: dict[str, object]) -> None:
    if manifest.get("schema_version") != MODEL_SCHEMA_VERSION:
        raise ValueError("gam-era5 manifest has an unsupported schema")
    if manifest.get("selection_id") != SELECTED_MODEL_ID:
        raise ValueError(f"gam-era5 manifest is not the selected model {SELECTED_MODEL_ID}")
    if manifest.get("model_family") != "gamm":
        raise ValueError("gam-era5 manifest must use the reviewed GAMM family")
    if manifest.get("pulses") != ["lp", "sp"]:
        raise ValueError("gam-era5 manifest must declare exact LP and SP pulses")
    provenance = manifest.get("component_provenance")
    if not isinstance(provenance, dict):
        raise ValueError("gam-era5 manifest has no selected component provenance")
    manifest_hash = provenance.get("component_manifest_sha256")
    if manifest_hash != COMPONENT_MANIFEST_SHA256:
        raise ValueError("gam-era5 component manifest hash is not the reviewed authority")
    components = provenance.get("components")
    if not isinstance(components, dict) or set(components) != MODEL_PULSES:
        raise ValueError("gam-era5 component provenance must contain LP and SP")
    for pulse, targets in components.items():
        if not isinstance(targets, dict) or set(targets) != MODEL_TARGETS:
            raise ValueError(f"gam-era5 component provenance is incomplete for {pulse}")
        for target, component in targets.items():
            if not isinstance(component, dict):
                raise ValueError(f"gam-era5 component provenance is invalid for {pulse}/{target}")
            digest = component.get("sha256")
            if digest != COMPONENT_SHA256[pulse][target]:
                raise ValueError(f"gam-era5 component hash is not reviewed for {pulse}/{target}")
    expected_dates = qualified_dates()
    archive_prefix = manifest.get("archive_prefix")
    if not isinstance(archive_prefix, str):
        raise ValueError("gam-era5 manifest has no archive prefix")
    archive_path = PurePosixPath(archive_prefix)
    if (
        archive_path.is_absolute()
        or ".." in archive_path.parts
        or archive_path.parts[:3] != ("archive", "reanalysis", "gam-era5")
        or len(archive_path.parts) != 4
    ):
        raise ValueError("gam-era5 archive prefix is invalid")
    assets = manifest.get("assets")
    if not isinstance(assets, dict):
        raise ValueError("gam-era5 manifest has no assets")
    if set(assets) != {"lp", "sp", "boundary"}:
        raise ValueError("gam-era5 manifest contains unexpected asset keys")
    if manifest.get("validation_url") is not None:
        raise ValueError("gam-era5 manifest contains an unexpected validation URL")
    if not isinstance(assets.get("boundary"), str) or not assets["boundary"]:
        raise ValueError("gam-era5 manifest has no boundary asset")
    for pulse in MODEL_PULSES:
        daily = assets.get(pulse)
        if not isinstance(daily, dict) or sorted(daily) != expected_dates:
            raise ValueError(
                f"gam-era5 {pulse} assets must contain the exact {QUALIFIED_DAY_COUNT}-day window"
            )
        for day in expected_dates:
            expected_asset = f"{archive_prefix}/daily/{pulse}/{day.replace('-', '')}.json"
            if daily[day] != expected_asset:
                raise ValueError(f"gam-era5 {pulse} asset path is invalid for {day}")
    if assets.get("boundary") != f"{archive_prefix}/uk-boundary.geojson":
        raise ValueError("gam-era5 boundary asset path is invalid")
    if manifest.get("comparison") != f"{archive_prefix}/validation.json":
        raise ValueError("gam-era5 validation asset path is invalid")
    if manifest.get("source") != f"{archive_prefix}/source.json":
        raise ValueError("gam-era5 source asset path is invalid")
    expected_first = f"{QUALIFIED_FIRST_DAY.isoformat()}T00:00:00Z"
    expected_latest = f"{QUALIFIED_LAST_DAY.isoformat()}T23:00:00Z"
    if manifest.get("first_time_utc") != expected_first:
        raise ValueError(f"gam-era5 first time must be {expected_first}")
    if manifest.get("latest_time_utc") != expected_latest:
        raise ValueError(f"gam-era5 latest time must be {expected_latest}")


def _resolve_public_asset(source_dir: Path, asset: str) -> Path:
    """Resolve one manifest asset without links or paths outside the root."""

    relative = PurePosixPath(asset)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"public asset must be a safe relative path: {asset}")
    root = source_dir.resolve()
    candidate = root.joinpath(*relative.parts)
    try:
        candidate.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError(f"public asset escapes source directory: {asset}") from exc
    current = candidate
    while current != root:
        if current.is_symlink():
            raise ValueError(f"public asset may not use symlinks: {asset}")
        current = current.parent
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate.resolve()


def _normalise_object_prefix(value: str) -> str:
    prefix = PurePosixPath(value.strip("/"))
    if not prefix.parts or prefix.is_absolute() or ".." in prefix.parts:
        raise ValueError(f"invalid object prefix: {value}")
    return prefix.as_posix()


def sync_command(
    plan_path: Path,
    *,
    bucket: str,
    endpoint_url: str,
    profile: str | None = None,
    client: str = "aws",
    s3cmd_config: str | None = None,
) -> list[list[str]]:
    payload = validate_publication_plan(plan_path)
    commands = []
    if client == "s3cmd":
        base = ["s3cmd"]
        if s3cmd_config:
            base.extend(["-c", s3cmd_config])
        for obj in payload.get("objects", []):
            commands.append(
                [
                    *base,
                    "put",
                    str(obj["source"]),
                    f"s3://{bucket}/{obj['key']}",
                    "--acl-public",
                    f"--mime-type={obj['content_type']}",
                ]
            )
        return commands
    if client != "aws":
        raise ValueError(f"Unsupported object-store client: {client}")

    aws_base = ["aws"]
    if profile:
        aws_base.extend(["--profile", profile])
    if endpoint_url:
        aws_base.extend(["--endpoint-url", endpoint_url])
    for obj in payload.get("objects", []):
        commands.append(
            [
                *aws_base,
                "s3",
                "cp",
                str(obj["source"]),
                f"s3://{bucket}/{obj['key']}",
                "--content-type",
                str(obj["content_type"]),
                "--acl",
                "public-read",
                "--only-show-errors",
            ]
        )
    return commands


def write_sync_commands(
    plan_path: Path,
    output: Path,
    *,
    bucket: str,
    endpoint_url: str,
    profile: str | None = None,
    client: str = "aws",
    s3cmd_config: str | None = None,
    phase: str = "all",
) -> None:
    if phase not in {"all", "assets", "manifests"}:
        raise ValueError("publication phase must be all, assets, or manifests")
    payload = validate_publication_plan(plan_path)
    commands = sync_command(
        plan_path,
        bucket=bucket,
        endpoint_url=endpoint_url,
        profile=profile,
        client=client,
        s3cmd_config=s3cmd_config,
    )
    planned = list(zip(payload["objects"], commands, strict=True))
    latest = [item for item in planned if _is_latest_manifest(str(item[0]["key"]))]
    immutable = [item for item in planned if item not in latest]
    if phase == "assets":
        latest = []
    elif phase == "manifests":
        immutable = []
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "#!/bin/sh",
        "set -eu",
        "verify_tmp=''",
        'cleanup() { [ -z "$verify_tmp" ] || rm -f "$verify_tmp"; }',
        "trap cleanup EXIT HUP INT TERM",
        "# Verify the immutable plan before uploading any object.",
    ]
    for obj, _ in planned:
        source = _shell_quote(str(obj["source"]))
        expected = _shell_quote(str(obj["sha256"]))
        key = _shell_quote(str(obj["key"]))
        lines.append(
            f"actual=$(sha256sum {source} | awk '{{print $1}}'); "
            f"[ \"$actual\" = {expected} ] || {{ echo 'hash mismatch for ' {key} >&2; exit 1; }}"
        )
    lines.append("# Immutable assets first; latest manifests are the atomic promotion step.")
    for obj, command in immutable:
        exists_command = _object_exists_command(
            key=str(obj["key"]),
            bucket=bucket,
            endpoint_url=endpoint_url,
            profile=profile,
            client=client,
            s3cmd_config=s3cmd_config,
        )
        get_command = _object_get_command(
            key=str(obj["key"]),
            bucket=bucket,
            endpoint_url=endpoint_url,
            profile=profile,
            client=client,
            s3cmd_config=s3cmd_config,
        )
        key = _shell_quote(str(obj["key"]))
        expected = _shell_quote(str(obj["sha256"]))
        lines.append(
            f"if {' '.join(_shell_quote(part) for part in exists_command)} >/dev/null 2>&1; then"
        )
        lines.append('  verify_tmp=$(mktemp "${TMPDIR:-/tmp}/birdcast-publish.XXXXXX")')
        lines.append(
            "  "
            + " ".join(
                _shell_quote("$verify_tmp") if part == "{temporary}" else _shell_quote(part)
                for part in get_command
            ).replace("'$verify_tmp'", '"$verify_tmp"')
        )
        lines.append("  remote_actual=$(sha256sum \"$verify_tmp\" | awk '{print $1}')")
        lines.append("  rm -f \"$verify_tmp\"; verify_tmp=''")
        lines.append(
            f'  [ "$remote_actual" = {expected} ] || '
            f"{{ echo 'existing immutable object differs: ' {key} >&2; exit 1; }}"
        )
        lines.append(f"  echo 'immutable object already matches: ' {key}")
        lines.append("else")
        lines.append("  " + " ".join(_shell_quote(part) for part in command))
        lines.append("fi")
    lines.extend(" ".join(_shell_quote(part) for part in command) for _, command in latest)
    with NamedTemporaryFile(
        "w",
        dir=output.parent,
        prefix=f".{output.name}.",
        encoding="utf-8",
        delete=False,
    ) as handle:
        handle.write("\n".join(lines) + "\n")
        temporary = Path(handle.name)
    try:
        temporary.chmod(0o750)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def validate_publication_plan(plan_path: Path) -> dict[str, object]:
    """Validate plan structure and confirm every local object still matches it."""

    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("objects"), list):
        raise ValueError("publication plan must contain an objects list")
    objects = payload["objects"]
    if payload.get("object_count") != len(objects):
        raise ValueError("publication plan object_count does not match objects")
    source_dir = Path(str(payload.get("source_dir") or ""))
    if not source_dir.is_absolute() or not source_dir.is_dir():
        raise ValueError("publication plan source_dir must be an existing absolute directory")
    products = payload.get("products")
    if (
        not isinstance(products, list)
        or not products
        or not all(isinstance(product, str) and product for product in products)
    ):
        raise ValueError("publication plan must contain non-empty products")
    if len(set(products)) != len(products):
        raise ValueError("publication plan products must be unique")
    prefix = _normalise_object_prefix(str(payload.get("object_prefix") or ""))
    validate_release(source_dir, required_products=tuple(products))
    expected = {
        f"{prefix}/{path.relative_to(source_dir.resolve()).as_posix()}": path
        for path in _product_paths(source_dir, tuple(products))
    }
    seen_keys: set[str] = set()
    for obj in objects:
        if not isinstance(obj, dict):
            raise ValueError("publication plan objects must be JSON objects")
        source = Path(str(obj.get("source") or ""))
        key = str(obj.get("key") or "")
        expected_source = expected.get(key)
        if expected_source is None or source.resolve() != expected_source:
            raise ValueError(f"publication plan contains an unreferenced object: {key}")
        relative = source.resolve().relative_to(source_dir.resolve()).as_posix()
        verified_source = _resolve_public_asset(source_dir, relative)
        if verified_source != source.resolve():
            raise ValueError(f"planned source does not match its release asset: {source}")
        if key in seen_keys or not key:
            raise ValueError(f"duplicate or empty publication key: {key}")
        seen_keys.add(key)
        if source.stat().st_size != int(obj.get("size", -1)):
            raise ValueError(f"planned source size changed: {source}")
        if _sha256(source) != obj.get("sha256"):
            raise ValueError(f"planned source hash changed: {source}")
        expected_content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        if obj.get("content_type") != expected_content_type:
            raise ValueError(f"planned source content type changed: {source}")
    if seen_keys != set(expected):
        missing = sorted(set(expected) - seen_keys)
        raise ValueError(f"publication plan omits referenced objects: {', '.join(missing[:5])}")
    return payload


def _object_exists_command(
    *,
    key: str,
    bucket: str,
    endpoint_url: str,
    profile: str | None,
    client: str,
    s3cmd_config: str | None,
) -> list[str]:
    if client == "s3cmd":
        command = ["s3cmd"]
        if s3cmd_config:
            command.extend(["-c", s3cmd_config])
        return [*command, "info", f"s3://{bucket}/{key}"]
    command = ["aws"]
    if profile:
        command.extend(["--profile", profile])
    if endpoint_url:
        command.extend(["--endpoint-url", endpoint_url])
    return [
        *command,
        "s3api",
        "head-object",
        "--bucket",
        bucket,
        "--key",
        key,
    ]


def _object_get_command(
    *,
    key: str,
    bucket: str,
    endpoint_url: str,
    profile: str | None,
    client: str,
    s3cmd_config: str | None,
) -> list[str]:
    uri = f"s3://{bucket}/{key}"
    if client == "s3cmd":
        command = ["s3cmd"]
        if s3cmd_config:
            command.extend(["-c", s3cmd_config])
        return [*command, "get", "--force", uri, "{temporary}"]
    command = ["aws"]
    if profile:
        command.extend(["--profile", profile])
    if endpoint_url:
        command.extend(["--endpoint-url", endpoint_url])
    return [*command, "s3", "cp", uri, "{temporary}", "--only-show-errors"]


def _is_latest_manifest(key: str) -> bool:
    path = PurePosixPath(key)
    return len(path.parts) >= 3 and path.parts[-2] == "latest" and path.suffix == ".json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _shell_quote(value: str) -> str:
    if value and all(char.isalnum() or char in "-_./:=@" for char in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"
