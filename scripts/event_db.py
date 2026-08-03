#!/usr/bin/env python3
"""Build and query the event-map SQLite shadow database."""
import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from skills.shared.event_store import (  # noqa: E402
    COMPANY_POOL,
    DEFAULT_AUDIT,
    DEFAULT_DB,
    DEFAULT_MIGRATION_HISTORY,
    EventStore,
    build_shadow_database,
)


def print_rows(rows, as_json=False):
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif not rows:
        print("未找到匹配记录。")
    else:
        print(pd.DataFrame(rows).fillna("").to_string(index=False))


def legacy_company_keys(keyword):
    rows = []
    for sheet in ("科技公司池", "非科技公司池"):
        try:
            df = pd.read_excel(COMPANY_POOL, sheet_name=sheet, dtype=str).fillna("")
        except Exception:
            continue
        mask = df["公司名称"].str.contains(keyword, regex=False) | df.get("关联事件ID", "").astype(str).str.contains(keyword, regex=False)
        rows.extend(df[mask].to_dict("records"))
    return rows


def legacy_sector_codes(keyword):
    codes = set()
    code_map_path = COMPANY_POOL.parent / "company_code_map.csv"
    code_map = pd.read_csv(code_map_path, dtype=str).fillna("")
    code_dict = dict(zip(code_map["name"], code_map["code"].str.zfill(6)))
    for sheet in ("科技公司池", "非科技公司池"):
        try:
            df = pd.read_excel(COMPANY_POOL, sheet_name=sheet, dtype=str).fillna("")
        except Exception:
            continue
        mask = (
            df["一级赛道"].str.contains(keyword, regex=False)
            | df["二级环节"].str.contains(keyword, regex=False)
            | df["三级环节/定位"].str.contains(keyword, regex=False)
        )
        codes.update(code_dict.get(name, "") for name in df.loc[mask, "公司名称"])
    return {code for code in codes if code}


def compare(store, mode, keyword):
    if mode == "company":
        legacy = legacy_company_keys(keyword)
        sqlite_rows = store.company(keyword)
        legacy_events = {
            token for row in legacy for token in str(row.get("关联事件ID", "")).replace("，", ",").split(",") if token
        }
        sqlite_events = {row["event_id"] for row in sqlite_rows}
        return {
            "mode": mode,
            "keyword": keyword,
            "legacy_rows": len(legacy),
            "sqlite_rows": len(sqlite_rows),
            "only_legacy_event_ids": sorted(legacy_events - sqlite_events),
            "only_sqlite_event_ids": sorted(sqlite_events - legacy_events),
        }
    legacy_codes = legacy_sector_codes(keyword)
    sqlite_rows = store.sector(keyword)
    sqlite_codes = {row["stock_code"] for row in sqlite_rows if row.get("stock_code")}
    return {
        "mode": mode,
        "keyword": keyword,
        "legacy_company_count": len(legacy_codes),
        "sqlite_company_count": len(sqlite_codes),
        "only_legacy_codes": sorted(legacy_codes - sqlite_codes),
        "only_sqlite_codes": sorted(sqlite_codes - legacy_codes),
    }


def main():
    parser = argparse.ArgumentParser(description="事件地图SQLite影子库")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite路径")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="从最新CSV/Excel重建影子库")
    p_build.add_argument("--audit-output", default=str(DEFAULT_AUDIT))
    p_build.add_argument("--json", action="store_true")

    for name, help_text in (
        ("company", "查询公司产业角色与催化"),
        ("sector", "查询赛道公司与催化"),
        ("event", "查询事件及关联公司"),
        ("search", "跨事件、公司和角色搜索"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("keyword")
        p.add_argument("--json", action="store_true")

    p_cat = sub.add_parser("catalysts", help="查询前瞻催化")
    p_cat.add_argument("--sector")
    p_cat.add_argument("--json", action="store_true")

    p_audit = sub.add_parser("audit", help="查询导入异常摘要")
    p_audit.add_argument("--json", action="store_true")

    p_migration = sub.add_parser("migration-status", help="查看主库切换的真实材料更新轮次")
    p_migration.add_argument("--json", action="store_true")

    p_cmp = sub.add_parser("compare", help="SQLite与旧公司池双读比较")
    p_cmp.add_argument("mode", choices=["company", "sector"])
    p_cmp.add_argument("keyword")
    p_cmp.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if args.cmd == "build":
        report = build_shadow_database(args.db, args.audit_output)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"影子库已构建: {args.db}")
            print(f"审计报告: {args.audit_output}")
            print(json.dumps({
                "coverage": report["coverage"],
                "table_counts": report["counts"],
                "anomalies": report["anomalies"],
            }, ensure_ascii=False, indent=2))
        return

    if args.cmd == "migration-status":
        if DEFAULT_MIGRATION_HISTORY.exists():
            payload = json.loads(DEFAULT_MIGRATION_HISTORY.read_text(encoding="utf-8"))
        else:
            payload = {
                "required_unique_cycles": 3, "completed_unique_cycles": 0,
                "remaining_cycles": 3, "material_cycle_gate_passed": False, "cycles": [],
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    store = EventStore(args.db)
    if args.cmd == "company":
        rows = store.company(args.keyword)
    elif args.cmd == "sector":
        rows = store.sector(args.keyword)
    elif args.cmd == "event":
        rows = store.event(args.keyword)
    elif args.cmd == "search":
        rows = store.search(args.keyword)
    elif args.cmd == "catalysts":
        rows = store.catalysts(args.sector)
    elif args.cmd == "audit":
        rows = store.anomalies()
    else:
        result = compare(store, args.mode, args.keyword)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print_rows(rows, args.json)


if __name__ == "__main__":
    main()
