from __future__ import annotations

import json
from pathlib import Path

from birdcast_uk.publication import write_sync_commands


def test_sync_script_publishes_archive_before_latest(tmp_path: Path) -> None:
    source = tmp_path / "artifacts"
    (source / "archive").mkdir(parents=True)
    (source / "latest").mkdir()
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"source_dir": str(source), "object_prefix": "birdcast-uk"}),
        encoding="utf-8",
    )
    script = tmp_path / "sync.sh"

    write_sync_commands(
        plan,
        script,
        bucket="public",
        endpoint_url="https://object.invalid",
        profile="radar",
    )
    content = script.read_text(encoding="utf-8")

    assert content.index("birdcast-uk/archive") < content.index("birdcast-uk/latest")
    assert "--exclude 'latest/*'" in content
    assert "s3 sync" in content
    assert script.stat().st_mode & 0o777 == 0o750
