"""Shared defaults for the UK BirdCast research MVP."""

from __future__ import annotations

from pathlib import Path

OBJECT_PREFIX = "birdcast-uk"
PROCESSING_VERSION = "birdcast-uk-historical-0.4.0"
FORECAST_SCHEMA_VERSION = "birdcast-uk-forecast-1.0"
FORECAST_MODEL_ID = "pgssm-baseline-0.1"

DEFAULT_PUBLIC_BASE_URL = "https://ncas-radar-o.s3-ext.jc.rl.ac.uk/uk-wsr-visualizer-public"
DEFAULT_INTERNAL_ENDPOINT = "http://ncas-radar-o.s3.jc.rl.ac.uk"
DEFAULT_BUCKET = "uk-wsr-visualizer-public"
DEFAULT_AWS_PROFILE = "ncas-radar-o"

BIORAD_VPTS_PREFIX = "ukmo-nimrod/vpts/current_ci_le4"
BIORAD_MANIFEST_PREFIX = f"{OBJECT_PREFIX}/biorad/manifests"
OBSERVED_PREFIX = f"{OBJECT_PREFIX}/archive/observed"
ERA5_PREFIX = f"{OBJECT_PREFIX}/era5"
REANALYSIS_PREFIX = f"{OBJECT_PREFIX}/reanalysis/gam-era5"
ECMWF_PREFIX = f"{OBJECT_PREFIX}/ecmwf"
FORECAST_PREFIX = f"{OBJECT_PREFIX}/forecast"
UKMO_PVOL_CATALOG_URL = f"{DEFAULT_PUBLIC_BASE_URL}/ukmo-nimrod/catalog/pvol/catalog.json"
UKMO_VPTS_CATALOG_URL = (
    f"{DEFAULT_PUBLIC_BASE_URL}/ukmo-nimrod/catalog/vpts/current_ci_le4/catalog.json"
)

VPTS_PULSE_POLICY = "lp_preferred_sp_fallback"
VPTS_ALTITUDE_MIN_M = 200.0
VPTS_ALTITUDE_MAX_M = 4000.0
VPTS_BOOTSTRAP_LOOKBACK_DAYS = 8
VPTS_MAX_INCREMENT_DAYS = 7
VPTS_MAX_CATALOG_AGE_HOURS = 48.0
VPTS_MAX_INTEGRATION_GAP_MINUTES = 30.0
VPTS_RAIN_DBZH_THRESHOLD = 7.0
VPTS_RAIN_LAYER_FRACTION = 0.8

DEFAULT_PROJECT_ROOT = Path("/gws/ssde/j25a/ncas_radar/vol2/avocet/birdcast-uk")
DEFAULT_CLOUD_ROOT = Path("/opt/birdcast-uk")

UK_ERA5_AREA = {
    "north": 61.5,
    "west": -11.5,
    "south": 49.0,
    "east": 3.0,
}

# The operational UK LP scan geometry is 425 gates at 600 m. This value is
# used only when the aggregate PVOL catalogue omits max_range_m; generated
# radar metadata records the ODIM-derived provenance explicitly.
UK_PVOL_MAX_RANGE_M = 255_000.0

# Canonical analysis grid. Bounds are an EPSG:3035 rectangle around the UK
# radar network with roughly 300 km of transport context on every side.
FORECAST_GRID_CRS = "EPSG:3035"
FORECAST_GRID_RESOLUTION_M = 10_000
FORECAST_GRID_BOUNDS_M = (2_550_000, 2_500_000, 4_300_000, 4_800_000)
FORECAST_ALTITUDE_MIN_M = 200
FORECAST_ALTITUDE_MAX_M = 4_000
FORECAST_ENSEMBLE_SIZE = 50
FORECAST_HORIZON_HOURS = 96
FORECAST_STEP_HOURS = 6
FORECAST_FRESH_RADAR_HOURS = 6
FORECAST_STALE_RADAR_HOURS = 24

ECMWF_OPEN_DATA_SURFACE_PARAMETERS = ("10u", "10v", "2t", "2d", "msl", "tcc", "tp")
ECMWF_OPEN_DATA_PRESSURE_PARAMETERS = ("u", "v", "t", "r", "w")
ECMWF_OPEN_DATA_PRESSURE_LEVELS = (1000, 925, 850, 700)

ERA5_SINGLE_LEVEL_VARIABLES = (
    "surface_pressure",
    "mean_sea_level_pressure",
    "total_cloud_cover",
    "boundary_layer_height",
    "total_precipitation",
)

ERA5_PRESSURE_LEVEL_VARIABLES = (
    "temperature",
    "relative_humidity",
    "u_component_of_wind",
    "v_component_of_wind",
)

ERA5_PRESSURE_LEVELS = (
    "850",
)

VPTS_REQUIRED_FIELDS = (
    "radar",
    "date",
    "pulse",
    "key",
    "source_uri",
    "public_url",
    "file_format",
    "size",
    "etag",
)

NIGHTLY_REQUIRED_FIELDS = (
    "radar",
    "night_date",
    "migration_traffic_birds_per_km",
    "mean_mtr_birds_km_h",
    "peak_mtr_birds_km_h",
    "dominant_direction_deg",
    "mean_ground_speed_ms",
    "mean_flight_height_m",
    "coverage_fraction",
    "rain_contamination_fraction",
    "quality_class",
    "intensity_class",
)

VPTS_FILE_SUFFIXES = (
    ".csv",
    ".json",
    ".jsonl",
    ".parquet",
    ".tsv",
)
