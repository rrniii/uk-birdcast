from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "merge_gamm_component_predictions.py"
SPEC = importlib.util.spec_from_file_location("merge_component_predictions", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write_day(root: Path, day: str, *, shifted_hour: int | None = None) -> None:
    directory = root / day.replace("-", "")
    directory.mkdir(parents=True)
    header = (
        "time_utc,longitude,latitude,support,mtr_birds_km_h,vid_birds_per_km2,bird_u_ms,bird_v_ms\n"
    )
    rows = []
    for hour in range(24):
        longitude = -0.25 if hour == shifted_hour else -0.5
        rows.append(f"{day}T{hour:02d}:00:00Z,{longitude},51.5,.8,2,1,1,.5\n")
    (directory / "predictions_wide_lp.csv").write_text(header + "".join(rows), encoding="utf-8")


def test_component_merge_enforces_declared_contiguous_window(tmp_path: Path) -> None:
    root = tmp_path / "daily"
    _write_day(root, "2025-07-01")
    _write_day(root, "2025-07-02")

    result = MODULE.merge(
        root,
        "lp",
        tmp_path / "merged.csv",
        2,
        expected_start_day=date(2025, 7, 1),
        expected_end_day=date(2025, 7, 2),
    )

    assert result == {
        "days": 2,
        "first_day": "2025-07-01",
        "last_day": "2025-07-02",
        "rows": 48,
        "cells_per_hour": 1,
    }


def test_component_merge_rejects_shifted_coordinates_and_no_partial_output(
    tmp_path: Path,
) -> None:
    root = tmp_path / "daily"
    _write_day(root, "2025-07-01", shifted_hour=12)
    output = tmp_path / "merged.csv"

    with pytest.raises(ValueError, match="changes grid coordinates"):
        MODULE.merge(root, "lp", output, 1)

    assert not output.exists()


def test_component_merge_requires_matching_daily_authority(tmp_path: Path) -> None:
    root = tmp_path / "daily"
    _write_day(root, "2025-07-01")
    (root / "20250701" / "component-manifest.sha256").write_text(
        f"{'b' * 64}\n",
        encoding="ascii",
    )

    with pytest.raises(ValueError, match="wrong component authority"):
        MODULE.merge(
            root,
            "lp",
            tmp_path / "merged.csv",
            1,
            expected_component_manifest_sha256="a" * 64,
        )
