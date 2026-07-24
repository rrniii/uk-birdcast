#!/bin/bash
# Assemble and deny-test the Europe training input after source and ERA5 gates.
#SBATCH --job-name=birdcast-euro-assemble
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=24G
#SBATCH --output=logs/birdcast-euro-assemble-%j.out
#SBATCH --error=logs/birdcast-euro-assemble-%j.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u
: "${BIRDCAST_EURO_ROOT:?Set the immutable Europe release checkout}"
: "${BIRDCAST_EURO_PYTHON:?Set the branch Python executable}"
: "${BIRDCAST_EURO_SOURCE_FIDELITY:?Set the passed source fidelity summary}"
: "${BIRDCAST_EURO_HOURLY_ROOT:?Set the derived hourly root}"
: "${BIRDCAST_EURO_SITE_ERA5_ROOT:?Set the daily Europe ERA5 site-feature root}"
: "${BIRDCAST_EURO_SITE_ERA5_TABLE:?Set the merged site-feature Parquet path}"
: "${BIRDCAST_EURO_RADARS:?Set the Europe radar metadata}"
: "${BIRDCAST_EURO_UK_SP_TRAINING:?Set the immutable UK model-year training CSV}"
: "${BIRDCAST_EURO_COHORT:?Set the locked Aloft cohort JSON}"
: "${BIRDCAST_EURO_MODEL_SPEC:?Set the Europe GAMM spec}"
: "${BIRDCAST_EURO_TRAINING_CSV:?Set the Europe training CSV output}"
: "${BIRDCAST_EURO_TRANSFER_CSV:?Set the Europe transfer-validation CSV output}"
: "${BIRDCAST_EURO_TRAINING_FIDELITY:?Set the training policy report output}"
: "${BIRDCAST_EURO_TRAINING_CONTRACT:?Set the grid-support training contract output}"
: "${BIRDCAST_EURO_RUNTIME_MODEL_SPEC:?Set the immutable runtime GAMM spec output}"

test "$("$BIRDCAST_EURO_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["status"])' "$BIRDCAST_EURO_SOURCE_FIDELITY")" = passed
module load jaspy/3.12/v20250704
"$BIRDCAST_EURO_PYTHON" "$BIRDCAST_EURO_ROOT/scripts/merge_europe_era5_site_features.py" \
  --feature-root "$BIRDCAST_EURO_SITE_ERA5_ROOT" --output "$BIRDCAST_EURO_SITE_ERA5_TABLE" \
  --start-day 2025-07-14 --end-day 2026-07-13
"$BIRDCAST_EURO_PYTHON" "$BIRDCAST_EURO_ROOT/scripts/assemble_europe_training.py" \
  --aloft-parquet "$BIRDCAST_EURO_HOURLY_ROOT/**/*.parquet" \
  --uk-training-csv "$BIRDCAST_EURO_UK_SP_TRAINING" \
  --era5-parquet "$BIRDCAST_EURO_SITE_ERA5_TABLE" \
  --radar-metadata "$BIRDCAST_EURO_RADARS" \
  --validation-output "$BIRDCAST_EURO_TRANSFER_CSV" --output "$BIRDCAST_EURO_TRAINING_CSV"
"$BIRDCAST_EURO_PYTHON" -m birdcast_uk.cli europe verify-training \
  --training-csv "$BIRDCAST_EURO_TRAINING_CSV" --transfer-csv "$BIRDCAST_EURO_TRANSFER_CSV" \
  --cohort "$BIRDCAST_EURO_COHORT" --model-spec "$BIRDCAST_EURO_MODEL_SPEC" \
  --output "$BIRDCAST_EURO_TRAINING_FIDELITY"
"$BIRDCAST_EURO_PYTHON" "$BIRDCAST_EURO_ROOT/scripts/build_europe_training_contract.py" \
  --training-csv "$BIRDCAST_EURO_TRAINING_CSV" --model-spec "$BIRDCAST_EURO_MODEL_SPEC" \
  --output "$BIRDCAST_EURO_TRAINING_CONTRACT"
"$BIRDCAST_EURO_PYTHON" "$BIRDCAST_EURO_ROOT/scripts/prepare_europe_model_spec.py" \
  --model-spec "$BIRDCAST_EURO_MODEL_SPEC" --training-csv "$BIRDCAST_EURO_TRAINING_CSV" \
  --validation-csv "$BIRDCAST_EURO_TRANSFER_CSV" --training-fidelity "$BIRDCAST_EURO_TRAINING_FIDELITY" \
  --source-fidelity "$BIRDCAST_EURO_SOURCE_FIDELITY" --output "$BIRDCAST_EURO_RUNTIME_MODEL_SPEC"
