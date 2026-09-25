#!/usr/bin/env python3
"""Output-first A-share executable market brief.

This entry point deliberately does not call the legacy daily-review, radar, or
event-store pipelines.  It fetches only the bars needed by the published
article, builds one auditable price map for every market/sector/stock object,
and renders the article and its evidence from the same result.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
WORKSPACE_ROOT = REPO_ROOT.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_structure_v2 import build_market_structure_v2  # noqa: E402
from skills.shared.price_data import adjust_to_latest  # noqa: E402


SCHEMA_VERSION = "market-brief-2.0.0"
CONTRACT_VERSION = "market-brief-output-contract-1.0"
DEFAULT_OUTPUT_DIR = WORKSPACE_ROOT / "技能数据" / "运行记录" / "market_brief_v2"
# The box engine is deliberately run independently on the three headline
# indices. 沪深300 remains a benchmark for relative-sector context, not a
# fourth headline box.
INDEX_CODES = {
    "上证指数": "000001.SH",
    "创业板指": "399006.SZ",
    "科创50": "000688.SH",
    "沪深300": "000300.SH",
}
HEADLINE_INDEX_NAMES = ("上证指数", "创业板指", "科创50")
FINANCE_WORDS = ("证券", "银行", "保险", "金融")
NON_ACTIONABLE_SECTOR_NAMES = {"综合"}


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalise_bars(rows: Iterable[dict] | pd.DataFrame) -> pd.DataFrame:
    frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    if frame.empty:
        raise ValueError("K线为空")
    # Tushare's dedicated Shenwan endpoint uses pct_change while the generic
    # index/daily endpoints use pct_chg. Keep one internal field name so the
    # price-map and volume logic do not silently treat SW returns as missing.
    if "pct_change" in frame.columns and "pct_chg" not in frame.columns:
        frame = frame.rename(columns={"pct_change": "pct_chg"})
    required = {"trade_date", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"K线缺少字段: {sorted(missing)}")
    frame["trade_date"] = frame["trade_date"].astype(str)
    for column in ("open", "high", "low", "close", "pct_chg", "amount", "vol"):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return (
        frame.dropna(subset=["high", "low", "close"])
        .sort_values("trade_date")
        .drop_duplicates("trade_date", keep="last")
        .reset_index(drop=True)
    )


def _zone(level: dict | None) -> dict | None:
    if not level:
        return None
    value = level.get("zone", level)
    return {
        "lower": _number(value.get("lower")),
        "mid": _number(value.get("mid")),
        "upper": _number(value.get("upper")),
    }


def _zone_text(zone: dict | None, digits: int = 2) -> str:
    if not zone or zone.get("lower") is None or zone.get("upper") is None:
        return "数据不足"
    lower, upper = zone["lower"], zone["upper"]
    return f"{lower:.{digits}f}—{upper:.{digits}f}"


def _pct_change(frame: pd.DataFrame, sessions: int) -> float | None:
    if len(frame) <= sessions:
        return None
    base = float(frame.iloc[-sessions - 1]["close"])
    return round((float(frame.iloc[-1]["close"]) / base - 1) * 100, 2) if base else None


def _moving_averages(frame: pd.DataFrame) -> dict:
    closes = frame["close"]
    return {
        f"MA{window}": round(float(closes.tail(window).mean()), 2)
        if len(closes) >= window else None
        for window in (5, 10, 20, 30, 60, 250)
    }


def _volume_context(frame: pd.DataFrame) -> dict:
    if "amount" not in frame or frame["amount"].dropna().empty:
        return {
            "unit": None, "latest": None, "average_20d": None,
            "startup_baseline": None, "climax_candidate": None,
            "latest_multiple": None, "divergence_count": None,
            "latest_price_reaction": "成交额数据缺失", "daily": [],
        }
    work = frame.copy()
    work["amount_ma20"] = work["amount"].rolling(20, min_periods=5).mean()
    if "pct_chg" not in work:
        work["pct_chg"] = work["close"].pct_change() * 100
    work["amount_ratio"] = work["amount"] / work["amount_ma20"]
    recent = work.tail(60)
    starts = recent[(recent["pct_chg"] >= 3.0) & (recent["amount_ratio"] >= 1.35)]
    baseline_is_confirmed = not starts.empty
    startup = starts.iloc[0] if baseline_is_confirmed else recent.iloc[recent["amount_ratio"].fillna(0).argmax()]
    baseline = _number(startup.get("amount"))
    startup_date = str(startup.get("trade_date"))
    after = work[work["trade_date"] >= startup_date].copy()
    upper_wick = (
        (after["high"] - after[["open", "close"]].max(axis=1))
        / (after["high"] - after["low"]).replace(0, pd.NA)
        if "open" in after else pd.Series(0, index=after.index)
    )
    divergences = after[
        (after["amount_ratio"] >= 1.5)
        & ((after["pct_chg"] <= 0.5) | (upper_wick >= 0.35))
    ]
    latest = work.iloc[-1]
    latest_ratio = _number(latest.get("amount_ratio"))
    latest_pct = _number(latest.get("pct_chg"), 0.0) or 0.0
    latest_wick = _number(upper_wick.iloc[-1] if len(upper_wick) else 0, 0.0) or 0.0
    if latest_ratio is not None and latest_ratio >= 1.5 and (latest_pct <= 0.5 or latest_wick >= 0.35):
        reaction = "放量滞涨或冲高回落"
    elif latest_ratio is not None and latest_ratio >= 1.35 and latest_pct > 1:
        reaction = "放量上行"
    elif latest_ratio is not None and latest_ratio < 0.8:
        reaction = "缩量"
    else:
        reaction = "量价中性"
    # Tushare daily.amount is thousand yuan; fixtures may explicitly override.
    unit = "千元"
    daily = []
    recent_daily = work.tail(10).reset_index(drop=True)
    for idx, row in recent_daily.iterrows():
        previous_amount = recent_daily.iloc[idx - 1]["amount"] if idx else None
        daily.append({
            "trade_date": str(row["trade_date"]),
            "amount": round(float(row["amount"]), 2) if pd.notna(row["amount"]) else None,
            "amount_ratio_20d": round(float(row["amount_ratio"]), 2)
            if pd.notna(row["amount_ratio"]) else None,
            "amount_change_1d_pct": round((float(row["amount"]) / float(previous_amount) - 1) * 100, 2)
            if previous_amount and pd.notna(row["amount"]) else None,
            "pct_chg": round(float(row["pct_chg"]), 2) if pd.notna(row["pct_chg"]) else None,
        })
    previous_5 = work["amount"].tail(6).head(5).mean() if len(work) >= 6 else None
    return {
        "unit": unit,
        "latest": round(_number(latest.get("amount"), 0.0) or 0.0, 2),
        "average_20d": round(_number(latest.get("amount_ma20"), 0.0) or 0.0, 2),
        "startup_date": startup_date,
        "baseline_status": "confirmed_startup_bar" if baseline_is_confirmed else "largest_recent_volume_proxy",
        "startup_baseline": round(baseline, 2) if baseline is not None else None,
        "climax_multiple_candidate": 4.0,
        "climax_candidate": round(baseline * 4, 2) if baseline is not None else None,
        "latest_multiple": round(latest_ratio, 2) if latest_ratio is not None else None,
        "amount_change_1d_pct": round((float(latest["amount"]) / float(work.iloc[-2]["amount"]) - 1) * 100, 2)
        if len(work) >= 2 and pd.notna(work.iloc[-2]["amount"]) else None,
        "amount_change_vs_previous_5d_pct": round((float(latest["amount"]) / float(previous_5) - 1) * 100, 2)
        if previous_5 and pd.notna(latest["amount"]) else None,
        "divergence_count": int(len(divergences)),
        "divergence_dates": divergences["trade_date"].astype(str).tolist()[-5:],
        "latest_price_reaction": reaction,
        "daily": daily,
        "interpretation": "高潮量只是候选阈值；必须同时出现滞涨或冲高回落，第一次分歧不自动等于退潮",
    }


def _volume_box_context(
    frame: pd.DataFrame, support: dict | None, resistance: dict | None,
) -> dict:
    """Join the latest daily volume path to the price box boundary being tested."""
    if not support and not resistance:
        return {"signal": "边界数据不足", "gate": "insufficient_boundary"}
    if "amount" not in frame or frame["amount"].dropna().empty:
        return {"signal": "成交额缺失，不能确认突破质量", "gate": "volume_missing"}
    latest = frame.iloc[-1]
    current = float(latest["close"])
    high = float(latest["high"])
    low = float(latest["low"])
    pct = _number(latest.get("pct_chg"), 0.0) or 0.0
    amount_ma20 = frame["amount"].rolling(20, min_periods=5).mean().iloc[-1]
    ratio = float(latest["amount"] / amount_ma20) if amount_ma20 else None
    candle_range = max(high - low, 0.0001)
    upper_wick = (high - max(float(latest.get("open", current)), current)) / candle_range
    support_touch = bool(support and low <= support["upper"] and current >= support["lower"])
    resistance_rejected = bool(resistance and high >= resistance["lower"] and current < resistance["lower"])
    resistance_touch = bool(
        resistance and high >= resistance["lower"]
        and resistance["lower"] <= current <= resistance["upper"]
    )
    above_resistance = bool(resistance and current > resistance["upper"])
    below_support = bool(support and current < support["lower"])
    if above_resistance:
        if ratio is not None and ratio >= 1.2 and pct > 0:
            return {"signal": "放量突破确认", "gate": "breakout_confirmed", "amount_ratio_20d": round(ratio, 2)}
        return {"signal": "价格越过压力但量能不足，突破待确认", "gate": "breakout_pending_volume", "amount_ratio_20d": round(ratio, 2) if ratio is not None else None}
    if below_support:
        if ratio is not None and ratio >= 1.2 and pct < 0:
            return {"signal": "放量破位确认", "gate": "breakdown_confirmed", "amount_ratio_20d": round(ratio, 2)}
        return {"signal": "价格跌破支撑但量能未放大，破位待确认", "gate": "breakdown_pending_volume", "amount_ratio_20d": round(ratio, 2) if ratio is not None else None}
    if support_touch and ratio is not None and ratio < 1.0 and pct >= -0.5:
        return {"signal": "支撑区缩量承接", "gate": "support_absorption", "amount_ratio_20d": round(ratio, 2)}
    if resistance_rejected:
        return {
            "signal": "盘中触及压力后收于区间下方，突破未确认",
            "gate": "resistance_rejected",
            "amount_ratio_20d": round(ratio, 2) if ratio is not None else None,
        }
    if resistance_touch and ratio is not None and ratio >= 1.2 and (pct <= 0.5 or upper_wick >= 0.35):
        return {"signal": "压力区放量滞涨", "gate": "resistance_distribution", "amount_ratio_20d": round(ratio, 2)}
    if resistance_touch:
        return {"signal": "压力区测试，等待量价确认", "gate": "resistance_testing", "amount_ratio_20d": round(ratio, 2) if ratio is not None else None}
    if support_touch:
        return {"signal": "支撑区测试，等待承接确认", "gate": "support_testing", "amount_ratio_20d": round(ratio, 2) if ratio is not None else None}
    return {"signal": "箱体内部量价中性", "gate": "inside_box_neutral", "amount_ratio_20d": round(ratio, 2) if ratio is not None else None}


def _location_label(ratio: float | None, permission: str) -> str:
    if permission == "PRICE_BREAK_ABOVE_PREVIOUS_BOX":
        return "已向上穿越上一箱体"
    if permission == "PRICE_BREAK_BELOW_PREVIOUS_BOX":
        return "已向下穿越上一箱体"
    if ratio is None:
        return "边界待确认"
    if ratio <= 0.30:
        return "箱体下沿"
    if ratio >= 0.70:
        return "箱体上沿"
    return "箱体中部"


def _stage(frame: pd.DataFrame, structure: dict, volume: dict) -> str:
    ma = _moving_averages(frame)
    current = float(frame.iloc[-1]["close"])
    ret5 = _pct_change(frame, 5) or 0.0
    permission = structure.get("tradable_room", {}).get("permission")
    if permission == "PRICE_BREAK_BELOW_PREVIOUS_BOX":
        return "破位"
    if permission == "PRICE_BREAK_ABOVE_PREVIOUS_BOX":
        return "突破待确认"
    if volume.get("latest_price_reaction") == "放量滞涨或冲高回落":
        return "分歧"
    if ma.get("MA20") and current < ma["MA20"]:
        return "回踩" if ret5 > -4 else "下跌"
    if ret5 >= 6:
        return "加速"
    if ret5 >= 2:
        return "趋势运行"
    return "箱体震荡"


def _bottom_fractal(frame: pd.DataFrame) -> dict:
    """Report only confirmed three-bar bottom fractals; it is not a buy signal."""
    if len(frame) < 3:
        return {"confirmed": False, "trade_date": None}
    candidates = []
    for idx in range(1, len(frame) - 1):
        middle = frame.iloc[idx]
        if (
            float(middle["low"]) < float(frame.iloc[idx - 1]["low"])
            and float(middle["low"]) < float(frame.iloc[idx + 1]["low"])
            and float(middle["high"]) <= max(
                float(frame.iloc[idx - 1]["high"]), float(frame.iloc[idx + 1]["high"])
            )
        ):
            candidates.append(str(middle["trade_date"]))
    latest = candidates[-1] if candidates else None
    recency = (
        int(frame.index[frame["trade_date"].astype(str) == latest][-1])
        if latest else None
    )
    return {
        "confirmed": bool(latest and recency is not None and len(frame) - 1 - recency <= 5),
        "trade_date": latest,
        "interpretation": "底分型只说明局部止跌结构；仍需收盘站回关键均线或压力区确认",
    }


def build_price_map(
    item: dict, previous_snapshot: dict | None = None,
) -> dict:
    """Create the shared auditable map used by article and evidence views."""
    frame = _normalise_bars(item["bars"])
    name = str(item.get("name") or item.get("code") or "未命名")
    structure = build_market_structure_v2(
        frame, index_name=name, previous_snapshot=previous_snapshot,
        as_of_timestamp=str(item.get("as_of_timestamp") or datetime.now().astimezone().isoformat()),
    )
    box = structure["primary_box"]
    supports = [_zone(box.get("support")), _zone(box.get("secondary_support"))]
    resistances = [_zone(box.get("resistance")), _zone(box.get("secondary_resistance"))]
    volume = _volume_context(frame)
    ma = _moving_averages(frame)
    current = float(frame.iloc[-1]["close"])
    atr = float(structure["atr14"])
    volume_box = _volume_box_context(frame, supports[0], resistances[0])
    volume["box_signal"] = volume_box["signal"]
    volume["box_gate"] = volume_box["gate"]
    volume["box_amount_ratio_20d"] = volume_box.get("amount_ratio_20d")

    def distance(zone: dict | None, direction: str) -> dict | None:
        if not zone:
            return None
        target = zone["lower"] if direction == "up" else zone["upper"]
        points = target - current
        return {
            "pct": round(points / current * 100, 2),
            "atr": round(abs(points) / atr, 2) if atr else None,
        }

    permission = structure["tradable_room"]["permission"]
    stop_zone = supports[0]
    return {
        "name": name,
        "code": item.get("code"),
        "kind": item.get("kind", "asset"),
        "trade_date": str(frame.iloc[-1]["trade_date"]),
        "current": round(current, 2),
        "price_basis": item.get("price_basis", frame.iloc[-1].get("price_basis", "unspecified")),
        "adjustment_anchor_factor": _number(frame.iloc[-1].get("adj_factor")),
        "pct_today": round(_number(frame.iloc[-1].get("pct_chg"), 0.0) or 0.0, 2),
        "pct_5d": _pct_change(frame, 5),
        "pct_20d": _pct_change(frame, 20),
        "stage": _stage(frame, structure, volume),
        "current_location": _location_label(box.get("location_ratio"), permission),
        "support_zones": supports,
        "resistance_zones": resistances,
        "upside_room": distance(resistances[0], "up"),
        "downside_room": distance(supports[0], "down"),
        "moving_average_context": ma,
        "bottom_fractal": _bottom_fractal(frame),
        "volume": volume,
        "low_absorb_condition": "进入支撑区后缩量企稳，或盘中刺破后收盘重新站回区间",
        "confirmation": (
            f"收盘站上{_zone_text(resistances[0])}上沿，且成交与上涨家数同步改善；"
            f"量能闸门：{volume_box['signal']}"
            if resistances[0] else f"等待形成可验证的上方边界；量能闸门：{volume_box['signal']}"
        ),
        "invalidation": (
            f"收盘跌破{_zone_text(stop_zone)}下沿且次日不能收回"
            if stop_zone else "下方边界数据不足，暂不给结构失效观察线"
        ),
        "held_action": _held_action(permission, volume),
        "unheld_action": _unheld_action(permission, volume),
        "revision_from_previous": structure["revision_from_previous"],
        "structure": structure,
        "data_gaps": list(item.get("data_gaps", [])),
        "volume_box": volume_box,
    }


def _held_action(permission: str, volume: dict) -> str:
    if permission == "PRICE_BREAK_BELOW_PREVIOUS_BOX":
        return "优先核对原策略退出条件，并进入持仓专项复核"
    if volume.get("latest_price_reaction") == "放量滞涨或冲高回落":
        return "核对量价风险与原策略退出条件，不按单次分歧直接清仓"
    if permission in {"WATCH_RESISTANCE_REACTION", "NO_TRADE_NARROW_RANGE"}:
        return "按原策略复核持仓，压力附近不把箱体观察当加仓依据"
    return "以支撑区作复核路标，具体持有或退出仍按原策略判断"


def _unheld_action(permission: str, volume: dict) -> str:
    if permission == "PRICE_BREAK_BELOW_PREVIOUS_BOX":
        return "先观察能否收回原支撑，不把下跌本身当买点"
    if permission == "PRICE_BREAK_ABOVE_PREVIOUS_BOX":
        return "观察突破后的回踩确认，当前箱体不构成买入授权"
    if volume.get("latest_price_reaction") == "放量滞涨或冲高回落":
        return "观察缩量回踩第一支撑，不把分歧后的反弹当买点"
    return "观察第一支撑区的承接；个股买点须另做六层核验"


def _score_rows(sectors: list[dict]) -> list[dict]:
    rows = []
    for sector in sectors:
        if all(key in sector for key in ("metric_today", "metric_5d", "metric_20d")):
            today = _number(sector.get("metric_today"), 0.0) or 0.0
            metric_5d = _number(sector.get("metric_5d"), 0.0) or 0.0
            metric_20d = _number(sector.get("metric_20d"), 0.0) or 0.0
        else:
            frame = _normalise_bars(sector["bars"])
            today = _number(frame.iloc[-1].get("pct_chg"), 0.0) or 0.0
            metric_5d = _pct_change(frame, 5) or 0.0
            metric_20d = _pct_change(frame, 20) or 0.0
        rows.append({
            **sector,
            "metric_today": round(today, 2),
            "metric_5d": round(metric_5d, 2),
            "metric_20d": round(metric_20d, 2),
        })
    return rows


def _diffusion_label(breadth: dict) -> str:
    advance = _number(breadth.get("advance_ratio"))
    market = _number(breadth.get("market_advance_ratio"))
    if advance is None:
        return "扩散数据不足"
    if market is None:
        return f"板块上涨家数占比{advance:.0f}%"
    excess = advance - market
    if excess >= 10:
        return f"全景同步扩散（较全市场高{excess:.0f}个百分点）"
    if excess >= -5:
        return f"局部扩散（较全市场{excess:+.0f}个百分点）"
    return f"前排强、扩散偏弱（较全市场低{abs(excess):.0f}个百分点）"


def select_sector_roles(sectors: list[dict], limit: int = 5) -> list[dict]:
    """Select distinct decision roles, not a single strongest-sector ranking."""
    rows = [
        row for row in _score_rows(sectors)
        if str(row.get("name")) not in NON_ACTIONABLE_SECTOR_NAMES
    ]
    chosen: list[dict] = []

    def add(row: dict | None, role: str) -> None:
        if row and all(existing.get("name") != row.get("name") for existing in chosen):
            chosen.append({**row, "brief_role": role})

    finance = [r for r in rows if any(word in str(r.get("name")) for word in FINANCE_WORDS)]
    add(max(finance, key=lambda r: r["metric_today"], default=None), "容量确认")
    add(max(rows, key=lambda r: (r["metric_20d"], r["metric_5d"]), default=None), "已有趋势")
    add(max(rows, key=lambda r: r["metric_today"], default=None), "当日轮动")
    pullbacks = [r for r in rows if r["metric_20d"] > 0 and r["metric_today"] <= 0]
    add(max(pullbacks, key=lambda r: r["metric_20d"], default=None), "等待回踩")
    add(min(rows, key=lambda r: r["metric_5d"], default=None), "弱势回避")
    for row in sorted(rows, key=lambda r: r["metric_5d"], reverse=True):
        add(row, "补充观察")
        if len(chosen) >= limit:
            break
    return chosen[:limit]


def _representative_map(sector: dict, previous: dict | None) -> dict | None:
    representatives = sector.get("representatives") or []
    if not representatives:
        return None
    stock = representatives[0]
    anchor = _number(_normalise_bars(stock["bars"]).iloc[-1].get("adj_factor"))
    comparable = (previous and previous.get("code") == stock.get("code")
                  and previous.get("price_basis") == "qfq_latest_bar"
                  and previous.get("adjustment_anchor_factor") == anchor)
    result = build_price_map(stock, previous.get("structure") if comparable else None)
    result["role"] = stock.get("role", "容量核心")
    return result


def build_brief(bundle: dict, previous: dict | None = None, sector_limit: int = 5) -> dict:
    previous = previous or {}
    market_item = bundle["market"]
    target_date = str(bundle.get("expected_trade_date") or
                      _normalise_bars(market_item["bars"]).iloc[-1]["trade_date"])
    gaps = list(bundle.get("data_gaps", []))

    def current(item: dict) -> bool:
        actual = str(_normalise_bars(item["bars"]).iloc[-1]["trade_date"])
        if actual != target_date:
            gaps.append(f"{item.get('name', item.get('code'))}数据日期{actual}与目标{target_date}不一致，已剔除")
            return False
        return True

    if not current(market_item):
        raise ValueError(gaps[-1])
    market_map = build_price_map(market_item, previous.get("market", {}).get("structure"))
    # Keep ``market`` as the backward-compatible primary index while exposing
    # parallel V2 structures for the growth indices.  Each index gets its own
    # ATR, levels, box identity and previous-snapshot lineage.
    index_items = bundle.get("market_indices") or bundle.get("indices") or []
    index_outputs = []
    previous_indices = previous.get("market_indices", {})
    if isinstance(previous_indices, list):
        previous_indices = {row.get("name"): row for row in previous_indices}
    for item in index_items:
        name = str(item.get("name") or item.get("code") or "")
        if not name or name == market_item.get("name") or not current(item):
            continue
        prior = previous_indices.get(name, {}) if isinstance(previous_indices, dict) else {}
        index_outputs.append(build_price_map(
            item, prior.get("structure") or prior.get("price_map", {}).get("structure")
        ))
    available_indices = {row["name"] for row in index_outputs}
    for name in HEADLINE_INDEX_NAMES:
        if name != market_item.get("name") and name not in available_indices:
            if not any(name in gap for gap in gaps):
                gaps.append(f"{name}指数数据缺失，无法生成箱体")
    selected = select_sector_roles([row for row in bundle.get("sectors", []) if current(row)], sector_limit)
    sector_outputs = []
    previous_sectors = {row.get("name"): row for row in previous.get("sectors", [])}
    for row in selected:
        representatives = []
        for stock in row.get("representatives", []):
            if not current(stock):
                continue
            stock_frame = _normalise_bars(stock["bars"])
            basis = stock.get("price_basis", stock_frame.iloc[-1].get("price_basis"))
            if basis != "qfq_latest_bar":
                gaps.append(f"{stock.get('name', stock.get('code'))}价格复权口径未核验，暂不生成个股买卖区间")
                continue
            representatives.append(stock)
        row = {**row, "representatives": representatives}
        prior = previous_sectors.get(row.get("name"), {})
        sector_map = build_price_map(row, prior.get("price_map", {}).get("structure"))
        representative = _representative_map(row, prior.get("representative"))
        sector_outputs.append({
            "name": row.get("name"),
            "role": row.get("brief_role"),
            "proxy": {"name": row.get("name"), "code": row.get("code")},
            "price_map": sector_map,
            "breadth": row.get("breadth"),
            "representative": representative,
            "reason_status": row.get("reason_status", "本版未接事件库；价格异动原因待核验"),
        })
    result = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(),
        "as_of_date": market_map["trade_date"],
        "status": "degraded" if gaps else "ok",
        "market": market_map,
        "market_indices": index_outputs,
        "market_context": bundle.get("market_context", {}),
        "sectors": sector_outputs,
        "data_gaps": gaps,
        "human_input_requests": bundle.get("human_input_requests", []),
        "method_boundaries": [
            "价格结构用于制定情形，不自动解释上涨原因",
            "板块角色用于决定观察顺序，不构成机械买入信号",
            "支撑压力是ATR区间；触位后仍需承接或收盘确认",
            "第一版不调用事件库，原因不明时明确留空",
        ],
    }
    result["article"] = render_article(result)
    return result


def _market_paragraph(market: dict, context: dict) -> str:
    support1, support2 = market["support_zones"]
    resistance1, resistance2 = market["resistance_zones"]
    turnover = context.get("turnover", {})
    turn_text = ""
    if turnover.get("latest_yi") is not None:
        comparison = turnover.get("vs_5d_pct")
        comparison_text = (
            f"，较5日均量{comparison:+.1f}%" if comparison is not None else "，5日均量对比缺失"
        )
        turn_text = f" 两市成交额约{turnover['latest_yi']:.0f}亿元{comparison_text}。"
    upper_text = (
        f"向上先看{_zone_text(resistance1)}，穿越后看{_zone_text(resistance2)}"
        if resistance1 else "上方暂无可靠历史压力区，先按突破后的回踩确认处理"
    )
    lower_text = (
        f"向下先看{_zone_text(support1)}，失守再看{_zone_text(support2)}"
        if support1 else "下方暂无可靠支撑区，暂不提供抄底点"
    )
    return (
        f"上证截至{market['trade_date']}收于{market['current']:.2f}点，处在{market['stage']}的{market['current_location']}。"
        f"{lower_text}；{upper_text}。盘中刺破后收回仍按原结构处理，"
        f"只有收盘越过区间并由成交额和上涨家数共同确认，才升级为有效突破或破位。{turn_text}"
        f"当前动作是：{market['unheld_action']}；已有仓位则{market['held_action']}。"
    )


def _index_paragraph(index: dict) -> str:
    """Render one headline-index box without conflating it with sector views."""
    support1, support2 = index["support_zones"]
    resistance1, resistance2 = index["resistance_zones"]
    lower = (
        f"支撑先看{_zone_text(support1)}，失守再看{_zone_text(support2)}"
        if support1 else "支撑区数据不足"
    )
    upper = (
        f"压力先看{_zone_text(resistance1)}，突破后看{_zone_text(resistance2)}"
        if resistance1 else "压力区数据不足"
    )
    volume = index.get("volume", {})
    daily = volume.get("daily", [])
    daily_text = "、".join(
        f"{row['trade_date']} {row['amount_ratio_20d']:.2f}倍"
        for row in daily[-5:] if row.get("amount_ratio_20d") is not None
    ) or "数据不足"
    return (
        f"{index['name']}收于{index['current']:.2f}点（{index['pct_today']:+.2f}%），"
        f"处在{index['stage']}的{index['current_location']}；{lower}；{upper}。"
        f"5日/20日涨跌幅为{index['pct_5d'] if index['pct_5d'] is not None else '缺失'}%/"
        f"{index['pct_20d'] if index['pct_20d'] is not None else '缺失'}%。"
        f"近5日成交额/20日均量：{daily_text}；量价箱体信号：{index.get('volume_box', {}).get('signal', '数据不足')}。"
        f"确认条件：{index['confirmation']}；失效条件：{index['invalidation']}。"
        f"当前动作：{index['unheld_action']}。"
    )


def _amount_yi(value: float | None, unit: str | None) -> str:
    if value is None:
        return "缺失"
    if unit == "千元":
        return f"{value / 100000:.1f}亿元"
    return f"{value:.1f}"


def _sector_paragraph(row: dict) -> str:
    price = row["price_map"]
    rep = row.get("representative")
    breadth = row.get("breadth") or {}
    breadth_text = _diffusion_label(breadth)
    pressure1 = price["resistance_zones"][0]
    pressure_text = (
        f"第一压力{_zone_text(pressure1)}，第二压力{_zone_text(price['resistance_zones'][1])}"
        if pressure1 else "上方暂无可追溯历史压力，不给虚拟目标价"
    )
    base = (
        f"{row['name']}承担“{row['role']}”角色，当前为{price['stage']}，{breadth_text}。"
        f"板块代理{row['proxy'].get('code') or '未提供代码'}的第一支撑观察区为"
        f"{_zone_text(price['support_zones'][0])}，第二支撑{_zone_text(price['support_zones'][1])}；"
        f"{pressure_text}。到位不等于买入，需看到缩量承接或收盘收回。"
    )
    if not rep:
        return base + f"代表股数据缺失，暂不编造个股买点；{row['reason_status']}。"
    vol = rep["volume"]
    baseline_label = "启动基准量" if vol.get("baseline_status") == "confirmed_startup_bar" else "近60日最大量代理"
    ma30 = rep.get("moving_average_context", {}).get("MA30")
    fractal = rep.get("bottom_fractal", {})
    technical_extra = ""
    if ma30 is not None and rep["current"] < ma30:
        technical_extra = f" 现价仍在30日线{ma30:.2f}下方，优先等待确认底分型或重新站回均线。"
    elif fractal.get("confirmed"):
        technical_extra = f" 最近确认底分型日期为{fractal.get('trade_date')}，但仍需突破压力区验证。"
    return (
        base
        + f"代表股{rep['name']}（{rep.get('role')}）现价{rep['current']:.2f}，支撑观察区"
        f"{_zone_text(rep['support_zones'][0])}，压力区{_zone_text(rep['resistance_zones'][0])}，"
        f"结构失效观察条件为“{rep['invalidation']}”。已持有：{rep['held_action']}；"
        f"未持有：{rep['unheld_action']}。这些区间不是代表股的独立买卖指令。"
        f"{baseline_label}{_amount_yi(vol.get('startup_baseline'), vol.get('unit'))}，4倍高潮候选"
        f"{_amount_yi(vol.get('climax_candidate'), vol.get('unit'))}，目前记录为第"
        f"{vol.get('divergence_count', 0)}次放量分歧；只有接近阈值又滞涨/冲高回落才提高退出风险。"
        f"升级条件：{price['confirmation']}；失效条件：{price['invalidation']}。{technical_extra}"
    )


def _questions(result: dict) -> list[dict]:
    rows = []
    market = result["market"]
    rows.append({
        "question": "盘中跌破第一支撑后又收回，原计划还有效吗？",
        "answer": f"有效。以收盘是否重新站回{_zone_text(market['support_zones'][0])}为准；收盘失守且次日不能收回才改判。",
    })
    for sector in result["sectors"][:2]:
        rep = sector.get("representative")
        if not rep:
            continue
        rows.append({
            "question": f"{rep['name']}已经持有，第一次分歧要全部卖吗？",
            "answer": f"不自动清仓。当前记录为第{rep['volume'].get('divergence_count', 0)}次分歧；{rep['held_action']}。收盘失守结构观察区或再次放量滞涨，应进入持仓专项复核。",
        })
        rows.append({
            "question": f"没有{rep['name']}，直接启动还能追吗？",
            "answer": f"不把容量代表股自动当候选。可观察{_zone_text(rep['support_zones'][0])}附近承接或突破后回踩，买点仍需事件、公司和六层核验。",
        })
        break
    return rows[:6]


def render_article(result: dict) -> str:
    lines = [
        f"# 市场可执行简报｜{result['as_of_date']}", "",
        "## 盘面", "", _market_paragraph(result["market"], result.get("market_context", {})), "",
    ]
    if result.get("market_indices"):
        lines.extend(["## 三大指数箱体", ""])
        for index in result["market_indices"]:
            lines.extend([f"### {index['name']}", "", _index_paragraph(index), ""])
    lines.extend([
        "## 重点方向", "",
    ])
    for idx, sector in enumerate(result["sectors"], 1):
        lines.extend([f"### {idx}. {sector['name']}｜{sector['role']}", "", _sector_paragraph(sector), ""])
    questions = _questions(result)
    lines.extend(["## 实操问答", ""])
    for item in questions:
        lines.extend([f"- **问：{item['question']}**  ", f"  答：{item['answer']}", ""])
    observations = []
    market = result["market"]
    market_edges = [
        _zone_text(zone) for zone in (market["support_zones"][0], market["resistance_zones"][0]) if zone
    ]
    observations.append(f"上证先观察{'与'.join(market_edges) if market_edges else '新边界形成'}的反应。")
    for index in result.get("market_indices", []):
        edges = [_zone_text(zone) for zone in (
            index["support_zones"][0], index["resistance_zones"][0]
        ) if zone]
        observations.append(f"{index['name']}观察{'与'.join(edges) if edges else '新边界形成'}的反应。")
    for sector in result["sectors"][:4]:
        pmap = sector["price_map"]
        observations.append(
            f"{sector['name']}：观察{_zone_text(pmap['support_zones'][0])}承接，或{_zone_text(pmap['resistance_zones'][0])}突破质量。"
        )
    lines.extend(["## 下一交易日观察", ""] + [f"- {text}" for text in observations[:8]])
    requests = result.get("human_input_requests") or []
    if requests:
        lines.extend(["", "## 可补充线索", ""] + [f"- {item['prompt']}" for item in requests[:2]])
    if result.get("data_gaps"):
        lines.extend(["", "## 数据缺口", ""] + [f"- {gap}" for gap in result["data_gaps"]])
    return "\n".join(lines).rstrip() + "\n"


def _load_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key == "TUSHARE_TOKEN" and value:
            os.environ[key] = value


def _get_pro():
    _load_env()
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN未配置；也可用--input读取离线数据包")
    import tushare as ts
    return ts.pro_api(token)


def _stock_code(value: str) -> str:
    text = str(value)
    if "." in text:
        return text
    suffix = ".SH" if text.startswith(("5", "6", "9")) else ".SZ"
    return f"{text.zfill(6)}{suffix}"


def _fetch_bars(pro, method: str, code: str, start: str, end: str) -> pd.DataFrame:
    function = getattr(pro, method)
    frame = function(ts_code=code, start_date=start, end_date=end)
    if frame is None or frame.empty:
        raise RuntimeError(f"{method}({code})无数据")
    bars = _normalise_bars(frame)
    if str(bars.iloc[-1]["trade_date"]) != str(end):
        raise RuntimeError(f"{method}({code})日期与目标{end}不一致")
    if method == "daily":
        factors = pro.adj_factor(ts_code=code, start_date=start, end_date=end)
        bars = adjust_to_latest(bars, factors)
    return bars


def _fetch_sw_bars(
    pro, code: str, start: str, end: str, expected_date: str | None = None,
) -> pd.DataFrame:
    """Fetch SW industry bars from Tushare's dedicated endpoint.

    SW2021 codes are no longer guaranteed to be included in index_daily even
    when the generic daily snapshot itself is current. Reject stale SW data so
    a previous session cannot be presented as the requested close.
    """
    frame = pro.sw_daily(ts_code=code, start_date=start, end_date=end)
    if frame is None or frame.empty:
        raise RuntimeError(f"sw_daily({code})无数据")
    bars = _normalise_bars(frame)
    if expected_date and str(bars.iloc[-1]["trade_date"]) != str(expected_date):
        raise RuntimeError(
            f"sw_daily({code})最新日期{bars.iloc[-1]['trade_date']}，"
            f"落后于目标交易日{expected_date}"
        )
    return bars


def _latest_trade_date(pro, as_of: str | None) -> str:
    now = pd.Timestamp.now(tz="Asia/Shanghai")
    end = as_of or now.strftime("%Y%m%d")
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=15)).strftime("%Y%m%d")
    calendar = pro.trade_cal(exchange="SSE", start_date=start, end_date=end)
    days = sorted(calendar.loc[calendar["is_open"] == 1, "cal_date"].astype(str).tolist())
    days = [day for day in days if day <= end]
    if end == now.strftime("%Y%m%d") and now.hour < 16:
        days = [day for day in days if day < end]
    if not days:
        raise RuntimeError("未找到开放交易日")
    return days[-1]


def _membership_parameter(level: str) -> str:
    return {"L1": "l1_code", "L2": "l2_code", "L3": "l3_code"}.get(level, "l1_code")


def _batch_industry_candidates(pro, classes: pd.DataFrame, benchmark: pd.DataFrame) -> list[dict]:
    """Discover L1 roles from three market-wide date slices, then fetch only selected histories."""
    if len(benchmark) < 21:
        raise RuntimeError("沪深300历史不足21个交易日")
    dates = [
        str(benchmark.iloc[-1]["trade_date"]),
        str(benchmark.iloc[-6]["trade_date"]),
        str(benchmark.iloc[-21]["trade_date"]),
    ]
    snapshots = {}
    for day in dates:
        frame = pro.sw_daily(trade_date=day)
        if frame is None or frame.empty:
            raise RuntimeError(f"sw_daily({day})批量截面为空")
        if "pct_change" in frame.columns and "pct_chg" not in frame.columns:
            frame = frame.rename(columns={"pct_change": "pct_chg"})
        if "trade_date" not in frame or not (frame["trade_date"].astype(str) == str(day)).all():
            raise RuntimeError(f"sw_daily({day})返回了非目标日期数据")
        snapshots[day] = frame.set_index("ts_code")
    rows = []
    for _, item in classes.iterrows():
        name, code = str(item["industry_name"]), str(item["index_code"])
        try:
            current = snapshots[dates[0]].loc[code]
            close_now = float(current["close"])
            close_5 = float(snapshots[dates[1]].loc[code]["close"])
            close_20 = float(snapshots[dates[2]].loc[code]["close"])
            rows.append({
                "name": name, "code": code, "kind": "sector", "level": "L1",
                "metric_today": round(float(current["pct_chg"]), 2),
                "metric_5d": round((close_now / close_5 - 1) * 100, 2),
                "metric_20d": round((close_now / close_20 - 1) * 100, 2),
            })
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        raise RuntimeError("批量截面没有匹配到申万一级指数")
    return rows


def fetch_live_bundle(
    as_of: str | None = None, sector_limit: int = 5,
    focus_names: list[str] | None = None,
) -> dict:
    """Fetch only market, sector and representative-stock inputs consumed by output."""
    pro = _get_pro()
    trade_date = _latest_trade_date(pro, as_of)
    end = trade_date
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=420)).strftime("%Y%m%d")
    gaps: list[str] = []
    market_bars = _fetch_bars(pro, "index_daily", INDEX_CODES["上证指数"], start, end)
    headline_indices = []
    for name in HEADLINE_INDEX_NAMES:
        if name == "上证指数":
            continue
        try:
            headline_indices.append({
                "name": name, "code": INDEX_CODES[name], "kind": "market",
                "bars": _fetch_bars(pro, "index_daily", INDEX_CODES[name], start, end),
            })
        except Exception as exc:
            gaps.append(f"{name}指数K线缺失：{exc}")
    benchmark = _fetch_bars(pro, "index_daily", INDEX_CODES["沪深300"], start, end)
    classes = pro.index_classify(level="L1", src="SW2021")
    try:
        candidates = _batch_industry_candidates(pro, classes, benchmark)
    except Exception as exc:
        gaps.append(f"行业批量截面失败，已降级逐行业取数：{exc}")
        candidates = []
        for _, item in classes.iterrows():
            name, code = str(item["industry_name"]), str(item["index_code"])
            try:
                bars = _fetch_sw_bars(pro, code, start, end, expected_date=trade_date)
                candidates.append({
                    "name": name, "code": code, "kind": "sector", "level": "L1",
                    "bars": bars,
                    "metric_today": _number(bars.iloc[-1].get("pct_chg"), 0.0) or 0.0,
                    "metric_5d": _pct_change(bars, 5) or 0.0,
                    "metric_20d": _pct_change(bars, 20) or 0.0,
                })
            except Exception as item_exc:
                gaps.append(f"{name}行业K线缺失：{item_exc}")
    selected = select_sector_roles(candidates, sector_limit)
    if focus_names:
        focused = []
        for focus in focus_names:
            row = next(
                (item for item in candidates if str(item["name"]) == focus),
                next((item for item in candidates if focus in str(item["name"])), None),
            )
            if row:
                focused.append({**row, "brief_role": "用户指定观察"})
            else:
                gaps.append(
                    f"用户指定板块“{focus}”未在申万一级行业中匹配；可用离线数据包补充细分代理"
                )
        seen = {row["name"] for row in focused}
        selected = (focused + [row for row in selected if row["name"] not in seen])[:sector_limit]

    latest_daily = pro.daily(
        trade_date=trade_date,
        fields="ts_code,trade_date,open,high,low,close,pct_chg,vol,amount",
    )
    if latest_daily is None or latest_daily.empty:
        raise RuntimeError(f"{trade_date}全市场日线未就绪，不能生成当日简报")
    if not latest_daily["trade_date"].astype(str).eq(trade_date).all():
        raise RuntimeError("全市场日线含非目标日期，不能计算广度及成交额")
    basics = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name")
    name_map = dict(zip(basics["ts_code"], basics["name"])) if basics is not None else {}
    market_advance = None
    if latest_daily is not None and not latest_daily.empty:
        market_advance = round(float((pd.to_numeric(latest_daily["pct_chg"], errors="coerce") > 0).mean() * 100), 1)
    enriched = []
    for sector in selected:
        code, level = sector["code"], sector.get("level", "L1")
        try:
            sector_bars = sector.get("bars")
            if sector_bars is None:
                sector_bars = _fetch_sw_bars(
                    pro, code, start, end, expected_date=trade_date,
                )
            members = pro.index_member_all(**{_membership_parameter(level): code})
            if "is_new" in members:
                current = members[members["is_new"].astype(str).str.upper().isin(["Y", "1"])]
            else:
                current = members
            if current.empty:
                current = members
            codes = set(current["ts_code"].astype(str))
            daily_rows = latest_daily[latest_daily["ts_code"].isin(codes)].copy()
            daily_rows["amount"] = pd.to_numeric(daily_rows["amount"], errors="coerce")
            daily_rows["pct_chg"] = pd.to_numeric(daily_rows["pct_chg"], errors="coerce")
            advance = round(float((daily_rows["pct_chg"] > 0).mean() * 100), 1) if not daily_rows.empty else None
            leader = daily_rows.sort_values(["amount", "pct_chg"], ascending=False).iloc[0]
            stock_code = str(leader["ts_code"])
            stock_bars = _fetch_bars(pro, "daily", stock_code, start, end)
            representatives = [{
                "name": name_map.get(stock_code, stock_code), "code": stock_code,
                "kind": "stock", "role": "当日容量代理（不等于产业核心）", "bars": stock_bars,
            }]
        except Exception as exc:
            advance, representatives = None, []
            gaps.append(f"{sector['name']}代表股或广度缺失：{exc}")
            sector_bars = sector.get("bars")
            if sector_bars is None:
                gaps.append(f"{sector['name']}板块价格地图无法生成，已从正文剔除")
                continue
        enriched.append({
            **sector, "bars": sector_bars,
            "breadth": {"advance_ratio": advance, "market_advance_ratio": market_advance},
            "representatives": representatives,
        })

    amount_yi = None
    turnover_vs_5d = None
    try:
        amount_yi = round(float(pd.to_numeric(latest_daily["amount"], errors="coerce").sum()) / 100000, 1)
        calendar = pro.trade_cal(
            exchange="SSE",
            start_date=(datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=15)).strftime("%Y%m%d"),
            end_date=trade_date,
        )
        recent_days = sorted(
            calendar.loc[calendar["is_open"] == 1, "cal_date"].astype(str).tolist()
        )[-6:-1]
        prior_amounts = []
        for day in recent_days:
            daily = pro.daily(trade_date=day, fields="ts_code,amount")
            if daily is not None and not daily.empty:
                prior_amounts.append(float(pd.to_numeric(daily["amount"], errors="coerce").sum()) / 100000)
        if prior_amounts:
            average = sum(prior_amounts) / len(prior_amounts)
            turnover_vs_5d = round((amount_yi / average - 1) * 100, 1) if average else None
    except Exception:
        gaps.append("全市场成交额缺失")
    return {
        "expected_trade_date": trade_date,
        "market": {"name": "上证指数", "code": INDEX_CODES["上证指数"], "kind": "market", "bars": market_bars},
        "market_indices": headline_indices,
        "sectors": enriched,
        "market_context": {
            "breadth": {"advance_ratio": market_advance},
            "turnover": {"latest_yi": amount_yi, "vs_5d_pct": turnover_vs_5d},
        },
        "data_gaps": gaps,
        "human_input_requests": [{
            "type": "event_or_role_correction",
            "prompt": (
                "若你掌握重点板块的新增政策、供需、海外冲突或公司事件，请直接补充；"
                "下一版只把它作为待核验线索，不会仅因入库而自动看多。"
            ),
        }],
    }


def _jsonable_bundle(bundle: dict) -> dict:
    def convert(value: Any) -> Any:
        if isinstance(value, pd.DataFrame):
            return value.to_dict("records")
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        if isinstance(value, list):
            return [convert(item) for item in value]
        return value
    return convert(bundle)


def _load_previous(path: Path, current_date: str | None = None) -> dict | None:
    if not path.exists():
        return None
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
        previous_date = str(previous.get("as_of_date") or "")
        # A same-day rerun is not a new observation and must not inherit its
        # own box as the previous trading day's structure.
        if current_date and (not previous_date or previous_date >= str(current_date)):
            return None
        return previous
    except Exception:
        return None


def _load_previous_brief(
    current_date: str, lineage_dir: Path = DEFAULT_OUTPUT_DIR,
) -> tuple[dict | None, Path | None]:
    """Read the latest earlier trading-day brief from an explicit lineage store.

    Output location is deliberately independent: saving a focused or replay
    report elsewhere must not change the market's prior box.
    """
    if not lineage_dir.is_dir():
        return None, None
    for path in sorted(lineage_dir.glob("market_brief_????????_??????.json"), reverse=True):
        previous = _load_previous(path, current_date=current_date)
        if previous:
            return previous, path
    return None, None


def save_result(result: dict, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    date = result["as_of_date"]
    stamp = datetime.now().strftime("%H%M%S")
    json_path = output_dir / f"market_brief_{date}_{stamp}.json"
    md_path = output_dir / f"market_brief_{date}_{stamp}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(result["article"], encoding="utf-8")
    latest = output_dir / "latest.json"
    previous_latest_date = ""
    if latest.exists():
        try:
            previous_latest_date = str(json.loads(latest.read_text(encoding="utf-8")).get("as_of_date") or "")
        except (OSError, ValueError, TypeError):
            pass
    latest_updated = date >= previous_latest_date
    if latest_updated:
        latest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path),
            "latest": str(latest), "latest_updated": latest_updated}


def main() -> None:
    parser = argparse.ArgumentParser(description="生成正文+实操问答+证据展开的一体化市场简报")
    parser.add_argument("--input", help="离线数据包JSON；省略则使用Tushare取数")
    parser.add_argument("--as-of", help="历史截止日YYYYMMDD；默认最近交易日")
    parser.add_argument("--sector-limit", type=int, default=5)
    parser.add_argument("--focus", help="优先写入的申万一级板块，多个用逗号分隔")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--lineage-dir", default=str(DEFAULT_OUTPUT_DIR),
        help="上一交易日箱体的版本库；默认正式V2目录，与输出目录无关",
    )
    parser.add_argument("--previous-snapshot", help="显式指定早于目标交易日的V2 JSON快照")
    parser.add_argument("--json", action="store_true", help="标准输出完整证据JSON，否则输出文章")
    parser.add_argument("--save", action="store_true", help="保存版本化JSON、Markdown及latest.json")
    parser.add_argument("--dump-input", help="将本次实际消费的数据包另存为JSON")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    if args.input:
        bundle = json.loads(Path(args.input).read_text(encoding="utf-8"))
    else:
        focus_names = [name.strip() for name in (args.focus or "").split(",") if name.strip()]
        bundle = fetch_live_bundle(args.as_of, args.sector_limit, focus_names)
    market_rows = _normalise_bars(bundle["market"]["bars"])
    current_date = str(market_rows.iloc[-1]["trade_date"])
    if args.previous_snapshot:
        lineage_path = Path(args.previous_snapshot)
        previous = _load_previous(lineage_path, current_date=current_date)
        if previous is None:
            parser.error("--previous-snapshot必须是有效且早于目标交易日的V2快照")
    else:
        previous, lineage_path = _load_previous_brief(
            current_date, Path(args.lineage_dir)
        )
    result = build_brief(bundle, previous=previous, sector_limit=args.sector_limit)
    result["lineage"] = {
        "previous_date": previous.get("as_of_date") if previous else None,
        "source_path": str(lineage_path.resolve()) if lineage_path else None,
    }
    if args.dump_input:
        Path(args.dump_input).write_text(
            json.dumps(_jsonable_bundle(bundle), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if args.save:
        result["saved_paths"] = save_result(result, output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result["article"])


if __name__ == "__main__":
    main()
