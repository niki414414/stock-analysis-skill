import importlib.util
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sync_company_pool_preserves_multiple_company_roles(tmp_path, monkeypatch):
    updater = load_module(
        "event_map_updater_test",
        ROOT / "skills/update-event-map/scripts/event_map_updater.py",
    )
    pool = tmp_path / "公司.xlsx"
    code_map = tmp_path / "company_code_map.csv"
    existing = pd.DataFrame([{
        "大类": "科技", "一级赛道": "CDN/边缘计算", "二级环节": "CDN服务",
        "三级环节/定位": "安全接入", "公司名称": "深信服", "市场/属性": "A股",
        "角色": "代表公司/公司类型", "来源批次": "旧批次", "关联事件ID": "OLD-2026-001",
        "入库建议": "mapping", "置信度": "中", "备注": "",
    }])
    with pd.ExcelWriter(pool, engine="openpyxl") as writer:
        existing.to_excel(writer, sheet_name="科技公司池", index=False)
    pd.DataFrame([{"name": "深信服", "code": "300454"}]).to_csv(code_map, index=False)
    monkeypatch.setattr(updater, "COMPANY_POOL", str(pool))
    monkeypatch.setattr(updater, "CODE_MAP_CSV", str(code_map))

    mapping = [{
        "事件ID": "AI-AGENT-2026-001", "一级赛道": "AI应用",
        "二级环节": "AI安全", "三级零部件/材料/设备": "Agent权限治理",
        "代表公司/公司类型": "深信服",
    }]
    first = updater.sync_company_pool("tech", mapping_rows=mapping, date_short="0803")
    second = updater.sync_company_pool("tech", mapping_rows=mapping, date_short="0803")

    result = pd.read_excel(pool, sheet_name="科技公司池")
    assert first["added"] == 1
    assert second["added"] == 0
    assert len(result[result["公司名称"] == "深信服"]) == 2
    assert "AI-AGENT-2026-001" in set(result["关联事件ID"])


def test_extract_known_companies_from_descriptive_mapping_text():
    updater = load_module(
        "event_map_updater_extract_test",
        ROOT / "skills/update-event-map/scripts/event_map_updater.py",
    )
    code_map = {"金山办公": "688111", "用友网络": "600588", "福昕软件": "688095"}
    text = "办公SaaS、企业软件；定位样本：金山办公、用友网络、福昕软件"
    assert updater._extract_known_companies(text, code_map) == ["金山办公", "用友网络", "福昕软件"]


def test_parse_event_ids_accepts_multi_segment_prefixes():
    scanner = load_module(
        "catalyst_scanner_event_id_test",
        ROOT / "skills/top-picks/references/catalyst_left_side_scanner.py",
    )
    assert scanner.parse_event_ids("AI-SAAS-2026-001;AI-AGENT-2026-002") == [
        "AI-SAAS-2026-001", "AI-AGENT-2026-002"
    ]


def test_scanner_aggregates_multiple_active_events_per_company(tmp_path, monkeypatch):
    scanner = load_module(
        "catalyst_scanner_test",
        ROOT / "skills/top-picks/references/catalyst_left_side_scanner.py",
    )
    pool = tmp_path / "公司.xlsx"
    code_map = tmp_path / "company_code_map.csv"
    rows = pd.DataFrame([
        {"公司名称": "示例公司", "市场/属性": "A股", "关联事件ID": "EVT-2026-001",
         "一级赛道": "AI应用", "二级环节": "企业软件"},
        {"公司名称": "示例公司", "市场/属性": "A股", "关联事件ID": "EVT-2026-002",
         "一级赛道": "AI应用", "二级环节": "AI安全"},
    ])
    with pd.ExcelWriter(pool, engine="openpyxl") as writer:
        rows.to_excel(writer, sheet_name="科技公司池", index=False)
        rows.iloc[0:0].to_excel(writer, sheet_name="非科技公司池", index=False)
    pd.DataFrame([{"name": "示例公司", "code": "300001"}]).to_csv(code_map, index=False)
    monkeypatch.setattr(scanner, "COMPANY_POOL", str(pool))
    monkeypatch.setattr(scanner, "CODE_MAP_CSV", str(code_map))
    active = pd.DataFrame([
        {"事件ID": "EVT-2026-001", "catalyst_score": 7, "事件名称": "事件一",
         "当前状态": "观察", "bucket": "yellow"},
        {"事件ID": "EVT-2026-002", "catalyst_score": 9, "事件名称": "事件二",
         "当前状态": "行动", "bucket": "red"},
    ])

    result = scanner.load_company_candidates(active, source="excel")

    assert len(result) == 1
    assert result.iloc[0]["event_id"] == "EVT-2026-002"
    assert result.iloc[0]["matched_catalyst_count"] == 2
    assert set(result.iloc[0]["event_ids"].split(";")) == {"EVT-2026-001", "EVT-2026-002"}


def test_scanner_reads_company_relations_from_sqlite(tmp_path, monkeypatch):
    scanner = load_module(
        "catalyst_scanner_sqlite_test",
        ROOT / "skills/top-picks/references/catalyst_left_side_scanner.py",
    )
    store_module = load_module(
        "event_store_scanner_test", ROOT / "skills/shared/event_store.py"
    )
    db = tmp_path / "event.db"
    import sqlite3
    con = sqlite3.connect(db)
    con.executescript(store_module.SCHEMA_SQL)
    con.execute("INSERT INTO companies(company_name,stock_code) VALUES('示例软件','300001')")
    con.execute(
        "INSERT INTO events(source,event_id,sector,event_name) VALUES('tech','EVT-2026-001','AI应用','事件一')"
    )
    company_id = con.execute("SELECT company_id FROM companies").fetchone()[0]
    event_pk = con.execute("SELECT event_pk FROM events").fetchone()[0]
    con.execute(
        """INSERT INTO company_event_links(
               company_id,event_pk,role_2,role_3,relation_status,benefit_tier,mapping_basis
           ) VALUES(?,?,?,?,?,?,?)""",
        (company_id,event_pk,"企业软件","工作流Agent","映射已验证","明确映射","测试"),
    )
    con.commit(); con.close()
    monkeypatch.setattr(scanner, "EVENT_DB", str(db))
    active = pd.DataFrame([{
        "事件ID": "EVT-2026-001", "catalyst_score": 9, "事件名称": "事件一",
        "当前状态": "行动", "bucket": "red", "source": "tech",
    }])

    result = scanner.load_company_candidates(active)

    assert result.iloc[0]["code"] == "300001"
    assert result.iloc[0]["company_source"] == "sqlite"
    assert result.iloc[0]["benefit_tier"] == "明确映射"


def test_scanner_sqlite_failure_never_uses_excel_implicitly(monkeypatch):
    scanner = load_module(
        "catalyst_scanner_no_implicit_excel_test",
        ROOT / "skills/top-picks/references/catalyst_left_side_scanner.py",
    )
    def fail_sqlite(_):
        raise RuntimeError("主库不可读")
    monkeypatch.setattr(scanner, "_load_company_candidates_sqlite", fail_sqlite)
    monkeypatch.setattr(scanner, "_load_company_candidates_excel",
                        lambda _: pytest.fail("不应自动读取旧Excel"))
    with pytest.raises(RuntimeError, match="主库不可读"):
        scanner.load_company_candidates(pd.DataFrame([{"事件ID": "EVT-1"}]))
