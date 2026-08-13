#!/usr/bin/env python3
"""SQLite-primary event-map write/export command line."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skills.shared.event_store import DEFAULT_DB  # noqa: E402
from skills.shared.event_store_cutover import (  # noqa: E402
    apply_package_atomic, bootstrap_sqlite_primary, export_excel_from_db, export_source_rows,
)


def main():
    parser = argparse.ArgumentParser(description="事件地图SQLite主库切换工具")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap", help="从当前CSV一次性启动SQLite唯一事实源模式")
    apply = sub.add_parser("apply", help="事务应用updater格式变更包")
    apply.add_argument("--source", choices=["tech", "nonfin"], required=True)
    apply.add_argument("--changes", required=True)
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
        result = apply_package_atomic(args.db, args.changes, args.source)
    elif args.cmd == "export-csv":
        result = export_source_rows(args.db, args.output_root, args.date, args.source)
    else:
        result = {"output": export_excel_from_db(args.db, args.output, args.source)}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
