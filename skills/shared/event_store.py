#!/usr/bin/env python3
"""SQLite store for the event map.

SQLite is the production write source. CSV/Excel inputs are retained only for
bootstrap/recovery compatibility, and exports are read-only views.
Legacy function and filename names containing ``shadow`` remain temporarily to
avoid breaking consumers; ``metadata.build_mode`` defines the actual mode.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Union

import pandas as pd

from skills.shared.paths import workspace_root, event_db_path


WORKSPACE_ROOT = workspace_root()
DATA_ROOT = WORKSPACE_ROOT / "技能数据"
TECH_ROOT = DATA_ROOT / "科技产业事件"
NONFIN_ROOT = DATA_ROOT / "非科技产业事件地图"
COMPANY_POOL = DATA_ROOT / "公司.xlsx"
CODE_MAP = DATA_ROOT / "company_code_map.csv"
DEFAULT_DB = event_db_path()
DEFAULT_AUDIT = DATA_ROOT / "event_db_audit.json"
DEFAULT_MIGRATION_HISTORY = DATA_ROOT / "event_db_migration_history.json"
BJ_CODE_PREFIXES = ("83", "87", "88", "92", "43")
EVENT_ID_RE = re.compile(r"[A-Z][A-Z0-9-]*-\d{4}-\d{3}")
# 公司池保留了早期主题锚点ID；事件库后来改用正式事件ID。
# 这些兼容关系只用于迁移恢复，不代表新增事件。
POOL_EVENT_ALIASES = {
    ("tech", "AI-SOC-2026-001"): ("EDGE-AI-2026-001",),
    ("nonfin", "PHARMA-CXO"): ("CXO-2026-001", "MONKEY-2026-001"),
    ("nonfin", "PHARMA-INNOVATION"): ("INNODRUG-2026-001", "ESMO-2026-001"),
}


SCHEMA_SQL = """
PRAGMA foreign_keys=ON;
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE source_rows(
  source TEXT NOT NULL,
  table_name TEXT NOT NULL,
  row_key TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  row_json TEXT NOT NULL,
  PRIMARY KEY(source,table_name,row_key)
);
CREATE TABLE source_table_schemas(
  source TEXT NOT NULL,
  table_name TEXT NOT NULL,
  columns_json TEXT NOT NULL,
  PRIMARY KEY(source,table_name)
);
CREATE TABLE companies(
  company_id INTEGER PRIMARY KEY,
  company_name TEXT NOT NULL UNIQUE,
  stock_code TEXT UNIQUE,
  market TEXT NOT NULL DEFAULT 'A股',
  list_status TEXT,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE events(
  event_pk INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  event_id TEXT NOT NULL,
  sector TEXT,
  event_type TEXT,
  event_name TEXT NOT NULL,
  event_date TEXT,
  status TEXT,
  importance INTEGER,
  trade_status TEXT,
  expectation_gap TEXT,
  impact_direction TEXT,
  validation_metrics TEXT,
  last_verified TEXT,
  notes TEXT,
  UNIQUE(source,event_id)
);
CREATE TABLE raw_mappings(
  mapping_pk INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  mapping_id TEXT,
  event_id TEXT,
  sector TEXT,
  industry_1 TEXT,
  role_2 TEXT,
  role_3 TEXT,
  representative_text TEXT,
  source_row_json TEXT NOT NULL
);
CREATE TABLE industry_roles(
  role_id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  mapping_id TEXT,
  event_id TEXT,
  sector TEXT,
  industry_1 TEXT,
  role_2 TEXT,
  role_3 TEXT,
  company_type_text TEXT,
  validation_metrics TEXT,
  risk_notes TEXT
);
CREATE TABLE company_event_links(
  link_id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL,
  event_pk INTEGER NOT NULL,
  mapping_id TEXT,
  industry_1 TEXT,
  role_2 TEXT,
  role_3 TEXT,
  attention TEXT,
  traded_status TEXT,
  expectation_gap TEXT,
  validation_metrics TEXT,
  risk_notes TEXT,
  relation_status TEXT NOT NULL CHECK(relation_status IN ('映射已验证','已登记','待验证')),
  benefit_tier TEXT NOT NULL CHECK(benefit_tier IN ('明确映射','角色关联','主题观察')),
  mapping_basis TEXT NOT NULL,
  mapping_confidence TEXT,
  source_ref TEXT,
  last_verified TEXT,
  UNIQUE(company_id,event_pk,role_2,role_3),
  FOREIGN KEY(company_id) REFERENCES companies(company_id),
  FOREIGN KEY(event_pk) REFERENCES events(event_pk)
);
CREATE TABLE company_pool_links(
  pool_link_id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  pool_sheet TEXT NOT NULL,
  company_id INTEGER NOT NULL,
  sector TEXT,
  role_2 TEXT,
  role_3 TEXT,
  pool_event_ref TEXT,
  relation_status TEXT NOT NULL CHECK(relation_status IN ('已绑定事件','待绑定事件')),
  benefit_tier TEXT NOT NULL CHECK(benefit_tier IN ('明确映射','角色关联','主题观察')),
  mapping_basis TEXT NOT NULL,
  mapping_confidence TEXT,
  source_ref TEXT,
  UNIQUE(source,pool_sheet,company_id,sector,role_2,role_3,pool_event_ref),
  FOREIGN KEY(company_id) REFERENCES companies(company_id)
);
CREATE TABLE materials(
  material_pk INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  material_id TEXT NOT NULL,
  material_date TEXT,
  source_type TEXT,
  title TEXT,
  path_or_url TEXT,
  related_events TEXT,
  reliability TEXT,
  usage TEXT,
  notes TEXT,
  updated_at TEXT,
  UNIQUE(source,material_id)
);
CREATE TABLE material_mentions(
  mention_id INTEGER PRIMARY KEY,
  material_pk INTEGER,
  raw_company_name TEXT,
  company_id INTEGER,
  raw_text TEXT,
  suggested_event_id TEXT,
  confidence TEXT,
  review_status TEXT NOT NULL DEFAULT 'pending',
  FOREIGN KEY(material_pk) REFERENCES materials(material_pk),
  FOREIGN KEY(company_id) REFERENCES companies(company_id)
);
CREATE TABLE early_signals(
  signal_pk INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  signal_id TEXT NOT NULL,
  signal_date TEXT,
  event_id TEXT,
  title TEXT,
  stage TEXT,
  content TEXT,
  industry_role TEXT,
  representative_text TEXT,
  validation_metrics TEXT,
  trade_status TEXT,
  confidence TEXT,
  source_type TEXT,
  source_ref TEXT,
  risk_notes TEXT,
  updated_at TEXT,
  UNIQUE(source,signal_id)
);
CREATE TABLE forward_events(
  forward_pk INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  forward_id TEXT NOT NULL,
  event_name TEXT,
  event_time TEXT,
  probability TEXT,
  importance INTEGER,
  sector TEXT,
  benefit_direction TEXT,
  harm_direction TEXT,
  trade_status TEXT,
  expectation_gap TEXT,
  validation_metrics TEXT,
  event_window TEXT,
  updated_at TEXT,
  source_ref TEXT,
  UNIQUE(source,forward_id)
);
CREATE TABLE corrections(
  correction_id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  update_date TEXT,
  issue TEXT,
  judgement TEXT,
  event_id TEXT,
  action TEXT,
  priority TEXT,
  status TEXT,
  notes TEXT
);
CREATE TABLE sector_status(
  sector_id TEXT PRIMARY KEY,
  theme TEXT,
  sub_sector TEXT,
  stage TEXT,
  signal_stock_code TEXT,
  event_map_status TEXT,
  last_updated TEXT,
  note TEXT
);
CREATE TABLE import_anomalies(
  anomaly_id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  table_name TEXT NOT NULL,
  row_number INTEGER,
  issue_type TEXT NOT NULL,
  record_id TEXT,
  detail TEXT,
  row_json TEXT
);
CREATE INDEX idx_events_sector ON events(sector);
CREATE INDEX idx_events_id ON events(event_id);
CREATE INDEX idx_links_company ON company_event_links(company_id);
CREATE INDEX idx_links_event ON company_event_links(event_pk);
CREATE INDEX idx_links_role2 ON company_event_links(role_2);
CREATE INDEX idx_pool_links_company ON company_pool_links(company_id);
CREATE INDEX idx_pool_links_sector ON company_pool_links(sector);
CREATE INDEX idx_forward_sector ON forward_events(sector);
CREATE INDEX idx_anomaly_type ON import_anomalies(issue_type);
CREATE INDEX idx_source_rows_order ON source_rows(source,table_name,ordinal);
"""

SOURCE_TABLE_IDS = {
    "tech": {
        "events": "事件ID", "mapping": "映射ID", "forward": "前瞻ID",
        "corrections": None, "signals_early": "signal_id", "reviews": None,
        "sources": "来源ID", "weekly": None, "sectors_status": "sector_id",
    },
    "nonfin": {
        "events": "事件ID", "mapping": None, "forward": None,
        "signals_early": "signal_id", "sources": "来源ID",
    },
}


def _latest_tech_dir() -> Path:
    candidates = [Path(p) for p in glob.glob(str(TECH_ROOT / "csv*")) if Path(p).is_dir()]
    if not candidates:
        raise FileNotFoundError(f"未找到科技事件地图: {TECH_ROOT}")
    return max(candidates, key=_directory_date)


def _latest_nonfin_dir() -> Optional[Path]:
    candidates = [Path(p) for p in glob.glob(str(NONFIN_ROOT / "非科技主线产业事件地图_*")) if Path(p).is_dir()]
    return max(candidates, key=_directory_date) if candidates else None


def _directory_date(path: Path) -> str:
    dates = []
    for file in path.glob("*.csv"):
        dates.extend(re.findall(r"20\d{6}", file.name))
    dates.extend(re.findall(r"20\d{6}", path.name))
    return max(dates) if dates else "00000000"


def _load_csv(directory: Path, prefix: str):
    files = sorted(directory.glob(f"{prefix}_*.csv"))
    if not files:
        return pd.DataFrame(), None
    return pd.read_csv(files[-1], dtype=str).fillna(""), files[-1]


def _stars(value: Any) -> int:
    return str(value or "").count("★")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_json(row: pd.Series | dict) -> str:
    data = row.to_dict() if hasattr(row, "to_dict") else dict(row)
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _event_ids(value: Any) -> list[str]:
    return EVENT_ID_RE.findall(str(value or ""))


def _company_master() -> tuple[pd.DataFrame, dict[str, str]]:
    df = pd.read_csv(CODE_MAP, dtype=str).fillna("")
    df["code"] = df["code"].str.zfill(6)
    df = df[(df["name"].str.strip() != "") & (df["code"].str.strip() != "")]
    df = df.drop_duplicates("name", keep="last")
    return df, dict(zip(df["name"], df["code"]))


def extract_known_companies(text: Any, code_map: dict[str, str]) -> list[str]:
    value = str(text or "")
    names = [name for name in code_map if name and name in value]
    return sorted(set(names), key=lambda name: (value.find(name), -len(name)))


def pool_benefit_tier(row: pd.Series | dict) -> str:
    """Use only explicit pool wording; never infer company-level economic directness."""
    get = row.get
    text = " ".join(str(get(field, "") or "") for field in (
        "二级环节", "三级环节/定位", "备注"
    ))
    return "主题观察" if any(token in text for token in ("映射", "参股", "概念", "生态")) else "角色关联"


class EventStoreBuilder:
    def __init__(self, db_path: Union[Path, str] = DEFAULT_DB,
                 tech_dir: Optional[Union[Path, str]] = None,
                 nonfin_dir: Optional[Union[Path, str]] = None):
        self.db_path = Path(db_path)
        self.tech_dir = Path(tech_dir) if tech_dir else None
        self.nonfin_dir = Path(nonfin_dir) if nonfin_dir else None
        self.audit: dict[str, Any] = {"anomalies": {}, "sources": {}, "counts": {}}
        self.code_df, self.code_map = _company_master()
        self.company_ids: dict[str, int] = {}
        self.event_pks: dict[tuple[str, str], int] = {}

    def build(self) -> dict[str, Any]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.db_path.with_suffix(self.db_path.suffix + ".tmp")
        if temp_path.exists():
            temp_path.unlink()
        con = sqlite3.connect(temp_path)
        try:
            con.executescript(SCHEMA_SQL)
            self._import_companies(con)
            tech = self.tech_dir or _latest_tech_dir()
            nonfin = self.nonfin_dir if self.nonfin_dir is not None else _latest_nonfin_dir()
            self.audit["sources"] = {"tech": str(tech), "nonfin": str(nonfin) if nonfin else None}
            self._import_source_rows(con, "tech", tech)
            self._import_source(con, "tech", tech)
            if nonfin:
                self._import_source_rows(con, "nonfin", nonfin)
                self._import_source(con, "nonfin", nonfin)
            self._import_company_pool_links(con)
            self._write_metadata(con)
            con.commit()
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            fk_errors = con.execute("PRAGMA foreign_key_check").fetchall()
            if integrity != "ok" or fk_errors:
                raise RuntimeError(f"SQLite校验失败: integrity={integrity}, fk_errors={fk_errors[:5]}")
            self.audit["integrity"] = integrity
            self.audit["foreign_key_errors"] = 0
            self.audit["counts"] = self._table_counts(con)
            self.audit["coverage"] = {
                "security_master_companies": self.audit["counts"]["companies"],
                "event_linked_companies": con.execute(
                    "SELECT count(DISTINCT company_id) FROM company_event_links"
                ).fetchone()[0],
                "tech_event_linked_companies": con.execute(
                    "SELECT count(DISTINCT l.company_id) FROM company_event_links l "
                    "JOIN events e USING(event_pk) WHERE e.source='tech'"
                ).fetchone()[0],
                "nonfin_event_linked_companies": con.execute(
                    "SELECT count(DISTINCT l.company_id) FROM company_event_links l "
                    "JOIN events e USING(event_pk) WHERE e.source='nonfin'"
                ).fetchone()[0],
                "relationship_provenance": {
                    f"{row[0]} / {row[1]}": row[2]
                    for row in con.execute(
                        """SELECT relation_status,benefit_tier,count(*)
                           FROM company_event_links
                           GROUP BY relation_status,benefit_tier
                           ORDER BY relation_status,benefit_tier"""
                    )
                },
                "relationships_with_source": con.execute(
                    """SELECT count(*) FROM company_event_links
                       WHERE coalesce(trim(source_ref),'')<>''"""
                ).fetchone()[0],
                "relationships_with_verified_date": con.execute(
                    """SELECT count(*) FROM company_event_links
                       WHERE coalesce(trim(last_verified),'')<>''"""
                ).fetchone()[0],
                "company_pool_links": con.execute(
                    "SELECT count(*) FROM company_pool_links"
                ).fetchone()[0],
                "unbound_company_pool_links": con.execute(
                    "SELECT count(*) FROM company_pool_links WHERE relation_status='待绑定事件'"
                ).fetchone()[0],
            }
        finally:
            con.close()
        os.replace(temp_path, self.db_path)
        self.audit["db_path"] = str(self.db_path)
        self.audit["db_size_bytes"] = self.db_path.stat().st_size
        self.audit["built_at"] = datetime.now(timezone.utc).isoformat()
        return self.audit

    def _anomaly(self, con, source, table, row_number, issue, record_id, detail, row):
        con.execute(
            "INSERT INTO import_anomalies(source,table_name,row_number,issue_type,record_id,detail,row_json) VALUES(?,?,?,?,?,?,?)",
            (source, table, row_number, issue, record_id, detail, _row_json(row)),
        )
        self.audit["anomalies"][issue] = self.audit["anomalies"].get(issue, 0) + 1

    def _import_source_rows(self, con, source: str, directory: Path):
        """Preserve complete source rows so SQLite can later export without guessing fields."""
        for table_name, id_col in SOURCE_TABLE_IDS[source].items():
            frame, path = _load_csv(directory, table_name)
            if path is None:
                continue
            con.execute(
                "INSERT INTO source_table_schemas(source,table_name,columns_json) VALUES(?,?,?)",
                (source, table_name, json.dumps(list(frame.columns), ensure_ascii=False)),
            )
            for ordinal, (_, row) in enumerate(frame.iterrows(), start=1):
                raw_id = str(row.get(id_col, "") or "").strip() if id_col else ""
                if raw_id:
                    row_key = raw_id
                else:
                    payload = _row_json(row)
                    suffix = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
                    row_key = f"__row_{ordinal:06d}_{suffix}"
                con.execute(
                    "INSERT INTO source_rows(source,table_name,row_key,ordinal,row_json) VALUES(?,?,?,?,?)",
                    (source, table_name, row_key, ordinal, _row_json(row)),
                )

    def _import_companies(self, con):
        rows = [(r["name"].strip(), r["code"], "A股", r.get("list_status", "")) for _, r in self.code_df.iterrows()]
        con.executemany(
            "INSERT OR IGNORE INTO companies(company_name,stock_code,market,list_status) VALUES(?,?,?,?)", rows
        )
        self.company_ids = dict(con.execute("SELECT company_name,company_id FROM companies"))

    def _import_source(self, con, source: str, directory: Path):
        if source == "tech":
            self._import_tech(con, directory)
        else:
            self._import_nonfin(con, directory)

    def _import_tech(self, con, directory: Path):
        events, ep = _load_csv(directory, "events")
        self._record_file(ep)
        for idx, r in events.iterrows():
            eid = r.get("事件ID", "").strip()
            if not eid:
                self._anomaly(con, "tech", "events", idx + 2, "blank_id", "", "正式事件缺少ID，已跳过", r)
                continue
            cur = con.execute(
                """INSERT INTO events(source,event_id,sector,event_type,event_name,event_date,status,importance,trade_status,expectation_gap,impact_direction,validation_metrics,last_verified,notes)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("tech", eid, r.get("一级赛道", ""), r.get("二级事件", ""), r.get("事件名称", ""),
                 r.get("事件时间", ""), r.get("当前状态", ""), _stars(r.get("重要程度", "")),
                 r.get("是否已被交易", ""), r.get("是否存在预期差", ""), r.get("主要影响方向", ""),
                 r.get("后续观察指标", ""), r.get("最新更新时间", ""), r.get("备注", "")),
            )
            self.event_pks[("tech", eid)] = cur.lastrowid
        self._import_tech_mappings(con, directory)
        self._import_tech_supporting(con, directory)

    def _import_tech_mappings(self, con, directory: Path):
        mapping, path = _load_csv(directory, "mapping")
        self._record_file(path)
        for idx, r in mapping.iterrows():
            mapping_id, event_id = r.get("映射ID", ""), r.get("事件ID", "")
            rep = r.get("代表公司/公司类型", "")
            con.execute(
                "INSERT INTO raw_mappings(source,mapping_id,event_id,sector,industry_1,role_2,role_3,representative_text,source_row_json) VALUES(?,?,?,?,?,?,?,?,?)",
                ("tech", mapping_id, event_id, r.get("一级赛道", ""), r.get("一级产业", ""),
                 r.get("二级环节", ""), r.get("三级零部件/材料/设备", ""), rep, _row_json(r)),
            )
            event_pk = self.event_pks.get(("tech", event_id))
            if not event_pk:
                self._anomaly(con, "tech", "mapping", idx + 2, "orphan_event_ref", mapping_id,
                              f"mapping引用不存在的event_id={event_id}", r)
                continue
            names = extract_known_companies(rep, self.code_map)
            if not names:
                con.execute(
                    "INSERT INTO industry_roles(source,mapping_id,event_id,sector,industry_1,role_2,role_3,company_type_text,validation_metrics,risk_notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("tech", mapping_id, event_id, r.get("一级赛道", ""), r.get("一级产业", ""),
                     r.get("二级环节", ""), r.get("三级零部件/材料/设备", ""), rep,
                     r.get("验证指标", ""), r.get("风险点", "")),
                )
                continue
            for name in names:
                company_id = self.company_ids.get(name)
                if not company_id:
                    continue
                con.execute(
                    """INSERT OR IGNORE INTO company_event_links(company_id,event_pk,mapping_id,industry_1,role_2,role_3,attention,traded_status,expectation_gap,validation_metrics,risk_notes,relation_status,benefit_tier,mapping_basis,source_ref,last_verified)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (company_id, event_pk, mapping_id, r.get("一级产业", ""), r.get("二级环节", ""),
                     r.get("三级零部件/材料/设备", ""), r.get("当前市场关注度", ""),
                     r.get("是否已炒作", ""), r.get("预期差", ""), r.get("验证指标", ""),
                     r.get("风险点", ""), "映射已验证", "明确映射", "已验证产业mapping明确列名",
                     r.get("来源", ""), r.get("最新更新时间", "")),
                )

    def _import_tech_supporting(self, con, directory: Path):
        sources, path = _load_csv(directory, "sources")
        self._record_file(path)
        for idx, r in sources.iterrows():
            rid = r.get("来源ID", "").strip()
            if not rid:
                self._anomaly(con, "tech", "sources", idx + 2, "legacy_fragment", "",
                              "空来源ID行保留为迁移异常，未进入正式materials", r)
                continue
            con.execute(
                """INSERT OR IGNORE INTO materials(source,material_id,material_date,source_type,title,path_or_url,related_events,reliability,usage,notes,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("tech", rid, r.get("日期", ""), r.get("来源类型", ""), r.get("标题/内容", ""), r.get("URL", ""),
                 r.get("对应事件", ""), r.get("可靠性", ""), r.get("使用方式", ""), r.get("备注", ""), r.get("更新时间", "")),
            )
        signals, path = _load_csv(directory, "signals_early")
        self._record_file(path)
        for idx, r in signals.iterrows():
            rid = r.get("signal_id", "").strip()
            if not rid:
                self._anomaly(con, "tech", "signals_early", idx + 2, "legacy_fragment", "",
                              "空信号ID行保留为迁移异常，未进入正式early_signals", r)
                continue
            con.execute(
                """INSERT OR IGNORE INTO early_signals(source,signal_id,signal_date,event_id,title,stage,content,industry_role,representative_text,validation_metrics,trade_status,confidence,source_type,source_ref,risk_notes,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("tech", rid, r.get("signal_date", ""), r.get("event_id", ""), r.get("对应事件", ""),
                 r.get("signal_stage", ""), r.get("早期信号内容", ""), r.get("产业链环节", ""),
                 r.get("代表公司/公司类型", ""), r.get("later_validation", ""), r.get("market_trade_status", ""),
                 r.get("confidence_level", ""), r.get("source_type", ""), r.get("source_ref", ""),
                 r.get("风险/修正说明", ""), r.get("最新更新时间", "")),
            )
        forward, path = _load_csv(directory, "forward")
        self._record_file(path)
        for idx, r in forward.iterrows():
            rid = r.get("前瞻ID", "").strip()
            if not rid:
                self._anomaly(con, "tech", "forward", idx + 2, "legacy_fragment", "",
                              "空前瞻ID行保留为迁移异常，未进入正式forward_events", r)
                continue
            con.execute(
                """INSERT OR IGNORE INTO forward_events(source,forward_id,event_name,event_time,probability,importance,sector,benefit_direction,harm_direction,trade_status,expectation_gap,validation_metrics,event_window,updated_at,source_ref)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("tech", rid, r.get("事件", ""), r.get("时间", ""), r.get("发生概率", ""), _stars(r.get("重要程度", "")),
                 r.get("一级赛道", ""), r.get("可能受益方向", ""), r.get("可能受损方向", ""),
                 r.get("是否已被交易", ""), r.get("预期差", ""), r.get("后续观察指标", ""),
                 r.get("事件窗口", ""), r.get("更新时间", ""), r.get("来源", "")),
            )
        corrections, path = _load_csv(directory, "corrections")
        self._record_file(path)
        for _, r in corrections.iterrows():
            con.execute(
                "INSERT INTO corrections(source,update_date,issue,judgement,event_id,action,priority,status,notes) VALUES(?,?,?,?,?,?,?,?,?)",
                ("tech", r.get("更新日期", ""), r.get("问题", ""), r.get("判断", ""), r.get("对应事件ID", ""),
                 r.get("后续动作", ""), r.get("优先级", ""), r.get("状态", ""), r.get("备注", "")),
            )
        sectors, path = _load_csv(directory, "sectors_status")
        self._record_file(path)
        for _, r in sectors.iterrows():
            con.execute(
                "INSERT OR REPLACE INTO sector_status VALUES(?,?,?,?,?,?,?,?)",
                tuple(r.get(c, "") for c in ["sector_id", "theme", "sub_sector", "stage", "signal_stock_code", "event_map_status", "last_updated", "note"]),
            )

    def _import_nonfin(self, con, directory: Path):
        events, ep = _load_csv(directory, "events")
        self._record_file(ep)
        for idx, r in events.iterrows():
            eid = r.get("事件ID", "").strip()
            if not eid:
                self._anomaly(con, "nonfin", "events", idx + 2, "blank_id", "", "正式事件缺少ID，已跳过", r)
                continue
            cur = con.execute(
                """INSERT INTO events(source,event_id,sector,event_type,event_name,event_date,status,importance,trade_status,expectation_gap,impact_direction,validation_metrics,last_verified,notes)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("nonfin", eid, r.get("一级赛道", ""), r.get("二级事件", ""), r.get("事件名称", ""),
                 r.get("事件时间", ""), r.get("当前状态", ""), _stars(r.get("重要程度", "")),
                 r.get("是否已被交易", ""), r.get("是否存在预期差", ""), r.get("主要影响方向", ""),
                 r.get("后续观察指标", ""), r.get("最新更新时间", ""), r.get("备注", "")),
            )
            self.event_pks[("nonfin", eid)] = cur.lastrowid
        mapping, mp = _load_csv(directory, "mapping")
        self._record_file(mp)
        for idx, r in mapping.iterrows():
            eid = r.get("事件ID", "")
            event_pk = self.event_pks.get(("nonfin", eid))
            rep = r.get("代表公司类型", "")
            con.execute(
                "INSERT INTO raw_mappings(source,mapping_id,event_id,sector,industry_1,role_2,role_3,representative_text,source_row_json) VALUES(?,?,?,?,?,?,?,?,?)",
                ("nonfin", "", eid, r.get("一级赛道", ""), r.get("产业链位置", ""), r.get("受益方向", ""),
                 r.get("代表公司类型", ""), rep, _row_json(r)),
            )
            if not event_pk:
                self._anomaly(con, "nonfin", "mapping", idx + 2, "orphan_event_ref", "", f"event_id={eid}", r)
                continue
            # 非科技mapping同样包含明确的公司名；旧迁移只写raw_mappings，
            # 没有建立company_event_links，导致文章有归属但公司查询为空。
            names = extract_known_companies(rep, self.code_map)
            for name in names:
                company_id = self.company_ids.get(name)
                if not company_id:
                    continue
                con.execute(
                    """INSERT OR IGNORE INTO company_event_links(
                       company_id,event_pk,industry_1,role_2,role_3,relation_status,
                       benefit_tier,mapping_basis,mapping_confidence,source_ref,last_verified)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (company_id, event_pk, r.get("一级赛道", ""), r.get("产业链位置", ""),
                     r.get("受益方向", ""), "已登记", "角色关联",
                     "非科技mapping代表公司明确列名", r.get("备注", ""),
                     r.get("备注", "") or "非科技mapping", ""),
                )
        forward, path = _load_csv(directory, "forward")
        self._record_file(path)
        for idx, r in forward.iterrows():
            eid = r.get("事件ID", "").strip()
            if not eid:
                self._anomaly(con, "nonfin", "forward", idx + 2, "blank_id", "", "非科技前瞻缺少事件ID", r)
                continue
            rid = f"NF-FWD-{eid}"
            event = con.execute(
                "SELECT sector,event_name FROM events WHERE source='nonfin' AND event_id=?", (eid,)
            ).fetchone()
            con.execute(
                """INSERT OR IGNORE INTO forward_events(source,forward_id,event_name,event_time,probability,importance,sector,benefit_direction,harm_direction,trade_status,expectation_gap,validation_metrics,event_window,updated_at,source_ref)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("nonfin", rid, event[1] if event else eid, r.get("关键日期/窗口", ""), "", 0,
                 event[0] if event else "", r.get("可能结果", ""), "", "", r.get("验证逻辑", ""),
                 r.get("观察指标", ""), r.get("观察周期", ""), "", r.get("备注", "")),
            )
        signals, path = _load_csv(directory, "signals_early")
        self._record_file(path)
        for idx, r in signals.iterrows():
            rid = r.get("signal_id", "").strip()
            if not rid:
                self._anomaly(con, "nonfin", "signals_early", idx + 2, "legacy_fragment", "",
                              "空信号ID行保留为迁移异常，未进入正式early_signals", r)
                continue
            con.execute(
                """INSERT OR IGNORE INTO early_signals(source,signal_id,signal_date,event_id,title,stage,content,industry_role,representative_text,validation_metrics,trade_status,confidence,source_type,source_ref,risk_notes,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("nonfin", rid, r.get("signal_date", ""), r.get("event_id", ""), r.get("对应事件", ""),
                 r.get("signal_stage", ""), r.get("早期信号内容", ""), r.get("产业链环节", ""),
                 r.get("代表公司/公司类型", ""), r.get("later_validation", ""), r.get("market_trade_status", ""),
                 r.get("confidence_level", ""), r.get("source_type", ""), r.get("source_ref", ""),
                 r.get("风险/修正说明", ""), r.get("最新更新时间", "")),
            )
        sources, path = _load_csv(directory, "sources")
        self._record_file(path)
        for idx, r in sources.iterrows():
            rid = r.get("来源ID", "").strip()
            if not rid:
                self._anomaly(con, "nonfin", "sources", idx + 2, "blank_id", "", "非科技来源缺少ID", r)
                continue
            con.execute(
                """INSERT OR IGNORE INTO materials(source,material_id,material_date,source_type,title,path_or_url,related_events,reliability,usage,notes,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("nonfin", rid, r.get("发布日期/检索日期", r.get("日期", "")), r.get("来源类型", ""),
                 r.get("来源标题", r.get("标题/内容", "")), r.get("来源URL", r.get("URL", "")),
                 r.get("事件ID", r.get("对应事件", "")), r.get("可靠性", ""), "产业事件验证",
                 r.get("引用要点", ""), ""),
            )

    def _import_company_pool_links(self, con):
        if not COMPANY_POOL.exists():
            return
        for sheet, source in (("科技公司池", "tech"), ("非科技公司池", "nonfin")):
            try:
                pool = pd.read_excel(COMPANY_POOL, sheet_name=sheet, dtype=str).fillna("")
            except Exception:
                continue
            for idx, r in pool.iterrows():
                company_id = self.company_ids.get(r.get("公司名称", ""))
                if not company_id:
                    continue
                raw_event_ref = str(r.get("关联事件ID", "") or "").strip()
                refs = [part.strip() for part in re.split(r"[\s,，;；/|]+", raw_event_ref) if part.strip()]
                if not refs:
                    refs = [""]
                for eid in refs:
                    resolved_refs = [eid]
                    if eid:
                        resolved_refs.extend(POOL_EVENT_ALIASES.get((source, eid), ()))
                    resolved_refs = list(dict.fromkeys(resolved_refs))
                    resolved_pks = [self.event_pks.get((source, ref)) for ref in resolved_refs]
                    resolved_pks = [pk for pk in resolved_pks if pk]
                    event_pk = resolved_pks[0] if resolved_pks else None
                    source_parts = [
                        value for value in (r.get("来源批次", "").strip(), r.get("备注", "").strip())
                        if value
                    ]
                    con.execute(
                        """INSERT OR IGNORE INTO company_pool_links(
                           source,pool_sheet,company_id,sector,role_2,role_3,pool_event_ref,
                           relation_status,benefit_tier,mapping_basis,mapping_confidence,source_ref)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (source, sheet, company_id, r.get("一级赛道", ""),
                         r.get("二级环节", ""), r.get("三级环节/定位", ""), eid,
                         "已绑定事件" if event_pk else "待绑定事件", pool_benefit_tier(r),
                         "公司池登记", r.get("置信度", ""), "；".join(source_parts) or "公司池"),
                    )
                    if not event_pk:
                        continue
                    for linked_event_pk in resolved_pks:
                        con.execute(
                            """INSERT OR IGNORE INTO company_event_links(company_id,event_pk,industry_1,role_2,role_3,relation_status,benefit_tier,mapping_basis,mapping_confidence,source_ref,last_verified)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                            (company_id, linked_event_pk, r.get("一级赛道", ""), r.get("二级环节", ""),
                             r.get("三级环节/定位", ""), "已登记", pool_benefit_tier(r),
                             "公司池登记/历史ID兼容", r.get("置信度", ""),
                             "；".join(source_parts) or "公司池", ""),
                        )

    def _record_file(self, path: Optional[Path]):
        if path:
            self.audit.setdefault("input_files", {})[str(path)] = _file_sha256(path)

    def _write_metadata(self, con):
        values = {
            "schema_version": "3",
            "build_mode": "shadow",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "workspace_root": str(WORKSPACE_ROOT),
        }
        con.executemany("INSERT INTO metadata(key,value) VALUES(?,?)", values.items())

    @staticmethod
    def _table_counts(con) -> dict[str, int]:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {table: con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] for table in tables}


class EventStore:
    def __init__(self, db_path: Union[Path, str] = DEFAULT_DB):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"事件SQLite数据库不存在: {self.db_path}")

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in con.execute(sql, tuple(params)).fetchall()]
        finally:
            con.close()

    def source_table(self, source: str, table_name: str) -> list[dict[str, Any]]:
        """Return the lossless source rows that back the normalized tables.

        Analysis consumers use this when they still need the historical Chinese
        column contract.  The rows come from the SQLite primary store, never from
        a CSV export, and retain their original deterministic order.
        """
        rows = self._query(
            """SELECT row_json FROM source_rows
               WHERE source=? AND table_name=?
               ORDER BY ordinal,row_key""",
            (source, table_name),
        )
        return [json.loads(row["row_json"]) for row in rows]

    def company(self, keyword: str) -> list[dict[str, Any]]:
        like = f"%{keyword}%"
        rows = self._query(
            """SELECT c.stock_code,c.company_name,e.source,e.sector,e.event_id,e.event_name,e.status,
                      e.importance,e.trade_status,l.industry_1,l.role_2,l.role_3,l.relation_status,
                      l.benefit_tier,l.mapping_basis,l.mapping_confidence,l.source_ref,
                      l.validation_metrics,l.risk_notes,l.last_verified
               FROM companies c JOIN company_event_links l USING(company_id) JOIN events e USING(event_pk)
               WHERE c.company_name LIKE ? OR c.stock_code LIKE ?
               ORDER BY e.importance DESC,e.last_verified DESC,e.event_id""",
            (like, like),
        )
        rows.extend(self._query(
            """SELECT c.stock_code,c.company_name,NULL AS source,p.sector,
                      p.pool_event_ref AS event_id,NULL AS event_name,NULL AS status,
                      NULL AS importance,NULL AS trade_status,NULL AS industry_1,
                      p.role_2,p.role_3,p.relation_status,p.benefit_tier,p.mapping_basis,
                      p.mapping_confidence,p.source_ref,NULL AS validation_metrics,
                      NULL AS risk_notes,NULL AS last_verified
               FROM companies c JOIN company_pool_links p USING(company_id)
               WHERE (c.company_name LIKE ? OR c.stock_code LIKE ?)
                 AND p.relation_status='待绑定事件'
               ORDER BY c.company_name,p.sector,p.pool_event_ref""",
            (like, like),
        ))
        return rows

    def sector(self, keyword: str) -> list[dict[str, Any]]:
        like = f"%{keyword}%"
        return self._query(
            """SELECT c.stock_code,c.company_name,e.sector,e.event_id,e.event_name,e.status,e.importance,
                      e.trade_status,l.industry_1,l.role_2,l.role_3,l.relation_status,l.benefit_tier,
                      l.mapping_basis,l.mapping_confidence,l.source_ref,l.validation_metrics,l.risk_notes,l.last_verified
               FROM events e JOIN company_event_links l USING(event_pk) JOIN companies c USING(company_id)
               WHERE e.sector LIKE ? OR l.industry_1 LIKE ? OR l.role_2 LIKE ? OR l.role_3 LIKE ?
               ORDER BY e.importance DESC,c.company_name,e.event_id""",
            (like, like, like, like),
        )

    def event(self, event_id: str) -> list[dict[str, Any]]:
        return self._query(
            """SELECT e.source,e.sector,e.event_id,e.event_name,e.status,e.importance,e.trade_status,
                      e.expectation_gap,e.validation_metrics,e.notes,c.stock_code,c.company_name,
                      l.industry_1,l.role_2,l.role_3,l.relation_status,l.benefit_tier,
                      l.mapping_basis,l.mapping_confidence,l.source_ref,l.last_verified
               FROM events e LEFT JOIN company_event_links l USING(event_pk) LEFT JOIN companies c USING(company_id)
               WHERE e.event_id=? ORDER BY c.company_name""",
            (event_id,),
        )

    def companies_for_events(self, event_refs: Iterable[tuple[str, str]]) -> list[dict[str, Any]]:
        """Return normalized company links for exact ``(source, event_id)`` refs."""
        refs = list(dict.fromkeys(
            (str(source).strip(), str(event_id).strip())
            for source, event_id in event_refs
            if str(source).strip() and str(event_id).strip()
        ))
        if not refs:
            return []
        clauses = " OR ".join("(e.source=? AND e.event_id=?)" for _ in refs)
        params = [value for ref in refs for value in ref]
        return self._query(
            f"""SELECT c.stock_code,c.company_name,e.source,e.event_id,e.sector,
                       l.industry_1,l.role_2,l.role_3,l.relation_status,l.benefit_tier,
                       l.mapping_basis,l.mapping_confidence,l.source_ref,l.last_verified
                FROM events e JOIN company_event_links l USING(event_pk)
                JOIN companies c USING(company_id)
                WHERE {clauses}
                ORDER BY e.source,e.event_id,c.company_name,l.role_2,l.role_3""",
            params,
        )

    def catalysts(self, sector: Optional[str] = None):
        if sector:
            return self._query(
                "SELECT source,forward_id,event_name,event_time,probability,importance,sector,benefit_direction,harm_direction,trade_status,expectation_gap,validation_metrics,event_window,updated_at,source_ref FROM forward_events WHERE sector LIKE ? ORDER BY importance DESC,updated_at DESC",
                (f"%{sector}%",),
            )
        return self._query("SELECT source,forward_id,event_name,event_time,probability,importance,sector,benefit_direction,harm_direction,trade_status,expectation_gap,validation_metrics,event_window,updated_at,source_ref FROM forward_events ORDER BY importance DESC,updated_at DESC")

    def search(self, keyword: str) -> list[dict[str, Any]]:
        like = f"%{keyword}%"
        return self._query(
            """SELECT 'event' AS result_type,e.event_id AS result_id,e.event_name AS title,e.sector AS category,e.status AS detail
               FROM events e WHERE e.event_name LIKE ? OR e.notes LIKE ? OR e.validation_metrics LIKE ?
               UNION ALL
               SELECT 'company',c.stock_code,c.company_name,e.sector,l.role_2||' / '||l.role_3
               FROM companies c JOIN company_event_links l USING(company_id) JOIN events e USING(event_pk)
               WHERE c.company_name LIKE ? OR l.role_2 LIKE ? OR l.role_3 LIKE ?
               LIMIT 200""",
            (like, like, like, like, like, like),
        )

    def anomalies(self) -> list[dict[str, Any]]:
        return self._query(
            "SELECT issue_type,source,table_name,count(*) AS count FROM import_anomalies GROUP BY issue_type,source,table_name ORDER BY count DESC"
        )


def build_shadow_database(db_path: Union[Path, str] = DEFAULT_DB,
                          audit_path: Union[Path, str] = DEFAULT_AUDIT,
                          tech_dir: Optional[Union[Path, str]] = None,
                          nonfin_dir: Optional[Union[Path, str]] = None,
                          allow_primary_overwrite: bool = False):
    target = Path(db_path)
    if target.exists() and not allow_primary_overwrite:
        try:
            con = sqlite3.connect(target)
            mode = con.execute(
                "SELECT value FROM metadata WHERE key='build_mode'"
            ).fetchone()
            con.close()
        except sqlite3.Error:
            mode = None
        if mode and mode[0] == "sqlite_primary":
            raise RuntimeError(
                "拒绝用CSV重建sqlite_primary主库；请使用apply_package_atomic或显式恢复流程"
            )
    audit = EventStoreBuilder(db_path, tech_dir=tech_dir, nonfin_dir=nonfin_dir).build()
    audit_file = Path(audit_path)
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    audit_file.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit


def record_shadow_update_cycle(
    audit: dict[str, Any],
    context: Optional[dict[str, Any]] = None,
    history_path: Union[Path, str] = DEFAULT_MIGRATION_HISTORY,
) -> dict[str, Any]:
    """Record one unique, validated material-update snapshot for cutover gating."""
    if audit.get("integrity") != "ok" or audit.get("foreign_key_errors") != 0:
        raise ValueError("数据库完整性未通过，不得计入迁移观察轮次")
    fingerprint_payload = json.dumps(
        audit.get("input_files", {}), ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    fingerprint = hashlib.sha256(fingerprint_payload).hexdigest()
    path = Path(history_path)
    if path.exists():
        history = json.loads(path.read_text(encoding="utf-8"))
    else:
        history = {"schema_version": 1, "required_unique_cycles": 3, "cycles": []}
    context = context or {}
    material_cycle_id = context.get("material_cycle_id")
    if not material_cycle_id and context.get("date_short") and context.get("label"):
        material_cycle_id = f"{context['date_short']}:{context['label']}"

    cycle_row = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "input_fingerprint": fingerprint,
            "material_cycle_id": material_cycle_id,
            "context": context,
            "sources": audit.get("sources", {}),
            "counts": audit.get("counts", {}),
            "coverage": audit.get("coverage", {}),
            "integrity": audit.get("integrity"),
            "foreign_key_errors": audit.get("foreign_key_errors"),
    }
    same_material = next(
        (row for row in history["cycles"]
         if material_cycle_id and row.get("material_cycle_id") == material_cycle_id),
        None,
    )
    if same_material is not None:
        # One research-material batch can update tech and non-financial CSVs in
        # separate apply calls. Keep one observation cycle and replace it with
        # the final combined snapshot instead of falsely counting two rounds.
        recorded_at = same_material.get("recorded_at", cycle_row["recorded_at"])
        same_material.clear()
        same_material.update(cycle_row)
        same_material["recorded_at"] = recorded_at
        same_material["last_updated_at"] = datetime.now(timezone.utc).isoformat()
    elif not any(row.get("input_fingerprint") == fingerprint for row in history["cycles"]):
        history["cycles"].append(cycle_row)
    history["completed_unique_cycles"] = len(history["cycles"])
    history["remaining_cycles"] = max(
        0, history["required_unique_cycles"] - history["completed_unique_cycles"]
    )
    history["material_cycle_gate_passed"] = history["remaining_cycles"] == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    return history
