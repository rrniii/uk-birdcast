"""Process-guided ensemble state-space model for bird density."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Iterable


@dataclass(frozen=True)
class RadarObservation:
    row: int
    col: int
    density_birds_km2: float
    observation_variance: float
    u_ms: float
    v_ms: float


def radar_age_hours(analysis_time: datetime, observation_time: datetime | None) -> float:
    if observation_time is None:
        return math.inf
    return max(0.0, (analysis_time - observation_time).total_seconds() / 3600.0)


def operational_mode(age_hours: float) -> str:
    from .config import FORECAST_FRESH_RADAR_HOURS, FORECAST_STALE_RADAR_HOURS

    if age_hours <= FORECAST_FRESH_RADAR_HOURS:
        return "assimilated"
    if age_hours <= FORECAST_STALE_RADAR_HOURS:
        return "propagated"
    return "weather_only"


def initialise_ensemble(
    shape: tuple[int, int],
    observations: Iterable[RadarObservation],
    *,
    members: int,
    seed: int,
    climatology: float = 0.25,
):
    import numpy as np

    rng = np.random.default_rng(seed)
    weight = np.full(shape, 0.04, dtype="float32")
    field = np.full(shape, max(climatology, 0.01) * weight[0, 0], dtype="float32")
    yy, xx = np.indices(shape)
    for obs in observations:
        distance2 = (yy - obs.row) ** 2 + (xx - obs.col) ** 2
        local_weight = np.exp(-distance2 / (2.0 * 12.0**2)).astype("float32")
        field += local_weight * max(obs.density_birds_km2, 0.0)
        weight += local_weight
    field /= weight
    correlation_cells = 10
    coarse = rng.normal(
        0.0,
        0.35,
        size=(
            members,
            math.ceil(shape[0] / correlation_cells),
            math.ceil(shape[1] / correlation_cells),
        ),
    ).astype("float32")
    noise = np.exp(
        np.repeat(
            np.repeat(coarse, correlation_cells, axis=1),
            correlation_cells,
            axis=2,
        )[:, : shape[0], : shape[1]]
    )
    return np.maximum(field[None, :, :] * noise, 0.0)


def advect_ensemble(ensemble, u_ms, v_ms, *, resolution_m: float, hours: float):
    """Semi-Lagrangian bilinear advection on a north-to-south row grid."""

    import numpy as np

    _, height, width = ensemble.shape
    yy, xx = np.indices((height, width), dtype="float32")
    col_source = xx - np.asarray(u_ms, dtype="float32") * hours * 3600.0 / resolution_m
    row_source = yy + np.asarray(v_ms, dtype="float32") * hours * 3600.0 / resolution_m
    x0 = np.floor(col_source).astype("int32")
    y0 = np.floor(row_source).astype("int32")
    x1 = x0 + 1
    y1 = y0 + 1
    valid = (x0 >= 0) & (x1 < width) & (y0 >= 0) & (y1 < height)
    x0c, x1c = np.clip(x0, 0, width - 1), np.clip(x1, 0, width - 1)
    y0c, y1c = np.clip(y0, 0, height - 1), np.clip(y1, 0, height - 1)
    wx = col_source - x0
    wy = row_source - y0
    result = (
        ensemble[:, y0c, x0c] * (1 - wx) * (1 - wy)
        + ensemble[:, y0c, x1c] * wx * (1 - wy)
        + ensemble[:, y1c, x0c] * (1 - wx) * wy
        + ensemble[:, y1c, x1c] * wx * wy
    )
    return np.where(valid[None, :, :], result, 0.0).astype("float32")


def process_step(
    ensemble,
    u_ms,
    v_ms,
    valid_time: datetime,
    *,
    resolution_m: float,
    hours: float,
    seed: int,
):
    """Continuity transition plus a transparent seasonal source/sink baseline."""

    import numpy as np

    advected = advect_ensemble(ensemble, u_ms, v_ms, resolution_m=resolution_m, hours=hours)
    rng = np.random.default_rng(seed)
    hour = valid_time.astimezone(timezone.utc).hour
    month = valid_time.month
    nocturnal = 1.0 if hour >= 18 or hour < 6 else -0.65
    seasonal = 1.0 if month in (3, 4, 5, 8, 9, 10, 11) else 0.25
    rate = nocturnal * seasonal * 0.035 * hours
    members, height, width = advected.shape
    correlation_cells = 10
    coarse = rng.normal(
        0.0,
        0.08 * math.sqrt(hours),
        size=(
            members,
            math.ceil(height / correlation_cells),
            math.ceil(width / correlation_cells),
        ),
    ).astype("float32")
    process_noise = np.repeat(
        np.repeat(coarse, correlation_cells, axis=1),
        correlation_cells,
        axis=2,
    )[:, :height, :width]
    return np.maximum(advected * np.exp(rate + process_noise), 0.0).astype("float32")


def assimilate_localised(ensemble, observations: Iterable[RadarObservation], *, radius_cells: float = 18.0):
    """Deterministic local ensemble Kalman update for point observations."""

    import numpy as np

    ensemble = np.asarray(ensemble, dtype="float32").copy()
    _, height, width = ensemble.shape
    yy, xx = np.indices((height, width))
    influence = np.zeros((height, width), dtype="float32")
    for obs in observations:
        predicted = ensemble[:, obs.row, obs.col]
        variance = float(np.var(predicted, ddof=1)) + 1e-6
        gain = variance / (variance + max(obs.observation_variance, 1e-6))
        distance2 = (yy - obs.row) ** 2 + (xx - obs.col) ** 2
        localisation = np.exp(-distance2 / (2.0 * radius_cells**2)).astype("float32")
        innovation = obs.density_birds_km2 - predicted
        ensemble += localisation[None, :, :] * gain * innovation[:, None, None]
        influence = np.maximum(influence, localisation * gain)
    return np.maximum(ensemble, 0.0), influence
