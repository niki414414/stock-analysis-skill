import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_query_module():
    path = ROOT / "skills/stock-analysis/scripts/event_map_query.py"
    spec = importlib.util.spec_from_file_location("event_map_query_sqlite_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seed_primary_db(path: Path):
    from skills.shared.event_store import SCHEMA_SQL

    con = sqlite3.connect(path)
    con.executescript(SCHEMA_SQL)
    source_rows = {
        "events": [{
            "事件ID": "EVT-2026-001", "一级赛道": "测试行业",
            "事件名称": "SQLite新增事件", "事件时间": "2026-09-08",
            "当前状态": "进行中", "重要程度": "★★★★★",
            "是否已被交易": "未交易", "是否存在预期差": "有",
            "主要影响方向": "测试设备", "后续观察指标": "订单",
            "最新更新时间": "20260908", "备注": "主库记录",
        }],
        "mapping": [{
            "映射ID": "MAP-001", "事件ID": "EVT-2026-001",
            "事件名称": "SQLite新增事件", "一级赛道": "测试行业",
            "一级产业": "测试产业", "二级环节": "测试设备",
            "三级零部件/材料/设备": "部件A", "代表公司/公司类型": "示例公司",
        }],
        "forward": [{
            "前瞻ID": "FWD-001", "事件": "订单验证", "时间": "2026-09",
            "发生概率": "高", "重要程度": "★★★★★", "一级赛道": "测试行业",
            "可能受益方向": "测试设备", "可能受损方向": "",
            "是否已被交易": "未交易", "预期差": "有",
            "后续观察指标": "订单", "事件窗口": "30天",
        }],
        "corrections": [{
            "更新日期": "2026-09-08", "问题": "订单待确认", "判断": "继续验证",
            "对应事件ID": "EVT-2026-001", "后续动作": "跟踪公告",
            "优先级": "高", "状态": "开放", "备注": "",
        }],
        "sectors_status": [{
            "sector_id": "TEST", "theme": "测试行业", "sub_sector": "测试设备",
            "stage": "验证期", "signal_stock_code": "000001",
            "event_map_status": "active", "last_updated": "20260908", "note": "",
        }],
    }
    ordinal = 0
    for table_name, rows in source_rows.items():
        for row in rows:
            ordinal += 1
            row_key = (row.get("事件ID") or row.get("映射ID") or row.get("前瞻ID")
                       or row.get("sector_id") or f"row-{ordinal}")
            con.execute(
                "INSERT INTO source_rows VALUES(?,?,?,?,?)",
                ("tech", table_name, row_key, ordinal,
                 json.dumps(row, ensure_ascii=False)),
            )
    con.execute(
        "INSERT INTO companies(company_name,stock_code) VALUES('示例公司','000001')"
    )
    con.execute(
        """INSERT INTO events(
               source,event_id,sector,event_name,event_date,status,importance,
               trade_status,expectation_gap,impact_direction,validation_metrics,last_verified
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("tech", "EVT-2026-001", "测试行业", "SQLite新增事件", "2026-09-08",
         "进行中", 5, "未交易", "有", "测试设备", "订单", "20260908"),
    )
    company_id = con.execute("SELECT company_id FROM companies").fetchone()[0]
    event_pk = con.execute("SELECT event_pk FROM events").fetchone()[0]
    con.execute(
        """INSERT INTO company_event_links(
               company_id,event_pk,role_2,role_3,relation_status,benefit_tier,mapping_basis
           ) VALUES(?,?,?,?,?,?,?)""",
        (company_id, event_pk, "测试设备", "部件A", "映射已验证", "明确映射", "测试"),
    )
    con.commit()
    con.close()


class EventMapQuerySQLitePrimaryTests(unittest.TestCase):
    def test_primary_queries_read_lossless_rows_from_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            module = load_query_module()
            db = Path(directory) / "event.db"
            seed_primary_db(db)
            module.EVENT_DB = db

            events, event_src = module.query_events(source="tech")
            mapping, mapping_src = module.query_mapping(source="tech")
            forward, forward_src = module.query_forward(source="tech")
            corrections, correction_src = module.query_corrections(source="tech")
            status, status_src = module.query_status(source="tech")

            self.assertEqual(events.loc[0, "事件名称"], "SQLite新增事件")
            self.assertEqual(mapping.loc[0, "事件ID"], "EVT-2026-001")
            self.assertEqual(forward.loc[0, "前瞻ID"], "FWD-001")
            self.assertEqual(corrections.loc[0, "对应事件ID"], "EVT-2026-001")
            self.assertEqual(status.loc[0, "sector_id"], "TEST")
            self.assertTrue(all(src.startswith(f"sqlite://{db}") for src in (
                event_src, mapping_src, forward_src, correction_src, status_src
            )))

    def test_window_model_uses_sqlite_events_and_company_links(self):
        with tempfile.TemporaryDirectory() as directory:
            module = load_query_module()
            db = Path(directory) / "event.db"
            seed_primary_db(db)
            module.EVENT_DB = db

            scored, src = module.compute_scored_events(source="tech")

            self.assertTrue(src.startswith(f"sqlite://{db}"))
            row = next(item for item in scored if item["event_id"] == "EVT-2026-001")
            self.assertEqual(row["companies"], [{
                "name": "示例公司", "code": "000001", "sub2": "测试设备", "role": "部件A",
                "relation_status": "映射已验证", "benefit_tier": "明确映射",
            }])


if __name__ == "__main__":
    unittest.main()
