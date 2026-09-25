import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DOSSIER = load_module(
    ROOT / "skills/market-outlook/scripts/catalyst_dossier.py", "catalyst_dossier_context_test"
)
WINDOW = load_module(
    ROOT / "skills/stock-analysis/scripts/catalyst_window_model.py", "catalyst_window_date_test"
)


def seed_event_db(path: Path):
    from skills.shared.event_store import SCHEMA_SQL

    con = sqlite3.connect(path)
    con.executescript(SCHEMA_SQL)
    con.execute("INSERT INTO metadata VALUES('built_at','2026-08-22T00:00:00Z')")
    con.executemany(
        """INSERT INTO events(
               source,event_id,sector,event_name,event_date,status,importance,trade_status,
               impact_direction,validation_metrics,notes
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        [
            ("tech", "MLCC-2026-001", "被动元件", "MLCC涨价", "2026-05起", "升温", 5,
             "部分交易", "高容产品", "报价和交期", "基础事件"),
            ("tech", "MLCC-2026-002", "被动元件", "MLCC长协", "2026-07起", "验证", 5,
             "部分交易", "长单", "正式公告", "后续事件"),
            ("tech", "CAP-2026-001", "被动元件", "其他电容涨价", "2026-06-17", "观察", 4,
             "初步交易", "电容", "涨价函", "相邻事件"),
            ("tech", "AI-SAAS-2026-001", "AI应用", "企业级AI SaaS商业化", "2026-06", "验证", 4,
             "部分交易", "企业Agent、AI安全与模型治理", "ARR和付费率", "宽主题事件"),
        ],
    )
    con.executemany(
        "INSERT INTO companies(company_name,stock_code) VALUES(?,?)",
        [("示例公司", "000001"), ("深信服", "300454"), ("办公软件公司", "688001")],
    )
    event_pk = con.execute(
        "SELECT event_pk FROM events WHERE event_id='MLCC-2026-001'"
    ).fetchone()[0]
    company_id = con.execute("SELECT company_id FROM companies").fetchone()[0]
    con.execute(
        """INSERT INTO company_event_links(
               company_id,event_pk,role_2,role_3,relation_status,benefit_tier,mapping_basis
           ) VALUES(?,?,?,?,?,?,?)""",
        (company_id, event_pk, "MLCC", "高容产品", "映射已验证", "明确映射", "测试"),
    )
    con.execute(
        """INSERT INTO early_signals(
               source,signal_id,signal_date,event_id,title,stage,content,confidence
           ) VALUES(?,?,?,?,?,?,?,?)""",
        ("tech", "SIG-1", "2026-06-20", "MLCC-2026-001", "MLCC供给", "早期", "交期拉长", "中"),
    )
    con.executemany(
        """INSERT INTO early_signals(
               source,signal_id,signal_date,event_id,title,stage,content,representative_text,confidence
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        [
            ("tech", "SIG-AI-SAFE", "2026-08-02", "AI-SAAS-2026-001", "AI安全景气映射",
             "早期", "AI安全需求可能随Agent部署增长", "深信服", "中低"),
            ("tech", "SIG-AI-OFFICE", "2026-08-03", "AI-SAAS-2026-001", "AI办公付费",
             "验证", "办公席位和续费率验证", "办公软件公司", "中"),
        ],
    )
    con.execute(
        """INSERT INTO materials(
               source,material_id,material_date,source_type,title,path_or_url,
               related_events,reliability,usage
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        ("tech", "SRC-1", "2026-02-24", "产业媒体", "MLCC考虑涨价", "https://example.test",
         "MLCC-2026-001", "A", "验证涨价"),
    )
    con.execute(
        """INSERT INTO materials(
               source,material_id,material_date,source_type,title,path_or_url,
               related_events,reliability,usage
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        ("tech", "SRC-AI-WIDE", "2026-08-01", "研究材料", "企业软件周报", "local.txt",
         "AI-SAAS-2026-001", "C", "宽主题背景"),
    )
    ai_event_pk = con.execute(
        "SELECT event_pk FROM events WHERE event_id='AI-SAAS-2026-001'"
    ).fetchone()[0]
    deep_id = con.execute("SELECT company_id FROM companies WHERE company_name='深信服'").fetchone()[0]
    office_id = con.execute(
        "SELECT company_id FROM companies WHERE company_name='办公软件公司'"
    ).fetchone()[0]
    con.executemany(
        """INSERT INTO company_event_links(
               company_id,event_pk,role_2,role_3,relation_status,benefit_tier,mapping_basis
           ) VALUES(?,?,?,?,?,?,?)""",
        [
            (deep_id, ai_event_pk, "网络安全", "安全产品", "映射已验证", "明确映射", "宽事件映射"),
            (office_id, ai_event_pk, "AI办公", "文档软件", "映射已验证", "明确映射", "宽事件映射"),
        ],
    )
    con.execute(
        """INSERT INTO corrections(
               source,update_date,issue,judgement,event_id,action,priority,status
           ) VALUES(?,?,?,?,?,?,?,?)""",
        ("tech", "2026-07-13", "降价传闻", "原厂没有降价", "MLCC-2026-001;CAP-2026-001",
         "继续核实", "中", "已处理"),
    )
    con.execute(
        """INSERT INTO forward_events(
               source,forward_id,event_name,event_time,sector,validation_metrics
           ) VALUES(?,?,?,?,?,?)""",
        ("tech", "FWD-1", "MLCC业绩验证", "2026Q3", "被动元件", "毛利率"),
    )
    con.execute(
        """INSERT INTO sector_status(
               sector_id,theme,sub_sector,stage,last_updated
           ) VALUES(?,?,?,?,?)""",
        ("passive", "被动元件", "MLCC", "过热期", "2026-08-05"),
    )
    con.commit()
    con.close()


class CatalystDossierContextTests(unittest.TestCase):
    def test_topic_context_collects_full_history_and_reports_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event_db = root / "events.db"
            memory_db = root / "memory.db"
            seed_event_db(event_db)

            context = DOSSIER.topic_context(event_db, memory_db, "MLCC")

            self.assertEqual(
                [row["event_id"] for row in context["core_events"]],
                ["MLCC-2026-001", "MLCC-2026-002"],
            )
            self.assertEqual(
                [row["event_id"] for row in context["co_mentioned_events"]], ["CAP-2026-001"]
            )
            self.assertEqual(context["coverage"]["earliest_date"], "2026-02-24")
            self.assertEqual(context["coverage"]["latest_record_date"], "2026-07-13")
            self.assertEqual(context["coverage"]["records_by_type"], {
                "event": 2, "signal": 1, "material": 1, "correction": 1,
            })
            self.assertEqual(
                context["coverage"]["events_without_company_links"],
                ["tech/MLCC-2026-002"],
            )
            self.assertEqual(context["coverage"]["events_without_parseable_date"], [])
            self.assertEqual(len(context["forward_checks"]), 1)
            self.assertEqual(len(context["sector_status"]), 1)
            self.assertEqual(context["coverage"]["price_response_count"], 0)
            json.dumps(context, ensure_ascii=False)

    def test_subtopic_separates_direct_evidence_from_parent_event_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event_db = root / "events.db"
            seed_event_db(event_db)

            context = DOSSIER.topic_context(event_db, root / "memory.db", "AI安全")

            self.assertEqual(context["topic_status"], "supporting_evidence_only")
            self.assertEqual(context["core_events"], [])
            self.assertEqual(
                [row["event_id"] for row in context["parent_events"]], ["AI-SAAS-2026-001"]
            )
            self.assertEqual(
                [row["signal_id"] for row in context["supporting_records"]["signals"]],
                ["SIG-AI-SAFE"],
            )
            self.assertEqual(
                [row["record_id"] for row in context["timeline"]], ["SIG-AI-SAFE"]
            )
            parent_ids = {row["record_id"] for row in context["parent_context"]["timeline"]}
            self.assertIn("AI-SAAS-2026-001", parent_ids)
            self.assertIn("SIG-AI-OFFICE", parent_ids)
            self.assertIn("SRC-AI-WIDE", parent_ids)
            self.assertEqual(context["companies"], [])
            self.assertEqual(
                [row["company_name"] for row in context["company_mentions"]], ["深信服"]
            )
            self.assertIn(
                "主题尚未形成独立事件；上级事件仅作产业背景",
                context["coverage"]["warnings"],
            )

    def test_uncovered_topic_reports_coverage_gap_without_fake_lifecycle_warnings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event_db = root / "events.db"
            seed_event_db(event_db)

            context = DOSSIER.topic_context(event_db, root / "memory.db", "乳制品")

            self.assertEqual(context["topic_status"], "uncovered")
            self.assertEqual(context["timeline"], [])
            self.assertEqual(context["parent_events"], [])
            self.assertEqual(context["coverage"]["warnings"], ["事件库尚未覆盖该主题"])

    def test_month_level_event_date_is_parseable_without_claiming_exact_source_date(self):
        self.assertEqual(WINDOW.parse_event_date("2026-07起"), date(2026, 7, 1))
        self.assertEqual(WINDOW.parse_event_date("2026-04至2026-06"), date(2026, 4, 1))
        self.assertEqual(WINDOW.parse_event_date("2026Q3"), date(2026, 7, 1))


if __name__ == "__main__":
    unittest.main()
