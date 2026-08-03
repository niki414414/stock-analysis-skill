#!/usr/bin/env python3
"""Market Outlook 2.0 shadow-safe opportunity discovery radar.

The script discovers abnormal market structure and non-tech rotation directions.
It never emits a buy signal. Price strength is only a discovery trigger; a
direction remains "research only" until catalyst, company linkage and company
quality are verified by the market-outlook workflow.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from typing import Optional


WORKSPACE_ROOT = Path(
    os.path.abspath(os.path.expanduser(
        os.environ.get("TZ_CODEX_HOME", "~/Desktop/tz-codex")
    ))
)
REPO_ROOT = WORKSPACE_ROOT / "repo"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
DATA_ROOT = WORKSPACE_ROOT / "技能数据"
SKILL_ROOT = REPO_ROOT / "skills" / "market-outlook"
ALIASES_PATH = SKILL_ROOT / "config" / "sector_aliases.json"
EVENT_QUERY = (
    REPO_ROOT / "skills" / "stock-analysis" / "scripts" / "event_map_query.py"
)
NONFIN_ROOT = DATA_ROOT / "非科技产业事件地图"
COMPANY_POOL = DATA_ROOT / "公司.xlsx"
CODE_MAP = DATA_ROOT / "company_code_map.csv"
QUALITY_CACHE = (
    REPO_ROOT
    / "skills"
    / "quality-compounder"
    / "cache"
    / "fundamentals_cache.json"
)

TECH_SECTORS = {"电子", "计算机", "通信", "国防军工"}
BUCKET_WEIGHT = {"red": 4, "yellow": 3, "green": 2, "blue": 1, "gray": 0}


def load_repo_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key and value and (key == "TUSHARE_TOKEN" or key not in os.environ):
            os.environ[key] = value


def get_pro():
    load_repo_env()
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未配置；请检查 repo/.env")
    import tushare as ts
    return ts.pro_api(token)


def normalize_code(raw) -> Optional[str]:
    if raw is None or pd.isna(raw):
        return None
    text = str(raw).strip()
    if text.endswith(".0"):
        text = text[:-2]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits else None


def latest_nonfin_dir() -> Path:
    dirs = [
        Path(path) for path in glob.glob(
            str(NONFIN_ROOT / "非科技主线产业事件地图_CSV包_*")
        )
        if Path(path).is_dir()
    ]
    if not dirs:
        raise FileNotFoundError(f"未找到非科技事件CSV目录: {NONFIN_ROOT}")
    return max(dirs, key=lambda path: path.name)


def load_nonfin_tables() -> tuple[pd.DataFrame, pd.DataFrame, Path]:
    directory = latest_nonfin_dir()
    event_files = glob.glob(str(directory / "events_*.csv"))
    mapping_files = glob.glob(str(directory / "mapping_*.csv"))
    if not event_files:
        raise FileNotFoundError(f"{directory} 下未找到 events_*.csv")
    events = pd.read_csv(event_files[0], dtype=str).fillna("")
    mappings = (
        pd.read_csv(mapping_files[0], dtype=str).fillna("")
        if mapping_files else pd.DataFrame()
    )
    return events, mappings, directory


def load_active_windows() -> dict[str, dict]:
    cmd = [
        sys.executable, str(EVENT_QUERY), "window",
        "--source", "nonfin", "--json",
    ]
    env = os.environ.copy()
    env["TZ_CODEX_HOME"] = str(WORKSPACE_ROOT)
    result = subprocess.run(
        cmd, check=True, capture_output=True, text=True, env=env
    )
    rows = json.loads(result.stdout)
    return {str(row["event_id"]): row for row in rows}


def load_aliases() -> dict[str, list[str]]:
    return json.loads(ALIASES_PATH.read_text(encoding="utf-8"))


def fetch_rotation(pro, lookback_days: int = 55) -> tuple[list[dict], str]:
    classes = pro.index_classify(level="L1", src="SW2021")
    if classes is None or classes.empty:
        raise RuntimeError("无法获取申万一级行业列表")
    end = datetime.now()
    start = end - timedelta(days=lookback_days)
    benchmark = pro.index_daily(
        ts_code="000300.SH",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    ).sort_values("trade_date")
    if benchmark is None or len(benchmark) < 21:
        raise RuntimeError("沪深300历史数据不足")
    benchmark_today = float(benchmark["pct_chg"].iloc[-1])
    benchmark_5d = (benchmark["close"].iloc[-1] / benchmark["close"].iloc[-5] - 1) * 100
    benchmark_20d = (benchmark["close"].iloc[-1] / benchmark["close"].iloc[-20] - 1) * 100
    trade_date = str(benchmark["trade_date"].iloc[-1])

    rows = []
    for _, item in classes.iterrows():
        code = str(item.get("index_code", ""))
        name = str(item.get("industry_name", ""))
        if not code or not name:
            continue
        try:
            df = pro.index_daily(
                ts_code=code,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            ).sort_values("trade_date")
            if df is None or len(df) < 21:
                continue
            pct_today = float(df["pct_chg"].iloc[-1])
            pct_5d = (df["close"].iloc[-1] / df["close"].iloc[-5] - 1) * 100
            pct_20d = (df["close"].iloc[-1] / df["close"].iloc[-20] - 1) * 100
            rows.append({
                "sector": name,
                "index_code": code,
                "pct_today": round(pct_today, 2),
                "pct_5d": round(pct_5d, 2),
                "pct_20d": round(pct_20d, 2),
                "excess_today_vs_csi300": round(pct_today - benchmark_today, 2),
                "excess_5d_vs_csi300": round(pct_5d - benchmark_5d, 2),
                "excess_20d_vs_csi300": round(pct_20d - benchmark_20d, 2),
                "is_tech": name in TECH_SECTORS,
            })
        except Exception:
            continue
    rows.sort(key=lambda row: row["excess_5d_vs_csi300"], reverse=True)
    return rows, trade_date


def event_search_text(row: pd.Series) -> str:
    fields = [
        "事件ID", "一级赛道", "二级事件", "事件名称",
        "主要影响方向", "备注",
    ]
    return " ".join(str(row.get(field, "")) for field in fields)


def find_matching_events(
    sector: str,
    events: pd.DataFrame,
    active_windows: dict[str, dict],
    aliases: dict[str, list[str]],
) -> list[dict]:
    terms = aliases.get(sector, [sector])
    matches = []
    for _, event in events.iterrows():
        primary = str(event.get("一级赛道", ""))
        secondary = str(event.get("二级事件", ""))
        matched_terms = [
            term for term in terms
            if term and (
                (term == sector and term in primary)
                or (term != sector and (term in primary or term in secondary))
            )
        ]
        if not matched_terms:
            continue
        event_id = str(event.get("事件ID", ""))
        window = active_windows.get(event_id)
        if not window or window.get("bucket") == "gray":
            continue
        matches.append({
            "event_id": event_id,
            "event_name": str(event.get("事件名称", "")),
            "event_sector": str(event.get("一级赛道", "")),
            "matched_terms": matched_terms,
            "bucket": window.get("bucket"),
            "final_score": window.get("final_score"),
            "action_from_window_model": window.get("action"),
            "company_text": str(event.get("主要影响方向", "")),
        })
    matches.sort(
        key=lambda row: (
            BUCKET_WEIGHT.get(str(row["bucket"]), 0),
            float(row.get("final_score") or 0),
        ),
        reverse=True,
    )
    return matches


def load_company_sources(source: str = "sqlite"):
    code_map = pd.read_csv(CODE_MAP, dtype=str).fillna("")
    code_map["code"] = code_map["code"].apply(normalize_code)
    if source == "excel":
        pool = pd.read_excel(
            COMPANY_POOL, sheet_name="非科技公司池", dtype=str
        ).fillna("")
        return pool, code_map, None
    try:
        from skills.shared.event_store import EventStore
        return pd.DataFrame(), code_map, EventStore(DATA_ROOT / "event_map_shadow.db")
    except Exception as exc:
        print(f"[WARN] SQLite公司关系读取失败，启用Excel紧急回退: {exc}", file=sys.stderr)
        pool = pd.read_excel(
            COMPANY_POOL, sheet_name="非科技公司池", dtype=str
        ).fillna("")
        return pool, code_map, None


def company_candidates(
    event_matches: list[dict],
    pool: pd.DataFrame,
    code_map: pd.DataFrame,
    limit: int = 12,
    store=None,
    event_source: str = "nonfin",
) -> tuple[list[dict], list[str]]:
    event_ids = {row["event_id"] for row in event_matches}
    event_text = " ".join(row["company_text"] for row in event_matches)
    candidates = []
    seen = set()
    if store is not None:
        try:
            links = store.companies_for_events(
                [(event_source, event_id) for event_id in event_ids]
            )
        except Exception as exc:
            print(f"[WARN] SQLite候选查询失败，启用Excel紧急回退: {exc}", file=sys.stderr)
            fallback_pool = pd.read_excel(
                COMPANY_POOL, sheet_name="非科技公司池", dtype=str
            ).fillna("")
            return company_candidates(
                event_matches, fallback_pool, code_map, limit=limit,
                store=None, event_source=event_source,
            )
        for link in links:
            name = link["company_name"]
            if name in seen:
                continue
            candidates.append({
                "name": name,
                "code": link["stock_code"],
                "link_source": "structured_event_link",
                "confidence": link.get("mapping_confidence") or "",
                "relation_status": link.get("relation_status"),
                "benefit_tier": link.get("benefit_tier"),
                "company_source": "sqlite",
            })
            seen.add(name)
    else:
        for _, company in pool.iterrows():
            name = str(company.get("公司名称", "")).strip()
            refs = {
                part
                for part in re.split(
                    r"[\s,，;；/|]+",
                    str(company.get("关联事件ID", "")).strip(),
                )
                if part
            }
            if not name:
                continue
            link_source = None
            if event_ids.intersection(refs):
                link_source = "structured_event_link"
            elif name in event_text:
                link_source = "event_text_fallback"
            if not link_source or name in seen:
                continue
            code_rows = code_map[code_map["name"] == name]
            code = code_rows.iloc[0]["code"] if not code_rows.empty else None
            candidates.append({
                "name": name,
                "code": code,
                "link_source": link_source,
                "confidence": str(company.get("置信度", "")),
                "relation_status": "Excel兼容",
                "benefit_tier": "未分层",
                "company_source": "excel_fallback",
            })
            seen.add(name)
    gaps = sorted(
        name for name in code_map["name"].astype(str).unique()
        if name and name in event_text and name not in seen
    )
    for name in gaps:
        code = code_map.loc[code_map["name"] == name, "code"].iloc[0]
        candidates.append({
            "name": name,
            "code": code,
            "link_source": "event_text_code_map_only",
            "confidence": "待核验",
            "relation_status": "待验证",
            "benefit_tier": "主题观察",
            "company_source": "event_text_fallback",
        })
    return candidates[:limit], gaps


def load_quality_pool(path: Path = QUALITY_CACHE) -> tuple[dict[str, dict], dict]:
    """Load the latest quality-compounder Stage 1 cache as a read-only label."""
    if not path.exists():
        return {}, {"status": "missing", "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = {
            normalize_code(row.get("ts_code")): row
            for row in payload.get("candidates", [])
            if normalize_code(row.get("ts_code"))
        }
        return rows, {
            "status": "ok",
            "path": str(path),
            "trade_date": payload.get("trade_date"),
            "candidate_count": len(rows),
        }
    except Exception as exc:
        return {}, {
            "status": "invalid",
            "path": str(path),
            "error": str(exc),
        }


def classify_sector_evidence(
    *,
    excess_5d: float,
    advance_ratio: float,
    market_advance_ratio: float,
    positive_flow_days_5d: Optional[int],
    cumulative_flow_yi_5d: Optional[float],
    latest_flow_yi: Optional[float],
    active_catalyst: bool,
) -> tuple[str, dict]:
    """Classify evidence without a weighted score.

    Absolute breadth is compared with the whole market so a broad rebound does
    not make most sectors look independently strong. Missing moneyflow remains
    missing and never silently becomes zero.
    """
    advance_excess = advance_ratio - market_advance_ratio
    relative_strength = excess_5d >= 2
    relative_breadth = advance_excess >= 5
    persistent_flow = (
        positive_flow_days_5d is not None
        and cumulative_flow_yi_5d is not None
        and latest_flow_yi is not None
        and positive_flow_days_5d >= 3
        and cumulative_flow_yi_5d > 0
        and latest_flow_yi > 0
    )
    gates = {
        "relative_strength": bool(relative_strength),
        "relative_breadth": bool(relative_breadth),
        "persistent_flow": bool(persistent_flow),
        "active_catalyst": bool(active_catalyst),
    }
    gate_count = sum(gates.values())
    if persistent_flow and relative_strength and gate_count >= 3:
        state = "evidence_cluster"
    elif gate_count >= 2:
        state = "research_candidate"
    else:
        state = "weak_or_unconfirmed"
    return state, {
        "advance_excess_vs_market": round(advance_excess, 1),
        "gates": gates,
    }


def merge_candidate_labels(
    quality_rows: list[dict],
    catalyst_rows: list[dict],
    response_rows: list[dict],
) -> list[dict]:
    """Merge independent candidate sources without inventing a total score."""
    merged = defaultdict(lambda: {"tags": set()})
    for rows in (quality_rows, catalyst_rows, response_rows):
        for row in rows:
            code = normalize_code(row.get("code"))
            if not code:
                continue
            merged[code].update(
                {key: value for key, value in row.items() if key != "tags"}
            )
            merged[code]["code"] = code
            merged[code]["tags"].update(row.get("tags", []))
    output = []
    for row in merged.values():
        row["tags"] = sorted(row["tags"])
        row["evidence_count"] = len(row["tags"])
        row["next_action"] = (
            "six_layer_priority"
            if row["evidence_count"] >= 2
            else "watch_only"
        )
        output.append(row)
    output.sort(
        key=lambda row: (
            row["evidence_count"],
            row.get("net_mf_yi")
            if row.get("net_mf_yi") is not None else -10**9,
        ),
        reverse=True,
    )
    return output


def fetch_latest_moneyflow_frames(
    pro,
    trade_date: str,
    days: int = 5,
) -> tuple[dict[str, pd.DataFrame], dict]:
    """Fetch the latest *valid* Tushare moneyflow dates independently.

    Price and moneyflow publish at different times. Empty frames are skipped,
    and callers receive an explicit unavailable status instead of zero-filled
    flow.
    """
    end = datetime.strptime(trade_date, "%Y%m%d")
    try:
        calendar = pro.trade_cal(
            exchange="SSE",
            start_date=(end - timedelta(days=20)).strftime("%Y%m%d"),
            end_date=trade_date,
        )
        open_days = sorted(
            calendar.loc[calendar["is_open"] == 1, "cal_date"].astype(str),
            reverse=True,
        )
    except Exception as exc:
        return {}, {"status": "unavailable", "error": str(exc)}

    frames = {}
    errors = []
    for flow_date in open_days:
        try:
            frame = pro.moneyflow(
                trade_date=flow_date,
                fields=(
                    "ts_code,trade_date,net_mf_amount,"
                    "buy_lg_amount,sell_lg_amount,"
                    "buy_elg_amount,sell_elg_amount"
                ),
            )
            if frame is not None and not frame.empty:
                frames[flow_date] = frame
        except Exception as exc:
            errors.append({"trade_date": flow_date, "error": str(exc)})
        if len(frames) >= days:
            break
    if not frames:
        return {}, {
            "status": "unavailable",
            "requested_trade_date": trade_date,
            "errors": errors,
        }
    latest = next(iter(frames))
    return frames, {
        "status": "ok",
        "requested_trade_date": trade_date,
        "latest_trade_date": latest,
        "lagged": latest < trade_date,
        "valid_days": list(frames),
        "errors": errors,
    }


def build_sector_opportunity_map(
    *,
    pro,
    rotation: list[dict],
    trade_date: str,
    market_advance_ratio: float,
    events: pd.DataFrame,
    active_windows: dict[str, dict],
    aliases: dict[str, list[str]],
    pool: pd.DataFrame,
    code_map: pd.DataFrame,
    company_store=None,
) -> tuple[list[dict], dict]:
    """Build the balanced market→sector→candidate shadow-safe snapshot."""
    quality_pool, quality_meta = load_quality_pool()
    moneyflow_frames, moneyflow_meta = fetch_latest_moneyflow_frames(
        pro, trade_date
    )
    try:
        daily = pro.daily(
            trade_date=trade_date,
            fields="ts_code,trade_date,close,pct_chg,vol,amount",
        )
    except Exception as exc:
        return [], {
            "status": "unavailable",
            "error": f"daily({trade_date}) failed: {exc}",
            "quality_pool": quality_meta,
            "moneyflow": moneyflow_meta,
        }
    if daily is None or daily.empty:
        return [], {
            "status": "unavailable",
            "error": f"daily({trade_date}) returned empty",
            "quality_pool": quality_meta,
            "moneyflow": moneyflow_meta,
        }
    if market_advance_ratio <= 0:
        market_advance_ratio = round(
            float(
                (
                    pd.to_numeric(daily["pct_chg"], errors="coerce") > 0
                ).mean() * 100
            ),
            1,
        )
    try:
        basics = pro.stock_basic(
            exchange="", list_status="L", fields="ts_code,name"
        )
        name_map = dict(zip(basics["ts_code"], basics["name"]))
    except Exception:
        name_map = {}

    latest_flow = None
    if moneyflow_frames:
        latest_flow = moneyflow_frames[next(iter(moneyflow_frames))].copy()
        latest_flow = latest_flow.drop(columns=["trade_date"], errors="ignore")
        stocks = daily.merge(latest_flow, on="ts_code", how="left")
    else:
        stocks = daily.copy()
    numeric_fields = [
        "pct_chg", "amount", "net_mf_amount",
        "buy_lg_amount", "sell_lg_amount",
        "buy_elg_amount", "sell_elg_amount",
    ]
    for field in numeric_fields:
        if field in stocks:
            stocks[field] = pd.to_numeric(stocks[field], errors="coerce")
    if latest_flow is not None:
        stocks["large_net"] = (
            stocks["buy_lg_amount"].fillna(0)
            - stocks["sell_lg_amount"].fillna(0)
            + stocks["buy_elg_amount"].fillna(0)
            - stocks["sell_elg_amount"].fillna(0)
        )

    classes = pro.index_classify(level="L1", src="SW2021")
    class_code = dict(zip(classes["industry_name"], classes["index_code"]))
    opportunity_map = []
    membership_errors = []
    for rotation_row in rotation:
        sector = rotation_row["sector"]
        index_code = class_code.get(sector) or rotation_row.get("index_code")
        try:
            members = pro.index_member_all(l1_code=index_code)
        except Exception as exc:
            membership_errors.append({"sector": sector, "error": str(exc)})
            continue
        if members is None or members.empty:
            membership_errors.append({"sector": sector, "error": "empty"})
            continue
        current = members[
            members["is_new"].astype(str).str.upper().isin(["Y", "1"])
        ]
        if current.empty:
            current = members
        member_codes = {
            normalize_code(value) for value in current["ts_code"]
            if normalize_code(value)
        }
        sector_stocks = stocks[
            stocks["ts_code"].map(normalize_code).isin(member_codes)
        ].copy()
        if sector_stocks.empty:
            membership_errors.append({
                "sector": sector,
                "error": "no daily rows for current members",
            })
            continue
        advance_ratio = round(
            float((sector_stocks["pct_chg"] > 0).mean() * 100), 1
        )

        latest_flow_yi = None
        large_order_yi = None
        positive_flow_days = None
        cumulative_flow_yi = None
        flow_history = []
        if moneyflow_frames:
            for flow_date, frame in moneyflow_frames.items():
                member_flow = frame[
                    frame["ts_code"].map(normalize_code).isin(member_codes)
                ]
                net = pd.to_numeric(
                    member_flow["net_mf_amount"], errors="coerce"
                )
                flow_history.append({
                    "trade_date": flow_date,
                    "net_flow_yi": round(float(net.sum()) / 10000, 2),
                })
            latest_flow_yi = flow_history[0]["net_flow_yi"]
            positive_flow_days = sum(
                row["net_flow_yi"] > 0 for row in flow_history
            )
            cumulative_flow_yi = round(
                sum(row["net_flow_yi"] for row in flow_history), 2
            )
            large_order_yi = round(
                float(
                    sector_stocks.loc[
                        sector_stocks["large_net"].notna(), "large_net"
                    ].sum()
                ) / 10000,
                2,
            )

        matches = find_matching_events(
            sector, events, active_windows, aliases
        )
        catalyst_rows, mapping_gaps = company_candidates(
            matches[:4], pool, code_map, store=company_store
        )
        catalyst_candidates = [
            {
                **row,
                "tags": ["catalyst"],
            }
            for row in catalyst_rows if row.get("code")
        ]
        quality_candidates = [
            {
                "code": code,
                "name": quality_pool[code].get("name"),
                "pe_ttm": quality_pool[code].get("pe_ttm"),
                "cagr_np": quality_pool[code].get("cagr_np"),
                "tags": ["quality_core"],
            }
            for code in sorted(member_codes.intersection(quality_pool))
        ]
        response_candidates = []
        if latest_flow is not None:
            ranked = sector_stocks.copy()
            ranked["response_score"] = (
                ranked["pct_chg"].rank(pct=True)
                + ranked["net_mf_amount"].rank(pct=True)
                + ranked["amount"].rank(pct=True)
            )
            ranked = ranked.sort_values("response_score", ascending=False)
            for _, row in ranked.head(5).iterrows():
                response_candidates.append({
                    "code": normalize_code(row["ts_code"]),
                    "name": name_map.get(row["ts_code"]),
                    "pct_chg": round(float(row["pct_chg"]), 2),
                    "net_mf_yi": (
                        round(float(row["net_mf_amount"]) / 10000, 2)
                        if pd.notna(row["net_mf_amount"]) else None
                    ),
                    "amount_yi": round(float(row["amount"]) / 100000, 2),
                    "tags": ["market_response"],
                })
        merged_candidates = merge_candidate_labels(
            quality_candidates,
            catalyst_candidates,
            response_candidates,
        )

        active_catalyst = bool(matches)
        state_label, classification = classify_sector_evidence(
            excess_5d=float(rotation_row["excess_5d_vs_csi300"]),
            advance_ratio=advance_ratio,
            market_advance_ratio=market_advance_ratio,
            positive_flow_days_5d=positive_flow_days,
            cumulative_flow_yi_5d=cumulative_flow_yi,
            latest_flow_yi=latest_flow_yi,
            active_catalyst=active_catalyst,
        )
        opportunity_map.append({
            "sector": sector,
            "index_code": index_code,
            "is_tech": rotation_row["is_tech"],
            "state": state_label,
            "evidence": {
                "price_date": trade_date,
                "excess_today_vs_csi300": rotation_row[
                    "excess_today_vs_csi300"
                ],
                "excess_5d_vs_csi300": rotation_row[
                    "excess_5d_vs_csi300"
                ],
                "excess_20d_vs_csi300": rotation_row[
                    "excess_20d_vs_csi300"
                ],
                "advance_ratio": advance_ratio,
                **classification,
                "moneyflow_date": moneyflow_meta.get("latest_trade_date"),
                "moneyflow_lagged": moneyflow_meta.get("lagged"),
                "latest_net_flow_yi": latest_flow_yi,
                "positive_flow_days_5d": positive_flow_days,
                "cumulative_net_flow_yi_5d": cumulative_flow_yi,
                "large_order_proxy_yi": large_order_yi,
                "active_catalyst": active_catalyst,
            },
            "active_events": matches[:4],
            "quality_core_count": len(quality_candidates),
            "catalyst_candidate_count": len(catalyst_candidates),
            "company_mapping_gaps": mapping_gaps,
            "candidates": merged_candidates[:12],
        })
    state_weight = {
        "evidence_cluster": 2,
        "research_candidate": 1,
        "weak_or_unconfirmed": 0,
    }
    opportunity_map.sort(
        key=lambda row: (
            state_weight[row["state"]],
            row["evidence"].get("cumulative_net_flow_yi_5d")
            if row["evidence"].get("cumulative_net_flow_yi_5d") is not None
            else -10**9,
            row["evidence"]["excess_5d_vs_csi300"],
        ),
        reverse=True,
    )
    return opportunity_map, {
        "status": "ok",
        "price_date": trade_date,
        "market_advance_ratio": market_advance_ratio,
        "moneyflow": moneyflow_meta,
        "quality_pool": quality_meta,
        "membership_errors": membership_errors,
        "candidate_rule": (
            "two_or_more independent tags -> six_layer_priority; "
            "one tag -> watch_only"
        ),
        "no_buy_signal": True,
    }


def diagnose_abnormal_structure(state: Optional[dict]) -> dict:
    if not state:
        return {"triggered": False, "reason": "未提供market-state JSON"}
    breadth = state.get("breadth_today", {})
    styles = state.get("style_index_comparison", {})
    turnover = state.get("total_turnover", {})
    csi = float(styles.get("沪深300", {}).get("pct_today") or 0)
    tech_values = [
        styles.get("科创50", {}).get("pct_today"),
        styles.get("创业板指", {}).get("pct_today"),
    ]
    tech_values = [float(value) for value in tech_values if value is not None]
    tech_avg = sum(tech_values) / len(tech_values) if tech_values else 0
    advance = float(breadth.get("advance_pct") or 0)
    turnover_change = float(turnover.get("chg_vs_avg_pct") or 0)
    tech_gap = tech_avg - csi
    triggered = (
        abs(csi) >= 2
        or abs(tech_avg) >= 3
        or abs(turnover_change) >= 20
    )
    if not triggered:
        label = "无异常触发"
    elif csi <= -2 and advance < 35 and turnover_change >= 20:
        label = "全市场流动性踩踏结构"
    elif csi <= -2 and advance < 35:
        label = "全市场风险冲击结构"
    elif tech_gap <= -3:
        label = "科技风格冲击结构"
    elif csi >= 2 and advance >= 65:
        label = "全市场风险偏好修复结构"
    elif tech_gap >= 3:
        label = "科技风格上冲结构"
    else:
        label = "混合型异常结构"
    return {
        "triggered": triggered,
        "structure_label": label,
        "csi300_pct": round(csi, 2),
        "tech_average_pct": round(tech_avg, 2),
        "tech_gap_vs_csi300": round(tech_gap, 2),
        "advance_pct": round(advance, 1),
        "turnover_change_vs_average_pct": round(turnover_change, 1),
        "causal_warning": "结构标签不是新闻原因；必须另行搜索宏观、外盘、产业、公告和交易结构证据",
        "cause_search_queries": [
            f"A股 {breadth.get('trade_date', '')} 大跌 大涨 原因",
            f"科创50 创业板 {breadth.get('trade_date', '')} 异常波动 原因",
            f"A股 {breadth.get('trade_date', '')} 资金流 ETF 赎回 两融",
        ] if triggered else [],
    }


def build_radar(
    state: Optional[dict], top: int, company_source: str = "sqlite"
) -> dict:
    pro = get_pro()
    rotation, trade_date = fetch_rotation(pro)
    events, mappings, event_dir = load_nonfin_tables()
    active_windows = load_active_windows()
    aliases = load_aliases()
    pool, code_map, company_store = load_company_sources(company_source)
    market_advance_ratio = float(
        (state or {}).get("breadth_today", {}).get("advance_pct") or 0
    )
    opportunity_map, opportunity_meta = build_sector_opportunity_map(
        pro=pro,
        rotation=rotation,
        trade_date=trade_date,
        market_advance_ratio=market_advance_ratio,
        events=events,
        active_windows=active_windows,
        aliases=aliases,
        pool=pool,
        code_map=code_map,
        company_store=company_store,
    )

    selected = []
    for row in rotation:
        if row["is_tech"]:
            continue
        # Discovery threshold only. The prior shadow backtest showed this must
        # not be interpreted as a buy signal.
        if (
            row["excess_5d_vs_csi300"] < 2
            and row["excess_today_vs_csi300"] < 2
        ):
            continue
        matches = find_matching_events(
            row["sector"], events, active_windows, aliases
        )
        companies, gaps = company_candidates(
            matches[:4], pool, code_map, store=company_store
        )
        coverage = (
            "active_catalyst_found" if matches
            else "coverage_gap_requires_external_search"
        )
        selected.append({
            **row,
            "discovery_only_not_buy_signal": True,
            "event_coverage": coverage,
            "active_events": matches[:4],
            "company_candidates": companies,
            "company_mapping_gaps": gaps,
            "external_search_queries": [
                f"{trade_date} {row['sector']} 板块 上涨 下跌 原因",
                f"{row['sector']} 最新 政策 订单 涨价 业绩 催化",
            ],
            "next_gate": (
                "核实催化来源并对候选公司运行stock-analysis质量检查"
                if matches else
                "先外部搜索补漏；找不到可靠催化则不进入操作池"
            ),
        })
        if len(selected) >= top:
            break

    return {
        "version": "2.0",
        "trade_date": trade_date,
        "abnormal_structure": diagnose_abnormal_structure(state),
        "rotation_candidates": selected,
        "sector_opportunity_map": opportunity_map,
        "opportunity_snapshot_meta": opportunity_meta,
        "data_sources": {
            "rotation": "Tushare申万一级行业index_daily",
            "sector_members": "Tushare index_member_all(SW2021)",
            "sector_breadth": "Tushare daily按申万一级行业成分聚合",
            "sector_moneyflow": "Tushare moneyflow按申万一级行业成分聚合",
            "event_directory": str(event_dir),
            "event_window": "event_map_query.py window --source nonfin",
            "company_relations": (
                str(DATA_ROOT / "event_map_shadow.db")
                if company_store is not None else str(COMPANY_POOL)
            ),
            "company_source_mode": (
                "sqlite" if company_store is not None else "excel_fallback"
            ),
            "quality_pool": str(QUALITY_CACHE),
        },
        "known_limitations": [
            "非科技sectors_status表缺失，因此不调用status命令",
            "行业相对强势只用于发现，不是买入信号；历史影子回测未支持机械追涨",
            "事件地图可能漏项，coverage gap必须触发外部搜索",
            "event_text_fallback公司关联必须经公告/业务核实后才能使用",
            "脚本不替代个股质量、估值、价格和盈亏比检查",
            "sector_opportunity_map中的科技催化仍由正式工作流STEP 4核验",
            "大单/特大单只代表订单规模代理，不能识别真实机构身份",
            "质量池是候选标签，不是全市场股票宇宙，也不产生买入结论",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--market-state",
        help="market_state_fetcher.py --json 的输出文件；提供后生成异常结构诊断",
    )
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--company-source", choices=("sqlite", "excel"), default="sqlite",
        help="公司关系来源；默认SQLite，excel仅用于紧急回退/双读验收",
    )
    args = parser.parse_args()
    state = None
    if args.market_state:
        state = json.loads(Path(args.market_state).read_text(encoding="utf-8"))
    result = build_radar(
        state, max(1, args.top), company_source=args.company_source
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
