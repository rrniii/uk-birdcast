#!/bin/sh
# Pull a promoted Europe release and switch the Nginx source only after its
# manifest and fixed grid are present. The S3 manifest is uploaded last.
set -eu

: "${BIRDCAST_EURO_OBJECT_STORE_BUCKET:?}"
: "${BIRDCAST_EURO_OBJECT_STORE_ENDPOINT:?}"
: "${BIRDCAST_EURO_AWS_PROFILE:?}"
: "${BIRDCAST_EURO_OBJECT_PREFIX:?}"
: "${BIRDCAST_EURO_ARTIFACT_ROOT:?}"
: "${BIRDCAST_EURO_STAGE_ROOT:?}"

mkdir -p "$BIRDCAST_EURO_STAGE_ROOT"
stage="$(mktemp -d "$BIRDCAST_EURO_STAGE_ROOT/pull.XXXXXX")"
source="s3://${BIRDCAST_EURO_OBJECT_STORE_BUCKET}/${BIRDCAST_EURO_OBJECT_PREFIX}"

aws --profile "$BIRDCAST_EURO_AWS_PROFILE" --endpoint-url "$BIRDCAST_EURO_OBJECT_STORE_ENDPOINT" s3 sync \
  "$source" "$stage" --only-show-errors --delete

manifest="$stage/latest/reanalysis.json"
if [ ! -f "$manifest" ]; then
  exit 0
fi

grid="$(python3 - "$manifest" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
if manifest.get("release_status") != "published" or manifest.get("data_available") is not True:
    raise SystemExit(2)
asset = manifest.get("assets", {}).get("grid")
if not isinstance(asset, str) or not asset:
    raise SystemExit(3)
print(asset.lstrip("/"))
PY
)"
test -f "$stage/$grid"

release="$BIRDCAST_EURO_STAGE_ROOT/$(date -u +%Y%m%dT%H%M%SZ)"
mv "$stage" "$release"
ln -s "$release" "$BIRDCAST_EURO_ARTIFACT_ROOT.next"
mv -Tf "$BIRDCAST_EURO_ARTIFACT_ROOT.next" "$BIRDCAST_EURO_ARTIFACT_ROOT"
