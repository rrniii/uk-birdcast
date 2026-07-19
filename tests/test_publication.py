from __future__ import annotations

import json
from pathlib import Path
import subprocess

from birdcast_uk.publication import write_sync_commands


def test_sync_script_publishes_archive_before_latest(tmp_path: Path) -> None:
    source = tmp_path / "artifacts"
    (source / "archive").mkdir(parents=True)
    (source / "latest").mkdir()
    (source / "latest" / "historical.json").write_text("{}\n", encoding="utf-8")
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
    assert "--exclude '*.json'" in content
    assert "find " in content
    assert '"$manifest"' in content
    assert "s3 sync" in content
    assert script.stat().st_mode & 0o777 == 0o750
    subprocess.run(["sh", "-n", str(script)], check=True)


def test_s3cmd_script_publishes_latest_manifests_last(tmp_path: Path) -> None:
    source = tmp_path / "artifacts"
    (source / "archive").mkdir(parents=True)
    (source / "latest").mkdir()
    archive = source / "archive" / "frame.json"
    manifest = source / "latest" / "historical.json"
    archive.write_text("{}\n", encoding="utf-8")
    manifest.write_text("{}\n", encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "source_dir": str(source),
                "object_prefix": "birdcast-uk",
                "objects": [
                    {"source": str(manifest), "key": "birdcast-uk/latest/historical.json", "content_type": "application/json"},
                    {"source": str(archive), "key": "birdcast-uk/archive/frame.json", "content_type": "application/json"},
                ],
            }
        ),
        encoding="utf-8",
    )
    script = tmp_path / "sync-s3cmd.sh"

    write_sync_commands(
        plan,
        script,
        bucket="public",
        endpoint_url="http://object.invalid",
        client="s3cmd",
        s3cmd_config="/secure/s3cmd.conf",
    )
    content = script.read_text(encoding="utf-8")

    assert "s3cmd -c /secure/s3cmd.conf put" in content
    assert "--acl-public" in content
    assert content.index("birdcast-uk/archive/frame.json") < content.index("birdcast-uk/latest/historical.json")
    subprocess.run(["sh", "-n", str(script)], check=True)
