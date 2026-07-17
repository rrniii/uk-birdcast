# UK BirdCast Static Deployment

Target host:

```text
uk-birdcast-workstation-ssh
130.246.212.190
```

The MVP serves a static site from the JASMIN Cloud host and reads public data artifacts from the JASMIN Object Store. Heavy processing and historical backfills should run on JASMIN batch/GWS.

## Install

```bash
ssh -J login azimuth@130.246.212.190
sudo useradd --system --home /opt/birdcast-uk --shell /usr/sbin/nologin birdcast
sudo mkdir -p /opt/birdcast-uk/{repo,venv,data,site,logs} /etc/birdcast-uk
sudo chown -R birdcast:birdcast /opt/birdcast-uk
```

Clone or copy this repository into `/opt/birdcast-uk/repo`, then install:

```bash
sudo -u birdcast python3 -m venv /opt/birdcast-uk/venv
sudo -u birdcast /opt/birdcast-uk/venv/bin/pip install -e "/opt/birdcast-uk/repo[birdcast]"
sudo install -m 0640 -o root -g birdcast deploy/env/birdcast-uk.env.example /etc/birdcast-uk/birdcast-uk.env
sudo install -m 0640 -o root -g birdcast configs/birdcast_uk_object_store.example.toml /etc/birdcast-uk/object_store.toml
```

Edit `/etc/birdcast-uk/birdcast-uk.env` before enabling Object Store sync.

## Build Static Artifacts

```bash
sudo -u birdcast /opt/birdcast-uk/venv/bin/birdcast-uk static build \
  --output-dir /opt/birdcast-uk/data/static-artifacts \
  --public-base-url https://ncas-radar-o.s3-ext.jc.rl.ac.uk/uk-wsr-visualizer-public \
  --object-prefix birdcast-uk

sudo -u birdcast rm -rf /opt/birdcast-uk/site
sudo -u birdcast mkdir -p /opt/birdcast-uk/site
sudo -u birdcast cp -R /opt/birdcast-uk/data/static-artifacts/web/. /opt/birdcast-uk/site/
```

## Nginx

```bash
sudo install -m 0644 deploy/nginx/birdcast-uk.conf /etc/nginx/sites-available/birdcast-uk.conf
sudo ln -sf /etc/nginx/sites-available/birdcast-uk.conf /etc/nginx/sites-enabled/birdcast-uk.conf
sudo nginx -t
sudo systemctl reload nginx
```

The site is then available at:

```text
http://130.246.212.190/birdcast-uk/
```

## systemd Timers

```bash
sudo install -m 0644 deploy/systemd/birdcast-uk-*.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/birdcast-uk-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now \
  birdcast-uk-radars-refresh.timer \
  birdcast-uk-vpts-inventory.timer \
  birdcast-uk-observed-build.timer \
  birdcast-uk-era5-build-day.timer \
  birdcast-uk-feature-join.timer \
  birdcast-uk-static-site-refresh.timer \
  birdcast-uk-object-store-plan.timer \
  birdcast-uk-object-store-sync.timer
```

The VPTS reader uses only the public catalogue and exact object URLs. It never
modifies the production VPTS prefix. The publication timers write only beneath
the `birdcast-uk/` prefix.

```bash
sudo -u birdcast /bin/sh /opt/birdcast-uk/data/object-store/sync.sh
```

## BTO Validation Request

Generate the BTO request draft:

```bash
sudo -u birdcast /opt/birdcast-uk/venv/bin/birdcast-uk bto request-template \
  --output /opt/birdcast-uk/data/validation/bto/bto-data-request.md
```

Keep licensed BTO source data outside public Object Store prefixes. Publish only aggregate validation summaries.

## ERA5 with ECMWF Earthkit

The independent BirdCast ERA5 flow uses `earthkit-data` for CDS retrieval,
cache management, file decoding, and conversion to Xarray before extracting
the nearest native-grid values for the 17 UK radar sites. Configure the CDS
token in `/opt/birdcast-uk/.cdsapirc`; the systemd service sets `CDSAPI_RC`
explicitly and keeps a bounded 20 GB Earthkit cache under
`/opt/birdcast-uk/data/earthkit-cache`.

The daily timer requests data from seven days earlier to allow for ERA5T
availability. Request JSON, raw NetCDF, site features, and build status remain
separate artifacts.

### Request Smoke Test

```bash
sudo -u birdcast /opt/birdcast-uk/venv/bin/birdcast-uk era5 request \
  --day 2026-06-27 \
  --kind pressure-levels \
  --output-file /gws/ssde/j25a/ncas_radar/vol2/avocet/birdcast-uk/era5/raw/era5_pressure_levels_20260627_uk.nc \
  --request-json /opt/birdcast-uk/data/era5/era5_pressure_levels_20260627_request.json
```

This builds the CDS request JSON without downloading. To exercise Earthkit and
produce site features, add `--download` to `era5 build-day`. CDS credentials
and the ERA5 dataset terms must already be accepted by the account.
