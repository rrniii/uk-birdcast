from __future__ import annotations

from datetime import datetime, timezone

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("pyproj")

from birdcast_uk.ecmwf import normalise_cycle, open_data_requests
from birdcast_uk.grid import canonical_grid
from birdcast_uk.state_space import (
    RadarObservation,
    assimilate_localised,
    advect_ensemble,
    initialise_ensemble,
    operational_mode,
)


def test_canonical_grid_contract_and_radar_cell() -> None:
    grid = canonical_grid()

    assert grid.crs == "EPSG:3035"
    assert grid.resolution_m == 10_000
    assert grid.height == 230
    assert grid.width == 175
    cell = grid.cell_for_lonlat(-0.5303, 51.6894)
    assert cell is not None
    assert 0 <= cell[0] < grid.height
    assert 0 <= cell[1] < grid.width


def test_operational_stale_modes_are_explicit() -> None:
    assert operational_mode(6) == "assimilated"
    assert operational_mode(6.01) == "propagated"
    assert operational_mode(24) == "propagated"
    assert operational_mode(24.01) == "weather_only"


def test_advection_and_local_assimilation() -> None:
    ensemble = np.zeros((8, 8, 8), dtype="float32")
    ensemble[:, 4, 3] = np.linspace(1, 2, 8)
    u = np.full((8, 8), 10_000 / 3600, dtype="float32")
    v = np.zeros((8, 8), dtype="float32")

    advected = advect_ensemble(ensemble, u, v, resolution_m=10_000, hours=1)
    assert advected[:, 4, 4].mean() == pytest.approx(ensemble[:, 4, 3].mean(), rel=1e-5)

    observation = RadarObservation(4, 4, 5.0, 0.1, 0.0, 0.0)
    updated, influence = assimilate_localised(advected, [observation], radius_cells=2)
    assert updated[:, 4, 4].mean() > advected[:, 4, 4].mean()
    assert influence[4, 4] > influence[0, 0]


def test_initial_ensemble_is_reproducible_and_nonnegative() -> None:
    observation = RadarObservation(20, 20, 2.0, 0.2, 0.0, 0.0)
    first = initialise_ensemble((41, 41), [observation], members=50, seed=42)
    second = initialise_ensemble((41, 41), [observation], members=50, seed=42)

    assert np.array_equal(first, second)
    assert np.all(first >= 0)
    assert first[:, 20, 20].mean() > first[:, 0, 0].mean()


def test_ecmwf_cycle_request_covers_96_hours() -> None:
    cycle = normalise_cycle("2026-07-18T00:00:00Z")
    requests = open_data_requests(cycle)

    assert len(requests) == 2
    assert requests[0]["step"] == list(range(0, 97, 6))
    assert requests[0]["levtype"] == "sfc"
    assert requests[1]["levtype"] == "pl"
    assert requests[1]["levelist"] == [1000, 925, 850, 700]
    assert cycle == datetime(2026, 7, 18, tzinfo=timezone.utc)
