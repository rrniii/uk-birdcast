from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from birdcast_uk import historical_cycle as cycle


def fixture_sources(tmp_path):
    source = tmp_path / "input" / "single-site" / "radar-a" / "2026"
    source.mkdir(parents=True)
    for day in ("20260830", "20260831", "20260901"):
        stamp = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        for pulse in ("lp", "sp"):
            (source / f"{day}_{pulse}_vpts.csv").write_text(
                "datetime,height,dens,radar_latitude,radar_longitude,gap,ff,eta,day\n"
                f"{stamp}T12:00:00Z,200,2,51,0,FALSE,5,3,TRUE\n"
                f"{stamp}T12:00:00Z,400,2,51,0,FALSE,5,3,TRUE\n",
                encoding="utf-8",
            )
    return source


def build(tmp_path, **kwargs):
    return cycle.build_analysis(
        input_root=tmp_path / "input",
        cache_path=tmp_path / "state" / "cache.sqlite3",
        output_dir=tmp_path / "output",
        radars={"radar-a"},
        source_end=date(2026, 9, 1),
        published_end=date(2026, 8, 31),
        previous={"first_date": "2026-08-30", "latest_date": "2026-08-30", "source": {}},
        workers=1,
        **kwargs,
    )


def test_cached_analysis_is_repeatable_bounded_and_does_not_change_source(tmp_path):
    source = fixture_sources(tmp_path)
    original = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source.iterdir()}
    for path in source.iterdir():
        path.chmod(0o444)
    first = build(tmp_path)
    before = (tmp_path / "output" / "daily_totals.csv").read_bytes()
    second = build(tmp_path)
    assert first["changed_files"] == 6
    assert second["changed_files"] == 0
    assert first["input_snapshot_sha256"] == second["input_snapshot_sha256"]
    assert (tmp_path / "output" / "daily_totals.csv").read_bytes() == before
    rows = list(csv.DictReader(before.decode().splitlines()))
    assert {row["date"] for row in rows} == {"2026-08-30", "2026-08-31"}
    assert {row["pulse"] for row in rows} == {"lp", "sp"}
    assert all(float(row["vid_birds_per_km2"]) == 0.8 for row in rows)
    assert all(
        hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in original.items()
    )


def test_cache_invalidates_a_changed_source_only(tmp_path):
    source = fixture_sources(tmp_path)
    first = build(tmp_path)
    changed = source / "20260831_lp_vpts.csv"
    changed.write_text(changed.read_text().replace(",200,2,", ",200,3,"))
    second = build(tmp_path)
    assert second["changed_files"] == 1
    assert second["input_snapshot_sha256"] != first["input_snapshot_sha256"]


def test_cached_merge_matches_direct_legacy_statistics(tmp_path):
    source = fixture_sources(tmp_path)
    build(tmp_path)
    direct = {}
    for path in sorted(source.iterdir()):
        pulse = cycle.statistics.FILE_RE.fullmatch(path.name)["pulse"]
        cycle.statistics.summarize_file(path, "radar-a", pulse, "dens", 200, 4000, False, direct)
    expected = [
        row for row in cycle.statistics.finalize_daily_rows(direct) if row["date"] <= "2026-08-31"
    ]
    with (tmp_path / "output" / "daily_totals.csv").open() as handle:
        actual = list(csv.DictReader(handle))
    assert len(actual) == len(expected)
    for actual_row, expected_row in zip(actual, expected):
        for key, value in expected_row.items():
            assert actual_row[key] == str(value)


def test_missing_latest_source_boundary_blocks_analysis(tmp_path):
    source = fixture_sources(tmp_path)
    (source / "20260901_sp_vpts.csv").unlink()
    with pytest.raises(ValueError, match="Incomplete UTC boundaries"):
        build(tmp_path)
    assert not (tmp_path / "output" / "analysis_summary.json").exists()


def test_unusable_latest_radar_is_not_qualified_by_zero_density(tmp_path):
    source = fixture_sources(tmp_path)
    path = source / "20260831_sp_vpts.csv"
    path.write_text(path.read_text().replace(",FALSE,", ",TRUE,"))
    with pytest.raises(ValueError, match="No usable observations"):
        build(tmp_path)


def test_missing_historical_values_are_blank_but_measured_zero_is_preserved(tmp_path):
    source = fixture_sources(tmp_path)
    missing = source / "20260830_sp_vpts.csv"
    missing.write_text(missing.read_text().replace(",FALSE,", ",TRUE,"))
    zero = source / "20260830_lp_vpts.csv"
    zero.write_text(zero.read_text().replace(",200,2,", ",200,0,").replace(",400,2,", ",400,0,"))
    build(tmp_path)
    with (tmp_path / "output" / "daily_totals.csv").open() as handle:
        rows = [row for row in csv.DictReader(handle) if row["date"] == "2026-08-30"]
    assert {row["pulse"]: row["vid_birds_per_km2"] for row in rows} == {"sp": "", "lp": "0.0"}


def test_missing_pulse_is_explicit_not_replaced_by_other_pulse(tmp_path):
    fixture_sources(tmp_path)
    records = cycle.discover_sources(tmp_path / "input", {"radar-a"}, date(2026, 9, 1))
    records = [row for row in records if row[2] != "sp"]
    gaps = cycle.missing_source_days(records, {"radar-a"}, date(2026, 8, 30), date(2026, 9, 1))
    assert len(gaps) == 3
    assert all(row["pulse"] == "sp" for row in gaps)


@pytest.mark.parametrize(
    "outcome",
    [
        "prepare",
        "publish",
        "verification_failure",
        "post_promotion_verification_failure",
        "no_change",
    ],
)
def test_only_verified_publication_advances_checkpoint(tmp_path, monkeypatch, outcome):
    now = datetime.now(timezone.utc)
    from datetime import timedelta

    source_end = now.date() - timedelta(days=2)
    end = source_end - timedelta(days=1)
    previous = {"latest_date": end.isoformat(), "release_id": "old"}
    catalog = {
        "generated_at": now.isoformat(),
        "radars": [{"radar": "radar-a", "last_date": source_end.strftime("%Y%m%d")}],
    }
    radars = tmp_path / "radars.json"
    radars.write_text(json.dumps([{"slug": "radar-a"}]))
    (tmp_path / "runs").mkdir()
    checkpoint = tmp_path / "published.json"
    fingerprint = "same" if outcome == "no_change" else "new"
    checkpoint.write_text('{"release_id":"old","input_snapshot_sha256":"same"}')
    original_checkpoint = checkpoint.read_bytes()
    monkeypatch.setattr(cycle, "fetch_json", lambda url: catalog if url == "catalog" else previous)
    monkeypatch.setattr(
        cycle, "build_analysis", lambda **kw: {"input_snapshot_sha256": fingerprint}
    )
    monkeypatch.setattr(cycle, "build_static_artifacts", lambda *a, **kw: None)
    monkeypatch.setattr(cycle, "build_historical_products", lambda *a, **kw: {"release_id": "new"})
    monkeypatch.setattr(
        cycle, "build_publication_plan", lambda *a, **kw: {"object_count": 2, "objects": []}
    )
    phases = []
    monkeypatch.setattr(cycle, "write_sync_commands", lambda *a, **kw: phases.append(kw["phase"]))
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)
    verified = []

    def verify(*args):
        verified.append(True)
        if outcome == "verification_failure" or (
            outcome == "post_promotion_verification_failure" and len(verified) == 2
        ):
            raise ValueError("public checksum mismatch")

    monkeypatch.setattr(cycle, "verify_public_plan", verify)
    args = argparse.Namespace(
        public_base_url="https://example.invalid",
        object_prefix="birdcast-uk",
        radars=radars,
        catalog_url="catalog",
        state_root=tmp_path,
        input_root=tmp_path,
        workers=1,
        boundary=tmp_path / "boundary",
        publish=outcome != "prepare",
        bucket="public",
        s3cmd_config=tmp_path / "private-config",
    )
    if outcome.endswith("verification_failure"):
        with pytest.raises(ValueError, match="checksum"):
            cycle.run_cycle(args)
    else:
        result = cycle.run_cycle(args)
        if outcome == "no_change":
            assert result["state"] == "no_change"
            assert json.loads((Path(result["run_dir"]) / "result.json").read_text()) == result
            assert checkpoint.read_bytes() == original_checkpoint
    assert json.loads(checkpoint.read_text())["release_id"] == (
        "new" if outcome == "publish" else "old"
    )
    expected_phases = {
        "prepare": [],
        "no_change": [],
        "verification_failure": ["assets"],
        "post_promotion_verification_failure": ["assets", "manifests"],
        "publish": ["assets", "manifests"],
    }
    assert phases == expected_phases[outcome]
    assert len(verified) == len(phases)
