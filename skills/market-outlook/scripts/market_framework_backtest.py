#!/usr/bin/env python3
"""Historical contract and usefulness audit for the market-review framework.

This is an evaluation tool, not a trading strategy. It tests whether index
ranges are calibrated and whether fine-grained board discovery adds forward
information versus its same-day universe. No result is translated into a buy
signal.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


WORKSPACE_ROOT = Path(os.path.abspath(os.path.expanduser(
    os.environ.get("TZ_CODEX_HOME", "~/Desktop/tz-codex")
)))
FETCHER_PATH = (
    WORKSPACE_ROOT / "repo/skills/stock-analysis/scripts/market_state_fetcher.py"
)
INDEX_CODES = {
    "上证指数": "000001.SH", "创业板指": "399006.SZ", "科创50": "000688.SH",
}
BEHAVIOUR_TOKENS = (
    "昨日", "复牌", "连板", "首板", "涨停", "炸板", "换手",
    "振幅", "异动", "上市首",
)


def load_fetcher():
    spec = importlib.util.spec_from_file_location("market_state_fetcher", FETCHER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mean(values):
    values = [float(value) for value in values if value is not None and pd.notna(value)]
    return round(statistics.mean(values), 3) if values else None


def choose_anchors(open_days: list[str], samples: int, forward: int = 10) -> list[str]:
    eligible = open_days[:-forward]
    if samples <= 0:
        return []
    if samples == 1:
        return eligible[-1:] if eligible else []
    if len(eligible) <= samples:
        return eligible
    step = (len(eligible) - 1) / (samples - 1)
    return [eligible[round(index * step)] for index in range(samples)]


def index_range_audit(pro, fetcher, anchors: list[str], open_days: list[str]) -> dict:
    rows = []
    day_pos = {day: idx for idx, day in enumerate(open_days)}
    for anchor in anchors:
        levels = fetcher.fetch_index_technical_levels(pro, as_of_date=anchor)
        for name, code in INDEX_CODES.items():
            outlook = levels.get(name, {}).get("range_outlook", {})
            if not outlook:
                rows.append({"anchor": anchor, "index": name, "status": "missing"})
                continue
            idx = day_pos[anchor]
            future_days = open_days[idx + 1:idx + 11]
            future = pro.index_daily(
                ts_code=code, start_date=future_days[0], end_date=future_days[-1],
                fields="trade_date,high,low,close",
            ).sort_values("trade_date")
            for horizon in (5, 10):
                sample = future.head(horizon)
                support = outlook["support_zone"]
                resistance = outlook["resistance_zone"]
                realized_low = float(sample["low"].min())
                realized_high = float(sample["high"].max())
                end_close = float(sample["close"].iloc[-1])
                current = float(outlook["current"])
                rows.append({
                    "anchor": anchor, "index": name, "horizon": horizon,
                    "support_mid": support["mid"], "resistance_mid": resistance["mid"],
                    "support_breached": realized_low < float(support["lower"]),
                    "resistance_broken": realized_high > float(resistance["upper"]),
                    "contained": (
                        realized_low >= float(support["lower"])
                        and realized_high <= float(resistance["upper"])
                    ),
                    "boundary_touched": (
                        realized_low <= float(support["upper"])
                        or realized_high >= float(resistance["lower"])
                    ),
                    "forward_return_pct": round((end_close / current - 1) * 100, 2),
                    "predicted_upside_pct": outlook["upside_to_resistance_pct"],
                    "predicted_downside_pct": outlook["downside_to_support_pct"],
                })
    valid = [row for row in rows if row.get("horizon")]
    summaries = {}
    for horizon in (5, 10):
        group = [row for row in valid if row["horizon"] == horizon]
        summaries[str(horizon)] = {
            "n": len(group),
            "containment_rate_pct": round(mean([row["contained"] for row in group]) * 100, 1),
            "boundary_touch_rate_pct": round(mean([row["boundary_touched"] for row in group]) * 100, 1),
            "support_breach_rate_pct": round(mean([row["support_breached"] for row in group]) * 100, 1),
            "resistance_break_rate_pct": round(mean([row["resistance_broken"] for row in group]) * 100, 1),
        }
    by_index = {}
    for name in INDEX_CODES:
        by_index[name] = {}
        for horizon in (5, 10):
            group = [
                row for row in valid
                if row["index"] == name and row["horizon"] == horizon
            ]
            by_index[name][str(horizon)] = {
                "n": len(group),
                "containment_rate_pct": round(mean([row["contained"] for row in group]) * 100, 1),
                "boundary_touch_rate_pct": round(mean([row["boundary_touched"] for row in group]) * 100, 1),
                "support_breach_rate_pct": round(mean([row["support_breached"] for row in group]) * 100, 1),
                "resistance_break_rate_pct": round(mean([row["resistance_broken"] for row in group]) * 100, 1),
            }
    return {"summary": summaries, "by_index": by_index, "rows": rows}


def board_snapshot(pro, source: str, trade_date: str, sw_codes: set[str]) -> pd.DataFrame:
    frame = (
        pro.ths_daily(trade_date=trade_date)
        if source == "ths" else pro.sw_daily(trade_date=trade_date)
    )
    if frame is None or frame.empty:
        return pd.DataFrame()
    frame = frame.copy()
    if source == "sw":
        frame = frame[frame["ts_code"].isin(sw_codes)]
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["pct_change"] = pd.to_numeric(frame["pct_change"], errors="coerce")
    return frame.dropna(subset=["close", "pct_change"])


def board_discovery_audit(
    pro, anchors: list[str], open_days: list[str], top: int,
) -> dict:
    classes = pd.concat([
        pro.index_classify(level="L2", src="SW2021"),
        pro.index_classify(level="L3", src="SW2021"),
    ], ignore_index=True)
    sw_codes = set(classes["index_code"].astype(str))
    catalogue = pro.ths_index(exchange="A")
    catalogue = catalogue[
        catalogue["type"].astype(str).isin({"N", "I"})
        & (pd.to_numeric(catalogue["count"], errors="coerce") >= 3)
    ]
    ths_names = dict(zip(catalogue["ts_code"], catalogue["name"]))
    ths_codes = set(ths_names)
    day_pos = {day: idx for idx, day in enumerate(open_days)}
    rows = []
    for source in ("ths", "sw"):
        for anchor in anchors:
            current = board_snapshot(pro, source, anchor, sw_codes)
            if current.empty:
                rows.append({"source": source, "anchor": anchor, "status": "missing_anchor"})
                continue
            if source == "ths":
                current = current[current["ts_code"].isin(ths_codes)]
                current["name"] = current["ts_code"].map(ths_names).fillna("")
                current = current[~current["name"].map(
                    lambda name: any(token in str(name) for token in BEHAVIOUR_TOKENS)
                )]
            leaders = current.nlargest(top, "pct_change")
            leader_codes = set(leaders["ts_code"])
            idx = day_pos[anchor]
            for horizon in (1, 3, 5):
                outcome_date = open_days[idx + horizon]
                future = board_snapshot(pro, source, outcome_date, sw_codes)
                future_map = dict(zip(future["ts_code"], future["close"]))
                universe_returns = []
                leader_returns = []
                for row in current[["ts_code", "close"]].to_dict("records"):
                    end = future_map.get(row["ts_code"])
                    if end is None or not row["close"]:
                        continue
                    value = (float(end) / float(row["close"]) - 1) * 100
                    universe_returns.append(value)
                    if row["ts_code"] in leader_codes:
                        leader_returns.append(value)
                rows.append({
                    "source": source, "anchor": anchor, "horizon": horizon,
                    "n_leaders": len(leader_returns),
                    "leader_return_pct": mean(leader_returns),
                    "universe_return_pct": mean(universe_returns),
                    "excess_pct": (
                        round(mean(leader_returns) - mean(universe_returns), 3)
                        if leader_returns and universe_returns else None
                    ),
                    "leader_win_rate_pct": (
                        round(sum(value > 0 for value in leader_returns) / len(leader_returns) * 100, 1)
                        if leader_returns else None
                    ),
                })
    summaries = {}
    valid = [row for row in rows if row.get("horizon")]
    for source in ("ths", "sw"):
        summaries[source] = {}
        for horizon in (1, 3, 5):
            group = [row for row in valid if row["source"] == source and row["horizon"] == horizon]
            summaries[source][str(horizon)] = {
                "n_dates": len(group),
                "mean_excess_pct": mean([row["excess_pct"] for row in group]),
                "positive_excess_date_rate_pct": (
                    round(sum(row["excess_pct"] > 0 for row in group) / len(group) * 100, 1)
                    if group else None
                ),
                "mean_leader_win_rate_pct": mean([row["leader_win_rate_pct"] for row in group]),
            }
    return {"summary": summaries, "rows": rows}


def preheat_audit(
    pro, fetcher, anchors: list[str], open_days: list[str], samples: int,
) -> dict:
    """Small historical diagnostic; current membership creates survivorship bias."""
    if samples <= 0:
        selected = []
    elif len(anchors) > samples:
        positions = [round(i * (len(anchors) - 1) / (samples - 1)) for i in range(samples)] if samples > 1 else [len(anchors) - 1]
        selected = [anchors[pos] for pos in positions]
    else:
        selected = anchors
    sw_classes = pd.concat([
        pro.index_classify(level="L2", src="SW2021"),
        pro.index_classify(level="L3", src="SW2021"),
    ], ignore_index=True)
    sw_codes = set(sw_classes["index_code"].astype(str))
    day_pos = {day: idx for idx, day in enumerate(open_days)}
    rows = []
    for anchor in selected:
        payloads = {
            "ths": fetcher.fetch_ths_hotspot_momentum(pro, anchor, top=8),
            "sw": fetcher.fetch_sw_subindustry_momentum(pro, anchor, top=8),
        }
        for source, payload in payloads.items():
            current = board_snapshot(pro, source, anchor, sw_codes)
            close_map = dict(zip(current["ts_code"], current["close"]))
            idx = day_pos[anchor]
            for horizon in (3, 5):
                future = board_snapshot(pro, source, open_days[idx + horizon], sw_codes)
                future_map = dict(zip(future["ts_code"], future["close"]))
                for board in payload.get("boards", []):
                    code = board.get("board_code")
                    start_close = close_map.get(code)
                    end_close = future_map.get(code)
                    if start_close is None or end_close is None or not start_close:
                        continue
                    rows.append({
                        "anchor": anchor, "source": source, "horizon": horizon,
                        "board_code": code, "board_name": board.get("board_name"),
                        "preheat_state": (board.get("preheat_features") or {}).get("state"),
                        "forward_return_pct": round((float(end_close) / float(start_close) - 1) * 100, 3),
                    })
    summary = {}
    for source in ("ths", "sw"):
        summary[source] = {}
        for horizon in (3, 5):
            group = [row for row in rows if row["source"] == source and row["horizon"] == horizon]
            early = [row["forward_return_pct"] for row in group if row["preheat_state"] in {"early_improvement", "market_testing"}]
            other = [row["forward_return_pct"] for row in group if row["preheat_state"] not in {"early_improvement", "market_testing"}]
            summary[source][str(horizon)] = {
                "n_early": len(early), "n_other": len(other),
                "early_mean_return_pct": mean(early),
                "other_mean_return_pct": mean(other),
                "early_minus_other_pct": (
                    round(mean(early) - mean(other), 3) if early and other else None
                ),
            }
    return {
        "summary": summary, "rows": rows,
        "methodological_limit": (
            "历史成分使用当前is_new=Y口径，存在幸存者偏差；本结果只检查模块行为，"
            "不能作为预热信号的正式收益证明"
        ),
    }


def diagnose(index_audit: dict, board_audit: dict, preheat: dict = None) -> list[dict]:
    findings = []
    for horizon, row in index_audit["summary"].items():
        if row["boundary_touch_rate_pct"] < 25:
            findings.append({
                "severity": "warning", "module": "index_range", "horizon": horizon,
                "issue": "区间边界触达率过低，可能过宽，提示虽安全但缺乏操作解释力",
            })
        if row["containment_rate_pct"] < 65:
            findings.append({
                "severity": "warning", "module": "index_range", "horizon": horizon,
                "issue": "区间覆盖率偏低，支撑/压力容易被穿越",
            })
    for source, horizons in board_audit["summary"].items():
        for horizon, row in horizons.items():
            if row["mean_excess_pct"] is not None and row["mean_excess_pct"] <= 0:
                findings.append({
                    "severity": "boundary", "module": f"{source}_discovery", "horizon": horizon,
                    "issue": "热点榜后续平均超额不为正，只能用作发现器，不能按榜追涨",
                })
    if preheat:
        for source, horizons in preheat.get("summary", {}).items():
            for horizon, row in horizons.items():
                if min(row.get("n_early", 0), row.get("n_other", 0)) < 10:
                    findings.append({
                        "severity": "methodology", "module": f"{source}_preheat",
                        "horizon": horizon,
                        "issue": "预热与对照样本不平衡或过少，暂不能评价预测有效性",
                    })
    return findings


def main():
    parser = argparse.ArgumentParser(description="市场复盘模块历史回放与契约审计")
    parser.add_argument("--start", default="20260501")
    parser.add_argument("--end", default="20260817")
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--preheat-samples", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    fetcher = load_fetcher()
    pro = fetcher._get_pro()
    cal = pro.trade_cal(exchange="SSE", start_date=args.start, end_date=args.end)
    open_days = sorted(cal.loc[cal["is_open"] == 1, "cal_date"].astype(str).tolist())
    anchors = choose_anchors(open_days, args.samples)
    result = {
        "schema_version": "1.0",
        "purpose": "模块有效性与故障审计，不是交易策略收益回测",
        "period": {"start": args.start, "end": args.end, "anchors": anchors},
        "index_ranges": index_range_audit(pro, fetcher, anchors, open_days),
        "board_discovery": board_discovery_audit(pro, anchors, open_days, args.top),
    }
    result["preheat"] = preheat_audit(
        pro, fetcher, anchors, open_days, args.preheat_samples
    )
    result["findings"] = diagnose(
        result["index_ranges"], result["board_discovery"], result["preheat"]
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
