from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import birdcast_uk.publication as publication
from birdcast_uk.publication import (
    build_publication_plan,
    validate_publication_plan,
    validate_release,
    write_sync_commands,
)
from birdcast_uk.selected_model import (
    COMPONENT_MANIFEST_SHA256,
    COMPONENT_SHA256,
    QUALIFIED_FIRST_DAY,
    QUALIFIED_LAST_DAY,
    qualified_dates,
)

MODEL_TARGETS = {
    "mtr_birds_km_h",
    "vid_birds_per_km2",
    "bird_u_ms",
    "bird_v_ms",
}


def _write_release(root: Path) -> Path:
    archive = root / "archive" / "historical" / "release-a"
    latest = root / "latest"
    archive.mkdir(parents=True)
    latest.mkdir()
    (archive / "frame.json").write_text("{}\n", encoding="utf-8")
    manifest = {
        "data_available": True,
        "schema_version": "live-uk-bird-maps-historical-1.2",
        "release_id": "release-a",
        "first_date": "2026-08-15",
        "latest_date": "2026-08-15",
        "years": [2026],
        "source": {
            "files_seen": 1,
            "profiles_seen": 1,
            "rows_seen": 1,
            "failure_count": 0,
        },
        "radars": [{"slug": "test"}],
        "radar_coverage": [
            {
                "radar": "test",
                "first_date": "2026-08-15",
                "last_date": "2026-08-15",
            }
        ],
        "assets": {
            "daily_by_year": {"2026": "archive/historical/release-a/frame.json"},
            "annual": "archive/historical/release-a/frame.json",
            "phenology": "archive/historical/release-a/frame.json",
            "coverage": "archive/historical/release-a/frame.json",
            "boundary": "archive/historical/release-a/frame.json",
            "plots": [],
            "release_manifest": "archive/historical/release-a/manifest.json",
        },
    }
    content = json.dumps(manifest)
    (archive / "manifest.json").write_text(content, encoding="utf-8")
    (latest / "historical.json").write_text(content, encoding="utf-8")
    (latest / "forecast.json").write_text(
        json.dumps(
            {
                "data_available": False,
                "mode": "disabled",
                "valid_times_utc": [],
                "assets": {"frames": []},
            }
        ),
        encoding="utf-8",
    )
    return root


def _write_selected_model_release(root: Path) -> Path:
    source = _write_release(root)
    archive = source / "archive" / "reanalysis" / "gam-era5" / "release-model"
    archive.mkdir(parents=True)
    archive_prefix = "archive/reanalysis/gam-era5/release-model"
    boundary = "archive/reanalysis/gam-era5/release-model/uk-boundary.geojson"
    assets = {
        pulse: {
            day: f"{archive_prefix}/daily/{pulse}/{day.replace('-', '')}.json"
            for day in qualified_dates()
        }
        for pulse in ("lp", "sp")
    }
    assets["boundary"] = boundary
    for relative_path in (
        *(path for daily in (assets["lp"], assets["sp"]) for path in daily.values()),
        boundary,
        "archive/reanalysis/gam-era5/release-model/validation.json",
        "archive/reanalysis/gam-era5/release-model/source.json",
    ):
        path = source / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    components = {
        pulse: {target: {"sha256": COMPONENT_SHA256[pulse][target]} for target in MODEL_TARGETS}
        for pulse in ("lp", "sp")
    }
    (source / "latest" / "gam-era5.json").write_text(
        json.dumps(
            {
                "data_available": True,
                "schema_version": "live-uk-bird-maps-gam-era5-1.2",
                "selection_id": "uk-gamm-heldout-v2-sp-vector-925",
                "model_family": "gamm",
                "pulses": ["lp", "sp"],
                "archive_prefix": archive_prefix,
                "first_time_utc": f"{QUALIFIED_FIRST_DAY.isoformat()}T00:00:00Z",
                "latest_time_utc": f"{QUALIFIED_LAST_DAY.isoformat()}T23:00:00Z",
                "component_provenance": {
                    "component_manifest_sha256": COMPONENT_MANIFEST_SHA256,
                    "components": components,
                },
                "assets": assets,
                "comparison": ("archive/reanalysis/gam-era5/release-model/validation.json"),
                "source": "archive/reanalysis/gam-era5/release-model/source.json",
            }
        ),
        encoding="utf-8",
    )
    return source


def test_release_validation_requires_data_and_assets(tmp_path: Path) -> None:
    source = _write_release(tmp_path / "artifacts")

    result = validate_release(source, required_products=("historical",))

    assert result["ok"] is True
    assert result["checked_asset_count"] == 6


def test_two_phase_publication_keeps_latest_out_of_asset_upload(tmp_path: Path) -> None:
    source = _write_release(tmp_path / "artifacts")
    plan = tmp_path / "plan.json"
    build_publication_plan(source, plan, products=("historical",))
    assets, manifests = tmp_path / "assets.sh", tmp_path / "manifests.sh"
    for output, phase in ((assets, "assets"), (manifests, "manifests")):
        write_sync_commands(
            plan, output, bucket="public", endpoint_url="", client="s3cmd", phase=phase
        )
    # Hash checks mention all paths; inspect only commands performing uploads.
    asset_uploads = [line for line in assets.read_text().splitlines() if " put " in line]
    manifest_uploads = [line for line in manifests.read_text().splitlines() if " put " in line]
    assert asset_uploads and manifest_uploads
    assert all("/latest/" not in line for line in asset_uploads)
    assert all("/latest/" in line for line in manifest_uploads)


def test_release_validation_rejects_placeholder(tmp_path: Path) -> None:
    source = tmp_path / "artifacts"
    (source / "latest").mkdir(parents=True)
    (source / "latest" / "historical.json").write_text(
        json.dumps({"data_available": False, "assets": {}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not data-bearing"):
        validate_release(source, required_products=("historical",))


@pytest.mark.parametrize("latest_date", [None, "2026-08-14", "not-a-date"])
def test_historical_release_must_reach_valid_baseline_date(
    tmp_path: Path, latest_date: str | None
) -> None:
    source = _write_release(tmp_path / "artifacts")
    manifest_path = source / "latest" / "historical.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["latest_date"] = latest_date
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="required baseline 2026-08-15"):
        validate_release(source, required_products=("historical",))


def test_release_validation_rejects_data_bearing_forecast(
    tmp_path: Path,
) -> None:
    source = _write_release(tmp_path / "artifacts")
    (source / "latest" / "forecast.json").write_text(
        json.dumps(
            {
                "data_available": True,
                "mode": "forecast",
                "valid_times_utc": ["2026-08-16T00:00:00Z"],
                "assets": {"frames": ["archive/forecast/stale.json"]},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="forecast tombstone"):
        validate_release(source, required_products=("historical",))


def test_release_validation_requires_forecast_tombstone(tmp_path: Path) -> None:
    source = _write_release(tmp_path / "artifacts")
    (source / "latest" / "forecast.json").unlink()

    with pytest.raises(FileNotFoundError, match="forecast tombstone"):
        validate_release(source, required_products=("historical",))


def test_selected_model_exact_component_provenance_is_accepted(
    tmp_path: Path,
) -> None:
    source = _write_selected_model_release(tmp_path / "artifacts")

    result = validate_release(source, required_products=("gam-era5",))

    assert result["ok"] is True
    assert result["checked_asset_count"] == 733


@pytest.mark.parametrize(
    ("defect", "message"),
    [
        ("selection", "not the selected model"),
        ("pulse", "must contain LP and SP"),
        ("target", "incomplete for lp"),
        ("digest", "component hash is not reviewed"),
    ],
)
def test_selected_model_rejects_inexact_component_provenance(
    tmp_path: Path, defect: str, message: str
) -> None:
    source = _write_selected_model_release(tmp_path / "artifacts")
    manifest_path = source / "latest" / "gam-era5.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    provenance = manifest["component_provenance"]
    components = provenance["components"]
    if defect == "selection":
        manifest["selection_id"] = "unselected-model"
    elif defect == "pulse":
        components.pop("sp")
    elif defect == "target":
        components["lp"].pop("bird_v_ms")
    else:
        components["lp"]["bird_v_ms"]["sha256"] = "g" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        validate_release(source, required_products=("gam-era5",))


def test_selected_model_rejects_unqualified_date_window(tmp_path: Path) -> None:
    source = _write_selected_model_release(tmp_path / "artifacts")
    manifest_path = source / "latest" / "gam-era5.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assets"]["lp"].pop(qualified_dates()[-1])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="exact 365-day window"):
        validate_release(source, required_products=("gam-era5",))


def test_selected_model_rejects_missing_or_misdirected_daily_asset(tmp_path: Path) -> None:
    source = _write_selected_model_release(tmp_path / "artifacts")
    manifest_path = source / "latest" / "gam-era5.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assets"]["lp"][qualified_dates()[0]] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="lp asset path is invalid"):
        validate_release(source, required_products=("gam-era5",))


def test_selected_model_rejects_an_injected_private_asset(tmp_path: Path) -> None:
    source = _write_selected_model_release(tmp_path / "artifacts")
    private = source / "archive" / "reanalysis" / "gam-era5" / "release-model" / "private.csv"
    private.write_text("private model output\n", encoding="utf-8")
    manifest_path = source / "latest" / "gam-era5.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assets"]["private_predictions"] = str(private.relative_to(source))
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected asset keys"):
        validate_release(source, required_products=("gam-era5",))


def test_publication_api_rejects_unknown_products(tmp_path: Path) -> None:
    source = _write_release(tmp_path / "artifacts")

    with pytest.raises(ValueError, match="unsupported publication products"):
        build_publication_plan(
            source,
            tmp_path / "plan.json",
            object_prefix="birdcast-uk",
            products=("secret",),
        )


def test_publication_plan_contains_only_manifest_references(tmp_path: Path) -> None:
    source = _write_release(tmp_path / "artifacts")
    private = source / "gamm-experiments" / "predictions.csv"
    private.parent.mkdir()
    private.write_text("private model output\n", encoding="utf-8")

    plan = build_publication_plan(
        source,
        tmp_path / "plan.json",
        object_prefix="birdcast-uk",
        products=("historical",),
    )

    keys = {item["key"] for item in plan["objects"]}
    assert keys == {
        "birdcast-uk/archive/historical/release-a/frame.json",
        "birdcast-uk/archive/historical/release-a/manifest.json",
        "birdcast-uk/latest/forecast.json",
        "birdcast-uk/latest/historical.json",
    }
    assert not any("gamm-experiments" in key for key in keys)
    assert validate_publication_plan(tmp_path / "plan.json")["object_count"] == 4


@pytest.mark.parametrize("asset", ["../private.json", "/etc/passwd"])
def test_release_validation_rejects_escaping_assets(tmp_path: Path, asset: str) -> None:
    source = _write_release(tmp_path / "artifacts")
    manifest_path = source / "latest" / "historical.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assets"]["daily_by_year"]["2026"] = asset
    content = json.dumps(manifest)
    manifest_path.write_text(content, encoding="utf-8")
    (source / "archive" / "historical" / "release-a" / "manifest.json").write_text(
        content, encoding="utf-8"
    )

    with pytest.raises(ValueError, match="immutable release"):
        validate_release(source, required_products=("historical",))


def test_release_validation_rejects_symlink_assets(tmp_path: Path) -> None:
    source = _write_release(tmp_path / "artifacts")
    target = source / "archive" / "historical" / "release-a" / "frame.json"
    link = source / "archive" / "historical" / "release-a" / "linked.json"
    link.symlink_to(target)
    manifest_path = source / "latest" / "historical.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assets"]["daily_by_year"]["2026"] = "archive/historical/release-a/linked.json"
    content = json.dumps(manifest)
    manifest_path.write_text(content, encoding="utf-8")
    (source / "archive" / "historical" / "release-a" / "manifest.json").write_text(
        content, encoding="utf-8"
    )

    with pytest.raises(FileNotFoundError, match="references 1 missing assets"):
        validate_release(source, required_products=("historical",))


def test_plan_validation_detects_changed_source(tmp_path: Path) -> None:
    source = _write_release(tmp_path / "artifacts")
    plan_path = tmp_path / "plan.json"
    build_publication_plan(source, plan_path, products=("historical",))
    (source / "archive" / "historical" / "release-a" / "frame.json").write_text(
        '{"changed": true}\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="source size changed|source hash changed"):
        validate_publication_plan(plan_path)


@pytest.mark.parametrize("client", ["aws", "s3cmd"])
def test_sync_script_uses_exact_plan_and_promotes_manifest_last(
    tmp_path: Path, client: str
) -> None:
    source = _write_release(tmp_path / "artifacts")
    plan_path = tmp_path / "plan.json"
    build_publication_plan(
        source,
        plan_path,
        object_prefix="birdcast-uk",
        products=("historical",),
    )
    script = tmp_path / f"sync-{client}.sh"

    write_sync_commands(
        plan_path,
        script,
        bucket="public",
        endpoint_url="https://object.invalid",
        profile="radar",
        client=client,
        s3cmd_config="/secure/s3cmd.conf",
    )
    content = script.read_text(encoding="utf-8")

    assert "sha256sum" in content
    assert "s3 sync" not in content
    assert "existing immutable object differs" in content
    assert "immutable object already matches" in content
    assert ("head-object" in content) if client == "aws" else (" info " in content)
    assert content.index("archive/historical/release-a/frame.json") < content.rindex(
        "latest/historical.json"
    )
    assert script.stat().st_mode & 0o777 == 0o750
    subprocess.run(["sh", "-n", str(script)], check=True)


def test_sync_script_preserves_existing_file_when_atomic_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write_release(tmp_path / "artifacts")
    plan_path = tmp_path / "plan.json"
    build_publication_plan(source, plan_path, products=("historical",))
    script = tmp_path / "sync.sh"
    original = b"#!/bin/sh\necho previous-release\n"
    script.write_bytes(original)

    def fail_replace(source_path: object, destination_path: object) -> None:
        raise OSError("promotion failed")

    monkeypatch.setattr(publication.os, "replace", fail_replace)

    with pytest.raises(OSError, match="promotion failed"):
        write_sync_commands(
            plan_path,
            script,
            bucket="public",
            endpoint_url="https://object.invalid",
        )

    assert script.read_bytes() == original
    assert list(tmp_path.glob(".sync.sh.*")) == []


def test_plan_validation_rejects_an_injected_local_file(tmp_path: Path) -> None:
    source = _write_release(tmp_path / "artifacts")
    plan_path = tmp_path / "plan.json"
    build_publication_plan(source, plan_path, products=("historical",))
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    injected = tmp_path / "secret.txt"
    injected.write_text("secret\n", encoding="utf-8")
    payload["objects"].append(
        {
            "source": str(injected),
            "key": "birdcast-uk/archive/private.txt",
            "size": injected.stat().st_size,
            "sha256": "unused",
            "content_type": "text/plain",
        }
    )
    payload["object_count"] += 1
    plan_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unreferenced object"):
        validate_publication_plan(plan_path)
