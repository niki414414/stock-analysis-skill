"""Pure, reusable sector preheat feature calculations.

The module deliberately consumes already-fetched daily bars.  It does not
fetch data, rank securities or emit trade signals, so market review, catalyst
scanners and historical replay can share exactly the same evidence contract.
"""
from __future__ import annotations

from collections import defaultdict
from math import prod
from statistics import median
from typing import Iterable, Optional


FEATURE_VERSION = "sector_preheat_v1"


def _compound(values: Iterable[float]) -> Optional[float]:
    values = list(values)
    if not values:
        return None
    return (prod(1 + float(value) / 100 for value in values) - 1) * 100


def _round(value, digits=2):
    return round(float(value), digits) if value is not None else None


def build_sector_preheat_features(
    members: list[dict], benchmark_history: list[dict]
) -> dict:
    """Build early market-response evidence from member daily histories.

    ``members`` are the values returned by the basket price loader and must
    carry a ``history`` list.  ``benchmark_history`` contains trade_date and
    pct_chg.  All calculations are backward-looking as of the final bar.
    """
    histories = [row.get("history") or [] for row in members]
    histories = [rows for rows in histories if len(rows) >= 8]
    if not histories:
        return {
            "feature_version": FEATURE_VERSION,
            "status": "insufficient_history",
            "signal_count": 0,
            "signals": [],
        }

    by_date = defaultdict(list)
    close_by_code = {}
    for idx, rows in enumerate(histories):
        ordered = sorted(rows, key=lambda row: str(row["trade_date"]))
        close_by_code[idx] = ordered
        for row in ordered:
            by_date[str(row["trade_date"])].append(row)

    daily = []
    for date in sorted(by_date):
        rows = by_date[date]
        returns = [float(row["pct_chg"]) for row in rows]
        daily.append({
            "trade_date": date,
            "median_return": median(returns),
            "advance_ratio": sum(value > 0 for value in returns) / len(returns) * 100,
        })
    if len(daily) < 8:
        return {
            "feature_version": FEATURE_VERSION,
            "status": "insufficient_common_dates",
            "signal_count": 0,
            "signals": [],
        }

    benchmark = {
        str(row["trade_date"]): float(row["pct_chg"])
        for row in benchmark_history if row.get("pct_chg") is not None
    }
    recent3 = daily[-3:]
    prior5 = daily[-8:-3]
    recent3_daily_excess = [
        row["median_return"] - benchmark[row["trade_date"]]
        for row in recent3 if row["trade_date"] in benchmark
    ]
    prior5_daily_excess = [
        row["median_return"] - benchmark[row["trade_date"]]
        for row in prior5 if row["trade_date"] in benchmark
    ]
    recent3_excess = sum(recent3_daily_excess) if recent3_daily_excess else None
    acceleration = None
    if recent3_daily_excess and prior5_daily_excess:
        acceleration = (
            sum(recent3_daily_excess) / len(recent3_daily_excess)
            - sum(prior5_daily_excess) / len(prior5_daily_excess)
        )
    comparable = [row for row in daily[-5:] if row["trade_date"] in benchmark]
    down_days = [row for row in comparable if benchmark[row["trade_date"]] < 0]
    resilience_ratio = None
    resilience_excess = None
    if down_days:
        excesses = [
            row["median_return"] - benchmark[row["trade_date"]]
            for row in down_days
        ]
        resilience_ratio = sum(value > 0 for value in excesses) / len(excesses)
        resilience_excess = sum(excesses) / len(excesses)

    latest_breadth = daily[-1]["advance_ratio"]
    prior_breadth = sum(row["advance_ratio"] for row in daily[-4:-1]) / 3
    breadth_change = latest_breadth - prior_breadth

    above_ma5 = 0
    eligible_ma5 = 0
    leader_count = 0
    benchmark_recent3 = _compound([
        benchmark[row["trade_date"]] for row in recent3
        if row["trade_date"] in benchmark
    ]) or 0
    close_locations = []
    amount_ratios = []
    for rows in close_by_code.values():
        closes = [float(row["close"]) for row in rows]
        if len(closes) >= 5:
            eligible_ma5 += 1
            if closes[-1] >= sum(closes[-5:]) / 5:
                above_ma5 += 1
        member_recent3 = _compound([float(row["pct_chg"]) for row in rows[-3:]])
        if member_recent3 is not None and member_recent3 >= benchmark_recent3 + 1:
            leader_count += 1
        latest = rows[-1]
        high, low, close = latest.get("high"), latest.get("low"), latest.get("close")
        if high is not None and low is not None and float(high) > float(low):
            close_locations.append((float(close) - float(low)) / (float(high) - float(low)))
        ratio = latest.get("amount_ratio_20d")
        if ratio is not None:
            amount_ratios.append(float(ratio))

    above_ma5_ratio = above_ma5 / eligible_ma5 * 100 if eligible_ma5 else None
    median_close_location = median(close_locations) if close_locations else None
    median_amount_ratio = median(amount_ratios) if amount_ratios else None

    gates = {
        "relative_resilience": bool(
            resilience_ratio is not None
            and resilience_ratio >= 2 / 3
            and (resilience_excess or 0) >= 0.5
        ),
        "relative_strength_acceleration": bool(
            acceleration is not None and acceleration >= 0.6
            and recent3_excess is not None and recent3_excess > 0
        ),
        "breadth_inflection": bool(
            latest_breadth >= 35 and breadth_change >= 10
        ),
        "leader_ladder": bool(
            leader_count >= 2 and latest_breadth < 80
        ),
        "volume_price_absorption": bool(
            median_amount_ratio is not None
            and median_amount_ratio >= 1.2
            and median_close_location is not None
            and median_close_location >= 0.6
            and daily[-1]["median_return"] >= -0.5
        ),
    }
    signals = [name for name, passed in gates.items() if passed]
    if len(signals) >= 3:
        state = "market_testing"
    elif len(signals) >= 2:
        state = "early_improvement"
    else:
        state = "no_preheat"

    return {
        "feature_version": FEATURE_VERSION,
        "status": "ok",
        "state": state,
        "signal_count": len(signals),
        "signals": signals,
        "gates": gates,
        "relative_resilience_ratio": _round(resilience_ratio),
        "relative_resilience_excess_pct": _round(resilience_excess),
        "relative_strength_recent3_pct": _round(recent3_excess),
        "relative_strength_acceleration_pct_per_day": _round(acceleration),
        "breadth_today_pct": _round(latest_breadth, 1),
        "breadth_change_vs_prior3_pct": _round(breadth_change, 1),
        "above_ma5_ratio_pct": _round(above_ma5_ratio, 1),
        "leader_count": int(leader_count),
        "median_amount_ratio_20d": _round(median_amount_ratio),
        "median_close_location": _round(median_close_location),
        "limitations": [
            "日线代理不能识别真实机构订单流",
            "量价承接不等同盘中分时承接",
            "进入预热只代表优先观察，不是买入信号",
        ],
    }
