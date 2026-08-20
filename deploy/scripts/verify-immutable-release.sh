#!/bin/sh
# Fail closed unless the job uses the exact clean release and its local venv.
set -eu

: "${BIRDCAST_UK_ROOT:?Set the immutable repository release}"
: "${BIRDCAST_UK_RELEASE_SHA:?Set the full reviewed main commit SHA}"
: "${BIRDCAST_UK_PYTHON:?Set the release-local Python executable}"

printf '%s\n' "$BIRDCAST_UK_RELEASE_SHA" | grep -Eq '^[0-9a-f]{40}$'
test "$(git -C "$BIRDCAST_UK_ROOT" rev-parse HEAD)" = "$BIRDCAST_UK_RELEASE_SHA"
test -z "$(git -C "$BIRDCAST_UK_ROOT" status --porcelain)"

release_root="$(cd "$BIRDCAST_UK_ROOT" && pwd -P)"
python_dir="$(cd "$(dirname "$BIRDCAST_UK_PYTHON")" && pwd -P)"
python_path="$python_dir/$(basename "$BIRDCAST_UK_PYTHON")"
case "$python_path" in
    "$release_root"/.venv/bin/python*) ;;
    *) echo "Python is not from the immutable release venv: $python_path" >&2; exit 1 ;;
esac
venv_prefix="$($BIRDCAST_UK_PYTHON -c 'import sys; print(sys.prefix)')"
test "$(cd "$venv_prefix" && pwd -P)" = "$(cd "$release_root/.venv" && pwd -P)" || {
    echo "Python sys.prefix is not the immutable release venv: $venv_prefix" >&2
    exit 1
}

module_path="$(PYTHONPATH="$release_root/src" "$BIRDCAST_UK_PYTHON" -c \
    'from pathlib import Path; import birdcast_uk; print(Path(birdcast_uk.__file__).resolve())')"
case "$module_path" in
    "$release_root"/src/birdcast_uk/*) ;;
    *) echo "birdcast_uk imports outside the immutable release: $module_path" >&2; exit 1 ;;
esac
