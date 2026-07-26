#!/bin/sh
# Atomically expose a verified relative coastal activity/flow release.
set -eu

source_root=${1:?source artifact tree is required}
stage_root=${2:?release stage root is required}
current_link=${3:?current artifact symlink is required}
manifest="$source_root/latest/relative-flow.json"

test -f "$manifest"
release_status="$(python3 - "$manifest" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
if manifest.get("data_available") is not True:
    raise SystemExit(2)
if manifest.get("release_status") != "published-relative-research-product":
    raise SystemExit(3)
template = manifest.get("assets", {}).get("daily_template")
if not isinstance(template, str) or "{date}" not in template:
    raise SystemExit(4)
print(manifest["release_status"])
PY
)"
test "$release_status" = "published-relative-research-product"

manifest_sha="$(sha256sum "$manifest" | awk '{print $1}')"
release="$stage_root/relative-${manifest_sha%${manifest_sha#????????????}}"
test ! -e "$release"
mkdir -p "$release"
rsync -a --delete "$source_root/" "$release/"
ln -s "$release" "$current_link.next"
mv -Tf "$current_link.next" "$current_link"
