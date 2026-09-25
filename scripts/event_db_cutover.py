#!/usr/bin/env python3
"""SQLite-primary event-map write/export command line."""
import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skills.shared.catalyst_memory import (  # noqa: E402
    GRADES, KINDS, connect as connect_catalyst_memory, record_observation,
)
from skills.shared.event_store import DATA_ROOT, DEFAULT_DB  # noqa: E402
from skills.shared.event_store_cutover import (  # noqa: E402
    apply_package_atomic, bootstrap_sqlite_primary, export_excel_from_db, export_source_rows,
)


DEFAULT_MEMORY_DB = DATA_ROOT / "catalyst_research_memory.db"


def validate_lifecycle_observations(db_path, changes, default_source):
    """Validate lifecycle rows before the atomic primary-store mutation starts."""
    rows = changes.get("lifecycle_observations", []) or []
    if not isinstance(rows, list):
        raise ValueError("lifecycle_observations必须是列表")
    event_changes = bool(
        changes.get("additions", {}).get("events", [])
        or changes.get("updates", {}).get("events", [])
    )
    if event_changes and not rows and not str(
        changes.get("lifecycle_exempt_reason", "")
    ).strip():
        raise ValueError(
            "实质性事件新增/更新必须包含lifecycle_observations；"
            "纯格式修正请填写lifecycle_exempt_reason"
        )
    additions = {
        (default_source, str(row.get("事件ID", "")).strip())
        for row in changes.get("additions", {}).get("events", []) or []
    }
    existing = set()
    if Path(db_path).exists():
        con = sqlite3.connect(db_path)
        try:
            existing = set(con.execute("SELECT source,event_id FROM events").fetchall())
        finally:
            con.close()
    required = {"event_id", "observed_at", "kind", "summary", "source_grade"}
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"lifecycle_observations[{index}]必须是对象")
        missing = sorted(key for key in required if not str(row.get(key, "")).strip())
        if missing:
            raise ValueError(f"lifecycle_observations[{index}]缺少字段: {missing}")
        source = str(row.get("source") or default_source)
        event_id = str(row["event_id"]).strip()
        if (source, event_id) not in existing | additions:
            raise KeyError(f"生命周期记录引用不存在的事件: {source}/{event_id}")
        date.fromisoformat(str(row["observed_at"])[:10])
        if row["kind"] not in KINDS:
            raise ValueError(f"不支持的生命周期kind: {row['kind']}")
        if row["source_grade"] not in GRADES:
            raise ValueError(f"不支持的生命周期source_grade: {row['source_grade']}")
        if not isinstance(row.get("related_codes", []), list):
            raise ValueError(f"lifecycle_observations[{index}].related_codes必须是列表")
        if not isinstance(row.get("metadata", {}), dict):
            raise ValueError(f"lifecycle_observations[{index}].metadata必须是对象")
    return rows


def record_lifecycle_observations(memory_db, rows, default_source):
    """Append explicitly reviewed lifecycle deltas and report idempotent skips."""
    con = connect_catalyst_memory(memory_db)
    added = skipped = 0
    try:
        for row in rows:
            changed = record_observation(
                con,
                source=str(row.get("source") or default_source),
                event_id=str(row["event_id"]),
                observed_at=str(row["observed_at"]),
                kind=str(row["kind"]),
                summary=str(row["summary"]),
                source_grade=str(row["source_grade"]),
                source_ref=str(row.get("source_ref", "")),
                related_codes=row.get("related_codes", []) or [],
                metadata=row.get("metadata", {}) or {},
            )
            added += int(changed)
            skipped += int(not changed)
    finally:
        con.close()
    return {"added": added, "duplicate_skipped": skipped}


def main():
    parser = argparse.ArgumentParser(description="事件地图SQLite主库切换工具")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap", help="从当前CSV一次性启动SQLite唯一事实源模式")
    apply = sub.add_parser("apply", help="事务应用updater格式变更包")
    apply.add_argument("--source", choices=["tech", "nonfin"], required=True)
    apply.add_argument("--changes", required=True)
    apply.add_argument("--memory-db", default=str(DEFAULT_MEMORY_DB))
    export = sub.add_parser("export-csv", help="从SQLite完整导出CSV")
    export.add_argument("--output-root", required=True)
    export.add_argument("--date", required=True, help="YYYYMMDD")
    export.add_argument("--source", choices=["tech", "nonfin"])
    excel = sub.add_parser("export-excel", help="从SQLite直接导出Excel兼容文件")
    excel.add_argument("--output", required=True)
    excel.add_argument("--source", choices=["tech", "nonfin"], default="tech")
    args = parser.parse_args()
    if args.cmd == "bootstrap":
        result = bootstrap_sqlite_primary(args.db)
    elif args.cmd == "apply":
        changes = json.loads(Path(args.changes).read_text(encoding="utf-8"))
        lifecycle_rows = validate_lifecycle_observations(args.db, changes, args.source)
        result = apply_package_atomic(args.db, args.changes, args.source)
        result["lifecycle"] = record_lifecycle_observations(
            args.memory_db, lifecycle_rows, args.source
        )
    elif args.cmd == "export-csv":
        result = export_source_rows(args.db, args.output_root, args.date, args.source)
    else:
        result = {"output": export_excel_from_db(args.db, args.output, args.source)}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
