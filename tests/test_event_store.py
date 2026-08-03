import importlib.util
import sqlite3
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_store_module():
    path = ROOT / "skills/shared/event_store.py"
    spec = importlib.util.spec_from_file_location("event_store_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_event_store_company_sector_and_event_queries(tmp_path):
    module = load_store_module()
    db = tmp_path / "event.db"
    con = sqlite3.connect(db)
    con.executescript(module.SCHEMA_SQL)
    con.execute("INSERT INTO companies(company_name,stock_code) VALUES('示例软件','300001')")
    con.execute(
        "INSERT INTO events(source,event_id,sector,event_name,importance,status) VALUES('tech','AI-SAAS-2026-001','AI应用','企业Agent验证',4,'验证期')"
    )
    company_id = con.execute("SELECT company_id FROM companies").fetchone()[0]
    event_pk = con.execute("SELECT event_pk FROM events").fetchone()[0]
    con.execute(
        """INSERT INTO company_event_links(
               company_id,event_pk,role_2,role_3,validation_metrics,
               relation_status,benefit_tier,mapping_basis
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (company_id, event_pk, "企业软件", "工作流Agent", "ARR与付费率",
         "映射已验证", "明确映射", "测试依据"),
    )
    con.commit()
    con.close()

    store = module.EventStore(db)
    company = store.company("300001")
    sector = store.sector("AI应用")
    event = store.event("AI-SAAS-2026-001")
    candidates = store.companies_for_events([("tech", "AI-SAAS-2026-001")])

    assert company[0]["company_name"] == "示例软件"
    assert company[0]["role_3"] == "工作流Agent"
    assert company[0]["relation_status"] == "映射已验证"
    assert company[0]["benefit_tier"] == "明确映射"
    assert sector[0]["event_id"] == "AI-SAAS-2026-001"
    assert event[0]["stock_code"] == "300001"
    assert candidates[0]["company_name"] == "示例软件"
    assert candidates[0]["benefit_tier"] == "明确映射"
    assert store.companies_for_events([]) == []


def test_extract_known_companies_ignores_generic_company_types():
    module = load_store_module()
    code_map = {"金山办公": "688111", "用友网络": "600588"}
    assert module.extract_known_companies("办公SaaS；定位样本：金山办公、用友网络", code_map) == [
        "金山办公", "用友网络"
    ]
    assert module.extract_known_companies("AI SaaS、企业软件厂商", code_map) == []


def test_company_event_schema_keeps_screening_boundary(tmp_path):
    """事件库只解释召回关系，不复制六层的公司兑现与交易判断。"""
    module = load_store_module()
    db = tmp_path / "event.db"
    con = sqlite3.connect(db)
    con.executescript(module.SCHEMA_SQL)
    columns = {row[1] for row in con.execute("PRAGMA table_info(company_event_links)")}
    con.close()

    assert {"relation_status", "benefit_tier", "mapping_basis", "mapping_confidence",
            "source_ref", "last_verified"} <= columns
    assert not {"commercial_stage", "benefit_directness", "evidence_grade"} & columns


def test_pool_benefit_tier_only_downgrades_explicit_theme_wording():
    module = load_store_module()
    assert module.pool_benefit_tier({
        "二级环节": "AI大模型映射", "三级环节/定位": "参股月之暗面", "备注": ""
    }) == "主题观察"
    assert module.pool_benefit_tier({
        "二级环节": "高容MLCC", "三级环节/定位": "陶瓷粉体", "备注": "客户仍待六层核验"
    }) == "角色关联"


def test_migration_cycle_counts_only_unique_validated_snapshots(tmp_path):
    module = load_store_module()
    history_path = tmp_path / "history.json"
    audit = {
        "integrity": "ok", "foreign_key_errors": 0,
        "input_files": {"events.csv": "hash-a"},
        "sources": {"tech": "csv0803"}, "counts": {"events": 1},
        "coverage": {"event_linked_companies": 1},
    }
    first = module.record_shadow_update_cycle(audit, {"label": "one"}, history_path)
    duplicate = module.record_shadow_update_cycle(audit, {"label": "duplicate"}, history_path)
    audit["input_files"]["events.csv"] = "hash-b"
    second = module.record_shadow_update_cycle(audit, {"label": "two"}, history_path)

    assert first["completed_unique_cycles"] == 1
    assert duplicate["completed_unique_cycles"] == 1
    assert second["completed_unique_cycles"] == 2
    assert json.loads(history_path.read_text())["remaining_cycles"] == 1
