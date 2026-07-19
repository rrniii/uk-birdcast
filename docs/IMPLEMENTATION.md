# UK BirdCast historical implementation contract

## Operational product

- Historical radar reanalysis only; no public forecast.
- VPTS vertical integrated density from 200 to 4000 m.
- LP and SP retained as separate products, with LP selected by default.
- Daily radar-site maps for the latest complete 365-day window.
- All-hour radar-site summaries, time-series plots, and archive coverage
  plots. The displayed radar values aggregate available day, twilight, and
  night profiles rather than selecting a solar period.
- Natural Earth 1:10m country geometry rendered on a device-pixel-aware canvas.
- Versioned Object Store assets and an atomic `latest/historical.json` manifest.

VID is a bird-passage index in birds km-2. It is not an absolute bird count or
population estimate.

## Compute and storage boundary

The 149 GB VPTS object archive remains in the `ncas-radar-o` Object Store. Full
archive analysis belongs on JASMIN batch compute or the radar GWS, not on the
cloud web host. A successful batch run produces the compact aggregate contract:

```text
analysis_summary.json
daily_totals.csv
network_annual_seasonal_totals.csv
phenology.csv
coverage.csv
```

The JASMIN Cloud host consumes that package, creates browser-ready artifacts,
and publishes them under `birdcast-uk/historical/`. Year partitioning limits a
normal browser request to one year of radar-day data.

### Public Object Store CORS

The public bucket must allow browser `GET` and `HEAD` requests. Apply the
version-controlled policy after creating or replacing the bucket:

```bash
s3cmd --config "$BIRDCAST_UK_S3CMD_CONFIG" setcors \
  deploy/object-store/public-read-cors.xml \
  "s3://$BIRDCAST_UK_OBJECT_STORE_BUCKET"
```

Verify both anonymous access and the browser-origin response before deploying
the web client:

```bash
curl --fail --silent --show-error --dump-header - --output /dev/null \
  -H "Origin: http://uk-birdcast.tailea56a2.ts.net" \
  "$BIRDCAST_UK_PUBLIC_BASE_URL/birdcast-uk/latest/historical.json"
```

The response must be `200` and include `Access-Control-Allow-Origin` and
`Access-Control-Allow-Methods: GET,HEAD`. This CORS policy does not make private
objects public and grants no browser write permissions.

## Historical weather flow

ERA5 remains a standalone BirdCast flow using Earthkit. Radar summaries are
joined to ERA5 by radar and time for retrospective wind, temperature, cloud,
boundary-layer, and precipitation analyses. ERA5 source files, requests,
checksums, and derived feature tables remain under `birdcast-uk/era5/`.

ECMWF Open Data cycle retrieval and forecast generation are disabled. Their code
and archived test cycles remain for provenance but are outside the operational
product. The ERA5 model has no hour-of-day, date, season, daylight, twilight,
sunrise, sunset, or phenology predictor.

## Plot rules

- LP and SP plots are separate; no combined LP+SP population interpretation.
- The initial release is a rolling 365-day historical reanalysis window.
- Network plots use all available hours and report effort/coverage.
- Every plot labels VID as a passage index.

## BTO validation

BirdTrack complete-list reporting frequency and effort-normalised regional
summaries test phenology, event timing, and broad spatial plausibility. Licensed
raw records remain private. Only aggregate validation scores are published.
BTO data are not used to calibrate VID as an absolute population count.

The request is made through `https://www.bto.org/data/request`. Ask for weekly
10 km or agreed regional summaries with complete-list denominators, effort,
species groups, dates, and licence/version metadata. After private aggregation,
run `birdcast-uk bto validate`; the public result contains only correlation,
event overlap, peak-timing error, coverage, and total effort.
