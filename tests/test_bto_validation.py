from __future__ import annotations

import json
from pathlib import Path

from birdcast_uk.bto import validate_aggregates


def test_bto_aggregate_validation_publishes_scores_not_source_rows(tmp_path: Path) -> None:
    bto = tmp_path / "bto.csv"
    radar = tmp_path / "radar.csv"
    bto.write_text(
        "region,week_start,reporting_rate,complete_list_count\n"
        "north,2026-03-02,0.1,100\n"
        "north,2026-03-09,0.5,120\n"
        "north,2026-03-16,0.9,110\n"
        "south,2026-03-02,0.2,90\n"
        "south,2026-03-09,0.7,95\n"
        "south,2026-03-16,0.4,105\n",
        encoding="utf-8",
    )
    radar.write_text(
        "region,week_start,radar_intensity\n"
        "north,2026-03-02,10\n"
        "north,2026-03-09,40\n"
        "north,2026-03-16,80\n"
        "south,2026-03-02,15\n"
        "south,2026-03-09,60\n"
        "south,2026-03-16,30\n",
        encoding="utf-8",
    )

    result = validate_aggregates(bto, radar, tmp_path / "validation.json")
    published = json.loads((tmp_path / "validation.json").read_text(encoding="utf-8"))

    assert result["status"] == "validated"
    assert result["matched_region_week_count"] == 6
    assert result["metrics"]["spearman_correlation"] > 0.9
    assert result["metrics"]["median_peak_timing_error_weeks"] == 0
    assert "rows" not in published
