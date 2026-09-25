#!/usr/bin/env python3
"""Create and verify a credential-free local migration package for tz-codex."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path


WORKSPACE = Path(
    os.path.abspath(os.path.expanduser(
        os.environ.get("TZ_CODEX_HOME", "~/Desktop/tz-codex")
    ))
)
REPO = WORKSPACE / "repo"
ARCHIVE_ROOT = WORKSPACE / "迁移归档"

# 只复制活动数据，不递归打包旧迁移包和历史程序快照。
DATA_TARGETS = [
    Path("WORKSPACE.md"),
    Path("holdings.csv"),
    Path("研究材料"),
    Path("研究报告"),
    Path("分析记录"),
    Path("交易规则"),
    Path("技能数据/company_code_map.csv"),
    Path("技能数据/公司.xlsx"),
    # 文件名是迁移期遗留；库内metadata.build_mode=sqlite_primary才是主库身份依据。
    Path("技能数据/event_map_shadow.db"),
    Path("技能数据/catalyst_research_memory.db"),
    Path("技能数据/event_db_audit.json"),
    Path("技能数据/event_db_migration_history.json"),
    Path("技能数据/decision_journal.jsonl"),
    Path("技能数据/科技产业事件"),
    Path("技能数据/非科技产业事件地图"),
    Path("技能数据/事件地图变更包"),
    Path("技能数据/事件地图导出"),
    Path("技能数据/market_daily_snapshot"),
    Path("技能数据/运行记录"),
]

REQUIRED_DATA_TARGETS = (
    Path("技能数据/event_map_shadow.db"),
    Path("技能数据/catalyst_research_memory.db"),
)

FORBIDDEN_NAMES = {".env", ".git", "__pycache__", ".DS_Store"}
FORBIDDEN_SUFFIXES = {".pyc", ".log"}


def run(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def copy_filtered(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(
            src,
            dst,
            ignore=shutil.ignore_patterns(
                *FORBIDDEN_NAMES,
                *(f"*{suffix}" for suffix in FORBIDDEN_SUFFIXES),
            ),
        )
    elif src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_checksums(root: Path) -> list[dict[str, object]]:
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel == "SHA256SUMS.json":
            continue
        rows.append({
            "path": rel,
            "size": path.stat().st_size,
            "sha256": sha256(path),
        })
    return rows


def assert_no_credentials(root: Path) -> None:
    forbidden = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name in FORBIDDEN_NAMES or path.suffix in FORBIDDEN_SUFFIXES:
            forbidden.append(path.relative_to(root).as_posix())
    if forbidden:
        raise RuntimeError(f"迁移包包含禁止文件: {forbidden[:10]}")


def create_backup() -> Path:
    if not REPO.is_dir():
        raise FileNotFoundError(f"Git仓库不存在: {REPO}")

    status = run("git", "status", "--porcelain", cwd=REPO)
    if status:
        raise RuntimeError(
            "Git工作树存在未提交变更，已拒绝生成可能漏文件的备份。\n"
            "请先审核、测试并提交变更，再重新运行本脚本。"
        )

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    package_name = f"tz-codex-backup-{timestamp}"

    with tempfile.TemporaryDirectory(prefix="tz-codex-backup-") as tmp:
        stage = Path(tmp) / package_name
        stage.mkdir()

        head = run("git", "rev-parse", "HEAD", cwd=REPO)
        branch = run("git", "branch", "--show-current", cwd=REPO)
        upstream = run(
            "git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}",
            cwd=REPO,
        )
        divergence = run(
            "git", "rev-list", "--left-right", "--count", f"{upstream}...HEAD",
            cwd=REPO,
        ).split()
        behind, ahead = (int(divergence[0]), int(divergence[1]))

        # HEAD源码快照不含.git、忽略文件或凭据。
        source_zip = stage / "01_代码与记忆" / "repo-source.zip"
        source_zip.parent.mkdir(parents=True)
        run(
            "git", "archive", "--format=zip",
            f"--output={source_zip}", "HEAD",
            cwd=REPO,
        )

        # Git bundle保留全部提交和分支，可在无网络环境恢复完整历史。
        bundle = stage / "01_代码与记忆" / "stock-analysis-skill.bundle"
        run("git", "bundle", "create", str(bundle), "--all", cwd=REPO)
        run("git", "bundle", "verify", str(bundle), cwd=REPO)

        data_root = stage / "02_活动数据"
        copied = []
        missing = []
        for rel in DATA_TARGETS:
            src = WORKSPACE / rel
            if src.exists():
                copy_filtered(src, data_root / rel)
                copied.append(rel.as_posix())
            else:
                missing.append(rel.as_posix())
        missing_required = [str(rel) for rel in REQUIRED_DATA_TARGETS if rel.as_posix() in missing]
        if missing_required:
            raise RuntimeError(f"关键活动数据库缺失，拒绝生成不完整备份: {missing_required}")

        manifest = {
            "format_version": 2,
            "created_at": datetime.now().astimezone().isoformat(),
            "workspace": str(WORKSPACE),
            "git_head": head,
            "git_branch": branch,
            "git_worktree_clean": True,
            "git_upstream": upstream,
            "git_ahead": ahead,
            "git_behind": behind,
            "copied_targets": copied,
            "required_data_targets": [rel.as_posix() for rel in REQUIRED_DATA_TARGETS],
            "missing_optional_targets": missing,
            "credentials_included": False,
        }
        manifest_path = stage / "MANIFEST.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        restore = stage / "RESTORE.md"
        restore.write_text(
            "# tz-codex 本地迁移包\n\n"
            "1. 将 `02_活动数据/` 内容复制到新的 `tz-codex/`。\n"
            "2. 用 `git clone 01_代码与记忆/stock-analysis-skill.bundle repo` "
            "恢复完整Git历史；或解压 `repo-source.zip` 只恢复当前源码。\n"
            "3. 根据 `repo/.env.example` 新建 `repo/.env`，重新填写令牌；"
            "迁移包不包含任何凭据。\n"
            "4. 运行 `event_map_updater.py validate` 和单元测试确认完整性。\n"
            "5. 使用本脚本的 `--verify` 参数校验ZIP内全部SHA-256。\n",
            encoding="utf-8",
        )

        assert_no_credentials(stage)
        checksums = build_checksums(stage)
        (stage / "SHA256SUMS.json").write_text(
            json.dumps(checksums, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        zip_path = ARCHIVE_ROOT / f"{package_name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(p for p in stage.rglob("*") if p.is_file()):
                archive.write(path, Path(package_name) / path.relative_to(stage))

    verify_backup(zip_path)
    return zip_path


def verify_backup(zip_path: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="tz-codex-verify-") as tmp:
        target = Path(tmp)
        with zipfile.ZipFile(zip_path) as archive:
            bad = archive.testzip()
            if bad:
                raise RuntimeError(f"ZIP CRC校验失败: {bad}")
            archive.extractall(target)
        roots = [p for p in target.iterdir() if p.is_dir()]
        if len(roots) != 1:
            raise RuntimeError("迁移包顶层目录结构异常")
        root = roots[0]
        manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
        format_version = int(manifest.get("format_version", 1))
        rows = json.loads((root / "SHA256SUMS.json").read_text(encoding="utf-8"))
        if format_version >= 2:
            checksummed_paths = {row["path"] for row in rows}
            for rel in REQUIRED_DATA_TARGETS:
                target_path = root / "02_活动数据" / rel
                if not target_path.is_file():
                    raise RuntimeError(f"迁移包缺少关键活动数据库: {rel.as_posix()}")
                if target_path.relative_to(root).as_posix() not in checksummed_paths:
                    raise RuntimeError(f"关键活动数据库未纳入SHA-256清单: {rel.as_posix()}")
        for row in rows:
            path = root / row["path"]
            if not path.is_file():
                raise RuntimeError(f"缺失文件: {row['path']}")
            if path.stat().st_size != row["size"] or sha256(path) != row["sha256"]:
                raise RuntimeError(f"哈希不匹配: {row['path']}")
        assert_no_credentials(root)
        return format_version


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", type=Path, help="只校验已有迁移包")
    args = parser.parse_args()
    if args.verify:
        version = verify_backup(args.verify.expanduser().resolve())
        print("迁移包校验通过" if version >= 2 else "迁移包包内哈希校验通过；旧格式未校验关键数据覆盖")
        return
    try:
        output = create_backup()
    except (FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    print(output)


if __name__ == "__main__":
    main()
