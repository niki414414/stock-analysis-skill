#!/usr/bin/env python3
"""A股主板“推土机”双通道短线股票池扫描器。

共同底线：近期有过涨停激活，但排除跌停、连板、末端大涨/大跳空、二波反弹、
跌破或明显偏离MA5。通过底线后分为两条路线：
1. 完全均线型：均线发散向上，K线贴MA5、像台阶一样均匀推进；
2. 活跃兼顾型：在合格上升结构中优先近期成交、振幅和量能活跃的股票。

倍量只提高观察顺序，不是必选条件，更不是次日买入信号。

均线使用后复权比例等价序列（close * adj_factor）；涨停判断使用未复权
pre_close和交易所两位小数涨停价。脚本只生成研究观察池，不生成买入指令。
"""

from __future__ import annotations

import argparse
import json
import math
import os
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from pathlib import Path

import pandas as pd


MAIN_BOARD_PREFIXES = ("000", "001", "002", "003", "600", "601", "603", "605")
OUTPUT_COLUMNS = [
    "ts_code", "name", "industry", "trade_date", "close", "signal",
    "s1_ma10_stable", "s2_ma5_rising", "e2_ma5_early", "close_above_ma10_5d",
    "close_above_ma5_5d", "ma5_rising_5d", "ma10_rising_5d",
    "consecutive_days_above_ma5", "trend_stage", "trend_stage_note",
    "predicted_ma5", "predicted_ma10", "predicted_ma20", "next_ma5_threshold",
    "auction_reference_low", "auction_reference_high", "auction_reference_mid",
    "auction_band_valid", "auction_low25", "auction_high75",
    "limit_up_count_10d", "limit_up_count_15d", "last_limit_up_date",
    "days_since_limit_up", "bias_ma5_pct", "bias_ma10_pct", "return_5d_pct",
    "post_limit_peak_gain_pct", "volume_ratio_5_20", "avg_amount_5d_qianyuan",
    "volume_ratio_prev", "volume_ratio_prev5_median", "effective_volume_trigger",
    "volume_observation",
    "daily_pct_chg", "gap_pct", "limit_down", "max_limit_up_streak_15d",
    "avg_amplitude_5d_pct", "max_abs_gap_5d_pct", "max_bias_ma5_8d_pct",
    "ma_bull_stack", "trend_linearity_8d", "second_wave_rebound",
    "full_ma_lane", "active_lane", "selection_lane", "shape_score", "activity_score",
    "sustained_strength", "continuity_tier", "continuity_reason",
    "large_bearish_day_5d", "risk_tier", "risk_flags",
    "limit_theme", "limit_up_reason", "limit_status",
]


def load_workspace_env() -> Path:
    root = Path(os.environ.get("TZ_CODEX_HOME", "~/Desktop/tz-codex")).expanduser()
    env_path = root / "repo" / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key == "TUSHARE_TOKEN" and value:
                os.environ[key] = value
    return root


def is_main_board_code(ts_code: str) -> bool:
    symbol = str(ts_code).split(".", 1)[0]
    return len(symbol) == 6 and symbol.startswith(MAIN_BOARD_PREFIXES)


def eligible_stock(row: pd.Series, as_of: str, min_list_days: int = 20) -> bool:
    name = str(row.get("name", "")).upper()
    if "ST" in name or "退" in name or not is_main_board_code(row.get("ts_code", "")):
        return False
    list_date = str(row.get("list_date", ""))
    if len(list_date) != 8:
        return False
    return (pd.Timestamp(as_of) - pd.Timestamp(list_date)).days >= min_list_days


def china_price_limit(pre_close: float, rate: float = 0.10) -> float:
    value = Decimal(str(pre_close)) * (Decimal("1") + Decimal(str(rate)))
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def next_price_tick_above(value: float, tick: str = "0.01") -> float:
    """Return the first valid positive price tick strictly above ``value``."""
    step = Decimal(tick)
    floor_tick = Decimal(str(value)).quantize(step, rounding=ROUND_FLOOR)
    return float(floor_tick + step)


def is_limit_up(close: float, pre_close: float) -> bool:
    if not all(math.isfinite(float(x)) and float(x) > 0 for x in (close, pre_close)):
        return False
    return float(close) >= china_price_limit(float(pre_close)) - 0.001


def is_limit_down(close: float, pre_close: float) -> bool:
    if not all(math.isfinite(float(x)) and float(x) > 0 for x in (close, pre_close)):
        return False
    value = Decimal(str(pre_close)) * Decimal("0.90")
    limit_price = float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return float(close) <= limit_price + 0.001


def prepare_bars(frame: pd.DataFrame) -> pd.DataFrame:
    bars = frame.copy().sort_values("trade_date").drop_duplicates("trade_date")
    numeric = ["open", "high", "low", "close", "pre_close", "vol", "amount", "adj_factor"]
    for col in numeric:
        bars[col] = pd.to_numeric(bars.get(col), errors="coerce")
    bars = bars.dropna(subset=["close", "pre_close", "adj_factor"])
    bars["adj_close"] = bars["close"] * bars["adj_factor"]
    bars["ma5"] = bars["adj_close"].rolling(5).mean()
    bars["ma10"] = bars["adj_close"].rolling(10).mean()
    bars["ma20"] = bars["adj_close"].rolling(20).mean()
    bars["daily_pct_chg"] = (bars["close"] / bars["pre_close"] - 1) * 100
    bars["gap_pct"] = (bars["open"] / bars["pre_close"] - 1) * 100
    bars["amplitude_pct"] = (bars["high"] - bars["low"]) / bars["pre_close"] * 100
    bars["bias_ma5_pct"] = (bars["adj_close"] / bars["ma5"] - 1) * 100
    bars["limit_up"] = [is_limit_up(c, p) for c, p in zip(bars["close"], bars["pre_close"])]
    bars["limit_down"] = [is_limit_down(c, p) for c, p in zip(bars["close"], bars["pre_close"])]
    return bars.reset_index(drop=True)


def max_true_streak(values: pd.Series) -> int:
    best = current = 0
    for value in values.fillna(False).astype(bool):
        current = current + 1 if value else 0
        best = max(best, current)
    return best


def linearity(values: pd.Series) -> float:
    """R² of a straight rising path; used only as a shape proxy."""
    y = pd.to_numeric(values, errors="coerce").dropna().reset_index(drop=True)
    if len(y) < 3 or float(y.std()) == 0:
        return 0.0
    x = pd.Series(range(len(y)), dtype=float)
    corr = x.corr(y.astype(float))
    return float(corr * corr) if pd.notna(corr) else 0.0


def detect_second_wave(bars: pd.DataFrame) -> bool:
    """Conservative proxy for a rebound after a material prior peak and drawdown."""
    window = bars.tail(45).reset_index(drop=True)
    if len(window) < 35:
        return False
    prior = window.iloc[:-12]
    if prior.empty:
        return False
    peak_idx = int(prior["adj_close"].idxmax())
    peak = float(window.loc[peak_idx, "adj_close"])
    after_peak = window.loc[peak_idx + 1:]
    if after_peak.empty:
        return False
    trough = float(after_peak["adj_close"].min())
    latest = float(window.iloc[-1]["adj_close"])
    drawdown = trough / peak - 1
    rebound = latest / trough - 1 if trough > 0 else 0
    recent_rising = float(window.iloc[-1]["ma5"]) > float(window.iloc[-5]["ma5"])
    near_prior_peak = peak * 0.93 <= latest <= peak * 1.03
    return bool(drawdown <= -0.12 and rebound >= 0.08 and recent_rising and near_prior_peak)


def trailing_true_count(values: pd.Series) -> int:
    """Count consecutive truthy observations ending at the latest bar."""
    count = 0
    for value in reversed(values.fillna(False).astype(bool).tolist()):
        if not value:
            break
        count += 1
    return count


def classify_trend_stage(days_above_ma5: int) -> tuple[str, str]:
    """Expose 2/5/8/16-day stages without pretending one duration is universal."""
    if days_above_ma5 >= 16:
        return "EXTENDED_16D", "强环境中的延伸趋势；不是追高许可，仍等低吸"
    if days_above_ma5 >= 8:
        return "MATURE_8D", "八日以上成熟趋势；八日只是分层，不是固定公式"
    if days_above_ma5 >= 5:
        return "STANDARD_5D", "五日标准趋势层"
    if days_above_ma5 >= 2:
        return "EARLY_2D", "两日早期试验层；仅小仓观察，等待市场与板块确认"
    return "MA10_ONLY", "仅MA10稳态，MA5短趋势尚未连续"


def analyze_stock(frame: pd.DataFrame, meta: dict) -> dict | None:
    bars = prepare_bars(frame)
    if len(bars) < 20:
        return None
    tail2, tail5, tail8, tail10, tail15 = bars.tail(2), bars.tail(5), bars.tail(8), bars.tail(10), bars.tail(15)
    close_above_ma10 = bool((tail5["adj_close"] > tail5["ma10"]).all())
    close_above_ma5 = bool((tail5["adj_close"] > tail5["ma5"]).all())
    ma5_rising = bool((tail5["ma5"].diff().dropna() >= 0).all())
    ma10_rising = bool((tail5["ma10"].diff().dropna() >= 0).all())
    s1_ma10_stable = close_above_ma10
    s2_ma5_rising = close_above_ma5 and ma5_rising
    e2_ma5_early = bool(
        (tail2["adj_close"] > tail2["ma5"]).all()
        and (tail2["ma5"].diff().dropna() >= 0).all()
    )
    limit_rows = tail15[tail15["limit_up"]]
    if limit_rows.empty:
        return None

    latest = bars.iloc[-1]
    last_limit_idx = int(limit_rows.index[-1])
    post = bars.loc[last_limit_idx:]
    limit_close_adj = float(bars.loc[last_limit_idx, "adj_close"])
    peak_gain = (float(post["adj_close"].max()) / limit_close_adj - 1) * 100
    vol20 = bars.tail(20)["vol"].mean()
    vol_ratio = bars.tail(5)["vol"].mean() / vol20 if vol20 and math.isfinite(vol20) else None
    avg_amount = bars.tail(5)["amount"].mean()
    prior_close = bars["close"].shift(1)
    daily_ret = bars["close"] / prior_close - 1
    bearish = (bars["close"] < bars["open"]) & (daily_ret <= -0.05) & (bars["vol"] > bars["vol"].rolling(20).mean() * 1.2)
    bias5 = (latest["adj_close"] / latest["ma5"] - 1) * 100
    bias10 = (latest["adj_close"] / latest["ma10"] - 1) * 100
    ret5 = (latest["adj_close"] / bars.iloc[-6]["adj_close"] - 1) * 100
    next_ma5_threshold = bars.tail(4)["adj_close"].mean() / latest["adj_factor"]
    # Original pushdozer boundary: previous close > bid > next-day dynamic-MA5
    # threshold.  Publish only actually quotable prices, so neither endpoint
    # silently violates a strict inequality.
    auction_low = next_price_tick_above(float(next_ma5_threshold))
    auction_high = round(float(latest["close"]) - 0.01, 2)
    auction_band_valid = bool(auction_low <= auction_high)
    auction_mid = (auction_low + auction_high) / 2 if auction_band_valid else None
    auction_low25 = auction_low + (auction_high - auction_low) * 0.25 if auction_band_valid else None
    auction_high75 = auction_low + (auction_high - auction_low) * 0.75 if auction_band_valid else None
    prior_vol = float(bars.iloc[-2]["vol"])
    prior5_median_vol = float(bars.iloc[-6:-1]["vol"].median())
    volume_ratio_prev = float(latest["vol"]) / prior_vol if prior_vol > 0 else None
    volume_ratio_prev5_median = float(latest["vol"]) / prior5_median_vol if prior5_median_vol > 0 else None
    day_range = float(latest["high"] - latest["low"])
    close_location = (float(latest["close"]) - float(latest["low"])) / day_range if day_range > 0 else 0.5
    effective_volume_trigger = bool(
        volume_ratio_prev is not None and volume_ratio_prev >= 1.8
        and volume_ratio_prev5_median is not None and volume_ratio_prev5_median >= 1.5
        and float(latest["close"]) > float(latest["open"])
        and close_location >= 0.60 and auction_band_valid and bias5 <= 7
    )
    if effective_volume_trigger:
        volume_observation = "有效倍量观察点；只提高次日关注度，不构成必买"
    elif volume_ratio_prev is not None and volume_ratio_prev >= 1.8:
        volume_observation = "出现倍量但量价结构未通过；检查长上影、阴线或乖离"
    else:
        volume_observation = "未出现有效倍量；不否决原趋势候选"
    days_above_ma5 = trailing_true_count(bars["adj_close"] > bars["ma5"])
    trend_stage, trend_stage_note = classify_trend_stage(days_above_ma5)

    max_lu_streak = max_true_streak(tail15["limit_up"])
    avg_amplitude5 = float(tail5["amplitude_pct"].mean())
    max_gap5 = float(tail5["gap_pct"].abs().max())
    max_bias8 = float(tail8["bias_ma5_pct"].abs().max())
    linearity8 = linearity(tail8["adj_close"])
    above_ma5_8d = int((tail8["adj_close"] > tail8["ma5"]).sum())
    ma_bull_stack = bool(latest["ma5"] > latest["ma10"] > latest["ma20"])
    second_wave = detect_second_wave(bars)

    hard_exclusions = []
    if bool(latest["limit_down"]) or float(latest["daily_pct_chg"]) <= -7:
        hard_exclusions.append("当日跌停或大幅破位")
    if max_lu_streak >= 2:
        hard_exclusions.append("近15日出现连续涨停")
    if bool((tail2["daily_pct_chg"] >= 7).any()):
        hard_exclusions.append("最近两日末端涨幅过大")
    if max_gap5 >= 4:
        hard_exclusions.append("近5日出现大跳空")
    if not bool(latest["adj_close"] > latest["ma5"]):
        hard_exclusions.append("收盘未站在MA5上")
    if bias5 > 5:
        hard_exclusions.append("收盘偏离MA5超过5%")
    if hard_exclusions:
        return None

    base_shape = bool(
        latest["ma5"] > latest["ma10"]
        and ma5_rising
        and ma10_rising
        and int((tail5["adj_close"] > tail5["ma5"]).sum()) >= 4
        and max_bias8 <= 8
    )
    liquid_enough = bool(pd.notna(avg_amount) and avg_amount >= 80000)
    full_ma_lane = bool(
        base_shape and ma_bull_stack and above_ma5_8d >= 7
        and days_above_ma5 >= 5 and linearity8 >= 0.45
        and avg_amplitude5 >= 1.5 and liquid_enough
    )
    active_lane = bool(
        base_shape and liquid_enough and avg_amplitude5 >= 2.5
        and (vol_ratio is not None and vol_ratio >= 1.0)
        and 2 <= int(len(bars) - 1 - last_limit_idx) <= 14
    )
    if not (full_ma_lane or active_lane):
        return None

    shape_score = (
        (25 if ma_bull_stack else 0) + min(days_above_ma5, 10) * 3
        + above_ma5_8d * 3 + linearity8 * 20 - max_bias8 * 2
        - (8 if second_wave else 0)
    )
    activity_score = (
        min(avg_amplitude5, 8) * 8
        + min(float(vol_ratio or 0), 3) * 12
        + min(float(avg_amount or 0) / 100000, 5) * 5
        + (12 if effective_volume_trigger else 0)
    )
    selection_lane = "双通道" if full_ma_lane and active_lane else ("完全均线型" if full_ma_lane else "活跃兼顾型")

    def predicted_flat_ma(days: int) -> float:
        # 假设次日价格等于最新收盘价时的次日动态均线。
        adjusted = bars.tail(days - 1)["adj_close"].sum() + latest["adj_close"]
        return adjusted / days / latest["adj_factor"]

    flags = []
    if second_wave:
        flags.append("局部冲高回撤后再上行，是否二波需看图复核")
    if peak_gain > 20:
        flags.append("涨停后最高涨幅>20%")
    if bool(bearish.tail(5).any()):
        flags.append("近5日放量大阴")
    if pd.notna(avg_amount) and avg_amount < 50000:
        flags.append("近5日平均成交额<5000万元")
    if s2_ma5_rising and not s1_ma10_stable:
        flags.append("仅S2，尚未形成MA10稳态")
    if e2_ma5_early and not (s1_ma10_stable or s2_ma5_rising):
        flags.append("早期2日试验层，尚未形成5日趋势")
    tier = "A_结构较好" if not flags else ("B_需等待" if len(flags) == 1 else "C_高风险")
    sustained_strength = bool(
        tier == "A_结构较好" and full_ma_lane
        and days_above_ma5 >= 5 and shape_score >= 60
    )
    if sustained_strength:
        continuity_tier = "A_持续强势"
        continuity_reason = "均线多头、至少5日沿MA5推进且形态分不低于60"
    elif tier == "A_结构较好" and days_above_ma5 >= 5 and shape_score >= 50:
        continuity_tier = "B_结构合格待确认"
        continuity_reason = "趋势结构合格，但持续性或均线完整度未达到A层"
    else:
        continuity_tier = "C_活跃观察"
        continuity_reason = "主要由活跃度或早期结构入池，不能把放量等同于持续强势"
    signal = "S1+S2" if s1_ma10_stable and s2_ma5_rising else (
        "S1_MA10_STABLE" if s1_ma10_stable else (
            "S2_MA5_RISING" if s2_ma5_rising else "E2_MA5_EARLY"
        )
    )

    return {
        "ts_code": meta.get("ts_code"), "name": meta.get("name"),
        "industry": meta.get("industry"), "trade_date": str(latest["trade_date"]),
        "close": round(float(latest["close"]), 2), "signal": signal,
        "s1_ma10_stable": s1_ma10_stable, "s2_ma5_rising": s2_ma5_rising,
        "e2_ma5_early": e2_ma5_early,
        "close_above_ma10_5d": close_above_ma10,
        "close_above_ma5_5d": close_above_ma5, "ma5_rising_5d": ma5_rising,
        "ma10_rising_5d": ma10_rising,
        "consecutive_days_above_ma5": days_above_ma5,
        "trend_stage": trend_stage, "trend_stage_note": trend_stage_note,
        "predicted_ma5": round(float(predicted_flat_ma(5)), 2),
        "predicted_ma10": round(float(predicted_flat_ma(10)), 2),
        "predicted_ma20": round(float(predicted_flat_ma(20)), 2),
        # Keep enough precision to show why the first valid 0.01 quote is
        # strictly above the theoretical (non-quotable) threshold.
        "next_ma5_threshold": round(float(next_ma5_threshold), 4),
        "auction_reference_low": round(auction_low, 2) if auction_band_valid else None,
        "auction_reference_high": round(auction_high, 2) if auction_band_valid else None,
        "auction_reference_mid": round(auction_mid, 2) if auction_mid is not None else None,
        "auction_band_valid": auction_band_valid,
        "auction_low25": round(float(auction_low25), 2) if auction_low25 is not None else None,
        "auction_high75": round(float(auction_high75), 2) if auction_high75 is not None else None,
        "limit_up_count_10d": int(tail10["limit_up"].sum()),
        "limit_up_count_15d": int(tail15["limit_up"].sum()),
        "last_limit_up_date": str(limit_rows.iloc[-1]["trade_date"]),
        "days_since_limit_up": int(len(bars) - 1 - last_limit_idx),
        "bias_ma5_pct": round(float(bias5), 2), "bias_ma10_pct": round(float(bias10), 2),
        "return_5d_pct": round(float(ret5), 2), "post_limit_peak_gain_pct": round(float(peak_gain), 2),
        "volume_ratio_5_20": round(float(vol_ratio), 2) if vol_ratio is not None else None,
        "avg_amount_5d_qianyuan": round(float(avg_amount), 2) if pd.notna(avg_amount) else None,
        "volume_ratio_prev": round(float(volume_ratio_prev), 2) if volume_ratio_prev is not None else None,
        "volume_ratio_prev5_median": round(float(volume_ratio_prev5_median), 2) if volume_ratio_prev5_median is not None else None,
        "effective_volume_trigger": effective_volume_trigger,
        "volume_observation": volume_observation,
        "daily_pct_chg": round(float(latest["daily_pct_chg"]), 2),
        "gap_pct": round(float(latest["gap_pct"]), 2),
        "limit_down": bool(latest["limit_down"]),
        "max_limit_up_streak_15d": int(max_lu_streak),
        "avg_amplitude_5d_pct": round(avg_amplitude5, 2),
        "max_abs_gap_5d_pct": round(max_gap5, 2),
        "max_bias_ma5_8d_pct": round(max_bias8, 2),
        "ma_bull_stack": ma_bull_stack,
        "trend_linearity_8d": round(linearity8, 3),
        "second_wave_rebound": second_wave,
        "full_ma_lane": full_ma_lane, "active_lane": active_lane,
        "selection_lane": selection_lane,
        "shape_score": round(float(shape_score), 2),
        "activity_score": round(float(activity_score), 2),
        "sustained_strength": sustained_strength,
        "continuity_tier": continuity_tier,
        "continuity_reason": continuity_reason,
        "large_bearish_day_5d": bool(bearish.tail(5).any()),
        "risk_tier": tier, "risk_flags": "；".join(flags),
    }


def sort_by_activity(result: pd.DataFrame) -> pd.DataFrame:
    """生成低吸观察顺序，同时保留纯资金活跃度排名。

    A层优先，随后是没有放量大阴的B层，再后是其余B/C层。所有候选仍保留，
    避免把排序偏好偷偷变成硬筛选。activity_rank保留纯资金活跃度次序。
    """
    if result.empty:
        return result
    activity_keys = ["limit_up_count_10d", "volume_ratio_5_20", "days_since_limit_up", "avg_amount_5d_qianyuan"]
    activity_ascending = [False, False, True, False]
    frame = result.sort_values(
        activity_keys, ascending=activity_ascending, na_position="last",
    ).reset_index(drop=True)
    frame["activity_rank"] = range(1, len(frame) + 1)
    if "large_bearish_day_5d" not in frame.columns:
        frame["large_bearish_day_5d"] = False
    if "risk_tier" not in frame.columns:
        frame["risk_tier"] = "A_结构较好"
    frame["observation_priority"] = frame["risk_tier"].map({
        "A_结构较好": 0, "B_需等待": 1, "C_高风险": 2,
    }).fillna(1)
    frame["continuity_priority"] = frame.get(
        "continuity_tier", pd.Series(index=frame.index, dtype=object)
    ).map({
        "A_持续强势": 0, "B_结构合格待确认": 1, "C_活跃观察": 2,
    }).fillna(2)
    return frame.sort_values(
        ["continuity_priority", "observation_priority", "large_bearish_day_5d", *activity_keys],
        ascending=[True, True, True, *activity_ascending], na_position="last",
    ).reset_index(drop=True)


def select_dual_lane(result: pd.DataFrame, per_lane: int = 5, total: int = 10) -> pd.DataFrame:
    """Take shape and activity leaders separately, merge duplicates, never force a quota."""
    if result.empty:
        return result
    shape = result[result["full_ma_lane"]].sort_values(
        ["shape_score", "activity_score"], ascending=False
    ).head(per_lane)
    active = result[result["active_lane"]].sort_values(
        ["activity_score", "shape_score"], ascending=False
    ).head(per_lane)
    selected = pd.concat([shape, active]).drop_duplicates("ts_code", keep="first")
    if len(selected) < total:
        remainder = result[~result["ts_code"].isin(selected["ts_code"])].sort_values(
            ["shape_score", "activity_score"], ascending=False
        )
        selected = pd.concat([selected, remainder.head(total - len(selected))])
    selected["continuity_priority"] = selected.get(
        "continuity_tier", pd.Series(index=selected.index, dtype=object)
    ).map({
        "A_持续强势": 0, "B_结构合格待确认": 1, "C_活跃观察": 2,
    }).fillna(2)
    return selected.sort_values(
        ["continuity_priority", "shape_score", "activity_score"],
        ascending=[True, False, False],
    ).head(total).reset_index(drop=True)


def scan_frames(bars: pd.DataFrame, stock_basic: pd.DataFrame, as_of: str) -> pd.DataFrame:
    eligible = stock_basic[stock_basic.apply(lambda row: eligible_stock(row, as_of), axis=1)]
    meta_map = eligible.set_index("ts_code").to_dict("index")
    universe = bars[bars["ts_code"].isin(meta_map)].copy()
    universe = universe[universe["trade_date"].astype(str) <= str(as_of)]
    rows = []
    stale = []
    for code, frame in universe.groupby("ts_code", sort=False):
        if str(frame["trade_date"].max()) != str(as_of):
            stale.append({"ts_code": code, "latest_date": str(frame["trade_date"].max())})
            continue
        result = analyze_stock(frame, {"ts_code": code, **meta_map[code]})
        if result:
            rows.append(result)
    if not rows:
        result = pd.DataFrame(columns=OUTPUT_COLUMNS)
    else:
        result = sort_by_activity(pd.DataFrame(rows, columns=OUTPUT_COLUMNS))
    result.attrs["excluded_stale"] = stale
    return result


def fetch_data(pro, as_of: str, lookback_days: int = 45) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    calendar = pro.trade_cal(
        exchange="SSE", start_date=(pd.Timestamp(as_of) - pd.Timedelta(days=lookback_days)).strftime("%Y%m%d"),
        end_date=as_of, fields="cal_date,is_open",
    )
    dates = sorted(calendar.loc[calendar["is_open"].eq(1), "cal_date"].astype(str))
    now = pd.Timestamp.now(tz="Asia/Shanghai")
    dates = [day for day in dates if day <= as_of]
    if as_of == now.strftime("%Y%m%d") and now.hour < 16:
        dates = [day for day in dates if day < as_of]
    if not dates:
        raise RuntimeError("指定区间没有已完成的交易日")
    frames = []
    fields = "ts_code,trade_date,open,high,low,close,pre_close,vol,amount"
    for trade_date in dates:
        daily = pro.daily(trade_date=trade_date, fields=fields)
        adj = pro.adj_factor(trade_date=trade_date, fields="ts_code,trade_date,adj_factor")
        if daily is not None and not daily.empty and adj is not None and not adj.empty:
            frames.append(daily.merge(adj, on=["ts_code", "trade_date"], how="inner"))
    if not frames:
        raise RuntimeError("指定区间没有取得日线数据")
    bars = pd.concat(frames, ignore_index=True)
    actual_as_of = str(bars["trade_date"].max())
    if actual_as_of != dates[-1]:
        raise RuntimeError(f"全市场行情过期：目标{dates[-1]}，实际{actual_as_of}")
    basics = pro.stock_basic(
        exchange="", list_status="L", fields="ts_code,symbol,name,industry,market,list_date",
    )
    return bars, basics, actual_as_of


def fetch_limit_context(pro, as_of: str, lookback_days: int = 20) -> pd.DataFrame:
    """补充最近一次涨停的题材和原因；无权限或无数据时安全降级。"""
    fields = "ts_code,trade_date,theme,lu_desc,status"
    try:
        start = (pd.Timestamp(as_of) - pd.Timedelta(days=lookback_days)).strftime("%Y%m%d")
        frame = pro.kpl_list(start_date=start, end_date=as_of, tag="涨停", fields=fields)
        if frame is None or frame.empty:
            return pd.DataFrame(columns=["ts_code", "limit_theme", "limit_up_reason", "limit_status"])
        frame = frame.sort_values("trade_date").drop_duplicates("ts_code", keep="last")
        return frame.rename(columns={
            "theme": "limit_theme", "lu_desc": "limit_up_reason", "status": "limit_status",
        })[["ts_code", "limit_theme", "limit_up_reason", "limit_status"]]
    except Exception:
        return pd.DataFrame(columns=["ts_code", "limit_theme", "limit_up_reason", "limit_status"])


def attach_limit_context(result: pd.DataFrame, context: pd.DataFrame) -> pd.DataFrame:
    frame = result.copy()
    if not context.empty:
        frame = frame.drop(
            columns=["limit_theme", "limit_up_reason", "limit_status"], errors="ignore"
        )
        frame = frame.merge(context, on="ts_code", how="left")
    for column in ("limit_theme", "limit_up_reason", "limit_status"):
        if column not in frame:
            frame[column] = ""
        frame[column] = frame[column].fillna("")
    return frame


def write_markdown(result: pd.DataFrame, path: Path, as_of: str) -> None:
    lines = [
        f"# {as_of} 推土机短线股票池", "",
        "> 双通道：完全均线型＋活跃兼顾型。共同排除跌停、连板、末端大涨/大跳空、跌破或明显偏离MA5；局部二波只提示人工复核，倍量只提高观察度，不构成买入信号。", "",
        f"共筛出 **{len(result)}** 只。", "",
        "|代码|名称|持续性分层|通道|行业|15日涨停|近5日振幅|量能比|最近涨停|竞价参考区间|MA5乖离|参考标记|", "|---|---|---|---|---|---:|---:|---:|---|---|---:|---|",
    ]
    # Markdown按资金活跃度主排序展示前60；CSV/JSON保留全部候选。
    display = result.head(60)
    for _, row in display.iterrows():
        auction_band = (
            f"{row.auction_reference_low:.2f}—{row.auction_reference_high:.2f}"
            if bool(row.auction_band_valid) else "无合规报价空间"
        )
        lines.append(
            f"|{row.ts_code}|{row['name']}|{row.continuity_tier}|{row.selection_lane}|{row.industry}|{row.limit_up_count_15d}|{row.avg_amplitude_5d_pct:.2f}%|{row.volume_ratio_5_20}|"
            f"{row.last_limit_up_date}|{auction_band}|"
            f"{row.bias_ma5_pct:.2f}%|{row.risk_flags or '无'}|"
        )
    lines += ["", "说明：不足10只时不凑数。竞价参考区间严格位于次日MA5临界价与昨收之间，仅供次日预案；9:25核对竞价结果，未成交立即发起撤单并确认回报，撤单确认前不得下第二笔。"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="A股主板推土机短线股票池")
    parser.add_argument("--as-of", help="截止日YYYYMMDD，默认最近可得交易日")
    parser.add_argument("--lookback-days", type=int, default=100)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = load_workspace_env()
    import tushare as ts
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN未设置")
    as_of = args.as_of or pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y%m%d")
    pro = ts.pro_api(token)
    bars, basics, actual_as_of = fetch_data(pro, as_of, args.lookback_days)
    scanned = scan_frames(bars, basics, actual_as_of)
    excluded_stale = scanned.attrs.get("excluded_stale", [])
    result = select_dual_lane(scanned)
    result = attach_limit_context(result, fetch_limit_context(pro, actual_as_of))
    out_dir = args.output_dir or root / "技能数据" / "运行记录" / "涨停趋势扫描"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"limit_up_trend_{actual_as_of}"
    result.to_csv(out_dir / f"{stem}.csv", index=False, encoding="utf-8-sig")
    bars[bars["ts_code"].isin(result["ts_code"])].to_csv(
        out_dir / f"{stem}_selected_history.csv", index=False, encoding="utf-8-sig"
    )
    (out_dir / f"{stem}.json").write_text(
        json.dumps(result.to_dict("records"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_markdown(result, out_dir / f"{stem}.md", actual_as_of)
    print(json.dumps({"trade_date": actual_as_of, "candidates": len(result),
                      "excluded_stale": excluded_stale,
                      "output": str(out_dir / f'{stem}.md')}, ensure_ascii=False))


if __name__ == "__main__":
    main()
