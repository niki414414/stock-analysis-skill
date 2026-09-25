import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skills.shared.event_store import SCHEMA_SQL
from skills.shared.event_store_cutover import (
    apply_changes_transaction, export_excel_from_db, export_source_rows,
)
from scripts.event_db_cutover import (
    record_lifecycle_observations, validate_lifecycle_observations,
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


class EventStoreCutoverTests(unittest.TestCase):
    def test_event_change_requires_lifecycle_or_explicit_exemption(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "event.db"
            con = seed_source_db(db)
            con.close()
            changes = {"updates": {"events": [{
                "id_value": "EVT-2026-001", "fields": {"事件名称": "新名称"},
            }]}}

            with self.assertRaisesRegex(ValueError, "必须包含lifecycle_observations"):
                validate_lifecycle_observations(db, changes, "tech")

            changes["lifecycle_exempt_reason"] = "仅修正错别字，不改变事实或判断"
            self.assertEqual(validate_lifecycle_observations(db, changes, "tech"), [])

    def test_lifecycle_rows_are_validated_and_recorded_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            db = tmp_path / "event.db"
            memory_db = tmp_path / "memory.db"
            con = seed_source_db(db)
            con.execute(
                "INSERT INTO events(source,event_id,event_name) VALUES(?,?,?)",
                ("tech", "EVT-2026-001", "旧名称"),
            )
            con.commit()
            con.close()
            changes = {"lifecycle_observations": [{
                "event_id": "EVT-2026-001", "observed_at": "2026-09-08",
                "kind": "evidence", "summary": "新增订单验证",
                "source_grade": "official", "source_ref": "https://example.test/source",
                "related_codes": ["000001"],
            }]}

            rows = validate_lifecycle_observations(db, changes, "tech")
            first = record_lifecycle_observations(memory_db, rows, "tech")
            second = record_lifecycle_observations(memory_db, rows, "tech")

            self.assertEqual(first, {"added": 1, "duplicate_skipped": 0})
            self.assertEqual(second, {"added": 0, "duplicate_skipped": 1})
            check = sqlite3.connect(memory_db)
            stored = check.execute(
                "SELECT event_id,kind,summary FROM catalyst_observations"
            ).fetchone()
            check.close()
            self.assertEqual(stored, ("EVT-2026-001", "evidence", "新增订单验证"))

    def test_lifecycle_unknown_event_is_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "event.db"
            con = seed_source_db(db)
            con.close()
            with self.assertRaisesRegex(KeyError, "不存在的事件"):
                validate_lifecycle_observations(db, {"lifecycle_observations": [{
                    "event_id": "MISSING", "observed_at": "2026-09-08",
                    "kind": "evidence", "summary": "无法归属",
                    "source_grade": "official",
                }]}, "tech")

    def test_transaction_updates_adds_deletes_and_exports_losslessly(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
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
            self.assertEqual(summary["events"]["updated"], 1)
            self.assertEqual(summary["sources"]["added"], 1)
            self.assertEqual(summary["signals_early"]["deleted"], 1)
            exported = export_source_rows(db, tmp_path / "out", "20260813", source="tech")
            events = pd.read_csv(exported["tech.events"], dtype=str).fillna("")
            sources = pd.read_csv(exported["tech.sources"], dtype=str).fillna("")
            self.assertEqual(events.loc[0, "事件名称"], "新名称")
            self.assertEqual(events.loc[0, "当前状态"], "已验证")
            self.assertEqual(sources.loc[0, "备注"], "保留完整中文")

    def test_transaction_rolls_back_entire_package_on_duplicate_id(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "event.db"
            con = seed_source_db(db)
            before = hashlib.sha256(db.read_bytes()).hexdigest()
            with self.assertRaises(sqlite3.IntegrityError):
                apply_changes_transaction(con, "tech", {
                    "additions": {
                        "sources": [{"来源ID": "SRC-NEW", "标题/内容": "应回滚"}],
                        "events": [{"事件ID": "EVT-2026-001", "事件名称": "重复"}],
                    }
                })
            con.close()
            self.assertEqual(before, hashlib.sha256(db.read_bytes()).hexdigest())
            check = sqlite3.connect(db)
            count = check.execute(
                "SELECT count(*) FROM source_rows WHERE row_key='SRC-NEW'"
            ).fetchone()[0]
            check.close()
            self.assertEqual(count, 0)

    def test_unknown_update_field_is_rejected_and_rolled_back(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "event.db"
            con = seed_source_db(db)
            with self.assertRaisesRegex(ValueError, "未知字段"):
                apply_changes_transaction(con, "tech", {
                    "updates": {"events": [{
                        "id_value": "EVT-2026-001", "fields": {"不存在字段": "值"},
                    }]}
                })
            row = con.execute(
                "SELECT row_json FROM source_rows WHERE row_key='EVT-2026-001'"
            ).fetchone()[0]
            self.assertEqual(json.loads(row)["事件名称"], "旧名称")
            con.close()

    def test_excel_export_contains_compatibility_sheets(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            db = tmp_path / "event.db"
            con = seed_source_db(db)
            con.close()
            output = tmp_path / "event.xlsx"
            export_excel_from_db(db, output, source="tech")
            workbook = pd.ExcelFile(output)
            expected = {"00_使用说明", "01_总览仪表盘", "02_事件总库", "10_早期信号追踪"}
            self.assertTrue(expected <= set(workbook.sheet_names))
            events = pd.read_excel(output, sheet_name="02_事件总库", dtype=str).fillna("")
            self.assertEqual(events.loc[0, "事件ID"], "EVT-2026-001")

    def test_nonfin_early_signal_can_be_stored_and_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            db = tmp_path / "event.db"
            con = seed_source_db(db)
            summary = apply_changes_transaction(con, "nonfin", {
                "additions": {"signals_early": [{
                    "signal_id": "NF-SIG-20260911-001",
                    "signal_date": "2026-09-01",
                    "对应事件": "日本长端利率与套利交易线索",
                    "早期信号内容": "日本10年期国债收益率逼近3%",
                    "confidence_level": "中",
                }]}
            })
            con.close()
            self.assertEqual(summary["signals_early"]["added"], 1)
            exported = export_source_rows(db, tmp_path / "out", "20260911", source="nonfin")
            signals = pd.read_csv(exported["nonfin.signals_early"], dtype=str).fillna("")
            self.assertEqual(signals.loc[0, "signal_id"], "NF-SIG-20260911-001")


if __name__ == "__main__":
    unittest.main()
