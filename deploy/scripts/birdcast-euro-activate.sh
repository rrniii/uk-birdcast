#!/bin/sh
# Activate a fully transferred Europe artifact tree. Called remotely by the
# JASMIN publication job after source, ERA5, training and GAMM checks pass.
set -eu

source_root=${1:?source artifact tree is required}
stage_root=${2:?release stage root is required}
current_link=${3:?current artifact symlink is required}
manifest="$source_root/latest/reanalysis.json"

test -f "$manifest"
model_id="$(python3 - "$manifest" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
if manifest.get("release_status") != "published" or manifest.get("data_available") is not True:
    raise SystemExit(2)
grid = manifest.get("assets", {}).get("grid")
if not isinstance(grid, str) or not grid:
    raise SystemExit(3)
model_id = str(manifest.get("model_id") or "europe-reanalysis")
if not model_id.replace("-", "").replace("_", "").replace(".", "").isalnum():
    raise SystemExit(4)
print(model_id)
PY
)"
grid="$(python3 - "$manifest" <<'PY'
import json
import sys
print(str(json.load(open(sys.argv[1], encoding="utf-8"))["assets"]["grid"]).lstrip("/"))
PY
)"
test -f "$source_root/$grid"

release="$stage_root/$model_id"
mkdir -p "$release"
rsync -a --delete "$source_root/" "$release/"
ln -s "$release" "$current_link.next"
mv -Tf "$current_link.next" "$current_link"
