#!/usr/bin/env python3
"""Stateful-ready market structure discovery for box-analysis V2.

The module is intentionally pure: it receives bars and returns an auditable
snapshot.  It does not fetch data, mutate the production event store, or
replace the existing ``range_outlook`` calculation.  Callers may persist the
snapshot and pass it back as ``previous_snapshot`` to preserve box identity
and explain revisions.

All thresholds in this first shadow version are calibration candidates.  The
output exposes them explicitly so historical replay can change them without
silently rewriting the method.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

import pandas as pd


SCHEMA_VERSION = "2.0-shadow.1"
DEFAULT_CONFIG = {
    "pivot_span": 2,
    "history_bars": 120,
    "cluster_atr_fraction": 0.18,
    "cluster_price_fraction": 0.0012,
    "zone_atr_fraction": 0.12,
    "zone_price_fraction": 0.0008,
    "reaction_atr_fraction": 0.35,
    "narrow_room_atr": 0.80,
    "near_boundary_atr": 0.30,
}


def _float(value: Any) -> float:
    return float(value)


def _normalise_bars(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_date", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"market_structure_v2 missing columns: {sorted(missing)}")
    bars = frame.copy()
    bars["trade_date"] = bars["trade_date"].astype(str)
    for column in ("open", "high", "low", "close", "amount", "vol"):
        if column in bars.columns:
            bars[column] = pd.to_numeric(bars[column], errors="coerce")
    bars = bars.dropna(subset=["high", "low", "close"])
    bars = bars.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    return bars.reset_index(drop=True)


def _atr14(bars: pd.DataFrame) -> float:
    if len(bars) < 2:
        return max(_float(bars.iloc[-1]["close"]) * 0.01, 0.01)
    previous = bars["close"].shift(1)
    true_range = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - previous).abs(),
            (bars["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    value = float(true_range.tail(14).mean())
    return value if value > 0 else max(_float(bars.iloc[-1]["close"]) * 0.01, 0.01)


def _candidate(
    price: float, source_type: str, trade_date: str, role_hint: str,
    detail: str, weight: float = 1.0,
) -> dict:
    return {
        "price": round(float(price), 4),
        "source_type": source_type,
        "trade_date": str(trade_date),
        "role_hint": role_hint,
        "detail": detail,
        "weight": float(weight),
    }


def generate_level_candidates(
    bars: pd.DataFrame, config: dict | None = None,
) -> tuple[list[dict], float]:
    """Generate point candidates with provenance using only visible bars."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    bars = _normalise_bars(bars)
    if len(bars) < 8:
        raise ValueError("market_structure_v2 requires at least 8 bars")
    bars = bars.tail(int(cfg["history_bars"])).reset_index(drop=True)
    atr = _atr14(bars)
    span = int(cfg["pivot_span"])
    rows: list[dict] = []

    for idx in range(span, len(bars) - span):
        segment = bars.iloc[idx - span:idx + span + 1]
        row = bars.iloc[idx]
        high = _float(row["high"])
        low = _float(row["low"])
        close = _float(row["close"])
        date = row["trade_date"]
        if high >= float(segment["high"].max()) and high > float(segment.drop(index=idx)["high"].max()):
            rows.append(_candidate(high, "swing_high", date, "resistance", f"{span}+{span}日局部高点", 1.5))
        if low <= float(segment["low"].min()) and low < float(segment.drop(index=idx)["low"].min()):
            rows.append(_candidate(low, "swing_low", date, "support", f"{span}+{span}日局部低点", 1.5))
        if close >= float(segment["close"].max()) and close > float(segment.drop(index=idx)["close"].max()):
            rows.append(_candidate(close, "close_pivot_high", date, "resistance", "局部收盘高点", 0.8))
        if close <= float(segment["close"].min()) and close < float(segment.drop(index=idx)["close"].min()):
            rows.append(_candidate(close, "close_pivot_low", date, "support", "局部收盘低点", 0.8))

    latest_date = bars.iloc[-1]["trade_date"]
    for window in (5, 10, 20, 60, 120):
        if len(bars) < window:
            continue
        sample = bars.tail(window)
        high_idx = sample["high"].idxmax()
        low_idx = sample["low"].idxmin()
        rows.append(_candidate(
            bars.loc[high_idx, "high"], f"rolling_high_{window}",
            bars.loc[high_idx, "trade_date"], "resistance", f"近{window}日最高点", 0.9,
        ))
        rows.append(_candidate(
            bars.loc[low_idx, "low"], f"rolling_low_{window}",
            bars.loc[low_idx, "trade_date"], "support", f"近{window}日最低点", 0.9,
        ))

    for window in (20, 60, 120, 250):
        if len(bars) >= window:
            value = float(bars["close"].tail(window).mean())
            rows.append(_candidate(
                value, f"ma_{window}", latest_date, "dynamic",
                f"当日MA{window}（动态背景，非独立转折证据）", 0.6,
            ))

    gap_start = max(1, len(bars) - 60)
    for idx in range(gap_start, len(bars)):
        previous = bars.iloc[idx - 1]
        row = bars.iloc[idx]
        date = row["trade_date"]
        if _float(row["low"]) > _float(previous["high"]):
            rows.append(_candidate(previous["high"], "gap_up_lower_edge", date, "support", "向上缺口下沿", 1.0))
            rows.append(_candidate(row["low"], "gap_up_upper_edge", date, "support", "向上缺口上沿", 1.0))
        if _float(row["high"]) < _float(previous["low"]):
            rows.append(_candidate(row["high"], "gap_down_lower_edge", date, "resistance", "向下缺口下沿", 1.0))
            rows.append(_candidate(previous["low"], "gap_down_upper_edge", date, "resistance", "向下缺口上沿", 1.0))
    return rows, atr


def _touch_episodes(bars: pd.DataFrame, lower: float, upper: float) -> list[list[int]]:
    touched = [
        idx for idx, row in bars.iterrows()
        if _float(row["low"]) <= upper and _float(row["high"]) >= lower
    ]
    episodes: list[list[int]] = []
    for idx in touched:
        if not episodes or idx > episodes[-1][-1] + 1:
            episodes.append([idx])
        else:
            episodes[-1].append(idx)
    return episodes


def _level_id(index_name: str, center: float, provenance: list[dict], tolerance: float) -> str:
    earliest = min((row["trade_date"] for row in provenance), default="unknown")
    bucket = round(center / max(tolerance, 0.0001))
    digest = hashlib.sha1(f"{index_name}|{earliest}|{bucket}".encode()).hexdigest()[:8]
    return f"LV-{digest}"


def cluster_levels(
    bars: pd.DataFrame, candidates: list[dict], atr: float,
    index_name: str, config: dict | None = None,
) -> list[dict]:
    """Cluster nearby point candidates into auditable support/resistance zones."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    bars = _normalise_bars(bars).tail(int(cfg["history_bars"])).reset_index(drop=True)
    current = _float(bars.iloc[-1]["close"])
    tolerance = max(atr * cfg["cluster_atr_fraction"], current * cfg["cluster_price_fraction"])
    half_width = max(atr * cfg["zone_atr_fraction"], current * cfg["zone_price_fraction"])
    clusters: list[list[dict]] = []
    for item in sorted(candidates, key=lambda row: row["price"]):
        if not clusters:
            clusters.append([item])
            continue
        weighted_center = sum(x["price"] * x["weight"] for x in clusters[-1]) / sum(x["weight"] for x in clusters[-1])
        if abs(item["price"] - weighted_center) <= tolerance:
            clusters[-1].append(item)
        else:
            clusters.append([item])

    levels = []
    latest_index = len(bars) - 1
    for group in clusters:
        center = sum(x["price"] * x["weight"] for x in group) / sum(x["weight"] for x in group)
        lower, upper = center - half_width, center + half_width
        episodes = _touch_episodes(bars, lower, upper)
        touch_dates = [bars.iloc[episode[-1]]["trade_date"] for episode in episodes]
        if center < current - half_width:
            role = "support"
        elif center > current + half_width:
            role = "resistance"
        else:
            role = "decision_boundary"

        reaction_values = []
        rejection_count = 0
        for episode in episodes:
            end = episode[-1]
            future = bars.iloc[end + 1:min(end + 4, len(bars))]
            if future.empty:
                continue
            if role == "support":
                reaction = (float(future["high"].max()) - center) / atr
            elif role == "resistance":
                reaction = (center - float(future["low"].min())) / atr
            else:
                reaction = float((future["close"] - center).abs().max()) / atr
            reaction_values.append(round(reaction, 3))
            if reaction >= cfg["reaction_atr_fraction"]:
                rejection_count += 1

        upward_breaks = 0
        downward_breaks = 0
        for idx in range(1, len(bars)):
            previous = _float(bars.iloc[idx - 1]["close"])
            close = _float(bars.iloc[idx]["close"])
            upward_breaks += int(previous < lower and close > upper)
            downward_breaks += int(previous > upper and close < lower)
        role_flip_count = min(upward_breaks, downward_breaks)
        source_types = sorted({row["source_type"] for row in group})
        non_ma_sources = [source for source in source_types if not source.startswith("ma_")]
        last_touch_idx = max((episode[-1] for episode in episodes), default=0)
        recency_score = max(0.0, 3.0 - (latest_index - last_touch_idx) / 20.0)
        score = (
            min(len(episodes), 5) * 1.4
            + min(len(non_ma_sources), 4) * 0.8
            + min(rejection_count, 3) * 0.7
            + min(role_flip_count, 2) * 0.6
            + recency_score
        )
        # A moving average without price-structure confluence remains context.
        if not non_ma_sources:
            score = min(score, 2.0)
        last_bar = bars.iloc[-1]
        testing = _float(last_bar["low"]) <= upper and _float(last_bar["high"]) >= lower
        levels.append({
            "level_id": _level_id(index_name, center, group, tolerance),
            "zone": {"lower": round(lower, 2), "mid": round(center, 2), "upper": round(upper, 2)},
            "current_role": role,
            "lifecycle": "testing" if testing else "active",
            "strength_score": round(score, 2),
            "touch_count": len(episodes),
            "touch_dates": touch_dates[-8:],
            "rejection_count": rejection_count,
            "reaction_atr": reaction_values[-5:],
            "role_flip_count": role_flip_count,
            "source_types": source_types,
            "provenance": sorted(group, key=lambda row: (row["trade_date"], row["source_type"])),
        })
    return sorted(levels, key=lambda row: row["zone"]["mid"])


def _nearest_levels(levels: list[dict], current: float) -> tuple[list[dict], list[dict], list[dict]]:
    supports = sorted(
        [row for row in levels if row["current_role"] == "support"],
        key=lambda row: current - row["zone"]["upper"],
    )
    resistances = sorted(
        [row for row in levels if row["current_role"] == "resistance"],
        key=lambda row: row["zone"]["lower"] - current,
    )
    boundaries = sorted(
        [row for row in levels if row["current_role"] == "decision_boundary"],
        key=lambda row: abs(row["zone"]["mid"] - current),
    )
    return supports, resistances, boundaries


def _same_zone(left: dict | None, right: dict | None, tolerance: float) -> bool:
    if not left or not right:
        return left is right
    return abs(float(left["zone"]["mid"]) - float(right["zone"]["mid"])) <= tolerance


def build_market_structure_v2(
    frame: pd.DataFrame, index_name: str,
    as_of_timestamp: str | None = None,
    previous_snapshot: dict | None = None,
    config: dict | None = None,
) -> dict:
    """Build one transparent, version-ready market-structure snapshot."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    bars = _normalise_bars(frame)
    candidates, atr = generate_level_candidates(bars, cfg)
    levels = cluster_levels(bars, candidates, atr, index_name, cfg)
    current = _float(bars.iloc[-1]["close"])
    trade_date = bars.iloc[-1]["trade_date"]
    supports, resistances, boundaries = _nearest_levels(levels, current)
    support = supports[0] if supports else None
    resistance = resistances[0] if resistances else None
    second_support = supports[1] if len(supports) > 1 else None
    second_resistance = resistances[1] if len(resistances) > 1 else None

    up_room = ((resistance["zone"]["lower"] - current) / atr) if resistance else None
    down_room = ((current - support["zone"]["upper"]) / atr) if support else None
    box_width = (
        resistance["zone"]["mid"] - support["zone"]["mid"]
        if support and resistance else None
    )
    if support and resistance and box_width and box_width > 0:
        location_ratio = (current - support["zone"]["mid"]) / box_width
    else:
        location_ratio = None

    previous_box = (previous_snapshot or {}).get("primary_box", {})
    previous_support = previous_box.get("support")
    previous_resistance = previous_box.get("resistance")
    breakout_buffer = atr * 0.05
    broke_previous_up = bool(
        previous_resistance
        and current > float(previous_resistance["zone"]["upper"]) + breakout_buffer
    )
    broke_previous_down = bool(
        previous_support
        and current < float(previous_support["zone"]["lower"]) - breakout_buffer
    )

    if broke_previous_up:
        permission = "PRICE_BREAK_ABOVE_PREVIOUS_BOX"
        action = "收盘已向上越过上一版压力区，转看新压力；仍需成交和广度确认"
    elif broke_previous_down:
        permission = "PRICE_BREAK_BELOW_PREVIOUS_BOX"
        action = "收盘已向下越过上一版支撑区，转看次级支撑；仍需成交和广度确认"
    elif up_room is not None and down_room is not None and max(up_room, down_room) <= cfg["narrow_room_atr"]:
        permission = "NO_TRADE_NARROW_RANGE"
        action = "上下可交易空间都不足，保持不动，等边界反应"
    elif down_room is not None and down_room <= cfg["near_boundary_atr"]:
        permission = "WATCH_SUPPORT_REACTION"
        action = "接近下沿，只观察承接和收盘收回，不因触位自动买入"
    elif up_room is not None and up_room <= cfg["near_boundary_atr"]:
        permission = "WATCH_RESISTANCE_REACTION"
        action = "接近上沿，不追涨，观察突破质量或冲高回落"
    else:
        permission = "WAIT_FOR_EDGE_OR_BREAK"
        action = "位于结构中部，等待靠近边界或有效突破"

    tolerance = max(atr * cfg["cluster_atr_fraction"], current * cfg["cluster_price_fraction"])
    same_box = _same_zone(support, previous_box.get("support"), tolerance) and _same_zone(
        resistance, previous_box.get("resistance"), tolerance
    )
    inside_previous_box = bool(
        previous_support and previous_resistance
        and float(previous_support["zone"]["lower"]) <= current
        <= float(previous_resistance["zone"]["upper"])
    )
    if same_box and previous_snapshot:
        box_id = previous_box.get("box_id")
        revision_type = "unchanged_boundaries"
        revision_reason = "主支撑与主压力仍落在上一版聚类区内"
    elif inside_previous_box and not broke_previous_up and not broke_previous_down:
        box_id = previous_box.get("box_id")
        revision_type = "internal_levels_updated"
        revision_reason = "收盘仍在上一版外边界内，保留箱体身份，只更新内部路标"
    else:
        seed = f"{index_name}|{trade_date}|{support and support['level_id']}|{resistance and resistance['level_id']}"
        box_id = f"BOX-{hashlib.sha1(seed.encode()).hexdigest()[:10]}"
        if not previous_snapshot:
            revision_type = "initial"
        elif broke_previous_up:
            revision_type = "upward_break_from_previous_box"
        elif broke_previous_down:
            revision_type = "downward_break_from_previous_box"
        else:
            revision_type = "boundaries_reselected"
        revision_reason = (
            "首次生成V2结构快照" if not previous_snapshot
            else (
                "收盘向上越过上一版压力区，重建更高一层边界"
                if broke_previous_up else
                "收盘向下越过上一版支撑区，重建更低一层边界"
                if broke_previous_down else
                "新的价格反应使主边界超出上一版聚类容差"
            )
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "shadow",
        "parameter_status": "candidate_requires_cross_regime_replay",
        "index_name": index_name,
        "as_of_timestamp": as_of_timestamp or datetime.now().astimezone().isoformat(),
        "trade_date": trade_date,
        "current": round(current, 2),
        "atr14": round(atr, 2),
        "primary_box": {
            "box_id": box_id,
            "support": support,
            "resistance": resistance,
            "secondary_support": second_support,
            "secondary_resistance": second_resistance,
            "decision_boundaries": boundaries[:2],
            "width_points": round(box_width, 2) if box_width is not None else None,
            "width_atr": round(box_width / atr, 2) if box_width is not None else None,
            "location_ratio": round(location_ratio, 3) if location_ratio is not None else None,
        },
        "tradable_room": {
            "up_to_first_resistance_atr": round(up_room, 3) if up_room is not None else None,
            "down_to_first_support_atr": round(down_room, 3) if down_room is not None else None,
            "permission": permission,
            "action": action,
        },
        "revision_from_previous": {
            "type": revision_type,
            "reason": revision_reason,
            "previous_box_id": previous_box.get("box_id"),
            "price_only_transition": (
                "upward_break" if broke_previous_up else
                "downward_break" if broke_previous_down else "inside_or_unclassified"
            ),
            "confirmation_needed": (
                "成交额、市场广度和核心驱动板块——本模块只确认价格穿越"
                if broke_previous_up or broke_previous_down else None
            ),
        },
        "level_map": levels,
        "calibration_config": cfg,
        "interpretation_boundary": [
            "点位是ATR聚类区而非精确单点",
            "触碰次数不自动等于支撑增强，还需比较反应幅度和收盘",
            "均线只是动态背景，没有价格结构共振时不单独作为边界",
            "本版只产生影子结构和观察权限，不产生买卖信号",
        ],
    }
