from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load() -> object:
    path = Path(__file__).parents[1] / "scripts" / "merge_europe_era5_fidelity.py"
    spec = importlib.util.spec_from_file_location("merge_europe_era5_fidelity", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_era5_fidelity_merge_requires_every_day(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "20250714.json").write_text('{"status":"passed","release_id":"release-a","radars_sha256":"abc"}', encoding="utf-8")
    with pytest.raises(ValueError, match="missing=1"):
        _load().merge(
            reports, tmp_path / "summary.json", start_day="2025-07-14", end_day="2025-07-15",
            release_id="release-a", radars_sha256="abc",
        )

    (reports / "20250715.json").write_text('{"status":"passed","release_id":"other","radars_sha256":"abc"}', encoding="utf-8")
    with pytest.raises(ValueError, match="stale=1"):
        _load().merge(
            reports, tmp_path / "summary.json", start_day="2025-07-14", end_day="2025-07-15",
            release_id="release-a", radars_sha256="abc",
        )
