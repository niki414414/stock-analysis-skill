"""Transactional SQLite write path and lossless CSV export for event-map cutover."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Union

import pandas as pd

from skills.shared.event_store import DEFAULT_AUDIT, DEFAULT_DB, SOURCE_TABLE_IDS, build_shadow_database


TABLE_COLUMNS = {
    "tech": {
        "events": ["事件ID", "一级赛道", "二级事件", "事件名称", "事件时间", "当前状态", "重要程度", "影响周期", "市场关注度", "信息来源类型", "来源URL", "是否已被交易", "是否存在预期差", "主要影响方向", "后续观察指标", "最新更新时间", "备注"],
        "mapping": ["映射ID", "事件ID", "事件名称", "一级赛道", "一级产业", "二级环节", "三级零部件/材料/设备", "代表公司/公司类型", "当前市场关注度", "是否已炒作", "预期差", "验证指标", "风险点", "最新更新时间", "来源"],
        "forward": ["前瞻ID", "事件", "时间", "发生概率", "重要程度", "一级赛道", "可能受益方向", "可能受损方向", "是否已被交易", "预期差", "后续观察指标", "事件窗口", "更新时间", "来源"],
        "corrections": ["更新日期", "问题", "判断", "对应事件ID", "后续动作", "优先级", "状态", "备注"],
        "signals_early": ["signal_id", "signal_date", "event_id", "对应事件", "signal_stage", "早期信号内容", "产业链环节", "代表公司/公司类型", "later_validation", "market_trade_status", "confidence_level", "source_type", "source_ref", "风险/修正说明", "最新更新时间"],
        "reviews": ["事件", "事件时间", "一级赛道", "事件状态", "第一阶段最先上涨方向", "第二阶段扩散方向", "第三阶段影子/补涨方向", "持续周期", "有效方向", "退潮信号", "最终结论", "更新时间"],
        "sources": ["来源ID", "日期", "来源类型", "标题/内容", "URL", "对应事件", "可靠性", "使用方式", "备注", "更新时间"],
        "weekly": ["章节", "核心结论", "关键事件/方向", "市场交易状态", "预期差", "历史相似案例", "需要跟踪的指标", "风险提示"],
        "sectors_status": ["sector_id", "theme", "sub_sector", "stage", "signal_stock_code", "event_map_status", "last_updated", "note"],
    },
    "nonfin": {
        "events": ["事件ID", "一级赛道", "二级事件", "事件名称", "事件时间", "当前状态", "重要程度", "是否已被交易", "是否存在预期差", "主要影响方向", "后续观察指标", "备注"],
        "mapping": ["事件ID", "一级赛道", "二级事件", "产业链位置", "受益方向", "代表公司类型", "弹性来源", "风险点", "备注"],
        "forward": ["事件ID", "观察周期", "关键日期/窗口", "观察指标", "验证逻辑", "可能结果", "跟踪优先级", "备注"],
        "sources": ["来源ID", "日期", "来源类型", "标题/内容", "URL", "对应事件", "可靠性"],
    },
}


def _row_key(source: str, table: str, row: dict[str, Any], ordinal: int) -> str:
    id_col = SOURCE_TABLE_IDS[source][table]
    value = str(row.get(id_col, "") or "").strip() if id_col else ""
    if value:
        return value
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return f"__row_{ordinal:06d}_{hashlib.sha256(payload.encode()).hexdigest()[:12]}"


def _assert_source_rows(con: sqlite3.Connection) -> None:
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_rows'"
    ).fetchone()
    if not exists:
        raise RuntimeError("数据库尚无source_rows；请先用新版builder重建一次")


def _columns(con: sqlite3.Connection, source: str, table: str) -> list[str]:
    row = con.execute(
        "SELECT columns_json FROM source_table_schemas WHERE source=? AND table_name=?",
        (source, table),
    ).fetchone()
    return json.loads(row[0]) if row else TABLE_COLUMNS[source][table]


def apply_changes_transaction(con: sqlite3.Connection, source: str, changes: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Apply one updater-format package to source_rows in a single transaction."""
    if source not in TABLE_COLUMNS:
        raise ValueError(f"未知source: {source}")
    summary: dict[str, dict[str, int]] = {}
    con.execute("PRAGMA foreign_keys=ON")
    _assert_source_rows(con)
    con.execute("BEGIN IMMEDIATE")
    try:
        for table, rows in changes.get("additions", {}).items():
            if table not in TABLE_COLUMNS[source]:
                raise ValueError(f"{source}不支持表: {table}")
            columns = _columns(con, source, table)
            ordinal = con.execute(
                "SELECT coalesce(max(ordinal),0) FROM source_rows WHERE source=? AND table_name=?",
                (source, table),
            ).fetchone()[0]
            for row in rows or []:
                ordinal += 1
                unknown = sorted(set(row) - set(columns))
                if unknown:
                    raise ValueError(f"{source}.{table}未知字段: {unknown}")
                normalized = {col: row.get(col, "") for col in columns}
                key = _row_key(source, table, normalized, ordinal)
                con.execute(
                    "INSERT INTO source_rows(source,table_name,row_key,ordinal,row_json) VALUES(?,?,?,?,?)",
                    (source, table, key, ordinal, json.dumps(normalized, ensure_ascii=False, sort_keys=True)),
                )
                summary.setdefault(table, {"added": 0, "updated": 0, "deleted": 0})["added"] += 1

        for table, updates in changes.get("updates", {}).items():
            id_col = SOURCE_TABLE_IDS[source].get(table)
            if table not in TABLE_COLUMNS[source] or not id_col:
                raise ValueError(f"{source}.{table}不支持按ID更新")
            for update in updates or []:
                columns = _columns(con, source, table)
                key = str(update.get("id_value", ""))
                found = con.execute(
                    "SELECT row_json FROM source_rows WHERE source=? AND table_name=? AND row_key=?",
                    (source, table, key),
                ).fetchone()
                if not found:
                    raise KeyError(f"未找到{source}.{table}.{key}")
                row = json.loads(found[0])
                for field, value in update.get("fields", {}).items():
                    if field not in columns:
                        raise ValueError(f"{source}.{table}未知字段: {field}")
                    row[field] = value
                con.execute(
                    "UPDATE source_rows SET row_json=? WHERE source=? AND table_name=? AND row_key=?",
                    (json.dumps(row, ensure_ascii=False, sort_keys=True), source, table, key),
                )
                summary.setdefault(table, {"added": 0, "updated": 0, "deleted": 0})["updated"] += 1

        for table, deletions in changes.get("deletions", {}).items():
            if table not in TABLE_COLUMNS[source]:
                raise ValueError(f"{source}不支持表: {table}")
            id_col = SOURCE_TABLE_IDS[source].get(table)
            for deletion in deletions or []:
                id_value = deletion.get("id_value")
                match = deletion.get("match") or {}
                if id_value is not None and id_col:
                    match = {id_col: id_value}
                if not match:
                    raise ValueError(f"{source}.{table} deletion缺少选择器")
                candidates = con.execute(
                    "SELECT row_key,row_json FROM source_rows WHERE source=? AND table_name=?",
                    (source, table),
                ).fetchall()
                keys = []
                for key, payload in candidates:
                    row = json.loads(payload)
                    if all(str(row.get(col, "") or "").strip() == str(value or "").strip()
                           for col, value in match.items()):
                        keys.append(key)
                if not keys:
                    raise KeyError(f"未找到{source}.{table} deletion={match}")
                con.executemany(
                    "DELETE FROM source_rows WHERE source=? AND table_name=? AND row_key=?",
                    [(source, table, key) for key in keys],
                )
                summary.setdefault(table, {"added": 0, "updated": 0, "deleted": 0})["deleted"] += len(keys)
        con.commit()
    except Exception:
        con.rollback()
        raise
    return summary


def export_source_rows(db_path: Union[Path, str], output_root: Union[Path, str], date: str,
                       source: str | None = None) -> dict[str, str]:
    """Export complete source rows to deterministic UTF-8-SIG CSV files."""
    root = Path(output_root)
    con = sqlite3.connect(db_path)
    _assert_source_rows(con)
    exported: dict[str, str] = {}
    sources = [source] if source else ["tech", "nonfin"]
    try:
        for src in sources:
            directory = root / (f"csv{date[-4:]}" if src == "tech" else f"非科技主线产业事件地图_CSV包_{date}")
            directory.mkdir(parents=True, exist_ok=True)
            for table, columns in TABLE_COLUMNS[src].items():
                columns = _columns(con, src, table)
                payloads = con.execute(
                    "SELECT row_json FROM source_rows WHERE source=? AND table_name=? ORDER BY ordinal,row_key",
                    (src, table),
                ).fetchall()
                rows = [json.loads(row[0]) for row in payloads]
                frame = pd.DataFrame(rows, columns=columns)
                path = directory / f"{table}_{date}_sqlite_export.csv"
                frame.to_csv(path, index=False, encoding="utf-8-sig")
                exported[f"{src}.{table}"] = str(path)
    finally:
        con.close()
    return exported


def export_excel_from_db(db_path: Union[Path, str], output_path: Union[Path, str],
                         source: str = "tech") -> str:
    """Export complete source rows directly to an Excel compatibility workbook."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_names = {
        "tech": {
            "events": "02_事件总库", "mapping": "03_产业映射树", "reviews": "04_事件复盘库",
            "forward": "05_未来12个月前瞻", "weekly": "06_周报模板",
            "corrections": "07_动态修正清单", "sources": "08_来源与调研记录",
            "signals_early": "10_早期信号追踪", "sectors_status": "11_赛道状态",
        },
        "nonfin": {
            "events": "02_事件总库", "mapping": "03_产业映射树",
            "forward": "05_前瞻跟踪", "sources": "08_来源记录",
        },
    }
    con = sqlite3.connect(db_path)
    _assert_source_rows(con)
    try:
        counts = {}
        frames = {}
        for table, columns in TABLE_COLUMNS[source].items():
            columns = _columns(con, source, table)
            payloads = con.execute(
                "SELECT row_json FROM source_rows WHERE source=? AND table_name=? ORDER BY ordinal,row_key",
                (source, table),
            ).fetchall()
            frames[table] = pd.DataFrame([json.loads(row[0]) for row in payloads], columns=columns)
            counts[table] = len(payloads)
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            pd.DataFrame({"说明": ["本文件由SQLite唯一事实源直接导出，不得反向编辑入库。"]}).to_excel(
                writer, sheet_name="00_使用说明", index=False
            )
            pd.DataFrame([{"表": table, "行数": count} for table, count in counts.items()]).to_excel(
                writer, sheet_name="01_总览仪表盘", index=False
            )
            for table, frame in frames.items():
                frame.to_excel(writer, sheet_name=sheet_names[source][table], index=False)
    finally:
        con.close()
    return str(path)


def apply_package_atomic(db_path: Union[Path, str], changes_path: Union[Path, str], source: str,
                         audit_path: Union[Path, str] = DEFAULT_AUDIT) -> dict[str, Any]:
    """Stage, mutate, export, rebuild and atomically replace the production database."""
    target = Path(db_path)
    changes = json.loads(Path(changes_path).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="event-db-cutover-") as tmp:
        tmp_root = Path(tmp)
        staged = tmp_root / "staged.db"
        shutil.copy2(target, staged)
        con = sqlite3.connect(staged)
        try:
            summary = apply_changes_transaction(con, source, changes)
        finally:
            con.close()
        export_root = tmp_root / "export"
        export_source_rows(staged, export_root, changes.get("date", "20990101"))
        tech_dir = next(export_root.glob("csv*"))
        nonfin_dir = next(export_root.glob("非科技主线产业事件地图_CSV包_*"))
        rebuilt = tmp_root / "rebuilt.db"
        temp_audit = tmp_root / "audit.json"
        audit = build_shadow_database(rebuilt, temp_audit, tech_dir=tech_dir, nonfin_dir=nonfin_dir)
        con = sqlite3.connect(rebuilt)
        con.execute("UPDATE metadata SET value='sqlite_primary' WHERE key='build_mode'")
        con.commit()
        con.close()
        audit["sources"] = {"tech": "sqlite://source_rows/tech", "nonfin": "sqlite://source_rows/nonfin"}
        audit["write_mode"] = "sqlite_primary_atomic"
        audit["change_package"] = str(Path(changes_path).resolve())
        os.replace(rebuilt, target)
        Path(audit_path).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"summary": summary, "audit": audit}


def bootstrap_sqlite_primary(db_path: Union[Path, str] = DEFAULT_DB,
                             audit_path: Union[Path, str] = DEFAULT_AUDIT) -> dict[str, Any]:
    """Build complete source_rows from current CSVs, validate, then atomically enable primary mode."""
    target = Path(db_path)
    with tempfile.TemporaryDirectory(prefix="event-db-bootstrap-") as tmp:
        staged = Path(tmp) / "primary.db"
        temp_audit = Path(tmp) / "audit.json"
        audit = build_shadow_database(staged, temp_audit)
        con = sqlite3.connect(staged)
        con.execute("UPDATE metadata SET value='sqlite_primary' WHERE key='build_mode'")
        con.commit()
        mode = con.execute("SELECT value FROM metadata WHERE key='build_mode'").fetchone()[0]
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        fk_errors = con.execute("PRAGMA foreign_key_check").fetchall()
        con.close()
        if mode != "sqlite_primary" or integrity != "ok" or fk_errors:
            raise RuntimeError("SQLite主库启动校验失败")
        os.replace(staged, target)
        audit["write_mode"] = "sqlite_primary"
        audit["sources"] = {"tech": "sqlite://source_rows/tech", "nonfin": "sqlite://source_rows/nonfin"}
        Path(audit_path).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit
