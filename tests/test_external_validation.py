import pytest

from birdcast_uk.external_validation import evaluate_external_vpts, hourly_vpts_observations


def test_evaluate_external_vpts_scores_matching_hours():
    report = evaluate_external_vpts(
        observations=[
            {
                "time_utc": "2026-07-11T00:00:00Z",
                "mtr_birds_km_h": 2.0,
                "vid_birds_per_km2": 1.0,
                "bird_u_ms": 3.0,
                "bird_v_ms": 4.0,
            }
        ],
        predictions=[
            {
                "time_utc": "2026-07-11T00:00:00.000000000",
                "mtr_birds_km_h": 3.0,
                "vid_birds_per_km2": 1.5,
                "bird_u_ms": 2.0,
                "bird_v_ms": 5.0,
            }
        ],
        site={"radar": "frabb"},
        model={"family": "gamm"},
    )

    assert report["validation_class"] == "external_spatial_transfer"
    assert report["matched_hour_count"] == 1
    assert report["metrics"]["mtr_birds_km_h"] == {
        "count": 1,
        "observed_mean": 2.0,
        "modelled_mean": 3.0,
        "bias": 1.0,
        "mae": 1.0,
        "rmse": 1.0,
    }


def test_hourly_vectors_ignore_profiles_with_missing_vector_inputs() -> None:
    observations = hourly_vpts_observations(
        [
            {
                "radar": "frabb",
                "datetime": "2026-07-11T00:05:00Z",
                "mtr": 2.0,
                "vid": 1.0,
                "ground_speed_ms": 10.0,
                "direction_deg": 90.0,
            },
            {
                "radar": "frabb",
                "datetime": "2026-07-11T00:15:00Z",
                "mtr": 4.0,
                "vid": 2.0,
                "ground_speed_ms": 20.0,
                "direction_deg": None,
            },
        ]
    )

    assert observations[0]["profile_count"] == 2
    assert observations[0]["bird_u_ms"] == pytest.approx(10.0)
    assert observations[0]["bird_v_ms"] == pytest.approx(0.0, abs=1e-12)
