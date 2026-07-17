"""Canonical projected grid for UK BirdCast products."""

from __future__ import annotations

from dataclasses import dataclass

from .config import FORECAST_GRID_BOUNDS_M, FORECAST_GRID_CRS, FORECAST_GRID_RESOLUTION_M


@dataclass(frozen=True)
class ForecastGrid:
    crs: str
    resolution_m: int
    x_min_m: int
    y_min_m: int
    x_max_m: int
    y_max_m: int

    @property
    def width(self) -> int:
        return (self.x_max_m - self.x_min_m) // self.resolution_m

    @property
    def height(self) -> int:
        return (self.y_max_m - self.y_min_m) // self.resolution_m

    def x_centres(self):
        import numpy as np

        return self.x_min_m + (np.arange(self.width, dtype="float64") + 0.5) * self.resolution_m

    def y_centres(self):
        import numpy as np

        return self.y_max_m - (np.arange(self.height, dtype="float64") + 0.5) * self.resolution_m

    def lonlat(self):
        import numpy as np
        from pyproj import Transformer

        x, y = np.meshgrid(self.x_centres(), self.y_centres())
        transformer = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)
        lon, lat = transformer.transform(x, y)
        return np.asarray(lon), np.asarray(lat)

    def cell_for_lonlat(self, longitude: float, latitude: float) -> tuple[int, int] | None:
        from pyproj import Transformer

        transformer = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)
        x, y = transformer.transform(longitude, latitude)
        col = int((x - self.x_min_m) // self.resolution_m)
        row = int((self.y_max_m - y) // self.resolution_m)
        if 0 <= row < self.height and 0 <= col < self.width:
            return row, col
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "crs": self.crs,
            "resolution_m": self.resolution_m,
            "bounds_m": [self.x_min_m, self.y_min_m, self.x_max_m, self.y_max_m],
            "shape": [self.height, self.width],
            "row_order": "north_to_south",
        }


def canonical_grid() -> ForecastGrid:
    return ForecastGrid(FORECAST_GRID_CRS, FORECAST_GRID_RESOLUTION_M, *FORECAST_GRID_BOUNDS_M)
