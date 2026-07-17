"""Small AWS CLI wrappers for JASMIN Object Store workflows."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import subprocess

from .config import DEFAULT_AWS_PROFILE, DEFAULT_BUCKET, DEFAULT_INTERNAL_ENDPOINT, DEFAULT_PUBLIC_BASE_URL


@dataclass(frozen=True)
class ObjectRecord:
    key: str
    size: int
    modified_time: str
    s3_uri: str
    public_url: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def aws_base(*, profile: str = DEFAULT_AWS_PROFILE, endpoint_url: str = DEFAULT_INTERNAL_ENDPOINT) -> list[str]:
    command = ["aws"]
    if profile:
        command.extend(["--profile", profile])
    if endpoint_url:
        command.extend(["--endpoint-url", endpoint_url])
    return command


def list_objects(
    *,
    bucket: str = DEFAULT_BUCKET,
    prefix: str,
    profile: str = DEFAULT_AWS_PROFILE,
    endpoint_url: str = DEFAULT_INTERNAL_ENDPOINT,
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
) -> list[ObjectRecord]:
    command = [*aws_base(profile=profile, endpoint_url=endpoint_url), "s3", "ls", f"s3://{bucket}/{prefix.strip('/')}/", "--recursive"]
    try:
        output = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as exc:
        text = exc.output or ""
        if "NoSuchKey" in text or "NoSuchBucket" in text or "does not exist" in text:
            return []
        if not text.strip():
            return []
        raise
    records: list[ObjectRecord] = []
    for line in output.splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) != 4 or parts[2].upper() == "PRE":
            continue
        date_part, time_part, size_part, key = parts
        records.append(
            ObjectRecord(
                key=key,
                size=int(size_part),
                modified_time=f"{date_part}T{time_part}Z",
                s3_uri=f"s3://{bucket}/{key}",
                public_url=f"{public_base_url.rstrip('/')}/{key}",
            )
        )
    return records


def copy_object(
    *,
    s3_uri: str,
    output: Path,
    profile: str = DEFAULT_AWS_PROFILE,
    endpoint_url: str = DEFAULT_INTERNAL_ENDPOINT,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([*aws_base(profile=profile, endpoint_url=endpoint_url), "s3", "cp", s3_uri, str(output), "--only-show-errors"], check=True)
    return output


def utc_stamp() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
