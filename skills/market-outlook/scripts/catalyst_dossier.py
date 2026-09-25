#!/usr/bin/env python3
"""Query and maintain a catalyst's complete, longitudinal research dossier."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = Path(os.environ.get("TZ_CODEX_HOME", ROOT.parent))
sys.path.insert(0, str(ROOT))

from skills.shared.catalyst_memory import (  # noqa: E402
    basket_medians, calculate_price_response, connect, observations,
    intake_queue, record_intake, record_observation, resolve_intake,
    response_summary, store_price_response,
)

EVENT_DB = WORKSPACE / "技能数据" / "event_map_shadow.db"
MEMORY_DB = WORKSPACE / "技能数据" / "catalyst_research_memory.db"
EVENT_ID_RE = re.compile(r"[A-Z][A-Z0-9-]*-\d{4}-\d{3}")


def _dict_rows(con: sqlite3.Connection, sql: str, params=()) -> list[dict[str, Any]]:
    return [dict(row) for row in con.execute(sql, params).fetchall()]


def _text_matches(row: dict[str, Any], fields: tuple[str, ...], terms: tuple[str, ...]) -> bool:
    haystack = " ".join(str(row.get(field, "") or "") for field in fields).casefold()
    return any(term and term in haystack for term in terms)


def _context_date(value: Any) -> str:
    """Return a sortable approximate ISO date while preserving raw dates elsewhere."""
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", text)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    match = re.match(r"^(20\d{2})(\d{2})(\d{2})$", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    match = re.search(r"(20\d{2})[-年](\d{1,2})", text)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-01"
    match = re.search(r"(20\d{2})[年]?Q([1-4])", text, re.IGNORECASE)
    if match:
        month = {"1": 1, "2": 4, "3": 7, "4": 10}[match.group(2)]
        return f"{int(match.group(1)):04d}-{month:02d}-01"
    return ""


SIGNAL_FIELDS = (
    "event_id", "title", "content", "industry_role", "representative_text",
    "validation_metrics", "source_ref", "risk_notes",
)
MATERIAL_FIELDS = ("title", "related_events", "usage", "notes", "path_or_url")
CORRECTION_FIELDS = ("event_id", "issue", "judgement", "action", "notes")
FORWARD_FIELDS = (
    "forward_id", "event_name", "sector", "benefit_direction", "harm_direction",
    "expectation_gap", "validation_metrics", "source_ref",
)
COMPANY_LINK_FIELDS = (
    "company_name", "industry_1", "role_2", "role_3", "mapping_basis", "source_ref",
)
SECTOR_FIELDS = ("sector_id", "theme", "sub_sector", "event_map_status", "note")


def _company_links(
    con: sqlite3.Connection, refs: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    if not refs:
        return []
    clauses = " OR ".join("(e.source=? AND e.event_id=?)" for _ in refs)
    params = tuple(value for ref in sorted(refs) for value in ref)
    return _dict_rows(
        con,
        f"""SELECT e.source,e.event_id,c.stock_code,c.company_name,l.industry_1,
                   l.role_2,l.role_3,l.relation_status,l.benefit_tier,
                   l.mapping_basis,l.mapping_confidence,l.source_ref,l.last_verified
            FROM events e JOIN company_event_links l USING(event_pk)
            JOIN companies c USING(company_id)
            WHERE {clauses}
            ORDER BY e.source,e.event_id,l.benefit_tier,c.stock_code""",
        params,
    )


def _record_key(record_type: str, row: dict[str, Any]) -> tuple[str, str, str]:
    id_field = {
        "signal": "signal_id", "material": "material_id", "correction": "correction_id",
    }[record_type]
    return record_type, str(row.get("source", "")), str(row.get(id_field, ""))


def _company_mentions(
    con: sqlite3.Connection,
    direct_records: tuple[tuple[str, list[dict[str, Any]]], ...],
) -> list[dict[str, Any]]:
    """Return company names explicitly present in direct evidence, not inferred mappings."""
    documents = []
    for record_type, rows in direct_records:
        for row in rows:
            documents.append((_record_key(record_type, row), " ".join(
                str(value or "") for value in row.values()
            )))
    if not documents:
        return []
    mentions = []
    for company in _dict_rows(
        con, "SELECT stock_code,company_name FROM companies WHERE active=1 ORDER BY stock_code"
    ):
        name = str(company.get("company_name", "") or "").strip()
        if len(name) < 2:
            continue
        evidence = [
            {"record_type": key[0], "source": key[1], "record_id": key[2]}
            for key, text in documents if name in text
        ]
        if evidence:
            mentions.append({
                **company,
                "mention_status": "text_mention_only",
                "evidence_mentions": evidence,
            })
    return mentions


def _timeline_rows(
    events: list[dict[str, Any]], signals: list[dict[str, Any]],
    materials: list[dict[str, Any]], corrections: list[dict[str, Any]],
    lifecycle_observations: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    timeline = []
    for row in events:
        timeline.append({
            "date": row.get("event_date") or row.get("last_verified") or "",
            "record_type": "event", "source": row.get("source"),
            "record_id": row.get("event_id"), "event_ids": [row.get("event_id")],
            "title": row.get("event_name"),
            "detail": row.get("status"), "evidence_grade": "",
            "source_ref": "", "trade_status": row.get("trade_status"),
        })
    for row in signals:
        timeline.append({
            "date": row.get("signal_date") or row.get("updated_at") or "",
            "record_type": "signal", "source": row.get("source"),
            "record_id": row.get("signal_id"),
            "event_ids": EVENT_ID_RE.findall(str(row.get("event_id", "") or "")),
            "title": row.get("title"), "detail": row.get("content"),
            "evidence_grade": row.get("confidence"), "source_ref": row.get("source_ref"),
            "stage": row.get("stage"),
        })
    for row in materials:
        timeline.append({
            "date": row.get("material_date") or row.get("updated_at") or "",
            "record_type": "material", "source": row.get("source"),
            "record_id": row.get("material_id"),
            "event_ids": EVENT_ID_RE.findall(str(row.get("related_events", "") or "")),
            "title": row.get("title"), "detail": row.get("usage") or row.get("notes"),
            "evidence_grade": row.get("reliability"), "source_ref": row.get("path_or_url"),
        })
    for row in corrections:
        timeline.append({
            "date": row.get("update_date") or "", "record_type": "correction",
            "source": row.get("source"), "record_id": row.get("correction_id"),
            "event_ids": EVENT_ID_RE.findall(str(row.get("event_id", "") or "")),
            "title": row.get("issue"), "detail": row.get("judgement"),
            "evidence_grade": row.get("priority"), "source_ref": "",
            "status": row.get("status"), "action": row.get("action"),
        })
    for index, row in enumerate(lifecycle_observations or [], 1):
        timeline.append({
            "date": row.get("observed_at") or "", "record_type": "observation",
            "source": row.get("source"), "record_id": f"observation-{index}",
            "event_ids": [row.get("event_id")], "title": row.get("kind"),
            "detail": row.get("summary"), "evidence_grade": row.get("source_grade"),
            "source_ref": row.get("source_ref"),
        })
    for row in timeline:
        row["normalized_date"] = _context_date(row.get("date"))
    timeline.sort(key=lambda row: (
        row["normalized_date"] or "9999-99-99", row["record_type"], str(row["record_id"])
    ))
    return timeline


def topic_context(
    event_db: Path, memory_db: Path, keyword: str,
) -> dict[str, Any]:
    """Build an evidence bundle while separating a subtopic from its parent event."""
    con = sqlite3.connect(event_db)
    con.row_factory = sqlite3.Row
    query = keyword.strip()
    like = f"%{query}%"
    core_events = _dict_rows(
        con,
        """SELECT * FROM events
           WHERE event_id LIKE ? OR event_name LIKE ?
           ORDER BY coalesce(event_date,last_verified),source,event_id""",
        (like, like),
    )
    match_mode = "event_id_or_name"
    parent_events: list[dict[str, Any]] = []
    if not core_events:
        parent_events = _dict_rows(
            con,
            """SELECT * FROM events
               WHERE sector LIKE ? OR impact_direction LIKE ?
                  OR validation_metrics LIKE ? OR notes LIKE ?
               ORDER BY coalesce(event_date,last_verified),source,event_id""",
            (like, like, like, like),
        )
        match_mode = "supporting_evidence_only"
    core_events.sort(key=lambda row: (
        _context_date(row.get("event_date") or row.get("last_verified")) or "9999-99-99",
        row.get("source", ""), row.get("event_id", ""),
    ))
    parent_events.sort(key=lambda row: (
        _context_date(row.get("event_date") or row.get("last_verified")) or "9999-99-99",
        row.get("source", ""), row.get("event_id", ""),
    ))
    core_refs = {(row["source"], row["event_id"]) for row in core_events}
    core_ids = {row["event_id"] for row in core_events}
    parent_refs = {(row["source"], row["event_id"]) for row in parent_events}
    parent_ids = {row["event_id"] for row in parent_events}
    direct_terms = (query.casefold(),)
    associated_terms = tuple({
        query.casefold(), *(event_id.casefold() for event_id in (core_ids or parent_ids)),
    })

    all_signals = _dict_rows(con, "SELECT * FROM early_signals")
    all_materials = _dict_rows(con, "SELECT * FROM materials")
    all_corrections = _dict_rows(con, "SELECT * FROM corrections")
    all_forward = _dict_rows(con, "SELECT * FROM forward_events")

    if core_events:
        signals = [row for row in all_signals if _text_matches(row, SIGNAL_FIELDS, associated_terms)]
        materials = [row for row in all_materials if _text_matches(row, MATERIAL_FIELDS, associated_terms)]
        corrections = [row for row in all_corrections if _text_matches(row, CORRECTION_FIELDS, associated_terms)]
        forward_checks = [row for row in all_forward if _text_matches(row, FORWARD_FIELDS, associated_terms)]
        parent_signals: list[dict[str, Any]] = []
        parent_materials: list[dict[str, Any]] = []
        parent_corrections: list[dict[str, Any]] = []
        parent_forward: list[dict[str, Any]] = []
    else:
        signals = [row for row in all_signals if _text_matches(row, SIGNAL_FIELDS, direct_terms)]
        materials = [row for row in all_materials if _text_matches(row, MATERIAL_FIELDS, direct_terms)]
        corrections = [row for row in all_corrections if _text_matches(row, CORRECTION_FIELDS, direct_terms)]
        forward_checks = [row for row in all_forward if _text_matches(row, FORWARD_FIELDS, direct_terms)]
        direct_keys = {
            *(_record_key("signal", row) for row in signals),
            *(_record_key("material", row) for row in materials),
            *(_record_key("correction", row) for row in corrections),
        }
        parent_signals = [
            row for row in all_signals
            if _text_matches(row, SIGNAL_FIELDS, associated_terms)
            and _record_key("signal", row) not in direct_keys
        ]
        parent_materials = [
            row for row in all_materials
            if _text_matches(row, MATERIAL_FIELDS, associated_terms)
            and _record_key("material", row) not in direct_keys
        ]
        parent_corrections = [
            row for row in all_corrections
            if _text_matches(row, CORRECTION_FIELDS, associated_terms)
            and _record_key("correction", row) not in direct_keys
        ]
        parent_forward = [
            row for row in all_forward
            if _text_matches(row, FORWARD_FIELDS, associated_terms) and row not in forward_checks
        ]

    correction_ids = {
        event_id
        for row in corrections
        for event_id in EVENT_ID_RE.findall(str(row.get("event_id", "") or ""))
    }
    related_ids = correction_ids - core_ids - parent_ids
    co_mentioned_events = []
    if related_ids:
        placeholders = ",".join("?" for _ in related_ids)
        co_mentioned_events = _dict_rows(
            con,
            f"SELECT * FROM events WHERE event_id IN ({placeholders}) ORDER BY source,event_id",
            tuple(sorted(related_ids)),
        )

    all_core_companies = _company_links(con, core_refs)
    all_parent_companies = _company_links(con, parent_refs)
    companies = all_core_companies if core_events else [
        row for row in all_parent_companies if _text_matches(row, COMPANY_LINK_FIELDS, direct_terms)
    ]
    company_mentions = [] if core_events else _company_mentions(con, (
        ("signal", signals), ("material", materials), ("correction", corrections),
    ))

    sectors = {str(row.get("sector", "") or "") for row in core_events if row.get("sector")}
    parent_sectors = {
        str(row.get("sector", "") or "") for row in parent_events if row.get("sector")
    }
    all_status = _dict_rows(con, "SELECT * FROM sector_status")
    sector_terms = tuple({query.casefold(), *(sector.casefold() for sector in sectors)})
    sector_status = [row for row in all_status if _text_matches(
        row, SECTOR_FIELDS, sector_terms
    )]
    parent_sector_terms = tuple(sector.casefold() for sector in parent_sectors)
    parent_sector_status = [
        row for row in all_status
        if parent_sector_terms and _text_matches(row, SECTOR_FIELDS, parent_sector_terms)
        and row not in sector_status
    ]
    metadata = dict(con.execute("SELECT key,value FROM metadata").fetchall())
    con.close()

    lifecycle_observations = []
    price_responses = []
    memory_path = Path(memory_db)
    if core_refs and memory_path.exists():
        memory = connect(memory_path)
        for source, event_id in sorted(core_refs):
            lifecycle_observations.extend([
                {"source": source, "event_id": event_id, **row}
                for row in observations(memory, source, event_id)
            ])
            price_responses.extend(response_summary(memory, source, event_id))
        memory.close()

    timeline = _timeline_rows(
        core_events, signals, materials, corrections, lifecycle_observations
    )
    parent_timeline = _timeline_rows(
        parent_events, parent_signals, parent_materials, parent_corrections
    )

    known_dates = [row["normalized_date"] for row in timeline if row["normalized_date"]]
    records_by_month: dict[str, int] = {}
    records_by_type: dict[str, int] = {}
    for row in timeline:
        records_by_type[row["record_type"]] = records_by_type.get(row["record_type"], 0) + 1
        if row["normalized_date"]:
            month = row["normalized_date"][:7]
            records_by_month[month] = records_by_month.get(month, 0) + 1
    linked_refs = {(row["source"], row["event_id"]) for row in companies}
    warnings = []
    missing_links = [f"{source}/{event_id}" for source, event_id in sorted(core_refs - linked_refs)]
    topic_status = "independent_event" if core_events else (
        "supporting_evidence_only" if timeline or parent_events else "uncovered"
    )
    if topic_status == "uncovered":
        match_mode = "no_match"
    if topic_status == "uncovered":
        warnings.append("事件库尚未覆盖该主题")
    elif topic_status == "supporting_evidence_only":
        warnings.append("主题尚未形成独立事件；上级事件仅作产业背景")
        if not materials:
            warnings.append("细分主题没有可直接归属的材料记录")
        if company_mentions and not companies:
            warnings.append("相关公司仅见于材料文字点名，尚无细分主题结构化关系")
    if missing_links:
        warnings.append("部分核心事件没有结构化公司关系")
    if topic_status != "uncovered" and not lifecycle_observations:
        warnings.append("主题尚无生命周期观察记录")
    if topic_status != "uncovered" and not price_responses:
        warnings.append("主题尚无价格响应快照")
    if co_mentioned_events:
        warnings.append("共同出现事件仅作上下文，不代表已确认因果关联")

    return {
        "topic": query,
        "match_mode": match_mode,
        "topic_status": topic_status,
        "core_events": core_events,
        "parent_events": parent_events,
        "co_mentioned_events": co_mentioned_events,
        "timeline": timeline,
        "supporting_records": {
            "signals": signals, "materials": materials, "corrections": corrections,
        },
        "forward_checks": forward_checks,
        "companies": companies,
        "company_mentions": company_mentions,
        "sector_status": sector_status,
        "lifecycle_observations": lifecycle_observations,
        "price_responses": price_responses,
        "parent_context": {
            "timeline": parent_timeline,
            "supporting_records": {
                "signals": parent_signals,
                "materials": parent_materials,
                "corrections": parent_corrections,
            },
            "forward_checks": parent_forward,
            "companies": all_parent_companies,
            "sector_status": parent_sector_status,
        },
        "coverage": {
            "database_built_at": metadata.get("built_at"),
            "earliest_date": min(known_dates) if known_dates else None,
            "latest_record_date": max(known_dates) if known_dates else None,
            "event_count": len(core_events), "timeline_count": len(timeline),
            "parent_event_count": len(parent_events),
            "parent_context_record_count": len(parent_timeline),
            "records_by_month": records_by_month,
            "records_by_type": records_by_type,
            "events_without_company_links": missing_links,
            "events_without_parseable_date": [
                f"{row['source']}/{row['event_id']}" for row in core_events
                if row.get("event_date") and not _context_date(row.get("event_date"))
            ],
            "lifecycle_observation_count": len(lifecycle_observations),
            "price_response_count": len(price_responses),
            "warnings": warnings,
        },
    }


def render_topic_context(context: dict[str, Any]) -> str:
    coverage = context["coverage"]
    status_label = {
        "independent_event": "已有独立事件",
        "supporting_evidence_only": "仅有细分线索/上级事件背景",
        "uncovered": "事件库未覆盖",
    }.get(context.get("topic_status"), "未知")
    lines = [
        f"# 主题上下文｜{context['topic']}", "",
        f"- 覆盖状态：{status_label}",
        f"- 核心事件：{coverage['event_count']}条；直接主题记录：{coverage['timeline_count']}条",
        f"- 上级事件：{coverage['parent_event_count']}条；背景记录：{coverage['parent_context_record_count']}条",
        f"- 记录区间：{coverage['earliest_date'] or '未知'} 至 {coverage['latest_record_date'] or '未知'}",
        f"- 生命周期观察：{coverage['lifecycle_observation_count']}条；价格响应：{coverage['price_response_count']}条",
        "", "## 核心事件", "",
    ]
    if context["core_events"]:
        for row in context["core_events"]:
            lines.append(
                f"- {row['source']}/{row['event_id']}｜{row['event_date'] or '日期未知'}｜"
                f"{row['event_name']}｜{row['status'] or '状态未知'}"
            )
    else:
        lines.append("- 无")
    if context["parent_events"]:
        lines += ["", "## 上级事件背景", ""]
        for row in context["parent_events"]:
            lines.append(
                f"- {row['source']}/{row['event_id']}｜{row['event_date'] or '日期未知'}｜"
                f"{row['event_name']}（不计作本主题独立事件）"
            )
    if context["company_mentions"]:
        lines += ["", "## 材料点名公司", ""]
        lines.extend(
            f"- {row['stock_code']} {row['company_name']}｜仅为文字点名，未自动升级为主题映射"
            for row in context["company_mentions"]
        )
    lines += ["", "## 覆盖警告", ""]
    if coverage["warnings"]:
        lines.extend(f"- {warning}" for warning in coverage["warnings"])
    else:
        lines.append("- 无")
    lines += ["", "使用 `--json` 获取完整时间线、证据、公司关系和前瞻验证。"]
    return "\n".join(lines) + "\n"


def event_dossier(event_db: Path, memory_db: Path, source: str, event_id: str) -> dict[str, Any]:
    src = sqlite3.connect(event_db)
    src.row_factory = sqlite3.Row
    event = src.execute("SELECT * FROM events WHERE source=? AND event_id=?", (source, event_id)).fetchone()
    if event is None:
        src.close()
        raise KeyError(f"event not found: {source}/{event_id}")
    links = src.execute(
        """SELECT c.company_name,c.stock_code,l.role_2,l.role_3,l.relation_status,
                  l.benefit_tier,l.mapping_basis,l.source_ref,l.last_verified
           FROM company_event_links l JOIN companies c USING(company_id)
           WHERE l.event_pk=? ORDER BY l.benefit_tier,c.company_name""", (event["event_pk"],)
    ).fetchall()
    signals = src.execute(
        """SELECT signal_date,stage,title,content,confidence,source_type,source_ref,updated_at
           FROM early_signals WHERE source=? AND event_id LIKE ? ORDER BY signal_date""",
        (source, f"%{event_id}%"),
    ).fetchall()
    corrections = src.execute(
        """SELECT update_date,issue,judgement,action,priority,status,notes
           FROM corrections WHERE source=? AND event_id LIKE ? ORDER BY update_date""",
        (source, f"%{event_id}%"),
    ).fetchall()
    materials = src.execute(
        """SELECT material_date,source_type,title,path_or_url,reliability,usage,notes
           FROM materials WHERE source=? AND related_events LIKE ? ORDER BY material_date""",
        (source, f"%{event_id}%"),
    ).fetchall()
    src.close()
    mem = connect(memory_db)
    obs = observations(mem, source, event_id)
    responses = response_summary(mem, source, event_id)
    mem.close()
    return {
        "event": dict(event), "signals": [dict(r) for r in signals],
        "corrections": [dict(r) for r in corrections], "materials": [dict(r) for r in materials],
        "observations": obs, "companies": [dict(r) for r in links],
        "price_responses": responses, "basket_response": basket_medians(responses),
    }


def render(d: dict[str, Any]) -> str:
    e = d["event"]
    lines = [f"# {e['event_id']}｜{e['event_name']}", "",
             f"- 来源/赛道：{e['source']} / {e['sector'] or '未标注'}",
             f"- 原始事件时间：{e['event_date'] or '未知'}；状态：{e['status'] or '未知'}；交易状态：{e['trade_status'] or '未知'}",
             f"- 原始假设：{e['impact_direction'] or '未填写'}",
             f"- 验证指标：{e['validation_metrics'] or '未填写'}",
             f"- 反证或备注：{e['notes'] or '未填写'}", "", "## 连续观察", ""]
    timeline = []
    for row in d["materials"]:
        timeline.append((row.get("material_date") or "9999", "材料", row.get("title"), row.get("reliability"), row.get("path_or_url")))
    for row in d["signals"]:
        timeline.append((row.get("signal_date") or row.get("updated_at") or "9999", f"信号/{row.get('stage','')}", row.get("title") or row.get("content"), row.get("confidence"), row.get("source_ref")))
    for row in d["corrections"]:
        timeline.append((row.get("update_date") or "9999", f"修正/{row.get('status','')}", row.get("judgement") or row.get("issue"), row.get("priority"), ""))
    for row in d["observations"]:
        timeline.append((row["observed_at"], row["kind"], row["summary"], row["source_grade"], row["source_ref"]))
    if timeline:
        lines += ["|时间|类型|内容|证据等级|来源|", "|---|---|---|---|---|"]
        for when, kind, text, grade, ref in sorted(timeline, key=lambda x: x[0]):
            clean = lambda v: str(v or "").replace("|", "/").replace("\n", " ")
            lines.append(f"|{clean(when)}|{clean(kind)}|{clean(text)}|{clean(grade)}|{clean(ref)}|")
    else:
        lines.append("尚无连续观察记录。")
    lines += ["", "## 关联公司", "", "|代码|公司|角色|关系状态|受益层级|", "|---|---|---|---|---|"]
    for row in d["companies"]:
        lines.append(f"|{row['stock_code']}|{row['company_name']}|{row['role_2'] or row['role_3']}|{row['relation_status']}|{row['benefit_tier']}|")
    lines += ["", "## 催化后价格响应", ""]
    if not d["price_responses"]:
        lines.append("尚未生成价格响应快照。")
    else:
        lines += ["|锚点|代码|截至|T+0超额|T+1超额|T+3超额|T+5超额|T+10超额|T+20超额|", "|---|---|---|---:|---:|---:|---:|---:|---:|"]
        for row in d["price_responses"]:
            h = row["response"].get("horizons", {})
            val = lambda n: "" if h.get(f"t{n}") is None else f"{h[f't{n}']['excess_pct']:.2f}%"
            lines.append(f"|{row['anchor_label']} {row['anchor_date']}|{row['ts_code']}|{row['as_of']}|{val(0)}|{val(1)}|{val(3)}|{val(5)}|{val(10)}|{val(20)}|")
        med = d["basket_response"]
        lines += ["", "篮子中位超额：" + "；".join(
            f"T+{n}={med[f't{n}_median_excess_pct']:.2f}%" for n in (0,1,3,5,10,20)
            if med.get(f"t{n}_median_excess_pct") is not None
        )]
    return "\n".join(lines) + "\n"


def _load_pro():
    env = WORKSPACE / "repo" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("TUSHARE_TOKEN=") and "TUSHARE_TOKEN" not in os.environ:
                os.environ["TUSHARE_TOKEN"] = line.partition("=")[2].strip()
    import tushare as ts
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未配置")
    return ts.pro_api(token)


def _market_code(code: str) -> str:
    if "." in code:
        return code
    return f"{code}.SH" if code.startswith(("5", "6", "9")) else f"{code}.SZ"


def track(args: argparse.Namespace) -> None:
    dossier = event_dossier(args.event_db, args.memory_db, args.source, args.event_id)
    pro = _load_pro()
    anchor = date.fromisoformat(args.anchor_date)
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    start = (anchor - timedelta(days=30)).strftime("%Y%m%d")
    end = as_of.strftime("%Y%m%d")
    benchmark = pro.index_daily(ts_code=args.benchmark, start_date=start, end_date=end,
                                fields="trade_date,close").to_dict("records")
    mem = connect(args.memory_db)
    for company in dossier["companies"][:args.max_companies]:
        code = _market_code(company["stock_code"])
        bars = pro.daily(ts_code=code, start_date=start, end_date=end,
                         fields="trade_date,close").to_dict("records")
        response = calculate_price_response(bars, benchmark, args.anchor_date)
        store_price_response(mem, source=args.source, event_id=args.event_id,
                             anchor_label=args.anchor_label, anchor_date=args.anchor_date,
                             ts_code=code, company_name=company["company_name"],
                             benchmark_code=args.benchmark, as_of=as_of.isoformat(), response=response)
    mem.close()


def search(event_db: Path, keyword: str) -> list[dict[str, Any]]:
    con = sqlite3.connect(event_db)
    con.row_factory = sqlite3.Row
    q = f"%{keyword}%"
    rows = con.execute(
        """SELECT source,event_id,sector,event_name,event_date,status,trade_status,last_verified
           FROM events WHERE event_id LIKE ? OR event_name LIKE ? OR sector LIKE ? OR notes LIKE ?
           ORDER BY importance DESC,last_verified DESC LIMIT 50""", (q, q, q, q)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="催化生命周期研究记忆")
    p.add_argument("--event-db", type=Path, default=EVENT_DB)
    p.add_argument("--memory-db", type=Path, default=MEMORY_DB)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search"); s.add_argument("keyword"); s.add_argument("--json", action="store_true"); s.add_argument("--context", action="store_true", help="返回主题完整历史证据包")
    s = sub.add_parser("show"); s.add_argument("source", choices=["tech", "nonfin"]); s.add_argument("event_id"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("record"); s.add_argument("source", choices=["tech", "nonfin"]); s.add_argument("event_id"); s.add_argument("--date", required=True); s.add_argument("--kind", choices=sorted(__import__('skills.shared.catalyst_memory', fromlist=['KINDS']).KINDS), required=True); s.add_argument("--summary", required=True); s.add_argument("--grade", choices=sorted(__import__('skills.shared.catalyst_memory', fromlist=['GRADES']).GRADES), required=True); s.add_argument("--ref", default=""); s.add_argument("--codes", default="")
    s = sub.add_parser("track"); s.add_argument("source", choices=["tech", "nonfin"]); s.add_argument("event_id"); s.add_argument("--anchor-date", required=True); s.add_argument("--anchor-label", default="首次提出"); s.add_argument("--as-of"); s.add_argument("--benchmark", default="000300.SH"); s.add_argument("--max-companies", type=int, default=15); s.add_argument("--json", action="store_true")
    s = sub.add_parser("intake"); s.add_argument("--date", required=True); s.add_argument("--summary", required=True); s.add_argument("--grade", default="user_material"); s.add_argument("--ref", default=""); s.add_argument("--candidate-source", default=""); s.add_argument("--candidate-event", default="")
    s = sub.add_parser("queue"); s.add_argument("--status", default="pending"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("resolve"); s.add_argument("intake_id", type=int); s.add_argument("--status", choices=["linked", "new_event", "rejected"], required=True); s.add_argument("--source", default=""); s.add_argument("--event-id", default=""); s.add_argument("--notes", default="")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.cmd == "search":
        data = topic_context(args.event_db, args.memory_db, args.keyword) if args.context else search(args.event_db, args.keyword)
        if args.json: print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        elif args.context: print(render_topic_context(data), end="")
        else:
            for row in data: print(f"{row['source']}/{row['event_id']} | {row['sector']} | {row['event_name']} | {row['event_date']}")
        return
    if args.cmd == "record":
        con = connect(args.memory_db)
        changed = record_observation(con, source=args.source, event_id=args.event_id,
            observed_at=args.date, kind=args.kind, summary=args.summary, source_grade=args.grade,
            source_ref=args.ref, related_codes=[x.strip() for x in args.codes.split(",") if x.strip()])
        con.close(); print("已新增" if changed else "重复记录，未新增"); return
    if args.cmd == "intake":
        con = connect(args.memory_db)
        intake_id = record_intake(con, received_at=args.date, raw_summary=args.summary,
            source_grade=args.grade, source_ref=args.ref,
            candidate_source=args.candidate_source, candidate_event_id=args.candidate_event)
        con.close(); print(f"已进入待归因队列：{intake_id}"); return
    if args.cmd == "queue":
        con = connect(args.memory_db); rows = intake_queue(con, args.status); con.close()
        if args.json: print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for row in rows: print(f"#{row['intake_id']} {row['received_at']} | {row['raw_summary']} | 候选 {row['candidate_source']}/{row['candidate_event_id']}")
        return
    if args.cmd == "resolve":
        con = connect(args.memory_db)
        changed = resolve_intake(con, args.intake_id, status=args.status,
            candidate_source=args.source, candidate_event_id=args.event_id,
            resolution_notes=args.notes)
        con.close(); print("已处理" if changed else "未找到待处理记录"); return
    if args.cmd == "track":
        track(args)
    data = event_dossier(args.event_db, args.memory_db, args.source, args.event_id)
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str) if args.json else render(data))


if __name__ == "__main__":
    main()
