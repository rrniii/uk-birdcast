# UK BirdCast implementation contract

## Operational product

- National 10 km grid in EPSG:3035 with approximately 300 km of transport context.
- Vertically integrated biological-target density from 200 to 4000 m.
- Six-hour issue cycle with an hourly internal state and public lead times from 0 to 96 hours.
- Separate LP and SP source provenance; pulse products are never added as populations.
- Fifty-member ensemble with p10, p50, and p90 density, migration vectors, flight-height diagnostics, contamination probability, observation influence, and a quality flag.
- Versioned Zarr, Cloud Optimized GeoTIFF, lightweight web frames, and an atomic latest manifest.

## State-space model

The first operational model is a process-guided ensemble baseline. Its transition is a
semi-Lagrangian continuity step followed by an explicit seasonal/nocturnal
source-sink term. Radar density is assimilated with a localized ensemble Kalman
update. This is the benchmark and fallback for the optional ConvGRU residual in
`convgru.py`; an untrained neural residual is never used operationally.

Operational modes are contractual:

| Radar age | Mode | Behaviour |
|---|---|---|
| up to 6 h | `assimilated` | Localized radar update, then forecast |
| 6-24 h | `propagated` | Previous/climatological state with inflated spread |
| over 24 h | `weather_only` | Seasonal baseline and weather, wider uncertainty |

## Weather flows

Historical training and reanalysis use the standalone ERA5 flow. Runtime forcing
uses ECMWF Open Data through Earthkit. Every retrieved 00/06/12/18 cycle is
archived before inference because the upstream open archive is rolling. The raw
GRIB, request, checksum, licence, cycle time, and status are retained together.
Successful cycle directories are then copied to the standalone
`birdcast-uk/ecmwf/cycles/` Object Store prefix.

## Training and evaluation

- Training: 2013-2022.
- Validation: 2023.
- Locked temporal test: 2024-2025.
- Spatial test: leave-one-radar-out.
- Scores: CRPS, interval coverage, MAE, event precision/recall, peak-timing error,
  transport/direction error, and reliability by lead time and season.
- The production model may be refit on all accepted years only after locked-test
  results and model metadata have been frozen.

## BTO validation

BirdTrack complete-list reporting frequency and effort-normalised regional
summaries test phenology, event timing, and broad spatial plausibility. Licensed
raw records remain private. Only aggregate validation scores are published.
BTO data are not used to calibrate radar density as an absolute population count.

The request is made through `https://www.bto.org/data/request`. Ask for weekly
10 km or agreed regional summaries with complete-list denominators, effort,
species groups, dates, and licence/version metadata. BTO describes reporting
rate as the percentage of complete lists containing a species. After private
aggregation, run `birdcast-uk bto validate`; the public result contains only
correlation, event overlap, peak-timing error, coverage, and total effort.
