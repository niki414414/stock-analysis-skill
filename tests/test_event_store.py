import importlib.util
import sqlite3
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
        "INSERT INTO company_event_links(company_id,event_pk,role_2,role_3,validation_metrics) VALUES(?,?,?,?,?)",
        (company_id, event_pk, "企业软件", "工作流Agent", "ARR与付费率"),
    )
    con.commit()
    con.close()

    store = module.EventStore(db)
    company = store.company("300001")
    sector = store.sector("AI应用")
    event = store.event("AI-SAAS-2026-001")

    assert company[0]["company_name"] == "示例软件"
    assert company[0]["role_3"] == "工作流Agent"
    assert sector[0]["event_id"] == "AI-SAAS-2026-001"
    assert event[0]["stock_code"] == "300001"


def test_extract_known_companies_ignores_generic_company_types():
    module = load_store_module()
    code_map = {"金山办公": "688111", "用友网络": "600588"}
    assert module.extract_known_companies("办公SaaS；定位样本：金山办公、用友网络", code_map) == [
        "金山办公", "用友网络"
    ]
    assert module.extract_known_companies("AI SaaS、企业软件厂商", code_map) == []
