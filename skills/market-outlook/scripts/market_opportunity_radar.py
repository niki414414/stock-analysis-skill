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
DATA_ROOT = WORKSPACE_ROOT / "技能数据"
SKILL_ROOT = REPO_ROOT / "skills" / "market-outlook"
ALIASES_PATH = SKILL_ROOT / "config" / "sector_aliases.json"
EVENT_QUERY = (
    REPO_ROOT / "skills" / "stock-analysis" / "scripts" / "event_map_query.py"
)
NONFIN_ROOT = DATA_ROOT / "非科技产业事件地图"
COMPANY_POOL = DATA_ROOT / "公司.xlsx"
CODE_MAP = DATA_ROOT / "company_code_map.csv"

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


def load_company_sources() -> tuple[pd.DataFrame, pd.DataFrame]:
    pool = pd.read_excel(
        COMPANY_POOL, sheet_name="非科技公司池", dtype=str
    ).fillna("")
    code_map = pd.read_csv(CODE_MAP, dtype=str).fillna("")
    code_map["code"] = code_map["code"].apply(normalize_code)
    return pool, code_map


def company_candidates(
    event_matches: list[dict],
    pool: pd.DataFrame,
    code_map: pd.DataFrame,
    limit: int = 12,
) -> tuple[list[dict], list[str]]:
    event_ids = {row["event_id"] for row in event_matches}
    event_text = " ".join(row["company_text"] for row in event_matches)
    candidates = []
    seen = set()
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
        source = None
        if event_ids.intersection(refs):
            source = "structured_event_link"
        elif name in event_text:
            source = "event_text_fallback"
        if not source or name in seen:
            continue
        code_rows = code_map[code_map["name"] == name]
        code = (
            code_rows.iloc[0]["code"]
            if not code_rows.empty else None
        )
        candidates.append({
            "name": name,
            "code": code,
            "link_source": source,
            "confidence": str(company.get("置信度", "")),
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
        })
    return candidates[:limit], gaps


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


def build_radar(state: Optional[dict], top: int) -> dict:
    pro = get_pro()
    rotation, trade_date = fetch_rotation(pro)
    events, mappings, event_dir = load_nonfin_tables()
    active_windows = load_active_windows()
    aliases = load_aliases()
    pool, code_map = load_company_sources()

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
            matches[:4], pool, code_map
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
        "data_sources": {
            "rotation": "Tushare申万一级行业index_daily",
            "event_directory": str(event_dir),
            "event_window": "event_map_query.py window --source nonfin",
            "company_pool": str(COMPANY_POOL),
        },
        "known_limitations": [
            "非科技sectors_status表缺失，因此不调用status命令",
            "行业相对强势只用于发现，不是买入信号；历史影子回测未支持机械追涨",
            "事件地图可能漏项，coverage gap必须触发外部搜索",
            "event_text_fallback公司关联必须经公告/业务核实后才能使用",
            "脚本不替代个股质量、估值、价格和盈亏比检查",
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
    args = parser.parse_args()
    state = None
    if args.market_state:
        state = json.loads(Path(args.market_state).read_text(encoding="utf-8"))
    result = build_radar(state, max(1, args.top))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
