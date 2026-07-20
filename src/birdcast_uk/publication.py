"""Object-store publication planning for static BirdCast UK artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import mimetypes
from pathlib import Path

from .config import OBJECT_PREFIX
from .static_artifacts import utc_now, write_json


@dataclass(frozen=True)
class PublicationObject:
    source: str
    key: str
    size: int
    sha256: str
    content_type: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_publication_plan(source_dir: Path, output: Path, *, object_prefix: str = OBJECT_PREFIX) -> dict[str, object]:
    objects = []
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source_dir)
        key = "/".join([object_prefix.strip("/"), *relative.parts])
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
        "object_prefix": object_prefix,
        "source_dir": str(source_dir),
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

    checked_assets = 0
    products = {}
    for product in required_products:
        manifest_path = source_dir / "latest" / f"{product}.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"required release manifest is missing: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("data_available") is not True:
            raise ValueError(f"required product {product} is not data-bearing")
        missing = []
        for asset in _asset_paths(manifest.get("assets")):
            if asset.startswith(("http://", "https://")):
                continue
            if not (source_dir / asset).is_file():
                missing.append(asset)
        if missing:
            sample = ", ".join(missing[:5])
            raise FileNotFoundError(
                f"required product {product} references {len(missing)} missing assets: {sample}"
            )
        checked_assets += len(list(_asset_paths(manifest.get("assets"))))
        products[product] = {
            "manifest": str(manifest_path),
            "schema_version": manifest.get("schema_version"),
            "asset_count": len(list(_asset_paths(manifest.get("assets")))),
        }
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


def sync_command(
    plan_path: Path,
    *,
    bucket: str,
    endpoint_url: str,
    profile: str | None = None,
    client: str = "aws",
    s3cmd_config: str | None = None,
) -> list[list[str]]:
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
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
) -> None:
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    if client == "s3cmd":
        commands = sync_command(
            plan_path,
            bucket=bucket,
            endpoint_url=endpoint_url,
            client=client,
            s3cmd_config=s3cmd_config,
        )
        latest_manifests = [command for command in commands if "/latest/" in command[-3] and command[-3].endswith(".json")]
        other_objects = [command for command in commands if command not in latest_manifests]
        output.parent.mkdir(parents=True, exist_ok=True)
        lines = ["#!/bin/sh", "set -eu"]
        lines.extend(" ".join(_shell_quote(part) for part in command) for command in [*other_objects, *latest_manifests])
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        output.chmod(0o750)
        return
    if client != "aws":
        raise ValueError(f"Unsupported object-store client: {client}")

    source_dir = Path(str(payload["source_dir"]))
    object_prefix = str(payload["object_prefix"]).strip("/")
    aws_base = ["aws"]
    if profile:
        aws_base.extend(["--profile", profile])
    if endpoint_url:
        aws_base.extend(["--endpoint-url", endpoint_url])

    def sync(source: Path, target: str, *extra: str) -> list[str]:
        return [
            *aws_base,
            "s3",
            "sync",
            str(source),
            f"s3://{bucket}/{target}",
            "--acl",
            "public-read",
            "--only-show-errors",
            *extra,
        ]

    output.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#!/bin/sh", "set -eu"]
    archive_dir = source_dir / "archive"
    latest_dir = source_dir / "latest"
    lines.append(
        f'if [ -d {_shell_quote(str(archive_dir))} ]; then '
        + " ".join(_shell_quote(part) for part in sync(archive_dir, f"{object_prefix}/archive"))
        + "; fi"
    )
    lines.append(
        " ".join(
            _shell_quote(part)
            for part in sync(
                source_dir,
                object_prefix,
                "--exclude",
                "archive/*",
                "--exclude",
                "latest/*",
            )
        )
    )
    latest_target = f"s3://{bucket}/{object_prefix}/latest/"
    latest_without_manifests = sync(latest_dir, f"{object_prefix}/latest", "--exclude", "*.json")
    manifest_copy = [
        *aws_base,
        "s3",
        "cp",
        '"$manifest"',
        latest_target,
        "--content-type",
        "application/json",
        "--acl",
        "public-read",
        "--only-show-errors",
    ]
    latest_source = _shell_quote(str(latest_dir))
    lines.extend(
        [
            f"if [ -d {latest_source} ]; then",
            "  " + " ".join(_shell_quote(part) for part in latest_without_manifests),
            f"  find {latest_source} -maxdepth 1 -type f -name '*.json' -print | sort | while IFS= read -r manifest; do",
            "    " + " ".join(
                part if part == '"$manifest"' else _shell_quote(part)
                for part in manifest_copy
            ),
            "  done",
            "fi",
        ]
    )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    output.chmod(0o750)


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
