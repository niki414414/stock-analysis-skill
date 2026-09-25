#!/usr/bin/env python3
"""Compose existing market-state and sector-radar outputs into one evidence pack.

This is an orchestrator, not another scoring model. It owns data-date checks,
degradation flags and the stable hand-off contract used by Market Outlook.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import csv
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from market_review_renderer_v2 import build_structure_decision_card  # noqa: E402


WORKSPACE_ROOT = Path(os.path.abspath(os.path.expanduser(
    os.environ.get("TZ_CODEX_HOME", "~/Desktop/tz-codex")
)))
REPO_ROOT = WORKSPACE_ROOT / "repo"
STATE_SCRIPT = REPO_ROOT / "skills/stock-analysis/scripts/market_state_fetcher.py"
RADAR_SCRIPT = REPO_ROOT / "skills/market-outlook/scripts/market_opportunity_radar.py"
STRATEGY_ROUTER = REPO_ROOT / "scripts/stock_strategy_router.py"
DEFAULT_HOLDINGS = WORKSPACE_ROOT / "holdings.csv"
DEFAULT_OBSERVATION_LOG = (
    WORKSPACE_ROOT / "技能数据/运行记录/市场观察快照.jsonl"
)
DEFAULT_STRUCTURE_V2_LOG = (
    WORKSPACE_ROOT / "技能数据/运行记录/市场结构V2版本.jsonl"
)


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _derive_legacy_sector_views(state: dict, radar: dict) -> dict:
    """Reuse the radar's current view builder for pre-sector_views snapshots."""
    opportunity_map = radar.get("sector_opportunity_map") or []
    if not opportunity_map:
        return {}
    spec = importlib.util.spec_from_file_location("market_opportunity_radar", RADAR_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_sector_views(
        opportunity_map=opportunity_map, state=state, limit=10
    )


def strategy_guidance(state: dict, radar: dict) -> dict:
    """Route attention to existing strategies without running their scanners."""
    environment = radar.get("market_environment", {}) or {}
    views = radar.get("sector_views", {}) or _derive_legacy_sector_views(state, radar)
    label = environment.get("label")
    turnover_change = float(
        state.get("total_turnover", {}).get("chg_vs_avg_pct") or 0
    )
    advance = float(state.get("breadth_today", {}).get("advance_pct") or 0)
    limit_down = state.get("breadth_today", {}).get("limit_down")
    trend_count = len(views.get("trend_watch", []) or [])
    rotation_count = len(views.get("rotation_watch", []) or [])
    swing_sectors = []
    for row in views.get("trend_watch", []) or []:
        confidence = row.get("coverage_confidence")
        alignment = row.get("evidence", {}).get("core_panorama_alignment")
        if confidence not in {"normal", "limited"}:
            continue
        if alignment in {"core_only_leadership", "panorama_only_rotation"}:
            continue
        swing_sectors.append(row.get("sector"))
        if len(swing_sectors) >= 5:
            break
    short_mode = "enabled_selective"
    short_reason = "存在轮动方向，但仍需板块承接和个股活跃度确认"
    if label == "risk_contraction" or advance <= 35:
        short_mode = "defensive_or_off"
        short_reason = "风险收缩，短线信号失效率上升"
    elif label == "incremental_broad_rally" and rotation_count:
        short_mode = "enabled"
        short_reason = "增量普涨且存在轮动攻击，可运行活跃推土机"
    elif label == "broad_rebound_without_increment":
        short_mode = "enabled_selective"
        short_reason = "缩量普涨，只做活跃前排，不把普涨后排当强势"

    # 推土机原始材料写的是跌停低于20家；用户补充的10家作为更严格绿灯。
    # 上涨家数使用占比描述市场环境，不把“3000家”固化为长期买入门槛。
    if isinstance(limit_down, (int, float)):
        if limit_down >= 20:
            pushdozer_gate = "red"
            short_mode = "defensive_or_off"
            short_reason = f"跌停{int(limit_down)}家达到红灯，停止推土机新开仓"
        elif limit_down > 10:
            pushdozer_gate = "yellow"
            if short_mode == "enabled":
                short_mode = "enabled_reduced"
            short_reason += f"；跌停{int(limit_down)}家为黄灯，计划仓位减半"
        else:
            pushdozer_gate = "green"
            short_reason += f"；跌停{int(limit_down)}家为绿灯"
    else:
        pushdozer_gate = "unknown"
        if short_mode == "enabled":
            short_mode = "enabled_selective"
        short_reason += "；跌停家数缺失，不给完整成交授权"

    swing_mode = "enabled" if trend_count else "observe_only"
    swing_reason = (
        "趋势榜已有候选，优先把前排方向交给事件地图和六层分析"
        if trend_count else "没有满足持续性的板块，波段暂不扩展候选"
    )
    if turnover_change <= -20:
        swing_mode = "enabled_reduced" if trend_count else "observe_only"
        swing_reason += "；成交显著萎缩，降低仓位与追涨意愿"
    return {
        "short-ma5": {
            "mode": short_mode, "reason": short_reason,
            "pushdozer_gate": {
                "level": pushdozer_gate, "limit_down": limit_down,
                "advance_pct": advance,
                "rule": "跌停≤10绿灯；11—19黄灯半仓；≥20红灯停止。上涨占比仅作环境标签",
                "evidence_status": "博主规则候选，等待6—12个月分层回测",
            },
            "scanner_command": f"python3 {STRATEGY_ROUTER} --task short-ma5 --top 20",
            "output_contract": "约20只候选、前5核查池和图形教学；不进入六层",
        },
        "catalyst-swing": {
            "mode": swing_mode, "reason": swing_reason,
            "preferred_sectors": swing_sectors,
            "sector_gate": "优先正常/有限覆盖；核心与全景明显分歧或低覆盖只观察",
            "scanner_command": f"python3 {STRATEGY_ROUTER} --task catalyst-swing --top 20",
            "output_contract": "事件地图＋市场响应初筛，候选进入六层",
        },
        "quality-core": {
            "mode": "independent",
            "reason": "底仓质量与估值框架独立，不参与短线板块轮动",
            "scanner_command": f"python3 {STRATEGY_ROUTER} --task quality-core --top 20",
            "output_contract": "只在有配置需求时运行，候选进入六层",
        },
        "no_cross_strategy_ranking": True,
    }


def load_holdings(path: str | Path | None) -> list[dict]:
    if not path or not Path(path).exists():
        return []
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def fetch_holding_market_data(holdings: list[dict], trade_date: str) -> dict[str, dict]:
    """Fetch only the light data needed by the review triage table."""
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        env_path = REPO_ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.strip() and "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    if key.strip() == "TUSHARE_TOKEN":
                        token = value.strip()
                        break
    if not token or not trade_date:
        return {}
    import tushare as ts
    pro = ts.pro_api(token)
    start = (datetime.strptime(trade_date, "%Y%m%d").date()).strftime("%Y%m%d")
    # Calendar days, not trading days: 130 days safely covers MA60.
    from datetime import timedelta
    start = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=130)).strftime("%Y%m%d")
    output = {}
    for holding in holdings:
        raw_code = str(holding.get("code", "")).strip()
        digits = "".join(ch for ch in raw_code if ch.isdigit())
        if len(digits) != 6 or holding.get("asset_type") == "fund":
            continue
        ts_code = f"{digits}.SH" if digits.startswith(("5", "6")) else f"{digits}.SZ"
        try:
            api = pro.fund_daily if holding.get("asset_type") == "etf" else pro.daily
            frame = api(
                ts_code=ts_code, start_date=start, end_date=trade_date,
                fields="ts_code,trade_date,close,pct_chg",
            )
        except Exception as exc:
            output[raw_code] = {"status": "unavailable", "error": str(exc)}
            continue
        if frame is None or frame.empty:
            output[raw_code] = {"status": "unavailable", "error": "empty"}
            continue
        frame = frame.sort_values("trade_date")
        closes = frame["close"].astype(float)
        latest = float(closes.iloc[-1])
        ma20 = float(closes.tail(20).mean()) if len(closes) >= 20 else None
        ma60 = float(closes.tail(60).mean()) if len(closes) >= 60 else None
        output[raw_code] = {
            "status": "ok", "trade_date": str(frame["trade_date"].iloc[-1]),
            "latest_price": round(latest, 3),
            "pct_today": round(float(frame["pct_chg"].iloc[-1]), 2),
            "ma20": round(ma20, 3) if ma20 is not None else None,
            "ma60": round(ma60, 3) if ma60 is not None else None,
            "above_ma20": latest >= ma20 if ma20 is not None else None,
            "above_ma60": latest >= ma60 if ma60 is not None else None,
        }
    return output


def build_holding_review_queue(
    holdings: list[dict], state: dict, market_data: dict[str, dict] | None = None,
) -> list[dict]:
    """Create downstream work items; stock-specific actions remain six-layer work."""
    risk_contraction = float(
        state.get("breadth_today", {}).get("advance_pct") or 0
    ) <= 35
    queue = []
    for row in holdings:
        shares_text = str(row.get("shares", "")).strip()
        try:
            shares = float(shares_text) if shares_text else None
        except ValueError:
            shares = None
        if shares == 0:
            continue
        identity = str(row.get("role", "")).strip() or "unclassified"
        quote = (market_data or {}).get(str(row.get("code", "")).strip(), {})
        try:
            cost = float(row.get("cost")) if str(row.get("cost", "")).strip() else None
        except ValueError:
            cost = None
        latest = quote.get("latest_price")
        pnl_pct = (
            round((latest / cost - 1) * 100, 1)
            if isinstance(latest, (int, float)) and cost and cost > 0 else None
        )
        if quote.get("status") == "ok":
            trend_brief = (
                f"MA20{'上' if quote.get('above_ma20') else '下'} / "
                f"MA60{'上' if quote.get('above_ma60') else '下'}"
            )
        else:
            trend_brief = "行情数据待专项复核"
        queue.append({
            "account": row.get("account"), "code": row.get("code"),
            "name": row.get("name"), "asset_type": row.get("asset_type"),
            "shares": shares, "cost": cost,
            "market_data": {
                "trade_date": quote.get("trade_date"),
                "latest_price": latest, "pct_today": quote.get("pct_today"),
                "pnl_pct_vs_cost": pnl_pct,
                "ma20": quote.get("ma20"), "ma60": quote.get("ma60"),
                "trend_brief": trend_brief,
            },
            "strategy_identity": identity,
            "market_overlay": (
                "风险收缩：优先检查失效条件和仓位，不自动卖出"
                if risk_contraction else
                "市场未触发统一退出；继续按原策略身份检查"
            ),
            "required_analysis": (
                "基金/ETF持仓复核：对应指数趋势、成本与配置目的"
                if row.get("asset_type") in {"fund", "etf"} else
                "stock-analysis持仓模式：趋势、事件、支撑/失效和盈亏比"
            ),
            "identity_warning": (
                "策略身份缺失，先人工归类；禁止被套后临时改为波段或底仓"
                if identity == "unclassified" else
                "固定原策略身份，禁止因盈亏临时转换"
            ),
            "action_boundary": "本编排器不生成个股买卖价，须由对应持仓分析完成",
        })
    return queue


def _date_inventory(state: dict, radar: dict) -> dict:
    price_dates = {
        str(value) for value in (
            state.get("breadth_today", {}).get("trade_date"),
            state.get("subsector_basket_momentum", {}).get("trade_date"),
            radar.get("trade_date"),
            radar.get("opportunity_snapshot_meta", {}).get("price_date"),
        ) if value
    }
    moneyflow_date = (
        radar.get("opportunity_snapshot_meta", {})
        .get("moneyflow", {}).get("latest_trade_date")
    )
    return {
        "price_dates": sorted(price_dates),
        "price_dates_aligned": len(price_dates) <= 1,
        "moneyflow_date": moneyflow_date,
        "moneyflow_lagged": (
            bool(moneyflow_date and price_dates and moneyflow_date < max(price_dates))
        ),
        "hotspot_dates": {
            "ths": state.get("ths_hotspot_momentum", {}).get("trade_date"),
            "sw_l2_l3": state.get("sw_subindustry_momentum", {}).get("trade_date"),
        },
    }


def presentation_contract(output_mode: str) -> dict:
    """Define two renderings over the same evidence and judgment payload."""
    if output_mode == "decision":
        return {
            "mode": "decision",
            "purpose": "回答今天怎么做，不重复研究过程",
            "required_sections": [
                "一句市场姿态",
                "最多3个关键方向及其动作/确认/失效条件",
                "现有持仓逐项动作和唯一关键条件",
                "短线/波段/底仓的启动状态与今日主策略",
            ],
            "rules": [
                "只保留会改变行动的信息，不复述完整后台指标",
                "方向未确认时写观察，不用预测性措辞冒充事实",
                "没有满足条件的机会时允许明确输出不行动",
                "不得给跨策略总排名，不得由板块结论替代个股分析",
            ],
        }
    return {
        "mode": "analysis",
        "purpose": "解释判断如何形成，并保留教学和复核颗粒度",
        "required_sections": [
            "市场环境与关键变化",
            "3至5个重点方向的核心/全景、相对强弱和扩散证据",
            "每个方向的支持、反证、确认和失效条件",
            "现有持仓行动摘要与下一步专项任务",
            "三类策略启用状态",
        ],
        "rules": [
            "解释数字代表什么以及为什么影响判断",
            "趋势、轮动和预热严格分开",
            "保留反证和信息不足，不为形成结论而补造因果",
            "教学解释只讲一次，避免同一依据在多个章节重复",
        ],
    }


def build_hotspot_discovery(state: dict, limit: int = 15) -> dict:
    """Expose fine-grained discovery without mixing it into the SW L1 ranking."""
    def normalize(rows: list[dict], source_label: str) -> list[dict]:
        output = []
        for row in rows[:limit]:
            preheat = row.get("preheat_features", {}) or {}
            output.append({
                "source": row.get("source"),
                "source_label": source_label,
                "board_code": row.get("board_code"),
                "board_name": row.get("board_name"),
                "classification_level": row.get("classification_level"),
                "pct_today": row.get("pct_today"),
                "pct_5d": row.get("pct_5d"),
                "pct_20d": row.get("pct_20d"),
                "close_location": row.get("close_location"),
                "member_stats": row.get("member_stats", {}),
                "preheat_state": preheat.get("state"),
                "preheat_features": preheat,
                "interpretation_boundary": row.get(
                    "interpretation_boundary",
                    "细分行业用于轮动发现，仍需个股主营与位置复核",
                ),
            })
        return output

    ths = state.get("ths_hotspot_momentum", {}) or {}
    sw = state.get("sw_subindustry_momentum", {}) or {}
    ths_rows = normalize(ths.get("boards", []) or [], "同花顺热点概念")
    sw_rows = normalize(sw.get("boards", []) or [], "申万二/三级行业")

    theme_groups = (
        ("封装", "先进封装", "集成电路封测"),
        ("芯片", "半导体", "集成电路"),
        ("种子", "转基因", "种植", "农业"),
        ("养殖", "畜牧", "水产"),
        ("光伏", "太阳能"), ("电机",), ("航天", "卫星"),
        ("通信", "光模块", "CPO"), ("电子化学品", "湿电子化学"),
        ("磨料", "金刚石"), ("管材",), ("白银", "黄金", "贵金属"),
        ("机器人",), ("电池", "储能"), ("医药", "创新药"), ("算力",),
    )

    def matches(left: str, right: str) -> bool:
        left, right = str(left or ""), str(right or "")
        for group in theme_groups:
            if any(token in left for token in group) and any(token in right for token in group):
                return True
        compact_left = left.replace("指数", "").replace("(A股)", "")
        compact_right = right.replace("指数", "").replace("Ⅲ", "").replace("Ⅱ", "")
        return (
            len(compact_left) >= 3 and len(compact_right) >= 3
            and (compact_left in compact_right or compact_right in compact_left)
        )

    def sw_matches(left: str, right: str) -> bool:
        left, right = str(left or ""), str(right or "")
        compact_left = left.replace("Ⅲ", "").replace("Ⅱ", "").replace("其他", "")
        compact_right = right.replace("Ⅲ", "").replace("Ⅱ", "").replace("其他", "")
        if compact_left in compact_right or compact_right in compact_left:
            return min(len(compact_left), len(compact_right)) >= 2
        agriculture = ("种子", "种植")
        return any(token in left for token in agriculture) and any(
            token in right for token in agriculture
        )

    matched_ths = set()
    raw_spine = []
    for sw_row in sw_rows:
        contexts = []
        for index, ths_row in enumerate(ths_rows):
            if matches(sw_row.get("board_name"), ths_row.get("board_name")):
                contexts.append(ths_row)
                matched_ths.add(index)
        raw_spine.append({**sw_row, "ths_context": contexts[:3]})

    # Collapse nearby SW L2/L3 labels into one report slot. Keep the first
    # (market-ranked) row and retain the other official labels for audit.
    rotation_spine = []
    for row in raw_spine:
        existing = next((
            item for item in rotation_spine
            if sw_matches(item.get("board_name"), row.get("board_name"))
        ), None)
        if existing is None:
            rotation_spine.append({**row, "related_sw_classifications": []})
            continue
        existing["related_sw_classifications"].append({
            "board_code": row.get("board_code"),
            "board_name": row.get("board_name"),
            "classification_level": row.get("classification_level"),
            "pct_today": row.get("pct_today"),
            "pct_5d": row.get("pct_5d"),
        })
        known_context = {item.get("board_code") for item in existing["ths_context"]}
        existing["ths_context"].extend(
            item for item in row.get("ths_context", [])
            if item.get("board_code") not in known_context
        )
    supplemental = [
        row for index, row in enumerate(ths_rows) if index not in matched_ths
    ]
    return {
        "rotation_spine": rotation_spine,
        "supplemental_concepts": supplemental,
        # Compatibility views remain available for audit.
        "ths_market_hotspots": ths_rows,
        "sw_l2_l3_rotation": sw_rows,
        "data_dates": {
            "ths": ths.get("trade_date"), "sw_l2_l3": sw.get("trade_date"),
        },
        "usage": "申万二/三级是轮动主骨架；同花顺只补充或解释更细概念；不直接产生个股推荐",
    }


def build_observation_snapshot(review: dict) -> dict:
    """Keep only the compact daily signals needed for forward evaluation."""
    dates = review.get("data_dates", {}) or {}
    price_dates = dates.get("price_dates", []) or []
    discovery = review.get("sector_views", {}).get("hotspot_discovery", {}) or {}
    return {
        "schema_version": "1.0",
        "trade_date": max(price_dates) if price_dates else None,
        "generated_at": review.get("generated_at"),
        "market_environment": review.get("market", {}).get("environment", {}).get("label"),
        "index_roadmaps": review.get("market", {}).get("range_outlook", {}),
        "market_structure_v2": review.get("market", {}).get("structure_v2", {}),
        "rotation_spine": discovery.get("rotation_spine", [])[:15],
        "supplemental_concepts": discovery.get("supplemental_concepts", [])[:15],
        "trend_watch": review.get("sector_views", {}).get("trend", [])[:10],
        "preheat_watch": review.get("sector_views", {}).get("event_preheat", [])[:10],
        "evaluation_windows_trading_days": [1, 3, 5, 10],
    }


def save_observation_snapshot(review: dict, path: str | Path) -> dict:
    """Idempotently keep one compact JSONL record per price date."""
    snapshot = build_observation_snapshot(review)
    if not snapshot.get("trade_date"):
        return {"status": "skipped", "reason": "missing_trade_date"}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    records = []
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("trade_date") != snapshot["trade_date"]:
                records.append(record)
    records.append(snapshot)
    records.sort(key=lambda row: str(row.get("trade_date") or ""))
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
        encoding="utf-8",
    )
    temp.replace(target)
    return {"status": "saved", "path": str(target), "trade_date": snapshot["trade_date"]}


def save_structure_v2_versions(review: dict, path: str | Path) -> dict:
    """Append timestamped V2 structures without replacing same-day revisions."""
    structures = review.get("market", {}).get("structure_v2", {}) or {}
    rows = []
    for index_name, snapshot in structures.items():
        if not snapshot or snapshot.get("mode") != "shadow":
            continue
        rows.append({
            "index_name": index_name,
            "trade_date": snapshot.get("trade_date"),
            "as_of_timestamp": snapshot.get("as_of_timestamp"),
            "snapshot": snapshot,
        })
    if not rows:
        return {"status": "skipped", "reason": "missing_structure_v2"}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    records = []
    seen = set()
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            key = (record.get("index_name"), record.get("as_of_timestamp"))
            if key not in seen:
                records.append(record)
                seen.add(key)
    for row in rows:
        key = (row["index_name"], row["as_of_timestamp"])
        if key not in seen:
            records.append(row)
            seen.add(key)
    records.sort(key=lambda row: (str(row.get("as_of_timestamp") or ""), row.get("index_name") or ""))
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
        encoding="utf-8",
    )
    temp.replace(target)
    return {"status": "saved", "path": str(target), "versions_written": len(rows)}


def compose_review(
    state: dict, radar: dict, holdings: list[dict] | None = None,
    holding_market_data: dict[str, dict] | None = None,
    output_mode: str = "analysis",
) -> dict:
    if output_mode not in {"analysis", "decision"}:
        raise ValueError(f"未知输出模式: {output_mode}")
    dates = _date_inventory(state, radar)
    baskets = state.get("subsector_basket_momentum", {}) or {}
    views = radar.get("sector_views", {}) or _derive_legacy_sector_views(state, radar)
    hotspot_discovery = build_hotspot_discovery(state)
    environment = radar.get("market_environment")
    if not environment and state:
        spec = importlib.util.spec_from_file_location("market_opportunity_radar", RADAR_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        environment = module.classify_market_environment(state)
    missing = []
    for field, value in (
        ("market_environment", environment),
        ("breadth_today", state.get("breadth_today")),
        ("total_turnover", state.get("total_turnover")),
        ("style_index_comparison", state.get("style_index_comparison")),
        ("technical_levels", state.get("technical_levels")),
        ("sector_views", views),
    ):
        if not value:
            missing.append(field)
    review_queue = baskets.get("external_review_queue", []) or []
    conflicts = []
    for row in views.get("trend_watch", []) or []:
        alignment = row.get("evidence", {}).get("core_panorama_alignment")
        if alignment in {"core_only_leadership", "panorama_only_rotation", "external_panorama_only"}:
            conflicts.append({
                "sector": row.get("sector"), "alignment": alignment,
                "required_interpretation": "降低趋势置信度并说明核心/全景分歧",
            })
    status = "ready"
    if missing or not dates["price_dates_aligned"]:
        status = "degraded"
    structures_v2 = {
        name: (state.get("technical_levels", {}).get(name, {}) or {}).get(
            "market_structure_v2", {}
        )
        for name in ("上证指数", "创业板指", "科创50")
    }
    structure_cards_v2 = {
        name: build_structure_decision_card(snapshot)
        for name, snapshot in structures_v2.items() if snapshot
    }
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now().isoformat(),
        "status": status,
        "presentation": presentation_contract(output_mode),
        "data_dates": dates,
        "market": {
            "environment": environment or {},
            "breadth": state.get("breadth_today", {}),
            "turnover": state.get("total_turnover", {}),
            "style": state.get("style_index_comparison", {}),
            "margin_risk": state.get("margin_rolling_signal", {}),
            "abnormal_structure": radar.get("abnormal_structure", {}),
            "technical_levels": state.get("technical_levels", {}),
            "range_outlook": {
                name: (state.get("technical_levels", {}).get(name, {}) or {}).get(
                    "range_outlook", {}
                )
                for name in ("上证指数", "创业板指", "科创50")
            },
            "structure_v2": structures_v2,
            "structure_v2_cards": structure_cards_v2,
        },
        "sector_views": {
            "trend": views.get("trend_watch", []),
            "rotation": views.get("rotation_watch", []),
            "event_preheat": views.get("event_preheat_watch", []),
            "hotspot_discovery": hotspot_discovery,
        },
        "sector_evidence_detail": radar.get("sector_opportunity_map", []),
        "basket_input": {
            "model": baskets.get("input_model"),
            "external_approved_count": baskets.get("external_approved_count"),
            "review_queue": review_queue,
        },
        "decision_routes": {
            "strategy_guidance": strategy_guidance(state, {**radar, "sector_views": views}),
            "holding_review_queue": build_holding_review_queue(
                holdings or [], state, holding_market_data
            ),
            "next_steps": [
                "按strategy_guidance选择要运行的现有扫描器，不运行关闭的策略",
                "将holding_review_queue逐项交给对应持仓分析，不用板块强弱代替个股结论",
                "波段候选进入六层；短线候选使用推土机盘中确认；底仓保持独立",
            ],
        },
        "checks": {
            "missing_required_sections": missing,
            "core_panorama_conflicts": conflicts,
            "limitations": radar.get("known_limitations", []),
        },
        "interpretation_contract": [
            "先解释市场环境，再解释三张榜",
            "固定解读上证指数、创业板指、科创50的两级支撑/压力、上下空间、近期5日实际波动和越界改判条件",
            "同花顺热点与申万二三级用于补足一级行业颗粒度；概念热度不得冒充主营纯度",
            "趋势榜用于未来一周观察，轮动榜不得冒充趋势榜",
            "事件预热在价格确认前不得升级",
            "重点方向必须给支持、反证、确认和失效条件",
            "默认用户输出只保留盘面与板块、现有持仓、下一步策略三块",
            "限制模块数量而非证据颗粒度；重点板块逐项展示核心/全景、相对强弱、风险、确认和失效",
            "趋势与轮动必须分开；正文展示3至5个关键方向，完整后台榜单不倾倒",
            "候选扫描、六层分析和具体价格属于下一层，未经明确要求不自动启动",
            "本证据包不产生买入信号",
        ],
        "interaction_contract": {
            "ask_after_review": "今天实际有操作吗？如果有，请告诉我‘股票＋买/卖/加/减＋策略身份’；如果没有，回复‘无操作’即可。",
            "unknown_rule": "用户未回复时记录为决策未知，不得推测为无操作",
            "max_follow_up_questions": 2,
            "journal_entry": "scripts/daily_decision_journal.py",
            "outcome_windows_trading_days": [3, 5, 10],
            "evaluation_rule": "模型方向、条件触发、用户执行和交易结果分开评价",
        },
    }


def run_pipeline(top: int) -> tuple[dict, dict]:
    env = os.environ.copy()
    env["TZ_CODEX_HOME"] = str(WORKSPACE_ROOT)
    state_result = subprocess.run(
        [sys.executable, str(STATE_SCRIPT), "--json"], env=env,
        check=True, capture_output=True, text=True,
    )
    state = json.loads(state_result.stdout)
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as tmp:
        json.dump(state, tmp, ensure_ascii=False)
        tmp.flush()
        radar_result = subprocess.run(
            [sys.executable, str(RADAR_SCRIPT), "--market-state", tmp.name,
             "--top", str(top), "--json"], env=env,
            check=True, capture_output=True, text=True,
        )
    return state, json.loads(radar_result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-state", help="复用已有market-state JSON")
    parser.add_argument("--radar", help="复用已有radar JSON；须与market-state同时提供")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument(
        "--output-mode", choices=("analysis", "decision"), default="analysis",
        help="analysis保留研究与教学颗粒度；decision只规定行动版呈现边界",
    )
    parser.add_argument(
        "--holdings", default=str(DEFAULT_HOLDINGS),
        help="持仓CSV；默认工作区holdings.csv，不存在时返回空队列",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--save-observation", action="store_true",
        help="按交易日幂等保存轻量观察快照，用于未来1/3/5/10日验证",
    )
    parser.add_argument(
        "--observation-log", default=str(DEFAULT_OBSERVATION_LOG),
        help="轻量观察快照JSONL路径",
    )
    parser.add_argument(
        "--structure-v2-log", default=str(DEFAULT_STRUCTURE_V2_LOG),
        help="同一交易日可保留多个时间戳的V2结构版本JSONL",
    )
    args = parser.parse_args()
    if bool(args.market_state) != bool(args.radar):
        parser.error("--market-state 与 --radar 必须同时提供")
    if args.market_state:
        state, radar = load_json(args.market_state), load_json(args.radar)
    else:
        state, radar = run_pipeline(max(1, args.top))
    holdings = load_holdings(args.holdings)
    trade_date = radar.get("trade_date") or state.get("breadth_today", {}).get("trade_date")
    holding_market_data = fetch_holding_market_data(holdings, str(trade_date or ""))
    review = compose_review(
        state, radar, holdings, holding_market_data,
        output_mode=args.output_mode,
    )
    if args.save_observation:
        review["observation_snapshot"] = save_observation_snapshot(
            review, args.observation_log
        )
        review["structure_v2_version_log"] = save_structure_v2_versions(
            review, args.structure_v2_log
        )
    print(json.dumps(review, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
