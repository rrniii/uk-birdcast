from __future__ import annotations

from io import BytesIO
import csv
import importlib.util
import json
from pathlib import Path
import time

import pytest

from birdcast_uk.archive import VptsObject
from birdcast_uk.cli import build_parser
from birdcast_uk.europe import (
    build_aloft_cohort,
    build_europe_manifest,
    install_europe_static_site,
    iter_aloft_coverage,
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
    cohort = build_aloft_cohort(objects, minimum_training_days=2)

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


def test_chunk_retries_transient_transport_errors_but_denies_a_persistent_one(tmp_path: Path) -> None:
    from urllib.error import URLError

    source = (
        "radar,datetime,height,ff,dd,gap,dens,dbz,radar_latitude,radar_longitude\n"
        "bejab,2026-07-01T00:00:00Z,200,10,90,FALSE,2,-10,51.1,3.1\n"
    )
    chunk = {"source": "baltrad", "radar": "bejab", "year": "2026", "month": "07", "role": "training", "days": ["20260701"]}
    calls = 0

    def flaky(_url: str, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise URLError("temporary TLS failure")
        return ChunkOnlyResponse(source.encode())

    result = stream_aloft_chunk(
        chunk, output_root=tmp_path, public_base_url="https://example", opener=flaky, retry_delay_seconds=0
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
        objects, cohort=cohort, output=tmp_path / "chunks.jsonl", start_day="2025-07-14", end_day="2026-07-13"
    )

    assert result["source_object_count"] == 2
    assert [record["days"] for record in [read_jsonl_record(tmp_path / "chunks.jsonl", 0), read_jsonl_record(tmp_path / "chunks.jsonl", 1)]] == [["20250714"], ["20260713"]]


def test_fidelity_restreams_chunk_and_rejects_a_changed_derivative(tmp_path: Path) -> None:
    source = (
        "radar,datetime,height,ff,dd,gap,dens,dbz,radar_latitude,radar_longitude\n"
        "bejab,2026-07-01T00:00:00Z,200,10,90,FALSE,2,-10,51.1,3.1\n"
        "bejab,2026-07-01T00:00:00Z,400,10,90,FALSE,2,-10,51.1,3.1\n"
    )
    chunk = {"source": "baltrad", "radar": "bejab", "year": "2026", "month": "07", "role": "training", "days": ["20260701"]}
    stream_aloft_chunk(chunk, output_root=tmp_path, public_base_url="https://example", opener=opener(source))

    result = verify_aloft_chunk(chunk, hourly_root=tmp_path, public_base_url="https://example", opener=opener(source))
    assert result["status"] == "passed"
    assert result["raw_source_persisted"] is False

    pyarrow = __import__("pyarrow")
    parquet = __import__("pyarrow.parquet", fromlist=["read_table", "write_table"])
    path = tmp_path / "source=baltrad" / "year=2026" / "month=07" / "bejab.parquet"
    changed = parquet.read_table(path).to_pylist()
    changed[0]["mean_vid_birds_per_km2"] = 999.0
    parquet.write_table(pyarrow.Table.from_pylist(changed), path)
    try:
        verify_aloft_chunk(chunk, hourly_root=tmp_path, public_base_url="https://example", opener=opener(source))
    except ValueError as error:
        assert "hourly reconstruction mismatch" in str(error)
    else:
        raise AssertionError("changed hourly derivative must be denied")


def test_fidelity_reads_hive_partition_without_merging_source_column(tmp_path: Path) -> None:
    source = (
        "radar,datetime,height,ff,dd,gap,dens,dbz,radar_latitude,radar_longitude\n"
        "bejab,2026-07-01T00:00:00Z,200,10,90,FALSE,2,-10,51.1,3.1\n"
    )
    chunk = {"source": "baltrad", "radar": "bejab", "year": "2026", "month": "07", "role": "training", "days": ["20260701"]}
    stream_aloft_chunk(chunk, output_root=tmp_path, public_base_url="https://example", opener=opener(source))

    result = verify_aloft_chunk(chunk, hourly_root=tmp_path, public_base_url="https://example", opener=opener(source))
    assert result["hourly_row_count"] == 1


def test_fidelity_denies_a_partition_from_a_different_release(tmp_path: Path) -> None:
    source = (
        "radar,datetime,height,ff,dd,gap,dens,dbz,radar_latitude,radar_longitude\n"
        "bejab,2026-07-01T00:00:00Z,200,10,90,FALSE,2,-10,51.1,3.1\n"
    )
    chunk = {"source": "baltrad", "radar": "bejab", "year": "2026", "month": "07", "role": "training", "days": ["20260701"]}
    stream_aloft_chunk(
        chunk, output_root=tmp_path, public_base_url="https://example", opener=opener(source), release_id="release-a"
    )
    try:
        verify_aloft_chunk(
            chunk, hourly_root=tmp_path, public_base_url="https://example", opener=opener(source), release_id="release-b"
        )
    except ValueError as error:
        assert "declared Europe release" in str(error)
    else:
        raise AssertionError("a partition from another release must be denied")

    result = stream_aloft_chunk(
        chunk, output_root=tmp_path, public_base_url="https://example", opener=opener(source), release_id="release-b"
    )
    assert result["skipped"] is False
    assert result["release_id"] == "release-b"


def test_training_fidelity_rejects_transfer_and_non_sp_uk_rows(tmp_path: Path) -> None:
    cohort = tmp_path / "cohort.json"
    cohort.write_text(json.dumps({"entries": [
        {"source": "baltrad", "radar": "bejab", "role": "training"},
        {"source": "baltrad", "radar": "nlhrw", "role": "transfer-validation"},
    ]}), encoding="utf-8")
    training = tmp_path / "training.csv"
    training.write_text(
        "radar,source,pulse,weather_x\nbejab,aloft-baltrad,aloft,1\nchenies,jasmin-uk-sp,sp,2\n",
        encoding="utf-8",
    )
    result = verify_training_input_policy(training, cohort_json=cohort, required_predictors=["weather_x"])
    assert result["status"] == "passed"

    training.write_text(
        "radar,source,pulse,weather_x\nnlhrw,aloft-baltrad,aloft,1\n",
        encoding="utf-8",
    )
    try:
        verify_training_input_policy(training, cohort_json=cohort, required_predictors=["weather_x"])
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
    source.write_text(
        "time_utc,longitude,latitude,support,nearest_radar_km,prediction_class,"
        "mtr_birds_km_h,vid_birds_per_km2,bird_u_ms,bird_v_ms,"
        "uncertainty_mtr_birds_km_h\n"
        "2026-07-01T00:00:00Z,3.0,51.0,0.9,10,interpolation,5,2,1,2,0.2\n"
        "2026-07-01T01:00:00Z,3.0,51.0,0.9,10,interpolation,6,3,2,1,0.3\n",
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
    assert result["manifest"]["release_status"] == "research-preview"
    day = json.loads((tmp_path / "out/archive/reanalysis/euro-v1/2026-07-01.json").read_text())
    assert len(day["frames"]) == 2
    assert day["frames"][0]["mtr_birds_km_h"] == [5.0]


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
        ["europe", "stream-day", "--radar", "bejab", "--day", "2026-07-01", "--output", "day.parquet"]
    )
    assert args.radar == "bejab"


def test_europe_fitter_has_source_and_transfer_controls() -> None:
    script = (Path(__file__).parents[1] / "scripts/fit_europe_gamm.R").read_text(encoding="utf-8")
    assert "stats::relevel(factor(data$source)" in script
    assert "site_equal_weight" in script
    assert "leave_one_" in script
    assert "transfer_validation" in script
    assert 'model_time_terms = "none"' in script
    assert 'data$pulse == "lp"' in script
    sbatch = (Path(__file__).parents[1] / "deploy/slurm/birdcast-euro-aloft-stream.sbatch").read_text()
    assert "stream-chunk" in sbatch
    assert "radar-month" in sbatch
    assert "--require-pass" in (
        Path(__file__).parents[1] / "deploy/slurm/birdcast-euro-gamm.sbatch"
    ).read_text()


def test_europe_batch_jobs_pin_the_declared_release_and_bound_streaming() -> None:
    slurm_dir = Path(__file__).parents[1] / "deploy/slurm"
    scripts = list(slurm_dir.glob("birdcast-euro-*.sbatch"))
    assert scripts
    for script in scripts:
        content = script.read_text(encoding="utf-8")
        assert 'BIRDCAST_EURO_ROOT:?' in content, script.name
        assert 'BIRDCAST_EURO_PYTHON:?' in content, script.name
        if "-m birdcast_uk.cli" in content:
            assert 'export PYTHONPATH="$BIRDCAST_EURO_ROOT/src' in content, script.name

    stream = (slurm_dir / "birdcast-euro-aloft-stream.sbatch").read_text(encoding="utf-8")
    assert "timeout --kill-after=60s 50m" in stream
    gamm = (slurm_dir / "birdcast-euro-gamm.sbatch").read_text(encoding="utf-8")
    assert "#SBATCH --cpus-per-task=1" in gamm
    assert "#SBATCH --mem=128G" in gamm
    publish = (slurm_dir / "birdcast-euro-publish.sbatch").read_text(encoding="utf-8")
    assert "BIRDCAST_EURO_PUBLIC_HOST" in publish
    assert "birdcast-euro-activate.sh" in publish


def test_europe_public_promotion_only_switches_a_complete_manifest() -> None:
    root = Path(__file__).parents[1]
    script = (root / "deploy/scripts/birdcast-euro-object-store-pull.sh").read_text(encoding="utf-8")
    nginx = (root / "deploy/nginx/birdcast-uk.conf").read_text(encoding="utf-8")

    assert 'manifest.get("release_status") != "published"' in script
    assert 'manifest.get("data_available") is not True' in script
    assert 'test -f "$stage/$grid"' in script
    assert 'mv -Tf "$BIRDCAST_EURO_ARTIFACT_ROOT.next"' in script
    assert "alias /opt/birdcast-euro/artifacts-current/;" in nginx
    unit = (root / "deploy/systemd/birdcast-euro-object-store-pull.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=/etc/birdcast-uk/birdcast-euro.env" in unit
    activate = (root / "deploy/scripts/birdcast-euro-activate.sh").read_text(encoding="utf-8")
    assert 'manifest.get("release_status") != "published"' in activate
    assert 'test -f "$source_root/$grid"' in activate
    assert 'sha256sum "$manifest"' in activate
    assert 'test ! -e "$release"' in activate


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
            fieldnames=["row_id", "radar", "time_utc", "target", "observed", "predicted"],
        )
        writer.writeheader()
        for radar, row_id in (("bejab", "1"), ("nlhrw", "2")):
            writer.writerow(
                {"row_id": row_id, "radar": radar, "time_utc": "2026-01-01T00:00:00Z",
                 "target": "bird_u_ms", "observed": 1, "predicted": 1}
            )
            writer.writerow(
                {"row_id": row_id, "radar": radar, "time_utc": "2026-01-01T00:00:00Z",
                 "target": "bird_v_ms", "observed": 1, "predicted": 1}
            )
    folds = []
    for target in ("mtr_birds_km_h", "vid_birds_per_km2"):
        for radar in ("bejab", "nlhrw"):
            folds.append(
                {
                    "target": target,
                    "validation": "leave_one_radar_out",
                    "held_out": radar,
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
            }
        ),
        encoding="utf-8",
    )
    validator = load_script("validate_europe_gamm.py")
    result = validator.validate(metrics, tmp_path / "validation.json")

    assert result["release_passed"] is True
    assert result["site_equal_metrics"]["median_site_direction_error_deg"] == 0
    assert result["pooled_metrics_may_not_override_site_gates"] is True


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
                    "radar": "bejab", "time_utc": "2026-01-01T00:00:00Z",
                    "mean_mtr_birds_km_h": 4.0, "mean_vid_birds_per_km2": 2.0,
                    "bird_u_ms": 1.0, "bird_v_ms": 2.0, "profile_count": 12,
                    "rain_suspect_fraction": 0.0, "cohort_role": "training",
                },
                {
                    "radar": "nlhrw", "time_utc": "2026-01-01T00:00:00Z",
                    "mean_mtr_birds_km_h": 5.0, "mean_vid_birds_per_km2": 3.0,
                    "bird_u_ms": 2.0, "bird_v_ms": 1.0, "profile_count": 12,
                    "rain_suspect_fraction": 0.0, "cohort_role": "transfer-validation",
                },
            ]
        ),
        aloft,
    )
    era5 = tmp_path / "era5.parquet"
    parquet.write_table(
        pyarrow.Table.from_pylist(
            [
                {"radar": "bejab", "time_utc": "2026-01-01T00:00:00Z", "weather_x": 1.0},
                {"radar": "nlhrw", "time_utc": "2026-01-01T00:00:00Z", "weather_x": 2.0},
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
                    {"radar": "bejab", "latitude": 51.0, "longitude": 3.1,
                     "country": "BE", "network": "baltrad"},
                    {"radar": "nlhrw", "latitude": 52.1, "longitude": 4.8,
                     "country": "NL", "network": "baltrad"},
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
    assembler.assemble(args)

    assert "bejab" in output.read_text(encoding="utf-8")
    assert "chenies" in output.read_text(encoding="utf-8")
    assert "nlhrw" not in output.read_text(encoding="utf-8")
    assert "nlhrw" in validation.read_text(encoding="utf-8")
