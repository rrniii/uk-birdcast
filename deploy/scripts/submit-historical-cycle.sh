#!/bin/bash
# Lightweight cron-01 entrypoint. All analysis and transfers execute on LOTUS.
set -euo pipefail
test "$#" -eq 1 || { echo "Usage: $0 /private/historical-cycle.env" >&2; exit 2; }
set -a
. "$1"
set +a
: "${BIRDCAST_UK_ROOT:?Set the immutable code release}"
: "${BIRDCAST_UK_RELEASE_SHA:?Set the reviewed release SHA}"
: "${BIRDCAST_UK_CYCLE_STATE:?Set the private cycle state directory}"
test "$(git -C "$BIRDCAST_UK_ROOT" rev-parse HEAD)" = "$BIRDCAST_UK_RELEASE_SHA"
test -z "$(git -C "$BIRDCAST_UK_ROOT" status --porcelain)"
umask 077
mkdir -p "$BIRDCAST_UK_CYCLE_STATE/logs"
exec 9>"$BIRDCAST_UK_CYCLE_STATE/submission.lock"
flock -n 9 || exit 0
active_jobs=$(/usr/bin/squeue --noheader --user "$(id -un)" \
  --name birdcast-uk-historical-cycle --format '%A')
if [ -n "$active_jobs" ]; then
  printf 'Historical cycle already pending/running: %s\n' "$active_jobs"
  exit 0
fi
job_id=$(/usr/bin/sbatch --parsable --dependency=singleton --export=ALL \
  --chdir="$BIRDCAST_UK_CYCLE_STATE" \
  --output="$BIRDCAST_UK_CYCLE_STATE/logs/cycle-%j.out" \
  --error="$BIRDCAST_UK_CYCLE_STATE/logs/cycle-%j.err" \
  "$BIRDCAST_UK_ROOT/deploy/slurm/birdcast-uk-historical-cycle.sbatch")
printf '%s\n' "$job_id" > "$BIRDCAST_UK_CYCLE_STATE/submitted-job-id"
printf 'Submitted historical cycle %s from %s\n' "$job_id" "$BIRDCAST_UK_RELEASE_SHA"
