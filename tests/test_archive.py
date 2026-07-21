from __future__ import annotations

from birdcast_uk.archive import (
    aloft_daily_objects,
    build_comparison_index,
    build_crosswalk,
    compare_vpts_profiles,
    load_vpts_rows,
    select_vp,
    uk_vpts_object,
)


def test_uk_vpts_object_uses_existing_daily_archive_layout() -> None:
    obj = uk_vpts_object(radar="chenies", day="2026-07-18", pulse="sp")

    assert obj.source == "jasmin-uk"
    assert obj.pulse == "sp"
    assert obj.url.endswith("chenies/2026/20260718_sp_vpts.csv")


def test_aloft_daily_objects_resolve_existing_daily_vpts_paths() -> None:
    coverage = "directory,count\nbaltrad/hdf5/seang/2020/08/29,42\nbaltrad/hdf5/seang/2020/08/30,41\nuva/hdf5/seang/2020/08/29,2\n"

    objects = aloft_daily_objects(
        radar="seang",
        start_day="2020-08-29",
        end_day="2020-08-30",
        fetch=lambda _url: coverage,
    )

    assert [obj.day for obj in objects] == ["20200829", "20200830"]
    assert all(obj.source == "baltrad" for obj in objects)
    assert objects[0].url.endswith("baltrad/daily/seang/2020/seang_vpts_20200829.csv")


def test_vp_selection_is_an_in_memory_view_of_existing_vpts_rows() -> None:
    rows = [
        {"datetime": "2026-07-18T01:00:00Z", "height": "200", "dens": "2", "source": "jasmin-uk", "source_url": "https://example/uk.csv", "pulse": "lp"},
        {"datetime": "2026-07-18T01:00:00Z", "height": "400", "dens": "3", "source": "jasmin-uk", "source_url": "https://example/uk.csv", "pulse": "lp"},
        {"datetime": "2026-07-18T01:10:00Z", "height": "200", "dens": "4", "source": "jasmin-uk", "source_url": "https://example/uk.csv", "pulse": "lp"},
    ]

    profile = select_vp(rows, "2026-07-18T01:04:00Z")

    assert profile["selected_time_utc"] == "2026-07-18T01:00:00Z"
    assert profile["row_count"] == 2
    assert profile["provenance"]["source_url"] == "https://example/uk.csv"


def test_load_rows_keeps_source_objects_immutable_and_adds_memory_provenance() -> None:
    obj = uk_vpts_object(radar="chenies", day="2026-07-18", pulse="lp")
    source = "radar,datetime,height,dens\nCH,2026-07-18T01:00:00Z,200,2\n"

    rows = load_vpts_rows(obj, fetch=lambda _url: source)

    assert rows[0]["radar"] == "CH"
    assert rows[0]["source"] == "jasmin-uk"
    assert rows[0]["source_url"] == obj.url
    assert rows[0]["pulse"] == "lp"


def test_comparison_reports_only_matched_existing_profile_statistics() -> None:
    uk_rows = [
        {"datetime": "2026-07-18T01:00:00Z", "height": "200", "dens": "3", "eta": "4", "source": "jasmin-uk", "source_url": "https://example/uk.csv", "pulse": "lp"},
        {"datetime": "2026-07-18T01:00:00Z", "height": "400", "dens": "5", "eta": "8", "source": "jasmin-uk", "source_url": "https://example/uk.csv", "pulse": "lp"},
    ]
    aloft_rows = [
        {"datetime": "2026-07-18T01:00:00Z", "height": "200", "dens": "2", "eta": "3", "source": "baltrad", "source_url": "https://example/aloft.csv"},
        {"datetime": "2026-07-18T01:00:00Z", "height": "400", "dens": "4", "eta": "7", "source": "baltrad", "source_url": "https://example/aloft.csv"},
    ]

    report = compare_vpts_profiles(uk_rows, aloft_rows, requested="2026-07-18T01:03:00Z")

    assert report["match_class"] == "exact"
    assert report["common_altitude_count"] == 2
    assert report["metrics"]["dens"] == {"count": 2, "bias": 1.0, "mae": 1.0, "rmse": 1.0}
    assert report["uk"]["provenance"]["source_url"] == "https://example/uk.csv"
    assert report["aloft"]["provenance"]["source_url"] == "https://example/aloft.csv"


def test_crosswalk_requires_explicit_mapping_and_keeps_unmatched_radars_visible() -> None:
    payload = build_crosswalk(
        [{"slug": "chenies", "label": "Chenies"}, {"slug": "jersey", "label": "Jersey"}],
        [{"uk_radar": "chenies", "aloft_source": "baltrad", "aloft_radar": "ukche", "comparison_class": "exact"}],
    )

    assert payload["entry_count"] == 1
    assert payload["entries"][0]["uk_radar"] == "chenies"
    assert payload["unmatched_uk_radars"] == ["jersey"]


def test_comparison_index_only_publishes_reports_that_match_the_explicit_crosswalk() -> None:
    crosswalk = build_crosswalk(
        [{"slug": "chenies", "label": "Chenies"}],
        [{"uk_radar": "chenies", "aloft_source": "baltrad", "aloft_radar": "ukche", "comparison_class": "exact"}],
    )
    report = {
        "generated_at_utc": "2026-07-21T10:00:00Z",
        "requested_time_utc": "2026-07-18T01:00:00Z",
        "common_altitude_count": 25,
        "time_difference_seconds": 0,
        "within_time_tolerance": True,
        "uk": {"provenance": {"radar": "chenies"}},
        "aloft": {"provenance": {"source": "baltrad", "radar": "ukche"}},
        "metrics": {"dens": {"count": 25, "bias": 1.0}},
    }

    index = build_comparison_index(crosswalk, [report, {**report, "uk": {"provenance": {"radar": "jersey"}}}])

    assert index["status"] == "ready"
    assert index["report_count"] == 1
    assert index["entries"][0]["report_available"] is True
    assert "rows" not in index["entries"][0]
