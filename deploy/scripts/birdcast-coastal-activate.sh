#!/bin/sh
# Atomically expose a verified relative coastal activity/flow release.
set -eu

source_root=${1:?source artifact tree is required}
stage_root=${2:?release stage root is required}
current_link=${3:?current artifact symlink is required}

validate_release() {
    python3 - "$1" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath

root = Path(sys.argv[1]).resolve()
manifest_path = root / "latest" / "relative-flow.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("schema_version") != "birdcast-europe-relative-flow-1.1":
    raise SystemExit("unsupported relative-flow schema")
if manifest.get("data_available") is not True:
    raise SystemExit("relative-flow release is not data-bearing")
if manifest.get("release_status") != "published-relative-research-product":
    raise SystemExit("relative-flow release status is invalid")
release_id = manifest.get("release_id")
if not isinstance(release_id, str) or re.fullmatch(r"relative-[0-9a-f]{16}", release_id) is None:
    raise SystemExit("relative-flow release id is invalid")
dates = manifest.get("available_dates")
daily = manifest.get("assets", {}).get("integrity", {}).get("daily")
if not isinstance(dates, list) or not dates or dates != sorted(set(dates)):
    raise SystemExit("relative-flow dates are invalid")
if not isinstance(daily, dict) or set(daily) != set(dates):
    raise SystemExit("relative-flow integrity index is incomplete")
release_dir = root / "archive" / "relative-flow" / release_id
archive_manifest = release_dir / "manifest.json"
if archive_manifest.is_symlink() or not archive_manifest.is_file():
    raise SystemExit("relative-flow archive manifest is missing")
if archive_manifest.read_bytes() != manifest_path.read_bytes():
    raise SystemExit("relative-flow archive and latest manifests differ")
expected_release_manifest = f"archive/relative-flow/{release_id}/manifest.json"
expected_template = f"archive/relative-flow/{release_id}/days/{{date}}.json"
if manifest.get("assets", {}).get("release_manifest") != expected_release_manifest:
    raise SystemExit("relative-flow release manifest path is invalid")
if manifest.get("assets", {}).get("daily_template") != expected_template:
    raise SystemExit("relative-flow daily template is invalid")
expected_files = {PurePosixPath("manifest.json")}

for day in dates:
    record = daily[day]
    relative = PurePosixPath(str(record.get("path") or ""))
    expected = PurePosixPath("archive") / "relative-flow" / release_id / "days" / f"{day}.json"
    if relative != expected or relative.is_absolute() or ".." in relative.parts:
        raise SystemExit(f"unsafe relative-flow asset path: {relative}")
    path = root.joinpath(*relative.parts)
    current = path
    uses_symlink = False
    while current != root:
        uses_symlink = uses_symlink or current.is_symlink()
        current = current.parent
    if uses_symlink or not path.is_file() or root not in path.resolve().parents:
        raise SystemExit(f"missing relative-flow asset: {relative}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if path.stat().st_size != record.get("size_bytes") or digest != record.get("sha256"):
        raise SystemExit(f"relative-flow asset integrity failed: {relative}")
    expected_files.add(PurePosixPath("days") / f"{day}.json")
actual_files = set()
for path in release_dir.rglob("*"):
    if path.is_symlink():
        raise SystemExit(f"relative-flow release contains a symlink: {path}")
    if path.is_file():
        actual_files.add(PurePosixPath(path.relative_to(release_dir).as_posix()))
if actual_files != expected_files:
    raise SystemExit("relative-flow release contains unreferenced or missing files")
print(release_id)
PY
}

manifest="$source_root/latest/relative-flow.json"
test -f "$manifest"
release_id="$(validate_release "$source_root")"
release="$stage_root/$release_id"
mkdir -p "$stage_root"

if test -e "$release"; then
    test -d "$release"
    cmp "$manifest" "$release/latest/relative-flow.json"
    test "$(validate_release "$release")" = "$release_id"
else
    staging="$(mktemp -d "$stage_root/.${release_id}.XXXXXX")"
    cleanup() { rm -rf "$staging"; }
    trap cleanup EXIT HUP INT TERM
    mkdir -p "$staging/latest" "$staging/archive/relative-flow/$release_id"
    cp "$manifest" "$staging/latest/relative-flow.json"
    cp -R "$source_root/archive/relative-flow/$release_id/." \
        "$staging/archive/relative-flow/$release_id/"
    # Validate the staged copy before making its content-addressed name visible.
    test "$(validate_release "$staging")" = "$release_id"
    mv "$staging" "$release"
    trap - EXIT HUP INT TERM
fi

rm -f "$current_link.next"
ln -s "$release" "$current_link.next"
python3 - "$current_link.next" "$current_link" <<'PY'
import os
import sys

os.replace(sys.argv[1], sys.argv[2])
PY
