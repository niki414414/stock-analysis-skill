import json
import sqlite3
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "skills/market-outlook/scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import catalyst_timeline as timeline  # noqa: E402


def test_timeline_reads_both_sources_from_sqlite_without_inventing_origin(tmp_path):
    db_path = tmp_path / "events.db"
    with sqlite3.connect(db_path) as con:
        con.execute("CREATE TABLE source_rows(source TEXT, table_name TEXT, row_key TEXT, ordinal INTEGER, row_json TEXT)")
        rows = [
            ("tech", "forward", "FWD-20260901-001", {
                "前瞻ID": "FWD-20260901-001", "事件": "科技新增节点",
                "时间": "2026-10-01", "更新时间": "2026-09-01",
            }),
            ("nonfin", "events", "SHIP-1", {
                "事件ID": "SHIP-1", "一级赛道": "航运", "事件名称": "航运事件",
            }),
            ("nonfin", "forward", "SHIP-1", {
                "事件ID": "SHIP-1", "关键日期/窗口": "未来30天", "观察指标": "运价",
            }),
        ]
        con.executemany("INSERT INTO source_rows VALUES(?,?,?,?,?)", [
            (source, table, key, index,
             json.dumps(value, ensure_ascii=False))
            for index, (source, table, key, value) in enumerate(rows, 1)
        ])
    store = timeline.EventStore(db_path)
    tech = timeline.normalize_tech(store)
    nonfin = timeline.normalize_nonfin(store)
    assert tech[0]["origin_date"] == "2026-09-01"
    assert tech[0]["target_start"] == "2026-10-01"
    assert nonfin[0]["event"] == "航运事件"
    assert nonfin[0]["origin_date"] == ""
    assert nonfin[0]["target_start"] == ""
