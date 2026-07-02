#!/usr/bin/env python3
"""framework_snapshot.py — 框架版本快照工具。

每次框架发生实质性修改时调用，自动生成带版本号的备份。

用法：
  python3 framework_snapshot.py --version v4.6 --note "C/D强催化左侧试探+回测验证"
  python3 framework_snapshot.py list          # 列出所有版本
  python3 framework_snapshot.py diff v4.5     # 查看某版本与当前的差异行数
"""
import argparse
import glob
import os
import shutil
from datetime import datetime

TEMPLATE_PATH = os.path.expanduser(
    "~/.claude/skills/stock-analysis/references/analysis-prompt-template.md"
)
OUTPUT_FORMAT = os.path.expanduser(
    "~/.claude/skills/stock-analysis/references/output-format-template.md"
)
VERSIONS_DIR = os.path.expanduser("~/Desktop/tz/framework_versions")
SKILL_MD = os.path.expanduser("~/.claude/skills/stock-analysis/SKILL.md")
UPDATER_SCRIPT = os.path.expanduser(
    "~/.claude/skills/update-event-map/scripts/event_map_updater.py"
)
QUERY_SCRIPT = os.path.expanduser(
    "~/.claude/skills/stock-analysis/scripts/event_map_query.py"
)
SCANNER_SCRIPT = os.path.expanduser(
    "~/.claude/skills/top-picks/references/signal_scanner.py"
)
MEMORY_DIR = os.path.expanduser("~/.claude/projects/-Users-niki/memory")


def cmd_snapshot(version: str, note: str):
    date = datetime.now().strftime("%Y%m%d")
    version_dir = os.path.join(VERSIONS_DIR, f"{version}_{date}")
    os.makedirs(version_dir, exist_ok=True)

    copied = []
    for src, subdir in [
        (TEMPLATE_PATH, "framework"),
        (OUTPUT_FORMAT, "framework"),
        (SKILL_MD, "skills"),
        (UPDATER_SCRIPT, "scripts"),
        (QUERY_SCRIPT, "scripts"),
        (SCANNER_SCRIPT, "scripts"),
    ]:
        if os.path.exists(src):
            dst_dir = os.path.join(version_dir, subdir)
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copy2(src, dst_dir)
            copied.append(os.path.basename(src))

    # Copy memory files
    mem_dst = os.path.join(version_dir, "memory")
    os.makedirs(mem_dst, exist_ok=True)
    if os.path.isdir(MEMORY_DIR):
        for f in os.listdir(MEMORY_DIR):
            if f.endswith(".md"):
                shutil.copy2(os.path.join(MEMORY_DIR, f), mem_dst)
                copied.append(f"memory/{f}")

    # Write version manifest
    manifest = f"""# Framework Snapshot {version}
Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}
Note: {note}

## Files
{chr(10).join(f'- {f}' for f in copied)}

## Restore Instructions
1. Copy framework/ files to ~/.claude/skills/stock-analysis/references/
2. Copy skills/SKILL.md to ~/.claude/skills/stock-analysis/
3. Copy scripts/ to respective skill directories
4. Copy memory/ to ~/.claude/projects/-Users-niki/memory/ (or new project path)
5. Run: python3 event_map_updater.py status  # verify data integrity
"""
    with open(os.path.join(version_dir, "MANIFEST.md"), "w") as f:
        f.write(manifest)

    print(f"快照已创建: {version_dir}")
    print(f"  版本: {version}")
    print(f"  说明: {note}")
    print(f"  文件数: {len(copied)}")
    return version_dir


def cmd_list():
    if not os.path.isdir(VERSIONS_DIR):
        print("无历史版本")
        return
    versions = sorted(glob.glob(os.path.join(VERSIONS_DIR, "v*")))
    if not versions:
        print("无历史版本")
        return
    print(f"框架版本历史 ({VERSIONS_DIR}):\n")
    for vdir in versions:
        manifest = os.path.join(vdir, "MANIFEST.md")
        note = ""
        if os.path.exists(manifest):
            with open(manifest) as f:
                for line in f:
                    if line.startswith("Note:"):
                        note = line[5:].strip()
                        break
        name = os.path.basename(vdir)
        size = sum(os.path.getsize(os.path.join(dp, fn))
                   for dp, _, fns in os.walk(vdir) for fn in fns)
        print(f"  {name:<20} {size//1024:>4}KB  {note}")


def cmd_diff(version: str):
    target = None
    for vdir in glob.glob(os.path.join(VERSIONS_DIR, f"{version}*")):
        target = vdir
        break
    if not target:
        print(f"版本 {version} 不存在")
        return

    old = os.path.join(target, "framework", "analysis-prompt-template.md")
    if not os.path.exists(old):
        print(f"旧版本模板不存在: {old}")
        return

    with open(old) as f:
        old_lines = len(f.readlines())
    with open(TEMPLATE_PATH) as f:
        new_lines = len(f.readlines())
    print(f"  {version}: {old_lines}行")
    print(f"  当前版本: {new_lines}行")
    print(f"  差异: {new_lines - old_lines:+d}行")


def main():
    parser = argparse.ArgumentParser(description="框架版本快照工具")
    sub = parser.add_subparsers(dest="cmd")

    p_snap = sub.add_parser("snapshot", help="创建新版本快照")
    p_snap.add_argument("--version", required=True, help="版本号如v4.6")
    p_snap.add_argument("--note", required=True, help="变更说明")

    sub.add_parser("list", help="列出所有版本")

    p_diff = sub.add_parser("diff", help="与某版本比较")
    p_diff.add_argument("version", help="版本号如v4.5")

    args = parser.parse_args()
    if args.cmd == "snapshot":
        cmd_snapshot(args.version, args.note)
    elif args.cmd == "list":
        cmd_list()
    elif args.cmd == "diff":
        cmd_diff(args.version)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
