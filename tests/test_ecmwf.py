from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from birdcast_uk.ecmwf import (
    ECMWF_CYCLE_SCHEMA_VERSION,
    archive_cycle,
    newest_validated_cycle_manifest,
    normalise_cycle,
    validate_cycle_manifest,
)


def _grib2(payload: bytes = b"test-data") -> bytes:
    """Build one minimally framed GRIB2 message for container-level tests."""

    length = 16 + len(payload) + 4
    return b"GRIB" + b"\x00\x00\x00\x02" + length.to_bytes(8, "big") + payload + b"7777"


def _install_fake_earthkit(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[bytes | BaseException],
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    earthkit_module = ModuleType("earthkit")
    earthkit_module.__path__ = []  # type: ignore[attr-defined]
    data_module = ModuleType("earthkit.data")

    def from_source(*args: object, **kwargs: Any) -> object:
        index = len(calls)
        calls.append({"args": args, "kwargs": kwargs})
        outcome = outcomes[index]

        class Download:
            def to_target(self, target: str, destination: str) -> None:
                assert target == "file"
                if isinstance(outcome, BaseException):
                    raise outcome
                Path(destination).write_bytes(outcome)

        return Download()

    data_module.from_source = from_source  # type: ignore[attr-defined]
    earthkit_module.data = data_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "earthkit", earthkit_module)
    monkeypatch.setitem(sys.modules, "earthkit.data", data_module)
    return calls


def test_archive_cycle_is_transactional_and_manifest_is_strict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_earthkit(monkeypatch, [_grib2(), _grib2(b"pressure")])

    result = archive_cycle(tmp_path, cycle="2026-08-19T12:00:00Z")

    cycle_dir = tmp_path / "20260819T1200Z"
    manifest_path = cycle_dir / "manifest.json"
    assert result["status"] == "complete"
    assert result["schema_version"] == ECMWF_CYCLE_SCHEMA_VERSION
    assert "area" not in result
    assert result["content_contract"] == {
        "validation_level": "grib2-message-framing",
        "decoded": False,
        "field_inventory_verified": False,
        "expected_file_kinds": ["surface", "pressure"],
    }
    assert len(calls) == 2
    assert validate_cycle_manifest(manifest_path) == result
    assert newest_validated_cycle_manifest(tmp_path) == manifest_path.resolve()
    assert not list(tmp_path.glob(".*.staging-*"))
    for item in result["files"]:
        assert Path(item["path"]).is_absolute()
        assert Path(item["path"]).parent == cycle_dir.resolve()
        assert item["content_validation"]["decoded"] is False
        assert item["content_validation"]["message_count"] == 1


def test_valid_cached_cycle_is_reused_even_with_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_earthkit(monkeypatch, [_grib2(), _grib2()])
    first = archive_cycle(tmp_path, cycle="2026-08-19T12:00:00Z")
    calls = _install_fake_earthkit(
        monkeypatch,
        [AssertionError("a valid immutable cycle must not be downloaded again")],
    )

    second = archive_cycle(tmp_path, cycle="2026-08-19T12:00:00Z", overwrite=True)

    assert second == first
    assert calls == []


def test_corrupt_cached_cycle_is_rejected_then_transactionally_rebuilt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_earthkit(monkeypatch, [_grib2(), _grib2(b"old-pressure")])
    first = archive_cycle(tmp_path, cycle="2026-08-19T12:00:00Z")
    pressure = Path(first["files"][1]["path"])
    corrupt_bytes = pressure.read_bytes() + b"corrupt"
    pressure.write_bytes(corrupt_bytes)
    no_download_calls = _install_fake_earthkit(monkeypatch, [_grib2(), _grib2()])

    rejected = archive_cycle(tmp_path, cycle="2026-08-19T12:00:00Z")

    assert rejected["status"] == "failed"
    assert rejected["persisted"] is False
    assert "invalid cached cycle" in rejected["error"]
    assert no_download_calls == []
    assert pressure.read_bytes() == corrupt_bytes

    _install_fake_earthkit(monkeypatch, [_grib2(b"new-surface"), _grib2(b"new-pressure")])
    rebuilt = archive_cycle(tmp_path, cycle="2026-08-19T12:00:00Z", overwrite=True)

    assert rebuilt["status"] == "complete"
    assert validate_cycle_manifest(tmp_path / "20260819T1200Z" / "manifest.json") == rebuilt
    quarantines = list(tmp_path.glob(".20260819T1200Z.invalid-*"))
    assert len(quarantines) == 1
    assert (quarantines[0] / pressure.name).read_bytes() == corrupt_bytes


def test_partial_download_never_replaces_last_good_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_earthkit(monkeypatch, [_grib2(b"good-surface"), _grib2(b"good-pressure")])
    good = archive_cycle(tmp_path, cycle="2026-08-19T06:00:00Z")
    good_manifest = tmp_path / "20260819T0600Z" / "manifest.json"
    good_manifest_bytes = good_manifest.read_bytes()
    calls = _install_fake_earthkit(
        monkeypatch,
        [_grib2(b"partial-surface"), RuntimeError("pressure download interrupted")],
    )

    failed = archive_cycle(tmp_path, cycle="2026-08-19T12:00:00Z")

    assert failed["status"] == "failed"
    assert failed["persisted"] is False
    assert "pressure download interrupted" in failed["error"]
    assert len(calls) == 2
    assert not (tmp_path / "20260819T1200Z").exists()
    assert not list(tmp_path.glob(".20260819T1200Z.staging-*"))
    assert good_manifest.read_bytes() == good_manifest_bytes
    assert validate_cycle_manifest(good_manifest) == good
    assert newest_validated_cycle_manifest(tmp_path) == good_manifest.resolve()


def test_validation_rejects_empty_and_hash_mismatched_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_earthkit(monkeypatch, [_grib2(), _grib2()])
    result = archive_cycle(tmp_path, cycle="2026-08-19T12:00:00Z")
    manifest_path = tmp_path / "20260819T1200Z" / "manifest.json"
    surface = Path(result["files"][0]["path"])
    original = surface.read_bytes()

    surface.write_bytes(b"")
    with pytest.raises(ValueError, match="size mismatch"):
        validate_cycle_manifest(manifest_path)
    assert newest_validated_cycle_manifest(tmp_path) is None

    surface.write_bytes(original)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        validate_cycle_manifest(manifest_path)


def test_validation_rejects_bytes_that_are_not_grib2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_earthkit(monkeypatch, [b"not a GRIB", _grib2()])

    result = archive_cycle(tmp_path, cycle="2026-08-19T12:00:00Z")

    assert result["status"] == "failed"
    assert "invalid GRIB2 header" in result["error"]
    assert not (tmp_path / "20260819T1200Z").exists()


def test_normalise_cycle_validates_the_utc_instant() -> None:
    assert normalise_cycle("2026-08-19T07:00:00+01:00").hour == 6
    with pytest.raises(ValueError, match="00, 06, 12, or 18 UTC"):
        normalise_cycle("2026-08-19T06:00:00.000001Z")
