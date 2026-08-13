import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skills.shared.event_store import SCHEMA_SQL
from skills.shared.event_store_cutover import (
    apply_changes_transaction, export_excel_from_db, export_source_rows,
)


def seed_source_db(path: Path):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA_SQL)
    rows = [
        ("tech", "events", "EVT-2026-001", 1, {
            "事件ID": "EVT-2026-001", "一级赛道": "测试", "事件名称": "旧名称",
            "当前状态": "观察", "重要程度": "★★★",
        }),
        ("tech", "signals_early", "SIG-20260813-001", 1, {
            "signal_id": "SIG-20260813-001", "对应事件": "待删除",
        }),
    ]
    con.executemany(
        "INSERT INTO source_rows(source,table_name,row_key,ordinal,row_json) VALUES(?,?,?,?,?)",
        [(source, table, key, ordinal, json.dumps(row, ensure_ascii=False, sort_keys=True))
         for source, table, key, ordinal, row in rows],
    )
    con.commit()
    return con


def test_transaction_updates_adds_deletes_and_exports_losslessly(tmp_path):
    db = tmp_path / "event.db"
    con = seed_source_db(db)
    changes = {
        "additions": {"sources": [{
            "来源ID": "SRC-20260813-001", "日期": "2026-08-13",
            "标题/内容": "新增来源", "备注": "保留完整中文",
        }]},
        "updates": {"events": [{
            "id_value": "EVT-2026-001", "fields": {"事件名称": "新名称", "当前状态": "已验证"},
        }]},
        "deletions": {"signals_early": [{"id_value": "SIG-20260813-001"}]},
    }
    summary = apply_changes_transaction(con, "tech", changes)
    con.close()

    assert summary["events"]["updated"] == 1
    assert summary["sources"]["added"] == 1
    assert summary["signals_early"]["deleted"] == 1
    exported = export_source_rows(db, tmp_path / "out", "20260813", source="tech")
    events = pd.read_csv(exported["tech.events"], dtype=str).fillna("")
    sources = pd.read_csv(exported["tech.sources"], dtype=str).fillna("")
    assert events.loc[0, "事件名称"] == "新名称"
    assert events.loc[0, "当前状态"] == "已验证"
    assert sources.loc[0, "备注"] == "保留完整中文"


def test_transaction_rolls_back_entire_package_on_duplicate_id(tmp_path):
    db = tmp_path / "event.db"
    con = seed_source_db(db)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    with pytest.raises(sqlite3.IntegrityError):
        apply_changes_transaction(con, "tech", {
            "additions": {
                "sources": [{"来源ID": "SRC-NEW", "标题/内容": "应回滚"}],
                "events": [{"事件ID": "EVT-2026-001", "事件名称": "重复"}],
            }
        })
    con.close()
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert before == after
    check = sqlite3.connect(db)
    assert check.execute(
        "SELECT count(*) FROM source_rows WHERE row_key='SRC-NEW'"
    ).fetchone()[0] == 0
    check.close()


def test_unknown_update_field_is_rejected_and_rolled_back(tmp_path):
    db = tmp_path / "event.db"
    con = seed_source_db(db)
    with pytest.raises(ValueError, match="未知字段"):
        apply_changes_transaction(con, "tech", {
            "updates": {"events": [{
                "id_value": "EVT-2026-001", "fields": {"不存在字段": "值"},
            }]}
        })
    row = con.execute(
        "SELECT row_json FROM source_rows WHERE row_key='EVT-2026-001'"
    ).fetchone()[0]
    assert json.loads(row)["事件名称"] == "旧名称"
    con.close()


def test_excel_export_contains_compatibility_sheets(tmp_path):
    db = tmp_path / "event.db"
    con = seed_source_db(db)
    con.close()
    output = tmp_path / "event.xlsx"
    export_excel_from_db(db, output, source="tech")
    workbook = pd.ExcelFile(output)
    assert {"00_使用说明", "01_总览仪表盘", "02_事件总库", "10_早期信号追踪"} <= set(workbook.sheet_names)
    events = pd.read_excel(output, sheet_name="02_事件总库", dtype=str).fillna("")
    assert events.loc[0, "事件ID"] == "EVT-2026-001"
