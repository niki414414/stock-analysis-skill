#!/usr/bin/env python3
"""Scan and audit the read-only WeChat research inbox."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[4]
DEFAULT_INBOX = Path(os.environ.get(
    "WECHAT_RESEARCH_INBOX", "/Users/niki/wechat-research/精选文章"
))
DEFAULT_LEDGER = WORKSPACE / "技能数据/运行记录/wechat-research-intake/ledger.jsonl"
SUPPORTED = {".md", ".txt", ".pdf", ".docx"}


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def metadata(path: Path) -> dict:
    result = {"title": path.stem, "published_at": None, "source_url": None, "article_id": None}
    if path.suffix.lower() != ".md":
        return result
    text = path.read_text(encoding="utf-8", errors="replace")[:12000]
    if not text.startswith("---"):
        return result
    parts = text.split("---", 2)
    if len(parts) < 3:
        return result
    for key in result:
        match = re.search(rf"(?m)^{re.escape(key)}:\s*[\"']?(.*?)[\"']?\s*$", parts[1])
        if match:
            result[key] = match.group(1).strip().strip("\"'")
    return result


def read_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def scan(inbox: Path, ledger: Path) -> dict:
    if not inbox.is_dir():
        raise FileNotFoundError(f"收件箱不存在: {inbox}")
    history = read_ledger(ledger)
    latest_by_path = {}
    latest_by_version = {}
    for row in history:
        latest_by_path[row["path"]] = row
        latest_by_version[(row["path"], row["sha256"])] = row
    files = sorted(
        path for path in inbox.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED
    )
    items = []
    counts = {"new": 0, "changed": 0, "pending": 0, "processed": 0}
    for path in files:
        resolved = str(path.resolve())
        digest = sha256(path)
        version = latest_by_version.get((resolved, digest))
        previous = latest_by_path.get(resolved)
        if version and version.get("status") in {"complete", "skip"}:
            state = "processed"
        elif version:
            state = "pending"
        elif previous:
            state = "changed"
        else:
            state = "new"
        counts[state] += 1
        stat = path.stat()
        items.append({
            "path": resolved,
            "sha256": digest,
            "bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
            "state": state,
            "previous_status": (version or previous or {}).get("status"),
            **metadata(path),
        })
    return {
        "inbox": str(inbox.resolve()),
        "ledger": str(ledger.resolve()),
        "counts": counts,
        "items": items,
    }


def record(args) -> dict:
    inbox = Path(args.inbox).resolve()
    ledger = Path(args.ledger).resolve()
    target = Path(args.file).resolve()
    if not target.is_file() or not inside(target, inbox):
        raise ValueError("file必须是收件箱内的现有文件")
    row = {
        "recorded_at": datetime.now().astimezone().isoformat(),
        "path": str(target),
        "sha256": sha256(target),
        "route": args.route,
        "status": args.status,
        "event_ids": [item.strip() for item in args.event_ids.split(",") if item.strip()],
        "analysis_ref": args.analysis_ref or None,
        "note": args.note or None,
    }
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="微信研究文章收件箱扫描与处理台账")
    parser.add_argument("--inbox", default=str(DEFAULT_INBOX))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    sub = parser.add_subparsers(dest="command", required=True)
    scan_parser = sub.add_parser("scan")
    scan_parser.add_argument("--json", action="store_true")
    record_parser = sub.add_parser("record")
    record_parser.add_argument("--file", required=True)
    record_parser.add_argument("--route", choices=["event", "thesis", "hybrid", "skip"], required=True)
    record_parser.add_argument(
        "--status", choices=["complete", "partial", "deferred", "skip"], required=True
    )
    record_parser.add_argument("--event-ids", default="")
    record_parser.add_argument("--analysis-ref")
    record_parser.add_argument("--note")
    args = parser.parse_args()
    if args.command == "scan":
        payload = scan(Path(args.inbox), Path(args.ledger))
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(payload["counts"], ensure_ascii=False))
            for item in payload["items"]:
                if item["state"] != "processed":
                    print(f"{item['state']}\t{item['published_at'] or ''}\t{item['path']}")
    else:
        print(json.dumps(record(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
