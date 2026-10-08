"""Restore-package completeness checks, separate from ZIP hash integrity."""
import json
import zipfile
from pathlib import Path

import pytest

from scripts import create_local_backup as backup


def make_package(
    tmp_path: Path, include_memory: bool, checksum_memory: bool = True,
) -> Path:
    root = tmp_path / "backup-test"
    data = root / "02_活动数据" / "技能数据"
    data.mkdir(parents=True)
    (data / "event_map_shadow.db").write_bytes(b"event-db")
    if include_memory:
        (data / "catalyst_research_memory.db").write_bytes(b"memory-db")
    (root / "MANIFEST.json").write_text(
        json.dumps({"format_version": 2}), encoding="utf-8"
    )
    checksums = backup.build_checksums(root)
    if not checksum_memory:
        checksums = [row for row in checksums if not row["path"].endswith("catalyst_research_memory.db")]
    (root / "SHA256SUMS.json").write_text(json.dumps(checksums), encoding="utf-8")
    package = tmp_path / "package.zip"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            if path.is_file():
                archive.write(path, Path(root.name) / path.relative_to(root))
    return package


def test_backup_requires_catalyst_memory_and_verifies_coverage(tmp_path):
    assert Path("技能数据/catalyst_research_memory.db") in backup.DATA_TARGETS
    assert Path("技能数据/catalyst_research_memory.db") in backup.REQUIRED_DATA_TARGETS
    assert backup.verify_backup(make_package(tmp_path, include_memory=True)) == 2


def test_v2_archive_with_valid_hashes_but_no_catalyst_memory_fails(tmp_path):
    with pytest.raises(RuntimeError, match="catalyst_research_memory.db"):
        backup.verify_backup(make_package(tmp_path, include_memory=False))


def test_v2_archive_requires_catalyst_memory_checksum(tmp_path):
    with pytest.raises(RuntimeError, match="未纳入SHA-256清单"):
        backup.verify_backup(make_package(tmp_path, include_memory=True, checksum_memory=False))


def test_backup_contains_selected_primary_instead_of_default_db(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    (workspace / "repo").mkdir(parents=True)
    data = workspace / "技能数据"
    data.mkdir()
    (data / "event_map_shadow.db").write_bytes(b"wrong-default-database")
    (data / "catalyst_research_memory.db").write_bytes(b"memory-database")
    selected = workspace / "selected.db"
    selected.write_bytes(b"selected-primary-database")
    monkeypatch.setenv("TZ_CODEX_HOME", str(workspace))
    monkeypatch.setenv("EVENT_MAP_DB", "selected.db")
    monkeypatch.setattr(backup, "WORKSPACE", workspace)
    monkeypatch.setattr(backup, "REPO", workspace / "repo")
    monkeypatch.setattr(backup, "ARCHIVE_ROOT", workspace / "archives")

    def fake_git(*args, **kwargs):
        if args[1] == "status":
            return ""
        if args[1] == "rev-list":
            return "0 0"
        if args[1] == "rev-parse":
            return "fixture-head" if args[-1] == "HEAD" else "origin/main"
        if args[1] == "branch":
            return "main"
        if args[1] == "archive":
            Path(next(a.split("=", 1)[1] for a in args if a.startswith("--output="))).write_bytes(b"source-fixture")
            return ""
        if args[1:3] == ("bundle", "create"):
            Path(args[3]).write_bytes(b"bundle-fixture")
            return ""
        if args[1:3] == ("bundle", "verify"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(backup, "run", fake_git)
    package = backup.create_backup()
    with zipfile.ZipFile(package) as archive:
        root = archive.namelist()[0].split("/", 1)[0]
        assert archive.read(root + "/02_活动数据/技能数据/event_map_shadow.db") == b"selected-primary-database"
        manifest = json.loads(archive.read(root + "/MANIFEST.json"))
        assert manifest["event_db_source"] == str(selected)
