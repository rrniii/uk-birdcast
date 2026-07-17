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


def sync_command(plan_path: Path, *, bucket: str, endpoint_url: str, profile: str | None = None) -> list[list[str]]:
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    commands = []
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


def write_sync_commands(plan_path: Path, output: Path, *, bucket: str, endpoint_url: str, profile: str | None = None) -> None:
    commands = sync_command(plan_path, bucket=bucket, endpoint_url=endpoint_url, profile=profile)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#!/bin/sh", "set -eu"]
    for command in commands:
        lines.append(" ".join(_shell_quote(part) for part in command))
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
