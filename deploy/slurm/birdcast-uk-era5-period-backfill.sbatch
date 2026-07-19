#!/bin/bash
#SBATCH --job-name=birdcast-uk-era5-period
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --array=0-12%2
#SBATCH --time=12:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=24G
#SBATCH --output=logs/birdcast-uk-era5-period-%A_%a.out
#SBATCH --error=logs/birdcast-uk-era5-period-%A_%a.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u

: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"
: "${BIRDCAST_UK_VPTS_ARCHIVE_INVENTORY:?Set the historical inventory path}"
: "${BIRDCAST_UK_ERA5_RAW_DIR:?Set the ERA5 raw directory}"
: "${BIRDCAST_UK_ERA5_FEATURE_DIR:?Set the ERA5 site-feature directory}"
: "${BIRDCAST_UK_RADARS_FILE:?Set the radar metadata path}"

module load jaspy/3.12/v20250704
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli era5 readiness
period="$("$BIRDCAST_UK_PYTHON" -c '
import calendar, datetime, json, sys
window = json.load(open(sys.argv[1]))["window"]
start = datetime.datetime.strptime(window["start_date"], "%Y%m%d").date()
end = datetime.datetime.strptime(window["end_date"], "%Y%m%d").date()
periods = []
year, month = start.year, start.month
while (year, month) <= (end.year, end.month):
    first = max(start, datetime.date(year, month, 1))
    last = min(end, datetime.date(year, month, calendar.monthrange(year, month)[1]))
    periods.append((first.isoformat(), last.isoformat()))
    month += 1
    if month == 13:
        year += 1
        month = 1
index = int(sys.argv[2])
if index < len(periods):
    print(*periods[index])
' "$BIRDCAST_UK_VPTS_ARCHIVE_INVENTORY" "${SLURM_ARRAY_TASK_ID}")"

# A rolling 365-day window spans at most 13 calendar months. Empty final array
# elements are expected when it spans only 12.
[ -n "$period" ] || exit 0
read -r start_day end_day <<<"$period"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli era5 build-period \
  --start-day "$start_day" \
  --end-day "$end_day" \
  --raw-dir "$BIRDCAST_UK_ERA5_RAW_DIR" \
  --feature-dir "$BIRDCAST_UK_ERA5_FEATURE_DIR" \
  --radars "$BIRDCAST_UK_RADARS_FILE" \
  --download
