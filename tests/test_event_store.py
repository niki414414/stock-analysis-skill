import importlib.util
import sqlite3
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def load_store_module():
    path = ROOT / "skills/shared/event_store.py"
    spec = importlib.util.spec_from_file_location("event_store_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_updater_module():
    path = ROOT / "skills/update-event-map/scripts/event_map_updater.py"
    spec = importlib.util.spec_from_file_location("event_map_updater_delete_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_event_map_deletions_support_id_and_blank_field_match():
    module = load_updater_module()
    df = pd.DataFrame([
        {"映射ID": "MAP-001", "事件ID": "EVT-001", "备注": "formal"},
        {"映射ID": "", "事件ID": "", "备注": "legacy fragment"},
        {"映射ID": "MAP-002", "事件ID": "EVT-002", "备注": "keep"},
    ])
    cleaned, deleted = module._apply_deletions(
        df,
        "mapping",
        {"id_col": "映射ID"},
        [
            {"id_value": "MAP-001", "reason": "orphan relation"},
            {"match": {"映射ID": "", "事件ID": "", "备注": "legacy fragment"},
             "reason": "stale fragment"},
        ],
    )

    assert deleted == 2
    assert cleaned.to_dict("records") == [
        {"映射ID": "MAP-002", "事件ID": "EVT-002", "备注": "keep"}
    ]


def test_event_map_deletion_rejects_empty_selector():
    module = load_updater_module()
    df = pd.DataFrame([{"事件ID": "EVT-001"}])
    try:
        module._apply_deletions(df, "events", {"id_col": "事件ID"}, [{}])
    except ValueError as exc:
        assert "缺少id_value或match" in str(exc)
    else:
        raise AssertionError("empty deletion selector must be rejected")


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


def test_company_pool_links_preserve_unbound_excel_mappings(tmp_path):
    module = load_store_module()
    db = tmp_path / "event.db"
    con = sqlite3.connect(db)
    con.executescript(module.SCHEMA_SQL)
    con.execute("INSERT INTO companies(company_name,stock_code) VALUES('全志科技','300458')")
    company_id = con.execute("SELECT company_id FROM companies").fetchone()[0]
    con.execute(
        """INSERT INTO company_pool_links(
           source,pool_sheet,company_id,sector,role_2,role_3,pool_event_ref,
           relation_status,benefit_tier,mapping_basis,mapping_confidence,source_ref)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        ('tech', '科技公司池', company_id, '端侧AI与消费电子', 'AI SoC/端侧芯片',
         '端侧主控', 'AI-SOC-2026-001', '待绑定事件', '角色关联', '公司池登记', '中', '今日增补'),
    )
    con.commit()
    con.close()

    rows = module.EventStore(db).company('300458')
    assert len(rows) == 1
    assert rows[0]['event_id'] == 'AI-SOC-2026-001'
    assert rows[0]['relation_status'] == '待绑定事件'
    assert rows[0]['role_2'] == 'AI SoC/端侧芯片'


def test_nonfin_mapping_creates_company_event_link(tmp_path):
    module = load_store_module()
    source = tmp_path / "nonfin"
    source.mkdir()
    pd.DataFrame([{
        "事件ID": "CXO-2026-001", "一级赛道": "医药/创新药", "二级事件": "业绩验证",
        "事件名称": "CXO业绩验证", "事件时间": "2026H2", "当前状态": "进行中",
        "重要程度": "★★★★", "是否已被交易": "部分交易", "是否存在预期差": "有",
        "主要影响方向": "CXO龙头", "后续观察指标": "订单", "备注": "",
    }]).to_csv(source / "events_test.csv", index=False)
    pd.DataFrame([{
        "事件ID": "CXO-2026-001", "一级赛道": "医药/创新药", "二级事件": "业绩验证",
        "产业链位置": "CXO/CRO", "受益方向": "订单与利润改善",
        "代表公司类型": "药明康德、康龙化成、昭衍新药", "弹性来源": "订单",
        "风险点": "业务结构差异", "备注": "来源材料",
    }]).to_csv(source / "mapping_test.csv", index=False)

    db = tmp_path / "event.db"
    con = sqlite3.connect(db)
    con.executescript(module.SCHEMA_SQL)
    con.execute("INSERT INTO companies(company_name,stock_code) VALUES('康龙化成','300759')")
    con.commit()
    con.close()
    builder = module.EventStoreBuilder(db_path=db, nonfin_dir=source)
    builder.code_map = {"康龙化成": "300759"}
    con = sqlite3.connect(db)
    builder.company_ids = {"康龙化成": con.execute(
        "SELECT company_id FROM companies WHERE company_name='康龙化成'"
    ).fetchone()[0]}
    builder._import_nonfin(con, source)
    con.commit()
    con.close()
    rows = module.EventStore(db).company("300759")
    assert rows[0]["event_id"] == "CXO-2026-001"
    assert rows[0]["company_name"] == "康龙化成"


def test_csv_rebuild_refuses_to_overwrite_sqlite_primary(tmp_path):
    module = load_store_module()
    db = tmp_path / "event.db"
    con = sqlite3.connect(db)
    con.executescript(module.SCHEMA_SQL)
    con.execute("INSERT INTO metadata(key,value) VALUES('build_mode','sqlite_primary')")
    con.commit()
    con.close()
    try:
        module.build_shadow_database(db_path=db, audit_path=tmp_path / "audit.json")
    except RuntimeError as exc:
        assert "拒绝用CSV重建sqlite_primary主库" in str(exc)
    else:
        raise AssertionError("CSV rebuild must not overwrite a primary database")


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


def test_migration_cycle_merges_two_sources_from_same_material_batch(tmp_path):
    module = load_store_module()
    history_path = tmp_path / "history.json"
    audit = {
        "integrity": "ok", "foreign_key_errors": 0,
        "input_files": {"tech.csv": "hash-a"},
        "sources": {"tech": "csv0804", "nonfin": "csv0730"},
        "counts": {"events": 1}, "coverage": {},
    }
    context = {"date_short": "0804", "label": "0803_material_verified"}
    first = module.record_shadow_update_cycle(audit, {**context, "source": "tech"}, history_path)
    audit["input_files"]["nonfin.csv"] = "hash-b"
    audit["sources"]["nonfin"] = "csv0804"
    second = module.record_shadow_update_cycle(audit, {**context, "source": "nonfin"}, history_path)

    assert first["completed_unique_cycles"] == 1
    assert second["completed_unique_cycles"] == 1
    saved = json.loads(history_path.read_text())
    assert len(saved["cycles"]) == 1
    assert saved["cycles"][0]["sources"]["nonfin"] == "csv0804"
    assert "last_updated_at" in saved["cycles"][0]
