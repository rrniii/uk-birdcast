from __future__ import annotations

from birdcast_uk.scales import INTENSITY_PALETTE, linear_colour_scale, log_colour_scale


def test_log_colour_scale_is_quantitative_and_outward_rounded() -> None:
    scale = log_colour_scale([0, 1.2, 4.0, 19.0, 220.0], units="birds km-1 h-1")

    assert scale["transform"] == "log10"
    assert scale["minimum"] > 0
    assert scale["minimum"] <= 1.2
    assert scale["maximum"] >= 19.0
    assert scale["ticks"][0] == scale["minimum"]
    assert scale["ticks"][-1] == scale["maximum"]
    assert scale["palette"] == list(INTENSITY_PALETTE)


def test_linear_colour_scale_has_numeric_ticks() -> None:
    scale = linear_colour_scale([100.0, 200.0, 300.0], units="m")

    assert scale["transform"] == "linear"
    assert len(scale["ticks"]) == 5
    assert scale["ticks"] == sorted(scale["ticks"])
