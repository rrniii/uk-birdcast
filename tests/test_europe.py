from __future__ import annotations

import csv
import importlib.util
import json
import time
from io import BytesIO
from pathlib import Path

import pytest

from birdcast_uk.archive import VptsObject
from birdcast_uk.cli import build_parser
from birdcast_uk.europe import (
    build_aloft_cohort,
    build_europe_manifest,
    install_europe_static_site,
    iter_aloft_coverage,
    publish_europe_prediction_partitions,
    publish_europe_predictions,
    read_jsonl_record,
    stream_aloft_chunk,
    stream_aloft_hourly,
    write_aloft_chunk_manifest,
)
from birdcast_uk.europe_fidelity import verify_aloft_chunk, verify_training_input_policy


class ChunkOnlyResponse(BytesIO):
    def __init__(self, value: bytes, headers: dict[str, str] | None = None) -> None:
        super().__init__(value)
        self.headers = headers or {}

    def read(self, size: int = -1) -> bytes:
        assert size >= 0, "streaming code attempted a full-body read"
        return super().read(size)


def opener(payload: str, headers: dict[str, str] | None = None):
    def open_url(_url: str, **_kwargs):
        return ChunkOnlyResponse(payload.encode(), headers)

    return open_url


def test_coverage_is_streamed_and_cohort_is_locked_before_scoring() -> None:
    coverage = (
        "directory,file_count\n"
        "baltrad/hdf5/bejab/2025/01/01,288\n"
        "baltrad/hdf5/bejab/2025/01/02,288\n"
        "uva/hdf5/bejab/2025/01/01,288\n"
        "baltrad/hdf5/nlhrw/2025/01/01,288\n"
    )
    objects = list(iter_aloft_coverage(opener=opener(coverage)))
    cohort = build_aloft_cohort(objects, minimum_training_days=2, minimum_transfer_days=1)

    assert [(obj.radar, obj.day) for obj in objects] == [
        ("bejab", "20250101"),
        ("bejab", "20250102"),
        ("nlhrw", "20250101"),
    ]
    assert cohort[0].role == "training"
    assert cohort[1].role == "transfer-validation"


def test_daily_vpts_is_streamed_to_hourly_rows_without_raw_persistence() -> None:
    source = (
        "radar,datetime,height,ff,dd,gap,dens,dbz,radar_latitude,radar_longitude\n"
        "bejab,2026-07-01T00:00:00Z,200,10,90,FALSE,2,-10,51.1,3.1\n"
        "bejab,2026-07-01T00:05:00Z,200,8,0,FALSE,1,-10,51.1,3.1\n"
        "bejab,2026-07-01T00:00:00Z,400,10,90,FALSE,2,-10,51.1,3.1\n"
        "bejab,2026-07-01T00:05:00Z,400,8,0,FALSE,1,-10,51.1,3.1\n"
    )
    obj = VptsObject("baltrad", "bejab", "20260701", "https://example/vpts.csv")
    rows, audit = stream_aloft_hourly(
        obj,
        opener=opener(source, {"ETag": '"abc"', "Last-Modified": "today"}),
    )

    assert len(rows) == 1
    assert rows[0]["source"] == "aloft-baltrad"
    assert rows[0]["pulse"] == "aloft"
    assert rows[0]["profile_count"] == 2
    assert rows[0]["mean_vid_birds_per_km2"] == 0.6
    assert rows[0]["bird_u_ms"] is not None
    assert audit.row_count == 4
    assert audit.profile_count == 2
    assert audit.hourly_row_count == 1
    assert len(audit.sha256) == 64
    assert audit.availability == "available"


def test_missing_advertised_vpts_is_recorded_as_unavailable_not_zero() -> None:
    from urllib.error import HTTPError

    def missing(_url: str, **_kwargs):
        raise HTTPError(_url, 404, "missing", None, None)

    rows, audit = stream_aloft_hourly(
        VptsObject("baltrad", "bejab", "20260701", "https://example/missing.csv"), opener=missing
    )

    assert rows == []
    assert audit.availability == "unavailable"
    assert audit.unavailable_reason == "source_vpts_object_not_found"


def test_empty_or_malformed_aloft_csv_is_rejected() -> None:
    obj = VptsObject("baltrad", "bejab", "20260701", "https://example/empty.csv")
    with pytest.raises(ValueError, match="missing required columns"):
        stream_aloft_hourly(obj, opener=opener(""))
    with pytest.raises(ValueError, match="no data rows"):
        stream_aloft_hourly(obj, opener=opener("datetime,height,dens\n"))


def test_stream_deadline_prevents_a_stuck_source_read() -> None:
    def slow(_url: str, **_kwargs):
        time.sleep(0.05)
        return ChunkOnlyResponse(b"")

    with pytest.raises(TimeoutError, match="wall-clock deadline"):
        stream_aloft_hourly(
            VptsObject("baltrad", "bejab", "20260701", "https://example/slow.csv"),
            opener=slow,
            timeout_seconds=0.01,
        )


def test_chunk_retries_transient_transport_errors_but_denies_a_persistent_one(
    tmp_path: Path,
) -> None:
    from urllib.error import URLError

    source = (
        "radar,datetime,height,ff,dd,gap,dens,dbz,radar_latitude,radar_longitude\n"
        "bejab,2026-07-01T00:00:00Z,200,10,90,FALSE,2,-10,51.1,3.1\n"
    )
    chunk = {
        "source": "baltrad",
        "radar": "bejab",
        "year": "2026",
        "month": "07",
        "role": "training",
        "days": ["20260701"],
    }
    calls = 0

    def flaky(_url: str, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise URLError("temporary TLS failure")
        return ChunkOnlyResponse(source.encode())

    result = stream_aloft_chunk(
        chunk,
        output_root=tmp_path,
        public_base_url="https://example",
        opener=flaky,
        retry_delay_seconds=0,
    )
    assert result["hourly_row_count"] == 1
    assert calls == 2

    def permanently_unreachable(_url: str, **_kwargs):
        raise URLError("still unavailable")

    with pytest.raises(RuntimeError, match="failed after 2 attempts"):
        stream_aloft_chunk(
            chunk,
            output_root=tmp_path / "failed",
            public_base_url="https://example",
            opener=permanently_unreachable,
            retry_attempts=2,
            retry_delay_seconds=0,
        )


def test_month_chunks_are_restartable_derived_partitions(tmp_path: Path) -> None:
    objects = [
        VptsObject("baltrad", "bejab", "20260701", "https://example/1.csv"),
        VptsObject("baltrad", "bejab", "20260702", "https://example/2.csv"),
    ]
    cohort = {"entries": [{"source": "baltrad", "radar": "bejab", "role": "training"}]}
    result = write_aloft_chunk_manifest(objects, cohort=cohort, output=tmp_path / "chunks.jsonl")
    chunk = read_jsonl_record(tmp_path / "chunks.jsonl", 0)

    assert result["chunk_count"] == 1
    assert chunk["days"] == ["20260701", "20260702"]
    assert chunk["role"] == "training"


def test_chunk_manifest_can_be_limited_to_the_published_model_year(tmp_path: Path) -> None:
    objects = [
        VptsObject("baltrad", "bejab", "20250713", "https://example/old.csv"),
        VptsObject("baltrad", "bejab", "20250714", "https://example/first.csv"),
        VptsObject("baltrad", "bejab", "20260713", "https://example/last.csv"),
        VptsObject("baltrad", "bejab", "20260714", "https://example/new.csv"),
    ]
    cohort = {"entries": [{"source": "baltrad", "radar": "bejab", "role": "training"}]}
    result = write_aloft_chunk_manifest(
        objects,
        cohort=cohort,
        output=tmp_path / "chunks.jsonl",
        start_day="2025-07-14",
        end_day="2026-07-13",
    )

    assert result["source_object_count"] == 2
    assert [
        record["days"]
        for record in [
            read_jsonl_record(tmp_path / "chunks.jsonl", 0),
            read_jsonl_record(tmp_path / "chunks.jsonl", 1),
        ]
    ] == [["20250714"], ["20260713"]]


def test_fidelity_restreams_chunk_and_rejects_a_changed_derivative(tmp_path: Path) -> None:
    source = (
        "radar,datetime,height,ff,dd,gap,dens,dbz,radar_latitude,radar_longitude\n"
        "bejab,2026-07-01T00:00:00Z,200,10,90,FALSE,2,-10,51.1,3.1\n"
        "bejab,2026-07-01T00:00:00Z,400,10,90,FALSE,2,-10,51.1,3.1\n"
    )
    chunk = {
        "source": "baltrad",
        "radar": "bejab",
        "year": "2026",
        "month": "07",
        "role": "training",
        "days": ["20260701"],
    }
    stream_aloft_chunk(
        chunk, output_root=tmp_path, public_base_url="https://example", opener=opener(source)
    )

    result = verify_aloft_chunk(
        chunk, hourly_root=tmp_path, public_base_url="https://example", opener=opener(source)
    )
    assert result["status"] == "passed"
    assert result["raw_source_persisted"] is False

    pyarrow = __import__("pyarrow")
    parquet = __import__("pyarrow.parquet", fromlist=["read_table", "write_table"])
    path = tmp_path / "source=baltrad" / "year=2026" / "month=07" / "bejab.parquet"
    changed = parquet.read_table(path).to_pylist()
    changed[0]["mean_vid_birds_per_km2"] = 999.0
    parquet.write_table(pyarrow.Table.from_pylist(changed), path)
    try:
        verify_aloft_chunk(
            chunk, hourly_root=tmp_path, public_base_url="https://example", opener=opener(source)
        )
    except ValueError as error:
        assert "hourly reconstruction mismatch" in str(error)
    else:
        raise AssertionError("changed hourly derivative must be denied")


def test_fidelity_retries_transient_transport_errors_before_comparing(tmp_path: Path) -> None:
    from urllib.error import URLError

    source = (
        "radar,datetime,height,ff,dd,gap,dens,dbz,radar_latitude,radar_longitude\n"
        "bejab,2026-07-01T00:00:00Z,200,10,90,FALSE,2,-10,51.1,3.1\n"
    )
    chunk = {
        "source": "baltrad",
        "radar": "bejab",
        "year": "2026",
        "month": "07",
        "role": "training",
        "days": ["20260701"],
    }
    stream_aloft_chunk(
        chunk, output_root=tmp_path, public_base_url="https://example", opener=opener(source)
    )
    calls = 0

    def flaky(_url: str, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise URLError("temporary TLS reset")
        return ChunkOnlyResponse(source.encode())

    result = verify_aloft_chunk(
        chunk,
        hourly_root=tmp_path,
        public_base_url="https://example",
        opener=flaky,
        retry_delay_seconds=0,
    )
    assert result["status"] == "passed"
    assert calls == 2


def test_fidelity_retries_transient_http_service_unavailable_before_comparing(
    tmp_path: Path,
) -> None:
    from urllib.error import HTTPError

    source = (
        "radar,datetime,height,ff,dd,gap,dens,dbz,radar_latitude,radar_longitude\n"
        "bejab,2026-07-01T00:00:00Z,200,10,90,FALSE,2,-10,51.1,3.1\n"
    )
    chunk = {
        "source": "baltrad",
        "radar": "bejab",
        "year": "2026",
        "month": "07",
        "role": "training",
        "days": ["20260701"],
    }
    stream_aloft_chunk(
        chunk, output_root=tmp_path, public_base_url="https://example", opener=opener(source)
    )
    calls = 0

    def flaky(_url: str, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError(_url, 503, "Service Unavailable", None, None)
        return ChunkOnlyResponse(source.encode())

    result = verify_aloft_chunk(
        chunk,
        hourly_root=tmp_path,
        public_base_url="https://example",
        opener=flaky,
        retry_delay_seconds=0,
    )
    assert result["status"] == "passed"
    assert calls == 2


def test_fidelity_fails_closed_on_missing_source_object(tmp_path: Path) -> None:
    from urllib.error import HTTPError

    source = (
        "radar,datetime,height,ff,dd,gap,dens,dbz,radar_latitude,radar_longitude\n"
        "bejab,2026-07-01T00:00:00Z,200,10,90,FALSE,2,-10,51.1,3.1\n"
    )
    chunk = {
        "source": "baltrad",
        "radar": "bejab",
        "year": "2026",
        "month": "07",
        "role": "training",
        "days": ["20260701"],
    }
    stream_aloft_chunk(
        chunk, output_root=tmp_path, public_base_url="https://example", opener=opener(source)
    )
    calls = 0

    def missing(url: str, **_kwargs):
        nonlocal calls
        calls += 1
        raise HTTPError(url, 404, "Not Found", None, None)

    # The low-level stream represents a missing advertised object as an empty
    # profile, so the fidelity layer must reject it against the persisted rows.
    with pytest.raises(ValueError, match="hourly reconstruction mismatch"):
        verify_aloft_chunk(
            chunk,
            hourly_root=tmp_path,
            public_base_url="https://example",
            opener=missing,
            retry_delay_seconds=0,
        )
    assert calls == 1


def test_fidelity_reads_hive_partition_without_merging_source_column(tmp_path: Path) -> None:
    source = (
        "radar,datetime,height,ff,dd,gap,dens,dbz,radar_latitude,radar_longitude\n"
        "bejab,2026-07-01T00:00:00Z,200,10,90,FALSE,2,-10,51.1,3.1\n"
    )
    chunk = {
        "source": "baltrad",
        "radar": "bejab",
        "year": "2026",
        "month": "07",
        "role": "training",
        "days": ["20260701"],
    }
    stream_aloft_chunk(
        chunk, output_root=tmp_path, public_base_url="https://example", opener=opener(source)
    )

    result = verify_aloft_chunk(
        chunk, hourly_root=tmp_path, public_base_url="https://example", opener=opener(source)
    )
    assert result["hourly_row_count"] == 1


def test_fidelity_denies_a_partition_from_a_different_release(tmp_path: Path) -> None:
    source = (
        "radar,datetime,height,ff,dd,gap,dens,dbz,radar_latitude,radar_longitude\n"
        "bejab,2026-07-01T00:00:00Z,200,10,90,FALSE,2,-10,51.1,3.1\n"
    )
    chunk = {
        "source": "baltrad",
        "radar": "bejab",
        "year": "2026",
        "month": "07",
        "role": "training",
        "days": ["20260701"],
    }
    stream_aloft_chunk(
        chunk,
        output_root=tmp_path,
        public_base_url="https://example",
        opener=opener(source),
        release_id="release-a",
    )
    try:
        verify_aloft_chunk(
            chunk,
            hourly_root=tmp_path,
            public_base_url="https://example",
            opener=opener(source),
            release_id="release-b",
        )
    except ValueError as error:
        assert "declared Europe release" in str(error)
    else:
        raise AssertionError("a partition from another release must be denied")

    result = stream_aloft_chunk(
        chunk,
        output_root=tmp_path,
        public_base_url="https://example",
        opener=opener(source),
        release_id="release-b",
    )
    assert result["skipped"] is False
    assert result["release_id"] == "release-b"


def test_training_fidelity_rejects_transfer_and_non_sp_uk_rows(tmp_path: Path) -> None:
    cohort = tmp_path / "cohort.json"
    cohort.write_text(
        json.dumps(
            {
                "entries": [
                    {"source": "baltrad", "radar": "bejab", "role": "training"},
                    {"source": "baltrad", "radar": "nlhrw", "role": "transfer-validation"},
                ]
            }
        ),
        encoding="utf-8",
    )
    training = tmp_path / "training.csv"
    training.write_text(
        "radar,source,pulse,weather_x\nbejab,aloft-baltrad,aloft,1\nchenies,jasmin-uk-sp,sp,2\n",
        encoding="utf-8",
    )
    result = verify_training_input_policy(
        training, cohort_json=cohort, required_predictors=["weather_x"]
    )
    assert result["status"] == "passed"

    training.write_text(
        "radar,source,pulse,weather_x\nnlhrw,aloft-baltrad,aloft,1\n",
        encoding="utf-8",
    )
    try:
        verify_training_input_policy(
            training, cohort_json=cohort, required_predictors=["weather_x"]
        )
    except ValueError as error:
        assert "not permitted" in str(error)
    else:
        raise AssertionError("transfer radar leakage must be denied")


def test_europe_manifest_is_source_explicit_and_not_land_clipped(tmp_path: Path) -> None:
    payload = build_europe_manifest(
        model_id="euro-v1",
        first_time_utc="2020-01-01T00:00:00Z",
        latest_time_utc="2026-07-01T23:00:00Z",
        aloft_radar_count=100,
        uk_sp_radar_count=15,
        grid_asset="archive/grid.json",
        daily_asset_template="archive/{date}.json",
        validation_url="validation.json",
        output=tmp_path / "manifest.json",
    )

    assert payload["reference_scale"] == "aloft-baltrad"
    assert payload["training_sources"] == ["aloft-baltrad", "jasmin-uk-sp"]
    assert payload["support"]["land_mask_applied"] is False
    assert payload["support"]["maximum_distance_km"] == 250.0


def test_prediction_publication_writes_fixed_grid_and_daily_assets(tmp_path: Path) -> None:
    source = tmp_path / "predictions.csv"
    rows = "".join(
        f"2026-07-01T{hour:02d}:00:00Z,3.0,51.0,0.9,10,interpolation,{5 + hour},2,1,2,0.2,0.7\n"
        for hour in range(24)
    )
    source.write_text(
        "time_utc,longitude,latitude,support,nearest_radar_km,prediction_class,"
        "mtr_birds_km_h,vid_birds_per_km2,bird_u_ms,bird_v_ms,"
        "uncertainty_mtr_birds_km_h,uncertainty_vid_birds_per_km2\n" + rows,
        encoding="utf-8",
    )

    result = publish_europe_predictions(
        predictions_csv=source,
        output_root=tmp_path / "out",
        model_id="euro-v1",
        aloft_radar_count=1,
        uk_sp_radar_count=1,
        validation_url="validation.json",
    )

    assert result["day_count"] == 1
    assert result["prediction_row_count"] == 24
    assert result["manifest"]["release_status"] == "research-preview"
    assert result["manifest"]["available_dates"] == ["2026-07-01"]
    day = json.loads((tmp_path / "out/archive/reanalysis/euro-v1/2026-07-01.json").read_text())
    assert len(day["frames"]) == 24
    assert day["frames"][0]["mtr_birds_km_h"] == [5.0]
    assert day["frames"][0]["uncertainty_mtr_birds_km_h"] == [0.2]
    assert day["frames"][0]["uncertainty_vid_birds_per_km2"] == [0.7]
    assert "uncertainty" not in day["frames"][0]


def test_partitioned_prediction_publication_reconciles_every_daily_cell(tmp_path: Path) -> None:
    root = tmp_path / "predictions"
    root.mkdir()
    rows = "".join(
        f"2026-07-01T{hour:02d}:00:00Z,3,51,interpolation,{5 + hour},2,1,2\n" for hour in range(24)
    )
    (root / "prediction_20260701.csv").write_text(
        "time_utc,longitude,latitude,prediction_class,mtr_birds_km_h,vid_birds_per_km2,bird_u_ms,bird_v_ms\n"
        + rows,
        encoding="utf-8",
    )
    result = publish_europe_prediction_partitions(
        predictions_root=root,
        output_root=tmp_path / "out",
        model_id="euro-v1",
        aloft_radar_count=1,
        uk_sp_radar_count=1,
        validation_url="validation.json",
    )
    assert result["prediction_row_count"] == 24
    assert result["frame_count"] == 24
    assert result["cell_count"] == 1
    assert result["manifest"]["available_dates"] == ["2026-07-01"]

    (root / "prediction_20260702.csv").write_text(
        "time_utc,longitude,latitude,prediction_class,mtr_birds_km_h\n"
        "2026-07-02T00:00:00Z,4,51,interpolation,5\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="inconsistent"):
        publish_europe_prediction_partitions(
            predictions_root=root,
            output_root=tmp_path / "retry",
            model_id="euro-v1",
            aloft_radar_count=1,
            uk_sp_radar_count=1,
            validation_url="validation.json",
        )


def test_prediction_publication_rejects_incomplete_or_noncanonical_days(tmp_path: Path) -> None:
    header = "time_utc,longitude,latitude,prediction_class,mtr_birds_km_h\n"
    incomplete = tmp_path / "incomplete.csv"
    incomplete.write_text(
        header
        + "".join(f"2026-07-01T{hour:02d}:00:00Z,3,51,interpolation,5\n" for hour in range(23)),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exactly 24 canonical UTC hourly frames"):
        publish_europe_predictions(
            predictions_csv=incomplete,
            output_root=tmp_path / "incomplete-out",
            model_id="euro-v1",
            aloft_radar_count=1,
            uk_sp_radar_count=1,
            validation_url="validation.json",
        )

    partition_root = tmp_path / "noncanonical"
    partition_root.mkdir()
    timestamps = [f"2026-07-02T{hour:02d}:00:00Z" for hour in range(23)]
    timestamps.append("2026-07-02T23:30:00Z")
    (partition_root / "prediction_20260702.csv").write_text(
        header + "".join(f"{timestamp},3,51,interpolation,5\n" for timestamp in timestamps),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exactly 24 canonical UTC hourly frames"):
        publish_europe_prediction_partitions(
            predictions_root=partition_root,
            output_root=tmp_path / "noncanonical-out",
            model_id="euro-v1",
            aloft_radar_count=1,
            uk_sp_radar_count=1,
            validation_url="validation.json",
        )


@pytest.mark.parametrize(
    "model_id",
    ("../escape", "/tmp/escape", "Euro-v1", "model id", ".", "euro/v1"),
)
def test_europe_model_id_is_a_strict_safe_slug(tmp_path: Path, model_id: str) -> None:
    output = tmp_path / "manifest.json"

    with pytest.raises(ValueError, match="model_id"):
        build_europe_manifest(
            model_id=model_id,
            first_time_utc="2026-07-01T00:00:00Z",
            latest_time_utc="2026-07-01T23:00:00Z",
            aloft_radar_count=1,
            uk_sp_radar_count=1,
            grid_asset="archive/reanalysis/euro-v1/grid.json",
            daily_asset_template="archive/reanalysis/euro-v1/{date}.json",
            validation_url="validation.json",
            output=output,
        )

    assert not output.exists()


def test_monolithic_prediction_publication_reconciles_every_cell(tmp_path: Path) -> None:
    header = (
        "time_utc,longitude,latitude,prediction_class,mtr_birds_km_h,"
        "vid_birds_per_km2,bird_u_ms,bird_v_ms\n"
    )
    rows = [
        f"2026-07-01T{hour:02d}:00:00Z,{longitude},51,interpolation,5,2,1,2\n"
        for hour in range(24)
        for longitude in (3, 4)
    ]
    cases = {
        "missing": (rows[:-1], "frame is incomplete"),
        "duplicate": ([*rows, rows[0]], "duplicate Europe prediction cell"),
        "unsupported": (
            [rows[0].replace("interpolation", "unsupported"), *rows[1:]],
            "unsupported",
        ),
        "invalid": (
            [rows[0].replace(",3,51,", ",bad,51,"), *rows[1:]],
            "invalid Europe prediction coordinate",
        ),
    }

    for name, (case_rows, message) in cases.items():
        source = tmp_path / f"{name}.csv"
        source.write_text(header + "".join(case_rows), encoding="utf-8")
        output_root = tmp_path / f"{name}-out"
        with pytest.raises(ValueError, match=message):
            publish_europe_predictions(
                predictions_csv=source,
                output_root=output_root,
                model_id=f"euro-{name}",
                aloft_radar_count=1,
                uk_sp_radar_count=1,
                validation_url="validation.json",
            )
        assert not (output_root / "archive/reanalysis" / f"euro-{name}").exists()


def test_europe_model_release_is_immutable(tmp_path: Path) -> None:
    source = tmp_path / "predictions.csv"
    source.write_text(
        "time_utc,longitude,latitude,prediction_class,mtr_birds_km_h\n"
        + "".join(
            f"2026-07-01T{hour:02d}:00:00Z,3,51,interpolation,{hour + 1}\n" for hour in range(24)
        ),
        encoding="utf-8",
    )
    output_root = tmp_path / "out"
    publish_europe_predictions(
        predictions_csv=source,
        output_root=output_root,
        model_id="euro-immutable",
        aloft_radar_count=1,
        uk_sp_radar_count=1,
        validation_url="validation.json",
    )
    daily_path = output_root / "archive/reanalysis/euro-immutable/2026-07-01.json"
    manifest_path = output_root / "latest/reanalysis.json"
    original_daily = daily_path.read_bytes()
    original_manifest = manifest_path.read_bytes()

    with pytest.raises(FileExistsError, match="immutable"):
        publish_europe_predictions(
            predictions_csv=source,
            output_root=output_root,
            model_id="euro-immutable",
            aloft_radar_count=1,
            uk_sp_radar_count=1,
            validation_url="validation.json",
        )

    assert daily_path.read_bytes() == original_daily
    assert manifest_path.read_bytes() == original_manifest


def test_failed_partition_publication_discards_staging_and_preserves_latest(tmp_path: Path) -> None:
    predictions = tmp_path / "predictions"
    predictions.mkdir()
    header = "time_utc,longitude,latitude,prediction_class,mtr_birds_km_h\n"
    (predictions / "prediction_20260701.csv").write_text(
        header
        + "".join(f"2026-07-01T{hour:02d}:00:00Z,3,51,interpolation,5\n" for hour in range(24)),
        encoding="utf-8",
    )
    (predictions / "prediction_20260702.csv").write_text(
        header
        + "".join(f"2026-07-02T{hour:02d}:00:00Z,3,51,interpolation,5\n" for hour in range(23)),
        encoding="utf-8",
    )
    output_root = tmp_path / "out"
    latest = output_root / "latest/reanalysis.json"
    latest.parent.mkdir(parents=True)
    latest.write_text('{"sentinel":true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="exactly 24 canonical UTC hourly frames"):
        publish_europe_prediction_partitions(
            predictions_root=predictions,
            output_root=output_root,
            model_id="euro-transaction",
            aloft_radar_count=1,
            uk_sp_radar_count=1,
            validation_url="validation.json",
        )

    archive_root = output_root / "archive/reanalysis"
    assert not (archive_root / "euro-transaction").exists()
    assert not list(archive_root.glob(".euro-transaction.*.staging"))
    assert latest.read_text(encoding="utf-8") == '{"sentinel":true}\n'


def test_europe_page_is_separate_and_installable(tmp_path: Path) -> None:
    result = install_europe_static_site(tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    javascript = (tmp_path / "app.js").read_text(encoding="utf-8")

    assert result["ok"] is True
    assert "European Bird Maps" in html
    assert "/birdcast-euro/data" in (tmp_path / "config.json").read_text()
    assert "daily_template" in javascript
    assert (tmp_path / "radar-marker.svg").is_file()


def test_europe_cli_contracts_are_registered() -> None:
    parser = build_parser()
    args = parser.parse_args(["europe", "cohort", "--output", "cohort.json"])
    assert args.minimum_training_days == 365
    args = parser.parse_args(
        [
            "europe",
            "stream-day",
            "--radar",
            "bejab",
            "--day",
            "2026-07-01",
            "--output",
            "day.parquet",
        ]
    )
    assert args.radar == "bejab"
    args = parser.parse_args(
        [
            "coastal",
            "build-relative-flow",
            "--training-csv",
            "training.csv",
            "--cohort",
            "cohort.json",
            "--output-root",
            "out",
        ]
    )
    assert args.training_csv == "training.csv"


def test_europe_fitter_has_source_and_transfer_controls() -> None:
    script = (Path(__file__).parents[1] / "scripts/fit_europe_gamm.R").read_text(encoding="utf-8")
    assert "stats::relevel(factor(data$source)" in script
    assert "site_equal_weight" in script
    fit_target = script.split("fit_target <- function", 1)[1].split(
        "predict_target <- function", 1
    )[0]
    assert "site_counts <- table(subset$radar)" in fit_target
    assert "site_counts <- table(data$radar)" not in script
    assert "leave_one_" in script
    assert "transfer_validation" in script
    assert "transfer_validation_radar_count" in script
    assert "frame$.row_id <- seq_len(nrow(frame))" in script
    assert "model_time_terms = if (!is.null(spec$time_terms))" in script
    assert "time_index_hours" in script
    assert 'data$pulse == "lp"' in script
    assert "observed_threshold <-" in script
    assert "predicted_threshold <-" in script
    assert "predicted_event <- predicted >= predicted_threshold" in script
    probe = (Path(__file__).parents[1] / "scripts/probe_europe_time_resolution.R").read_text()
    assert "discrete=space_time_k <= 0" in probe
    sbatch = (
        Path(__file__).parents[1] / "deploy/slurm/birdcast-euro-aloft-stream.sbatch"
    ).read_text()
    assert "stream-chunk" in sbatch
    assert "radar-month" in sbatch
    assert (
        "--require-pass"
        in (Path(__file__).parents[1] / "deploy/slurm/birdcast-euro-gamm.sbatch").read_text()
    )


def test_europe_batch_jobs_pin_the_declared_release_and_bound_streaming() -> None:
    slurm_dir = Path(__file__).parents[1] / "deploy/slurm"
    scripts = list(slurm_dir.glob("birdcast-euro-*.sbatch"))
    assert scripts
    for script in scripts:
        content = script.read_text(encoding="utf-8")
        assert "BIRDCAST_EURO_ROOT:?" in content, script.name
        assert "BIRDCAST_EURO_PYTHON:?" in content, script.name
        if "-m birdcast_uk.cli" in content:
            assert 'export PYTHONPATH="$BIRDCAST_EURO_ROOT/src' in content, script.name

    stream = (slurm_dir / "birdcast-euro-aloft-stream.sbatch").read_text(encoding="utf-8")
    assert "timeout --kill-after=60s 50m" in stream
    gamm = (slurm_dir / "birdcast-euro-gamm.sbatch").read_text(encoding="utf-8")
    assert "#SBATCH --cpus-per-task=1" in gamm
    assert "#SBATCH --mem=128G" in gamm
    publish = (slurm_dir / "birdcast-euro-publish.sbatch").read_text(encoding="utf-8")
    assert "validate_europe_publication.py" in publish
    assert "BIRDCAST_EURO_PUBLIC_HOST" not in publish
    assert "BIRDCAST_EURO_PUBLIC_USER" not in publish
    assert "BIRDCAST_EURO_PUBLIC_STAGE_ROOT" not in publish
    assert "birdcast-euro-activate.sh" not in publish
    assert "\nrsync " not in publish
    assert "\nssh " not in publish


def test_absolute_europe_promotion_is_retired_and_relative_route_remains() -> None:
    root = Path(__file__).parents[1]
    retired = (
        root / "deploy/scripts/birdcast-euro-object-store-pull.sh",
        root / "deploy/scripts/birdcast-euro-activate.sh",
        root / "deploy/systemd/birdcast-euro-object-store-pull.service",
        root / "deploy/systemd/birdcast-euro-object-store-pull.timer",
        root / "deploy/env/birdcast-euro.env.example",
    )
    for path in retired:
        assert not path.exists(), path

    cloud_env = (root / "deploy/env/birdcast-uk.env.example").read_text(encoding="utf-8")
    assert "BIRDCAST_EURO_OBJECT_STORE" not in cloud_env
    assert "BIRDCAST_EURO_AWS_PROFILE" not in cloud_env
    assert "BIRDCAST_EURO_OBJECT_PREFIX" not in cloud_env
    assert "BIRDCAST_EURO_ARTIFACT_ROOT" not in cloud_env
    assert "BIRDCAST_EURO_STAGE_ROOT" not in cloud_env

    relative = (root / "deploy/scripts/birdcast-coastal-activate.sh").read_text(encoding="utf-8")
    nginx = (root / "deploy/nginx/birdcast-uk.conf").read_text(encoding="utf-8")
    assert "latest/relative-flow.json" in relative
    assert "published-relative-research-product" in relative
    assert "os.replace(sys.argv[1], sys.argv[2])" in relative
    assert "alias /opt/birdcast-euro/artifacts-current/;" in nginx
    assert "return 302 /europe-bird-maps/;" in nginx


def load_script(name: str):
    path = Path(__file__).parents[1] / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validation_uses_site_metrics_and_paired_vector_direction(tmp_path: Path) -> None:
    vectors = tmp_path / "vectors.csv"
    with vectors.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "validation",
                "row_id",
                "radar",
                "time_utc",
                "target",
                "observed",
                "predicted",
            ],
        )
        writer.writeheader()
        for radar, row_id in (("bejab", "1"), ("nlhrw", "2")):
            writer.writerow(
                {
                    "validation": "transfer_validation",
                    "row_id": row_id,
                    "radar": radar,
                    "time_utc": "2026-01-01T00:00:00Z",
                    "target": "bird_u_ms",
                    "observed": 1,
                    "predicted": 1,
                }
            )
            writer.writerow(
                {
                    "validation": "transfer_validation",
                    "row_id": row_id,
                    "radar": radar,
                    "time_utc": "2026-01-01T00:00:00Z",
                    "target": "bird_v_ms",
                    "observed": 1,
                    "predicted": 1,
                }
            )
    folds = []
    for target in ("mtr_birds_km_h", "vid_birds_per_km2"):
        for radar in ("bejab", "nlhrw"):
            folds.append(
                {
                    "target": target,
                    "validation": "transfer_validation",
                    "held_out": radar,
                    "row_count": 30,
                    "log1p_r_squared": 0.4,
                    "top_decile_f1": 0.7,
                }
            )
    metrics = tmp_path / "metrics.json"
    metrics.write_text(
        json.dumps(
            {
                "model_id": "euro-v1",
                "folds": folds,
                "heldout_radar_vectors": str(vectors),
                "transfer_validation_radar_count": 2,
            }
        ),
        encoding="utf-8",
    )
    validator = load_script("validate_europe_gamm.py")
    result = validator.validate(metrics, tmp_path / "validation.json")

    assert result["release_passed"] is True
    assert result["site_equal_metrics"]["median_site_direction_error_deg"] == 0
    assert result["pooled_metrics_may_not_override_site_gates"] is True

    for fold in folds:
        if fold["target"] == "vid_birds_per_km2":
            fold["log1p_r_squared"] = -0.1
    metrics.write_text(
        json.dumps(
            {
                "model_id": "euro-v1",
                "folds": folds,
                "heldout_radar_vectors": str(vectors),
                "transfer_validation_radar_count": 2,
            }
        ),
        encoding="utf-8",
    )
    failed = validator.validate(metrics, tmp_path / "failed-validation.json")
    assert failed["release_passed"] is False
    assert failed["gates"]["each_intensity_target_median_site_log1p_skill_positive"] is False


def test_radar_metadata_comes_from_derived_rows_not_raw_objects(tmp_path: Path) -> None:
    pyarrow = __import__("pyarrow")
    parquet = __import__("pyarrow.parquet", fromlist=["write_table"])
    derived = tmp_path / "aloft.parquet"
    parquet.write_table(
        pyarrow.Table.from_pylist(
            [
                {"radar": "bejab", "latitude": 50.9, "longitude": 3.0},
                {"radar": "bejab", "latitude": 51.1, "longitude": 3.2},
            ]
        ),
        derived,
    )
    uk = tmp_path / "uk.json"
    uk.write_text(
        json.dumps(
            {
                "radars": [
                    {
                        "slug": "chenies",
                        "label": "Chenies",
                        "latitude": 51.69,
                        "longitude": -0.53,
                        "max_range_m": 100000,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    metadata = load_script("build_europe_radar_metadata.py")
    args = type(
        "Args",
        (),
        {
            "aloft_parquet": str(derived),
            "uk_radars": str(uk),
            "overrides": None,
            "maximum_support_km": 250,
            "output": str(tmp_path / "radars.json"),
        },
    )()
    result = metadata.build(args)

    assert result["raw_aloft_persisted"] is False
    assert result["aloft_radar_count"] == 1
    assert result["uk_sp_radar_count"] == 1
    assert result["radars"][0]["country"] == "BE"


def test_training_assembler_keeps_transfer_radars_out_of_fit(tmp_path: Path) -> None:
    pyarrow = __import__("pyarrow")
    parquet = __import__("pyarrow.parquet", fromlist=["write_table"])
    aloft = tmp_path / "aloft.parquet"
    parquet.write_table(
        pyarrow.Table.from_pylist(
            [
                {
                    "radar": "bejab",
                    "time_utc": "2026-01-01T00:00:00Z",
                    "mean_mtr_birds_km_h": 4.0,
                    "mean_vid_birds_per_km2": 2.0,
                    "bird_u_ms": 1.0,
                    "bird_v_ms": 2.0,
                    "profile_count": 12,
                    "rain_suspect_fraction": 0.0,
                    "cohort_role": "training",
                },
                {
                    "radar": "nlhrw",
                    "time_utc": "2026-01-01T00:00:00Z",
                    "mean_mtr_birds_km_h": 5.0,
                    "mean_vid_birds_per_km2": 3.0,
                    "bird_u_ms": 2.0,
                    "bird_v_ms": 1.0,
                    "profile_count": 12,
                    "rain_suspect_fraction": 0.0,
                    "cohort_role": "transfer-validation",
                },
            ]
        ),
        aloft,
    )
    era5 = tmp_path / "era5.parquet"
    parquet.write_table(
        pyarrow.Table.from_pylist(
            [
                # ERA5 Parquet emits UTC timestamps with fractional seconds;
                # the Aloft derivatives use canonical trailing-Z strings.
                {"radar": "bejab", "time_utc": "2026-01-01T00:00:00.000000000", "weather_x": 1.0},
                {"radar": "nlhrw", "time_utc": "2026-01-01T00:00:00.000000000", "weather_x": 2.0},
            ]
        ),
        era5,
    )
    uk = tmp_path / "uk.csv"
    uk.write_text(
        "radar,pulse,time_utc,latitude,longitude,easting_m,northing_m,"
        "mtr_birds_km_h,vid_birds_per_km2,bird_u_ms,bird_v_ms,profile_count,"
        "rain_suspect_fraction,weather_x\n"
        "chenies,sp,2026-01-01T00:00:00Z,51.69,-0.53,1,2,6,3,1,1,12,0,1.5\n",
        encoding="utf-8",
    )
    metadata = tmp_path / "radars.json"
    metadata.write_text(
        json.dumps(
            {
                "radars": [
                    {
                        "radar": "bejab",
                        "latitude": 51.0,
                        "longitude": 3.1,
                        "country": "BE",
                        "network": "baltrad",
                    },
                    {
                        "radar": "nlhrw",
                        "latitude": 52.1,
                        "longitude": 4.8,
                        "country": "NL",
                        "network": "baltrad",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    assembler = load_script("assemble_europe_training.py")
    output = tmp_path / "training.csv"
    validation = tmp_path / "validation.csv"
    args = type(
        "Args",
        (),
        {
            "aloft_parquet": str(aloft),
            "uk_training_csv": str(uk),
            "era5_parquet": str(era5),
            "radar_metadata": str(metadata),
            "validation_output": str(validation),
            "output": str(output),
        },
    )()
    cohort = tmp_path / "cohort.json"
    cohort.write_text(
        json.dumps(
            {
                "entries": [
                    {"radar": "bejab", "role": "training"},
                    {"radar": "nlhrw", "role": "transfer-validation"},
                ]
            }
        ),
        encoding="utf-8",
    )
    args.cohort = str(cohort)
    assembler.assemble(args)

    assert "bejab" in output.read_text(encoding="utf-8")
    assert "chenies" in output.read_text(encoding="utf-8")
    assert "nlhrw" not in output.read_text(encoding="utf-8")
    assert "nlhrw" in validation.read_text(encoding="utf-8")


def test_country_exclusion_writes_a_declared_validation_sensitivity_cohort(tmp_path: Path) -> None:
    training = tmp_path / "training.csv"
    training.write_text(
        "radar,country,mtr_birds_km_h\nbejab,BE,1\nchenies,GBR,2\n",
        encoding="utf-8",
    )
    validation = tmp_path / "validation.csv"
    validation.write_text(
        "radar,country,mtr_birds_km_h\nrobar,RO,30\nbejab,BE,1\nesgld,ES,2\n",
        encoding="utf-8",
    )
    base_spec = tmp_path / "model-spec.json"
    base_spec.write_text(
        json.dumps(
            {
                "model_id": "europe-baseline",
                "training_csv": str(training),
                "validation_csv": str(validation),
            }
        ),
        encoding="utf-8",
    )
    module = load_script("prepare_europe_country_exclusion.py")
    output_validation = tmp_path / "non-ro.csv"
    output_spec = tmp_path / "non-ro-spec.json"
    audit = module.prepare(
        model_spec=base_spec,
        excluded_country={"RO"},
        output_validation_csv=output_validation,
        output_spec=output_spec,
        output_audit=tmp_path / "audit.json",
        model_id="europe-non-ro",
    )

    assert "robar" not in output_validation.read_text(encoding="utf-8")
    assert audit["validation_rows_removed"] == 1
    assert audit["training_excluded_country_rows"]["RO"]["row_count"] == 0
    restricted = json.loads(output_spec.read_text(encoding="utf-8"))
    assert restricted["cohort_restriction"]["publication_eligible"] is False
    assert restricted["cohort_restriction"]["validation_radars_removed"] == ["robar"]


def test_country_inclusion_restricts_both_gamm_inputs(tmp_path: Path) -> None:
    training = tmp_path / "training.csv"
    training.write_text(
        "radar,country,mtr_birds_km_h\nbejab,BE,1\nromed,RO,30\nchenies,GBR,2\n",
        encoding="utf-8",
    )
    validation = tmp_path / "validation.csv"
    validation.write_text(
        "radar,country,mtr_birds_km_h\ndksam,DK,3\nrobar,RO,30\nesgld,ES,2\n",
        encoding="utf-8",
    )
    base_spec = tmp_path / "model-spec.json"
    base_spec.write_text(
        json.dumps(
            {
                "model_id": "europe-baseline",
                "training_csv": str(training),
                "validation_csv": str(validation),
            }
        ),
        encoding="utf-8",
    )
    module = load_script("prepare_europe_country_exclusion.py")
    output_training = tmp_path / "regional-training.csv"
    output_validation = tmp_path / "regional-validation.csv"
    audit = module.prepare_inclusion(
        model_spec=base_spec,
        included_country={"BE", "DK", "ES", "GBR"},
        output_training_csv=output_training,
        output_validation_csv=output_validation,
        output_spec=tmp_path / "regional-spec.json",
        output_audit=tmp_path / "audit.json",
        model_id="regional-v1",
    )

    assert "romed" not in output_training.read_text(encoding="utf-8")
    assert "robar" not in output_validation.read_text(encoding="utf-8")
    assert audit["training_rows_retained"] == 2
    assert audit["validation_radars_retained"] == ["dksam", "esgld"]


def test_uk_coastal_corridor_uses_natural_earth_distance_and_overlap(tmp_path: Path) -> None:
    training = tmp_path / "training.csv"
    training.write_text(
        "radar,country,source,latitude,longitude\n"
        "frcoast,FR,aloft-baltrad,50.0,1.0\n"
        "frcoast2,FR,aloft-baltrad,50.5,1.0\n"
        "frcoast3,FR,aloft-baltrad,51.0,1.0\n"
        "frfar,FR,aloft-baltrad,43.0,1.0\n"
        "ukcoast,GBR,jasmin-uk-sp,50.5,-1.0\n"
        "ukcoast2,GBR,jasmin-uk-sp,51.0,-1.0\n"
        "ukcoast3,GBR,jasmin-uk-sp,51.5,-1.0\n"
        "ukfar,GBR,jasmin-uk-sp,58.0,-4.0\n",
        encoding="utf-8",
    )
    spec = tmp_path / "model-spec.json"
    spec.write_text(
        json.dumps({"model_id": "base", "training_csv": str(training)}), encoding="utf-8"
    )
    boundaries = tmp_path / "boundaries.geojson"
    boundaries.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"ADM0_A3": "GBR"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[-2, 49], [0, 49], [0, 52], [-2, 52], [-2, 49]]],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    module = load_script("prepare_uk_coastal_corridor.py")
    cohort = module.prepare(
        model_spec=spec,
        boundaries=boundaries,
        continental_countries={"FR"},
        coastline_distance_limit_km=350,
        overlap_distance_limit_km=350,
        output_training_csv=tmp_path / "corridor.csv",
        output_cohort=tmp_path / "cohort.json",
        output_spec=tmp_path / "corridor-spec.json",
        model_id="corridor-v1",
    )

    assert [item["radar"] for item in cohort["continental_radars"]] == [
        "frcoast",
        "frcoast2",
        "frcoast3",
    ]
    assert [item["radar"] for item in cohort["uk_radars"]] == ["ukcoast", "ukcoast2", "ukcoast3"]
    assert cohort["raw_input_persisted"] is False
    r_script = (Path(__file__).parents[1] / "scripts/probe_uk_coastal_corridor.R").read_text(
        encoding="utf-8"
    )
    assert "leave_one_continental_radar_out" in r_script
    assert "train_continental_test_uk" in r_script
    assert "train_uk_test_continental" in r_script
