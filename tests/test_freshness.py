from __future__ import annotations

import copy
from datetime import date, datetime, timezone

import pytest

from birdcast_uk.freshness import catalog_window, evaluate_freshness
from birdcast_uk.selected_model import SELECTION_ID

NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)


def products():
    return (
        {
            "generated_at": "2026-09-04T21:00:00Z",
            "radars": [
                {"radar": "a", "last_date": "20260901"},
                {"radar": "b", "last_date": "20260901"},
            ],
        },
        {
            "data_available": True,
            "latest_date": "2026-08-31",
            "radar_coverage": [
                {"radar": "a", "last_date": "2026-08-31"},
                {"radar": "b", "last_date": "2026-08-31"},
            ],
        },
        {
            "data_available": True,
            "selection_id": SELECTION_ID,
            "latest_time_utc": "2026-08-31T23:00:00Z",
        },
        {"data_available": False, "mode": "disabled", "valid_times_utc": []},
    )


def test_freshness_checks_observation_and_model_data_dates_not_shell_generation():
    report = evaluate_freshness(*products(), now=NOW, radars={"a", "b"})
    assert report["ok"]
    assert report["expected_observations_through"] == "2026-08-31"
    assert report["source_age_days"] == 5
    assert report["publication_lag_days"] == 0
    assert report["expected_model_through"] == "2026-09-01"
    assert report["model_publication_lag_days"] == 1


def test_fixed_july_model_is_now_a_real_publication_backlog():
    catalog, historical, model, forecast = products()
    model["latest_time_utc"] = "2026-07-13T23:00:00Z"
    model["generated_at_utc"] = NOW.isoformat()
    report = evaluate_freshness(catalog, historical, model, forecast, now=NOW, radars={"a", "b"})
    assert not report["ok"]
    assert report["model_publication_lag_days"] == 50
    assert not report["checks"]["model_publication_caught_up"]


def test_weather_delay_is_separate_from_model_backlog_and_outages_remain_visible():
    catalog, historical, model, forecast = products()
    historical["source"] = {
        "catch_up_missing_source_days": [{"radar": "a", "date": "2026-08-20", "pulse": "lp"}]
    }
    report = evaluate_freshness(
        catalog,
        historical,
        model,
        forecast,
        now=NOW,
        radars={"a", "b"},
        weather_available_through=date(2026, 8, 31),
    )
    assert report["ok"] and report["waiting_for_weather"]
    assert report["model_calendar_target"] == "2026-09-01"
    assert report["expected_model_through"] == "2026-08-31"
    assert report["model_publication_lag_days"] == 0
    assert len(report["observation_source_gaps"]) == 1
    stale = evaluate_freshness(
        catalog,
        historical,
        model,
        forecast,
        now=NOW,
        radars={"a", "b"},
        weather_available_through=date(2026, 8, 1),
    )
    assert not stale["checks"]["weather_source_recent"]


@pytest.mark.parametrize("failure", ["backlog", "source", "catalog", "radar", "forecast"])
def test_freshness_reports_independent_failure_modes(failure):
    catalog, historical, model, forecast = copy.deepcopy(products())
    check = {
        "backlog": "publication_caught_up",
        "source": "source_recent",
        "catalog": "catalogue_recent",
        "radar": "all_radars_reach_publication_date",
        "forecast": "forecast_disabled",
    }[failure]
    if failure == "backlog":
        historical["latest_date"] = "2026-08-15"
        historical["generated_at_utc"] = NOW.isoformat()  # Fresh packaging cannot hide old data.
    elif failure == "source":
        catalog["radars"][0]["last_date"] = "20260825"
    elif failure == "catalog":
        catalog["generated_at"] = "2026-08-20T21:00:00Z"
    elif failure == "radar":
        historical["radar_coverage"][0]["last_date"] = "2026-08-15"
    else:
        forecast["data_available"] = True
    report = evaluate_freshness(catalog, historical, model, forecast, now=NOW, radars={"a", "b"})
    assert not report["ok"]
    assert not report["checks"][check]
    assert report["alerts"]


def test_catalog_window_rejects_missing_duplicate_future_and_naive_metadata():
    catalog = products()[0]
    for bad in (
        {**catalog, "radars": catalog["radars"][:1]},
        {**catalog, "radars": catalog["radars"] * 2},
        {**catalog, "generated_at": "2026-09-04T00:00:00"},
        {**catalog, "generated_at": "2026-09-07T00:00:00Z"},
    ):
        with pytest.raises(ValueError):
            catalog_window(bad, {"a", "b"}, NOW)


def test_current_utc_source_day_is_not_treated_as_finished():
    catalog = products()[0]
    for row in catalog["radars"]:
        row["last_date"] = "20260906"
    assert catalog_window(catalog, {"a", "b"}, NOW)[1].isoformat() == "2026-09-05"
