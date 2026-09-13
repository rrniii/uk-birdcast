from datetime import date, datetime, timezone

import pytest

from birdcast_uk import weather_availability as weather

NOW = datetime(2026, 9, 13, 9, tzinfo=timezone.utc)


def metadata(dataset, end="2026-09-07T00:00:00+00:00"):
    return {"id": dataset, "extent": {"temporal": {"interval": [["1940-01-01", end]]}}}


def test_coverage_requires_both_weather_datasets(monkeypatch):
    def fetch(url):
        dataset = url.rsplit("/", 1)[1]
        end = "2026-09-08" if dataset.endswith("single-levels") else "2026-09-07"
        return metadata(dataset, end + "T00:00:00Z")

    monkeypatch.setattr(weather, "fetch_json", fetch)
    assert weather.available_weather_through(now=NOW) == date(2026, 9, 7)


@pytest.mark.parametrize("end", [None, "2026-09-14T00:00:00Z", "2026-09-07T00:00:00"])
def test_invalid_coverage_fails_closed(monkeypatch, end):
    monkeypatch.setattr(weather, "fetch_json", lambda url: metadata(url.rsplit("/", 1)[1], end))
    with pytest.raises(ValueError):
        weather.available_weather_through(now=NOW)
