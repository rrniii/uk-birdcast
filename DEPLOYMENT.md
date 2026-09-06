# Live UK Bird Maps deployment

## End-to-end topology

```text
read-only VPTS + ERA5 inputs
          |
          v
JASMIN batch/GWS: private inventories, models and predictions
          |
          v
validated public staging: historical + gam-era5 only
          |
          v
JASMIN Object Store: immutable assets, then latest manifests
          |
          v
JASMIN Cloud/Nginx: static browser only
```

The cloud host must not retrieve ERA5/ECMWF data, build observations, fit
models, or publish Object Store data. A successful web response proves only
that the static host is reachable; it does not prove source freshness, model
coverage, or release completion.

## Release authority

- `main` is the only release branch.
- CI, review, and a clean commit are required before deployment.
- Record the exact `main` commit SHA and use a detached checkout or immutable
  worktree for JASMIN and cloud deployment.
- Never deploy an uncommitted tree or a mutable research branch.
- Production model authority is selection
  `uk-gamm-heldout-v2-sp-vector-925`, manifest SHA-256
  `fabfeceba85ed637b8eddd7909199e22913b251a901f5f452c8011a35e9ac3ea`,
  and the eight exact component hashes in `birdcast_uk.selected_model`.
- `submit-historical-reanalysis.sh` and the family-level finalizer are research
  benchmark paths only. They cannot promote production.

A typical host layout keeps releases immutable and switches only reviewed
symlinks:

```text
/opt/birdcast-uk/source/                 shared Git checkout
/opt/birdcast-uk/releases/<commit>/      detached release worktree
/opt/birdcast-uk/repo -> releases/<commit>
/opt/birdcast-uk/venv -> releases/<commit>/.venv
/opt/birdcast-uk/data/                   persistent public-data cache
/opt/birdcast-uk/site/                   generated static shell
```

Create a release from the reviewed remote `main` commit:

```bash
git -C /opt/birdcast-uk/source fetch --prune origin main
release_sha="$(git -C /opt/birdcast-uk/source rev-parse origin/main)"
release_dir="/opt/birdcast-uk/releases/$release_sha"
sudo -u birdcast git -C /opt/birdcast-uk/source worktree add --detach \
  "$release_dir" "$release_sha"
sudo -u birdcast python3 -m venv "$release_dir/.venv"
sudo -u birdcast "$release_dir/.venv/bin/python" -m pip install \
  --disable-pip-version-check --upgrade "pip>=26.1.2"
sudo -u birdcast "$release_dir/.venv/bin/pip" install \
  --disable-pip-version-check "$release_dir[birdcast]"
git -C "$release_dir" diff --quiet
test -z "$(git -C "$release_dir" status --porcelain)"
```

Stop the web refresh timer while switching the two compatibility symlinks,
then restart and verify the recorded SHA. Do not edit a release directory in
place. Roll forward with a new SHA or roll back to an already retained SHA.

## Selected component publication

Copy `deploy/env/birdcast-uk.jasmin.env.example` to a private location and set
all paths there. Model files, daily predictions, merged CSVs, logs, credentials,
and publication plans must remain outside the public artifact root.

Before submission, verify:

```bash
export PYTHONPATH="$BIRDCAST_UK_ROOT/src"
"$BIRDCAST_UK_PYTHON" -c \
  'from pathlib import Path; from birdcast_uk.selected_model import validate_component_manifest; import os; validate_component_manifest(Path(os.environ["BIRDCAST_UK_COMPONENT_MANIFEST"]), verify_model_files=True)'
test -d "$BIRDCAST_UK_ERA5_GRID_DIR"
test -d "$BIRDCAST_UK_COMPONENT_PREDICTION_DIR"
test -d "$BIRDCAST_UK_COMPONENT_MERGED_DIR"
```

Declare the exact inclusive prediction range. The qualified selected-model
release is the independently validated 365-day evidence window:

```bash
export BIRDCAST_UK_EXPECTED_START_DAY=2025-07-14
export BIRDCAST_UK_EXPECTED_END_DAY=2026-07-13
export BIRDCAST_UK_EXPECTED_DAYS=365
```

The observation catch-up through 15 August 2026 is a separate historical
publication milestone. Do not extend the modelled reanalysis to that date
without first building the additional ERA5 grids and component predictions,
repeating the scientific validation, and declaring a new release contract.
Never derive coverage from the files that happen to be present.

Submit the production dependency chain:

```bash
cd "$BIRDCAST_UK_ROOT"
bash deploy/slurm/submit-selected-reanalysis.sh
```

The chain performs one daily component-prediction array, exact merge and
coverage reconciliation, component publication, and product-scoped Object
Store publication. Record every returned Slurm job ID. Completion requires
successful terminal status for the final publication job, not merely successful
submission or completion of an upstream array.

Daily acceptance requires:

- every selected-model ERA5 predictor, including 925 hPa `u`/`v`, in every
  daily grid header before any array task is submitted;
- every expected calendar date exactly once;
- exactly `00:00` through `23:00Z` for every date;
- one row per fixed-grid cell and hour, with finite required targets;
- identical LP/SP dates, timestamps, and coordinate sets;
- verified component model hashes and the expected selection ID.

Every array day records the reviewed manifest digest. The merge rejects a
missing or different sidecar, and the publication job revalidates the manifest
and all eight model files. Each Slurm stage also verifies the full Git SHA,
clean worktree, release-local venv, and imported package path.

## Product-scoped Object Store publication

The public scope is an explicit allowlist: `latest/historical.json`,
`latest/gam-era5.json`, and only the local assets referenced by those two
manifests. Run directories, logs, models, raw data, prediction CSVs, credentials,
and unrelated files must never appear in a plan.

The batch publication job runs the equivalent of:

```bash
birdcast-uk publish validate \
  --source-dir "$BIRDCAST_UK_ARTIFACT_ROOT" \
  --require historical --require gam-era5

birdcast-uk publish plan \
  --source-dir "$BIRDCAST_UK_ARTIFACT_ROOT" \
  --output "$BIRDCAST_UK_OBJECT_STORE_PLAN" \
  --object-prefix "$BIRDCAST_UK_OBJECT_PREFIX" \
  --product historical --product gam-era5

birdcast-uk publish sync-script \
  --plan "$BIRDCAST_UK_OBJECT_STORE_PLAN" \
  --output "$BIRDCAST_UK_OBJECT_STORE_SYNC_SCRIPT" \
  --bucket "$BIRDCAST_UK_OBJECT_STORE_BUCKET" \
  --client s3cmd --s3cmd-config "$BIRDCAST_UK_S3CMD_CONFIG"
```

The plan rejects escaping paths and symlinks and records size and SHA-256 for
every object. The generated script rechecks hashes immediately before upload,
uploads immutable assets first, and updates `latest/*.json` last as the atomic
promotion step. Keep both the plan and generated script outside the artifact
root.

## Forecast fail-closed policy

Forecast and ECMWF services must remain disabled and inactive. The additional
`ConditionPathExists=/etc/birdcast-uk/forecast-enabled` guard is defense in
depth; the sentinel must be absent in production.

```bash
sudo systemctl disable --now \
  birdcast-uk-ecmwf-archive.timer \
  birdcast-uk-forecast-build.timer
sudo rm -f /etc/birdcast-uk/forecast-enabled
systemctl is-enabled birdcast-uk-ecmwf-archive.timer
systemctl is-enabled birdcast-uk-forecast-build.timer
systemctl is-active birdcast-uk-ecmwf-archive.timer
systemctl is-active birdcast-uk-forecast-build.timer
```

Expected states are `disabled` and `inactive`. A forecast manifest must state
`data_available: false` and must not preserve stale validity times.

Forecasting may be reconsidered only after all of these are implemented and
accepted:

1. a documented VPTS availability/completeness latency contract;
2. independently accepted observation timestamp and per-radar age thresholds;
3. exclusion or explicit degradation of stale/missing radars, never zero-fill;
4. verified ECMWF cycle completeness, files, hashes, domain and issue time;
5. immutable forecast issues and an operational rollback path;
6. end-to-end tests showing that one fresh radar cannot make another stale
   radar eligible.

## Cloud web host

Install the environment and Nginx files from the selected release. The static
refresh service installs both the UK shell and the sole public Europe
relative-flow shell; it does not publish absolute Europe model output. Install
the read-only freshness service/timer too. Enable only these two cloud timers:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now birdcast-uk-static-site-refresh.timer
sudo systemctl enable --now birdcast-uk-freshness.timer
```

Disable all production timers on this host:

```bash
sudo systemctl disable --now \
  birdcast-uk-radars-refresh.timer \
  birdcast-uk-vpts-inventory.timer \
  birdcast-uk-observed-build.timer \
  birdcast-uk-era5-build-day.timer \
  birdcast-uk-era5-request-smoke.timer \
  birdcast-uk-feature-join.timer \
  birdcast-uk-object-store-plan.timer \
  birdcast-uk-object-store-sync.timer \
  birdcast-uk-ecmwf-archive.timer \
  birdcast-uk-forecast-build.timer
```

Retire the superseded absolute-Europe Object Store poller during this upgrade;
deleting its files from Git does not stop an already installed timer:

```bash
sudo systemctl disable --now birdcast-euro-object-store-pull.timer
sudo rm -f \
  /etc/systemd/system/birdcast-euro-object-store-pull.timer \
  /etc/systemd/system/birdcast-euro-object-store-pull.service \
  /etc/birdcast-euro/birdcast-euro.env
sudo systemctl daemon-reload
sudo systemctl reset-failed
test "$(systemctl is-active birdcast-euro-object-store-pull.timer)" = inactive
test "$(systemctl is-enabled birdcast-euro-object-store-pull.timer 2>/dev/null)" = not-found
```

Before cleaning its leaked staging directories, record
`readlink -f /opt/birdcast-euro/artifacts-current` and confirm that it is the
relative-flow release, not a `pull.*` directory. Delete only empty
`/opt/birdcast-euro/staged-artifacts/pull.*` directories; preserve the active
release and every `archive/relative-flow/` asset.

The canonical route is `/live-uk-bird-maps/`. The compatibility route
`/birdcast-uk/` redirects there, and `/birdcast-uk/data/` serves public data.
After installing `deploy/nginx/birdcast-uk.conf`, run `nginx -t` before reload.

## Release verification

### Recurring historical observations

The production historical updater is independent of the fixed model release.
Install `deploy/env/birdcast-uk.historical-cycle.env.example` as a private
`historical-cycle.env`, with the reviewed immutable SHA and release-local Python.
Do not modify the shared research environment or the radar source archive.

Use **cron-01.jasmin.ac.uk**, not a science VM or the cloud host. JASMIN requires
heavy work to run on LOTUS; the cron entry only submits a bounded batch job.
See [JASMIN cron guidance](https://help.jasmin.ac.uk/docs/workflow-management/using-cron/).
Preserve the existing crontab, and add this named block, substituting the actual
release entrypoint and private environment paths:

```cron
# BEGIN UK BIRD MAPS HISTORICAL PUBLICATION
35 */6 * * * crontamer -t 5m -l '/bin/bash /path/to/release/deploy/scripts/submit-historical-cycle.sh /private/historical-cycle.env'
# END UK BIRD MAPS HISTORICAL PUBLICATION
```

The submitter uses a lock, an exact-name pending/running job check and Slurm
`singleton`; the worker holds a separate filesystem lock throughout the cycle.
The first run builds a full private per-file statistics cache. Later runs parse
only new/changed CSVs, while reconciling the complete historical output. A size,
mtime or calculation-code change invalidates the corresponding cache entry.
Successful cache writes are restartable but **are not publication completion**.

The retrospective completeness policy is:

- Use all configured radars and both LP/SP products; never substitute one pulse
  for another or sum them together.
- Freeze the common catalogue UTC end and withhold the trailing incomplete
  local-solar observation day. In particular, UTC inputs through 1 September
  support complete local-solar days only through 31 August.
- Require the boundary UTC files and usable non-gap observations for every
  radar/pulse at the newest published day.
- Preserve earlier radar outages as missing observations. Record missing
  catch-up source days, and confirm HTTP 404 in the public archive; network
  failures or a local/public mismatch block publication. No zero-fill is used.
  Entirely missing/gap-only solar periods retain coverage counts but have null
  passage metrics; genuine measured zero densities remain valid zeros.
- Reject stale catalogues, changed-during-read files, source-count/start-date
  regressions, and concurrent changes to the public historical manifest.

Each run has a private directory with the catalogue snapshot, source inventory,
analysis, public staging tree, hashed publication plan and result. Only approved
historical assets and the disabled forecast tombstone are uploaded. Upload and
independently GET/hash **all immutable assets before promoting latest manifests**;
then GET/hash the complete plan again before advancing `published.json`.
No-op cycles leave the published manifests untouched. A failed run retains the
last publication/checkpoint and records `cycle-status.json`; inspect both that
file and Slurm terminal status, not the submission acknowledgement.

The cloud `birdcast-uk-freshness.timer` independently checks the public catalogue,
historical/model manifests and disabled forecast every hour. It writes
`/live-uk-bird-maps/freshness.json` and fails visibly in systemd on stale/unknown
data. The website displays its result, and treats a report older than three hours
as unverified. Operational thresholds are catalogue age <=72 hours, common radar
source age <=7 days, and publication lag <=2 days behind the complete source
window. These thresholds **do not accept or enable the forecast latency contract**.
The pinned July model window is reported, not incorrectly treated as a live feed.

After deployment, run the web refresh **twice** to test idempotence against
read-only release files, run freshness explicitly, trigger the exact cron
submitter, wait for the publication batch job to complete, and invoke it again to
verify a no-change cycle. Confirm that all unrelated cron entries are unchanged.

### Public release checks

Verify the actual public source, not a cached dashboard card:

```bash
curl --fail --silent --show-error \
  "$BIRDCAST_UK_PUBLIC_BASE_URL/birdcast-uk/latest/historical.json" \
  -o /tmp/birdcast-historical.json
curl --fail --silent --show-error \
  "$BIRDCAST_UK_PUBLIC_BASE_URL/birdcast-uk/latest/gam-era5.json" \
  -o /tmp/birdcast-reanalysis.json

jq -e '.data_available == true' /tmp/birdcast-historical.json
jq -e '.data_available == true' /tmp/birdcast-reanalysis.json
jq -e '.selection_id == "uk-gamm-heldout-v2-sp-vector-925"' \
  /tmp/birdcast-reanalysis.json
```

Also verify every referenced asset, first/latest timestamps, exact day count,
LP/SP coverage, deployed Git SHA, Nginx access/error logs, and an uncached
browser load. A release is incomplete until all checks pass.

## Rollback

Do not delete or overwrite immutable archive assets. To roll back data, rebuild
and verify a product-scoped plan whose `latest` manifests reference the prior
known-good immutable assets, then promote those manifests last. To roll back
code, switch the release symlinks to the prior recorded SHA, restart only the
web refresh service, and repeat the full verification above.
