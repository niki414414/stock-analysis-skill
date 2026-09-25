#!/usr/bin/env python3
"""Render a concise decision card from the auditable V2 structure snapshot."""
from __future__ import annotations

from typing import Any


def _zone(level: dict | None) -> str:
    if not level:
        return "待识别"
    zone = level.get("zone", {})
    lower, mid, upper = zone.get("lower"), zone.get("mid"), zone.get("upper")
    if lower is None or upper is None:
        return "待识别"
    return f"{lower:.2f}—{upper:.2f}（中心{mid:.2f}）"


def _level_reason(level: dict | None) -> str:
    if not level:
        return "当前数据未形成有效区域"
    source_labels = {
        "swing_high": "局部高点", "swing_low": "局部低点",
        "close_pivot_high": "收盘高点", "close_pivot_low": "收盘低点",
        "gap_up_lower_edge": "向上缺口", "gap_up_upper_edge": "向上缺口",
        "gap_down_lower_edge": "向下缺口", "gap_down_upper_edge": "向下缺口",
    }
    labels = []
    for source in level.get("source_types", []):
        label = source_labels.get(source)
        if label is None and source.startswith("rolling_high_"):
            label = source.replace("rolling_high_", "近") + "日高点"
        elif label is None and source.startswith("rolling_low_"):
            label = source.replace("rolling_low_", "近") + "日低点"
        elif label is None and source.startswith("ma_"):
            label = source.replace("ma_", "MA")
        if label and label not in labels:
            labels.append(label)
    dates = level.get("touch_dates", [])[-3:]
    pieces = [f"{level.get('touch_count', 0)}次独立触碰"]
    if labels:
        pieces.append("来源" + "/".join(labels[:4]))
    if dates:
        pieces.append("近期触碰" + "、".join(dates))
    return "；".join(pieces)


def _state_sentence(snapshot: dict) -> str:
    revision = snapshot.get("revision_from_previous", {}).get("type")
    labels = {
        "initial": "首次建立当前价格地图",
        "unchanged_boundaries": "沿用上一版箱体，边界未变",
        "internal_levels_updated": "仍在原箱体内，只更新内部路标",
        "upward_break_from_previous_box": "价格已向上越过上一版压力，转看更高层级",
        "downward_break_from_previous_box": "价格已向下越过上一版支撑，转看更低层级",
        "boundaries_reselected": "主边界已重新选择，旧箱体不再直接沿用",
    }
    return labels.get(revision, "当前结构等待进一步确认")


def build_structure_decision_card(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return concise user output and the selected derivation, unchanged in logic."""
    if not snapshot or snapshot.get("status") == "error":
        return {
            "status": "degraded",
            "headline": "V2结构不可用，继续以V1路标为准",
            "error": (snapshot or {}).get("error"),
        }
    box = snapshot.get("primary_box", {})
    support = box.get("support")
    resistance = box.get("resistance")
    support2 = box.get("secondary_support")
    resistance2 = box.get("secondary_resistance")
    permission = snapshot.get("tradable_room", {}).get("permission")
    current = snapshot.get("current")
    state = _state_sentence(snapshot)
    action = snapshot.get("tradable_room", {}).get("action")
    headline = f"{snapshot.get('index_name')}{current:.2f}：{state}。{action}。"

    upper_trigger = resistance.get("zone", {}).get("upper") if resistance else None
    lower_trigger = support.get("zone", {}).get("lower") if support else None
    up_target = _zone(resistance2) if resistance2 else "重建更高路标"
    down_target = _zone(support2) if support2 else "重建更低路标"
    return {
        "status": "ready",
        "schema_version": "decision-card-2.0-shadow.1",
        "as_of_timestamp": snapshot.get("as_of_timestamp"),
        "trade_date": snapshot.get("trade_date"),
        "index_name": snapshot.get("index_name"),
        "headline": headline,
        "decision": {
            "permission": permission,
            "action": action,
        },
        "price_map": {
            "first_support": _zone(support),
            "second_support": _zone(support2),
            "first_resistance": _zone(resistance),
            "second_resistance": _zone(resistance2),
        },
        "scenarios": {
            "up": {
                "trigger": f"收盘穿越{upper_trigger:.2f}" if upper_trigger is not None else "待生成上方触发位",
                "then": f"先看{up_target}；成交、广度和核心板块同步后才确认真突破",
            },
            "down": {
                "trigger": f"收盘跌破{lower_trigger:.2f}" if lower_trigger is not None else "待生成下方触发位",
                "then": f"先看{down_target}；盘中刺破后收回不视为有效破位",
            },
        },
        "derivation": {
            "support": _level_reason(support),
            "resistance": _level_reason(resistance),
            "box_id": box.get("box_id"),
            "revision": snapshot.get("revision_from_previous"),
            "atr14": snapshot.get("atr14"),
            "tradable_room": snapshot.get("tradable_room"),
            "raw_structure_available": True,
        },
        "validation_status": "frozen_shadow_output_waiting_for_blogger_and_market_outcome",
    }
