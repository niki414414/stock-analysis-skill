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
