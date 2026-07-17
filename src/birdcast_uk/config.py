"""Shared defaults for the UK BirdCast research MVP."""

from __future__ import annotations

from pathlib import Path

OBJECT_PREFIX = "birdcast-uk"
PROCESSING_VERSION = "birdcast-uk-observed-0.2.0"

DEFAULT_PUBLIC_BASE_URL = "https://ncas-radar-o.s3-ext.jc.rl.ac.uk/uk-wsr-visualizer-public"
DEFAULT_INTERNAL_ENDPOINT = "http://ncas-radar-o.s3.jc.rl.ac.uk"
DEFAULT_BUCKET = "uk-wsr-visualizer-public"
DEFAULT_AWS_PROFILE = "ncas-radar-o"

BIORAD_VPTS_PREFIX = "ukmo-nimrod/vpts/current_ci_le4"
BIORAD_MANIFEST_PREFIX = f"{OBJECT_PREFIX}/biorad/manifests"
OBSERVED_PREFIX = f"{OBJECT_PREFIX}/archive/observed"
ERA5_PREFIX = f"{OBJECT_PREFIX}/era5"
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

ERA5_SINGLE_LEVEL_VARIABLES = (
    "2m_temperature",
    "2m_dewpoint_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "surface_pressure",
    "mean_sea_level_pressure",
    "total_cloud_cover",
    "low_cloud_cover",
    "medium_cloud_cover",
    "high_cloud_cover",
    "boundary_layer_height",
    "total_column_water_vapour",
)

ERA5_PRESSURE_LEVEL_VARIABLES = (
    "geopotential",
    "temperature",
    "specific_humidity",
    "relative_humidity",
    "u_component_of_wind",
    "v_component_of_wind",
    "vertical_velocity",
    "fraction_of_cloud_cover",
)

ERA5_PRESSURE_LEVELS = (
    "1000",
    "975",
    "950",
    "925",
    "900",
    "875",
    "850",
    "800",
    "750",
    "700",
    "650",
    "600",
    "550",
    "500",
    "450",
    "400",
    "350",
    "300",
    "250",
    "200",
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
