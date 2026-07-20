"""Stable quantitative colour-scale contracts for published map products."""

from __future__ import annotations

import math
from typing import Iterable


INTENSITY_PALETTE = (
    "#101817",
    "#16484a",
    "#43887a",
    "#9fc57a",
    "#e5d17c",
    "#f2b863",
    "#e47b52",
    "#b94135",
)

# Robin passage: atmospheric charcoal through muted teal and pale gold, with
# robin red reserved for the most intense five percent of the displayed scale.
INTENSITY_PALETTE_POSITIONS = (0.0, 0.18, 0.38, 0.58, 0.75, 0.90, 0.95, 1.0)


def log_colour_scale(values: Iterable[float], *, units: str) -> dict[str, object]:
    positive = sorted(float(value) for value in values if math.isfinite(float(value)) and float(value) > 0)
    if not positive:
        lower, upper = 1.0, 10.0
    else:
        lower = _nice_floor(_percentile(positive, 0.01))
        upper = _nice_ceil(_percentile(positive, 0.99))
        if upper <= lower:
            upper = _nice_ceil(lower * 10.0)
    return {
        "transform": "log10",
        "minimum": lower,
        "maximum": upper,
        "ticks": _log_ticks(lower, upper),
        "units": units,
        "palette": list(INTENSITY_PALETTE),
        "palette_positions": list(INTENSITY_PALETTE_POSITIONS),
        "zero_colour": "#101817",
        "missing_colour": "transparent",
        "clamp": True,
        "scope": "complete published archive across LP and SP",
        "lower_percentile": 1,
        "upper_percentile": 99,
        "rounding": "outward to 1-2-5 times a power of ten",
    }


def linear_colour_scale(values: Iterable[float], *, units: str) -> dict[str, object]:
    finite = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not finite:
        lower, upper = 0.0, 1.0
    else:
        lower = _percentile(finite, 0.05)
        upper = _percentile(finite, 0.95)
        if upper <= lower:
            upper = lower + 1.0
    step = (upper - lower) / 4.0
    return {
        "transform": "linear",
        "minimum": lower,
        "maximum": upper,
        "ticks": [lower + step * index for index in range(5)],
        "units": units,
        "palette": list(INTENSITY_PALETTE),
        "palette_positions": list(INTENSITY_PALETTE_POSITIONS),
        "missing_colour": "transparent",
        "clamp": True,
        "scope": "complete published archive across LP and SP",
        "lower_percentile": 5,
        "upper_percentile": 95,
    }


def _percentile(values: list[float], fraction: float) -> float:
    if len(values) == 1:
        return values[0]
    position = fraction * (len(values) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def _nice_floor(value: float) -> float:
    exponent = math.floor(math.log10(max(value, 1e-12)))
    scale = 10.0**exponent
    mantissa = value / scale
    factor = 1.0 if mantissa < 2.0 else 2.0 if mantissa < 5.0 else 5.0
    return factor * scale


def _nice_ceil(value: float) -> float:
    exponent = math.floor(math.log10(max(value, 1e-12)))
    scale = 10.0**exponent
    mantissa = value / scale
    factor = 1.0 if mantissa <= 1.0 else 2.0 if mantissa <= 2.0 else 5.0 if mantissa <= 5.0 else 10.0
    return factor * scale


def _log_ticks(lower: float, upper: float) -> list[float]:
    ticks = []
    for exponent in range(math.floor(math.log10(lower)), math.ceil(math.log10(upper)) + 1):
        scale = 10.0**exponent
        for factor in (1.0, 2.0, 5.0):
            value = factor * scale
            if lower <= value <= upper:
                ticks.append(value)
    if lower not in ticks:
        ticks.insert(0, lower)
    if upper not in ticks:
        ticks.append(upper)
    return sorted(set(ticks))
