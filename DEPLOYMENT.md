# Live UK Bird Maps Static Deployment

Target host:

```text
uk-birdcast-workstation-ssh
130.246.212.190
```

The site is served from the JASMIN Cloud host and reads public data artifacts
from the JASMIN Object Store. Heavy processing and historical backfills run on
JASMIN batch/GWS.

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
http://130.246.212.190/live-uk-bird-maps/
```

## Web-Only systemd Timer

```bash
sudo install -m 0644 deploy/systemd/birdcast-uk-*.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/birdcast-uk-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now birdcast-uk-static-site-refresh.timer
```

The cloud workstation is a web server only. VPTS inventory, ERA5 retrieval,
feature preparation, model fitting, and Object Store publication run in the
JASMIN batch/GWS flow. Disable all data-production timers on this host:

```bash
sudo systemctl disable --now \
  birdcast-uk-radars-refresh.timer \
  birdcast-uk-vpts-inventory.timer \
  birdcast-uk-observed-build.timer \
  birdcast-uk-era5-build-day.timer \
  birdcast-uk-feature-join.timer \
  birdcast-uk-object-store-plan.timer \
  birdcast-uk-object-store-sync.timer \
  birdcast-uk-ecmwf-archive.timer \
  birdcast-uk-forecast-build.timer
```

The web client reads immutable daily assets and `latest/*.json` directly from
the public `birdcast-uk/` Object Store prefix. It has no credentials and cannot
modify VPTS, ERA5, or publication data.

The public artifact tree contains historical radar and ERA5-reanalysis
products only. Immutable assets are uploaded before the `latest/*.json`
manifests so readers do not observe a partial publication. Publication fails
closed if either required manifest is a placeholder or references a missing
asset.

## BTO Validation Request

Generate and process the BTO request on JASMIN batch/GWS, not on the cloud web
host. Keep licensed BTO source data outside public Object Store prefixes and
publish only aggregate validation summaries.

## ERA5 with ECMWF Earthkit

The independent BirdCast ERA5 flow uses `earthkit-data` for CDS retrieval,
cache management, file decoding, and conversion to Xarray before extracting
the nearest native-grid values for the 17 UK radar sites. CDS credentials,
Earthkit cache, request JSON, raw NetCDF, site features, and model tables all
remain on JASMIN. The cloud host has no ERA5 credential or processing role.

The ERA5 request envelope and published grid are derived from the union of the
radars' validated 255 km LP ranges, including sea areas. Natural Earth
coastlines are visual context only and never constrain extraction, model
support, or rendering.
