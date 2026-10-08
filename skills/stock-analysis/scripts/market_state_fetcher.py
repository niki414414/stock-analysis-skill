#!/usr/bin/env python3
"""
Market State Fetcher — 大盘状态机(market-state-machine.md)配套数据拉取脚本

一次运行输出market-state-machine.md四状态判断（市场状态/主线状态/风格状态/风险偏好）
需要的全部原始数据。大部分字段复用stock_data_fetcher.py里已经跑通验证过的函数，
本脚本只新增两个此前缺失的计算：
  - 风格指数相对强弱（科创50/创业板 vs 沪深300/中证1000 vs 银行/红利）
  - 背离占比（过去N个交易日中regime_label="抱团/集中"或"权重股压制"的天数占比，
    market-state-machine.md v1.1核心指标，替代原六层Layer0"分化期"判断）

Usage:
    python3 market_state_fetcher.py --json
"""

import os
import sys
import json
import argparse
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

_CODE_ROOT = Path(__file__).resolve().parents[3]
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))
from skills.shared.paths import repo_root, workspace_root
from skills.shared.datasource import get_pro as _shared_get_pro

warnings.filterwarnings("ignore")

WORKSPACE_ROOT = str(workspace_root())
REPO_ROOT = str(repo_root())
STOCK_SKILL_DIR = os.path.join(REPO_ROOT, "skills", "stock-analysis")
EXTERNAL_BASKET_SUPPLEMENT = os.path.join(
    REPO_ROOT, "skills", "market-outlook", "config",
    "subsector_basket_supplement.csv",
)
STRUCTURE_V2_VERSION_LOG = os.path.join(
    WORKSPACE_ROOT, "技能数据", "运行记录", "市场结构V2版本.jsonl"
)
sys.path.insert(0, os.path.join(STOCK_SKILL_DIR, "references"))
from stock_data_fetcher import (  # noqa: E402
    fetch_market_breadth,
    fetch_north_bound_flow_20d,
    fetch_margin_trend,
    fetch_market_moneyflow_dc,
    _log,
)
sys.path.insert(0, os.path.join(STOCK_SKILL_DIR, "scripts"))
from event_map_query import query_status  # noqa: E402
from skills.shared.sector_preheat import build_sector_preheat_features  # noqa: E402
sys.path.insert(0, os.path.join(REPO_ROOT, "skills", "market-outlook", "scripts"))
from market_structure_v2 import build_market_structure_v2  # noqa: E402

# 2026-07-22新增：市场级复盘专用的宏观避险篮子，跟事件地图/公司池完全独立维护——
# 贵金属这类避险资金流向不是科技/新兴产业催化驱动，事件地图从建库起就没有覆盖，
# 不适合塞进mapping表硬凑，单独在这里手工维护一个很小的清单即可。
MACRO_HEDGE_BASKETS = {
    "贵金属/黄金": {
        "600547.SH": "山东黄金", "600489.SH": "中金黄金",
        "600988.SH": "赤峰黄金", "601899.SH": "紫金矿业",
        "000975.SZ": "银泰黄金", "002155.SZ": "湖南黄金",
    },
}


def _get_pro():
    return _shared_get_pro()


# "国家队/托底资金"代理指标：ETF是历史上护盘资金的标准操作载体(申购一级市场份额，
# 不直接买个股)，份额单日暴增+护盘公告后加速，是能查到的最接近的间接证据。
# 不能100%证明买方就是"国家队"（ETF申购匿名），只能说申购幅度/时点与国家队入场
# 的市场公认模式吻合，报告里必须如实说明这个局限。
NATIONAL_TEAM_PROXY_ETFS = {
    "沪深300ETF": "510300.SH",
    "上证50ETF": "510050.SH",
    "科创50ETF": "588000.SH",
    "创业板ETF": "159915.SZ",
    "中证1000ETF": "512100.SH",
    "半导体ETF": "512480.SH",
}


def fetch_national_team_proxy(pro, days: int = 10) -> dict:
    """宽基/科技类ETF份额变化——护盘资金托底的间接代理指标（不直接等同于"国家队"）。"""
    start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    out = {}
    for name, code in NATIONAL_TEAM_PROXY_ETFS.items():
        try:
            df = pro.fund_share(ts_code=code, start_date=start, end_date=end)
            if df is None or df.empty or "fd_share" not in df.columns:
                out[name] = {"source": "empty"}
                continue
            df = df.sort_values("trade_date")
            shares = df["fd_share"].tolist()
            dates = df["trade_date"].tolist()
            if len(shares) < 2:
                out[name] = {"source": "insufficient_history"}
                continue
            # 找出区间内单日最大申购跳升(份额环比增幅最大的一天)
            daily_chg_pct = [
                round((shares[i] / shares[i - 1] - 1) * 100, 2) if shares[i - 1] else None
                for i in range(1, len(shares))
            ]
            max_idx = max(range(len(daily_chg_pct)), key=lambda i: (daily_chg_pct[i] if daily_chg_pct[i] is not None else -999))
            out[name] = {
                "period_chg_pct": round((shares[-1] / shares[0] - 1) * 100, 2),
                "max_single_day_chg_pct": daily_chg_pct[max_idx],
                "max_single_day_date": dates[max_idx + 1],
                "latest_date": dates[-1],
            }
        except Exception as e:
            out[name] = {"source": f"error: {e}"}
    return out


# 风格指数：科创50/创业板(成长/科技) vs 沪深300/中证1000(宽基) vs 银行/红利(防御)
STYLE_INDICES = {
    "科创50": "000688.SH",
    "创业板指": "399006.SZ",
    "沪深300": "000300.SH",
    "中证1000": "000852.SH",
    "中证银行": "399986.SZ",
    "上证红利": "000015.SH",
}


def fetch_margin_rolling_signal(pro, window: int = 250, signal_lookback_days: int = 20) -> dict:
    """
    两融余额滚动250日(约1年)分位 + "首次突破90%"预警信号是否仍在有效窗口内。

    2026-07-21回测依据（2020-2026，20个信号样本，避免用未来数据算分位——分位
    只用trailing window，不用全样本）：滚动250日分位从<90%首次冲上90%的信号后，
    沪深300/上证/创业板20日后表现明显弱于基准(胜率25-35% vs 应有50%+)，是真实的
    短期(约1个月)预警效果；但40-60日后效果基本消失，不能当成"顶部确认"信号，
    只能理解为"近期(1个月内)脆弱性上升"。

    已知局限（回测中发现）：这个"首次突破"定义只在从<90%穿越到>=90%那一刻触发，
    如果两融余额突破90%后一路创新高、从未跌回90%以下，中间不会再产生新信号——
    2026年4-6月这波就是如此，4/16触发一次信号后一路涨到6/25历史新高(3.01万亿)
    都没再触发。用这个信号时要知道它对"持续在高位加速"这种情况有盲区。

    每次跑market-outlook都应该现算这个值，不要用之前跑过的快照数字——两融余额
    每天都在变，"是否处于预警窗口内"这个判断必须用当天最新数据重新算。
    """
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=(window + signal_lookback_days + 30) * 1.6)).strftime("%Y%m%d")
    df = pro.margin(start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        return {"source": "no_data"}
    daily = df.groupby("trade_date")["rzye"].sum().reset_index().sort_values("trade_date").reset_index(drop=True)
    daily["rzye_yi"] = daily["rzye"] / 1e8
    if len(daily) < window + signal_lookback_days:
        return {"source": "insufficient_history"}

    percentiles = [None] * len(daily)
    for i in range(window, len(daily)):
        hist = daily["rzye_yi"].iloc[i - window:i]
        percentiles[i] = round((hist < daily["rzye_yi"].iloc[i]).sum() / len(hist) * 100, 1)
    daily["pct_rolling"] = percentiles

    current_pct = daily["pct_rolling"].iloc[-1]
    # 在最近signal_lookback_days天内，是否发生过"从<90%首次穿越到>=90%"
    recent = daily.tail(signal_lookback_days + 1).reset_index(drop=True)
    fresh_cross_date = None
    for i in range(1, len(recent)):
        p_prev, p_now = recent["pct_rolling"].iloc[i - 1], recent["pct_rolling"].iloc[i]
        if p_prev is not None and p_now is not None and p_prev < 90 <= p_now:
            fresh_cross_date = str(recent["trade_date"].iloc[i])

    return {
        "latest_date": str(daily["trade_date"].iloc[-1]),
        "rolling_percentile_250d": current_pct,
        "in_warning_window": fresh_cross_date is not None,
        "fresh_cross_date": fresh_cross_date,
        "note": "分位>=90%且在有效窗口内时，历史上20日后指数表现明显弱于基准；40-60日后效果消失，不是顶部确认信号",
    }


def fetch_total_turnover(pro, days: int = 6) -> dict:
    """
    两市(沪深)合计成交额趋势——market-state-machine.md市场状态判断依据"两市成交额
    较5日均值放大/萎缩"，此前分析只手动查过上证单市场数据，是个执行漏洞（上证
    2026-07-21约1.4万亿，只占两市合计约2.97万亿的一半，单看上证会低估真实放量
    程度）。这里改成用pro.daily()按交易日汇总全市场(沪深两市)成交额。
    """
    today = datetime.now()
    cal = pro.trade_cal(exchange="SSE",
                         start_date=(today - timedelta(days=days * 3)).strftime("%Y%m%d"),
                         end_date=today.strftime("%Y%m%d"))
    open_days = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist(), reverse=True)
    if open_days and open_days[0] == today.strftime("%Y%m%d"):
        probe = pro.daily(trade_date=open_days[0], fields="ts_code")
        if probe is None or probe.empty:
            open_days = open_days[1:]
    target_days = open_days[:days]

    daily_turnover = []
    for d in target_days:
        df = pro.daily(trade_date=d, fields="ts_code,amount")
        if df is None or df.empty:
            continue
        total_yi = round(df["amount"].sum() / 1e5, 1)  # amount单位千元，/1e5换算亿元
        daily_turnover.append({"trade_date": d, "total_amount_yi": total_yi})

    if not daily_turnover:
        return {"source": "empty"}

    daily_turnover.sort(key=lambda x: x["trade_date"])
    today_amount = daily_turnover[-1]["total_amount_yi"]
    prior = [d["total_amount_yi"] for d in daily_turnover[:-1]]
    avg_prior_5d = round(sum(prior) / len(prior), 1) if prior else None
    trend = None
    if avg_prior_5d:
        chg_pct = round((today_amount / avg_prior_5d - 1) * 100, 1)
        trend = "放大" if chg_pct > 5 else ("萎缩" if chg_pct < -5 else "持平")
    else:
        chg_pct = None

    return {
        "today_amount_yi": today_amount,
        "avg_prior_days_yi": avg_prior_5d,
        "chg_vs_avg_pct": chg_pct,
        "trend": trend,
        "daily_detail": daily_turnover,
    }


# 关键指数技术位：均线+压力/支撑参考位（2026-07-21新增）
TECHNICAL_LEVEL_INDICES = {
    "上证指数": "000001.SH",
    "沪深300": "000300.SH",
    "创业板指": "399006.SZ",
    "科创50": "000688.SH",
    "中证1000": "000852.SH",
}


def _load_previous_structure_v2(
    index_name: str, latest_trade_date: str,
    path: str = STRUCTURE_V2_VERSION_LOG,
) -> dict:
    """Read the latest persisted snapshot visible at the requested date."""
    target = Path(path)
    if not target.exists():
        return None
    candidates = []
    try:
        for line in target.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("index_name") != index_name:
                continue
            if str(record.get("trade_date") or "") > str(latest_trade_date):
                continue
            snapshot = record.get("snapshot") or {}
            if snapshot.get("mode") == "shadow":
                candidates.append(record)
    except (OSError, json.JSONDecodeError):
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda row: str(row.get("as_of_timestamp") or ""))
    return candidates[-1].get("snapshot")


def fetch_index_technical_levels(pro, as_of_date: str = None) -> dict:
    """
    关键指数的均线位置 + 压力/支撑参考位。

    压力位/支撑位就取N日内的最高/最低价，不排除最近的交易日——这里跟
    stock_data_fetcher.py的high_baseline逻辑（排除近20日避免"今天刚创新高
    就把自己当压力位"的重言式）刻意不一样：high_baseline是给个股判断"有没有
    真的创新高"用的，这里是给大盘找"当前价格上下最近的关键关口"用的，如果
    排除最近几天，恰好会把这几天内发生的最新急跌低点/反弹高点排除掉——而这
    往往是当前最该看的那个点位（比如这次的7/17低点3764，就在最近5天内）。
    2026-07-21实测过一次排除最近5日的版本，跑出"支撑位比现价还高"的荒谬结果，
    已改成不排除，同时输出现价与该点位的相对位置（待突破/已突破/待考验/已跌破）。
    """
    today = (
        datetime.strptime(as_of_date, "%Y%m%d")
        if as_of_date else datetime.now()
    )
    start = (today - timedelta(days=200)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    out = {}
    for name, code in TECHNICAL_LEVEL_INDICES.items():
        try:
            df = pro.index_daily(ts_code=code, start_date=start, end_date=end)
            if df is None or df.empty:
                out[name] = {"source": "empty"}
                continue
            df = df.sort_values("trade_date")
            closes = df["close"].tolist()
            highs = df["high"].tolist() if "high" in df.columns else closes
            lows = df["low"].tolist() if "low" in df.columns else closes
            current = closes[-1]

            # ATR is used to turn single technical prices into practical
            # zones.  A support/resistance level is never an exact point.
            true_ranges = []
            for idx in range(1, len(closes)):
                true_ranges.append(max(
                    float(highs[idx]) - float(lows[idx]),
                    abs(float(highs[idx]) - float(closes[idx - 1])),
                    abs(float(lows[idx]) - float(closes[idx - 1])),
                ))
            atr14 = (
                sum(true_ranges[-14:]) / min(14, len(true_ranges))
                if true_ranges else None
            )

            ma = {}
            for n in [5, 10, 20, 60]:
                ma[f"MA{n}"] = round(sum(closes[-n:]) / n, 2) if len(closes) >= n else None

            resistance, support = {}, {}
            for w in [20, 60, 120]:
                if len(highs) >= w:
                    r = round(max(highs[-w:]), 2)
                    resistance[f"压力位_{w}日"] = {"level": r, "status": "待突破" if r > current else "已突破"}
                if len(lows) >= w:
                    s = round(min(lows[-w:]), 2)
                    support[f"支撑位_{w}日"] = {"level": s, "status": "待考验" if s < current else "已跌破"}

            dates = df["trade_date"].tolist()
            # 短窗口(近期这一两周的起点)和长窗口(更大级别的起点)分开报告，不要
            # 二选一——2026-07-21实测科创50案例：60日窗口找到的是1-4月更深的底部，
            # 掩盖了7月这次相对温和的小回调，两个都有参考价值，不能只留一个
            swing_short = _find_swing_origin(closes, dates, lookback=20)
            swing_long = _find_swing_origin(closes, dates, lookback=60)

            # Use prior extremes (excluding the latest three bars) together
            # with MA20/MA60 to estimate the nearest tradable interval.  The
            # old rolling high/low fields remain for audit compatibility.
            prior_end = max(1, len(closes) - 3)
            upper_candidates = []
            lower_candidates = []
            for w in [20, 60, 120]:
                start_idx = max(0, prior_end - w)
                if prior_end > start_idx:
                    upper_candidates.append(max(float(v) for v in highs[start_idx:prior_end]))
                    lower_candidates.append(min(float(v) for v in lows[start_idx:prior_end]))
            for value in (ma.get("MA20"), ma.get("MA60")):
                if value is not None:
                    upper_candidates.append(float(value))
                    lower_candidates.append(float(value))
            meaningful_gap = max((atr14 or 0) * 0.15, current * 0.001)
            uppers = sorted({v for v in upper_candidates if v > current + meaningful_gap})
            lowers = sorted({v for v in lower_candidates if v < current - meaningful_gap}, reverse=True)
            nearest_upper = uppers[0] if uppers else current + 2 * (atr14 or current * 0.01)
            nearest_lower = lowers[0] if lowers else current - 2 * (atr14 or current * 0.01)
            second_upper = (
                uppers[1] if len(uppers) > 1
                else nearest_upper + 2 * (atr14 or current * 0.01)
            )
            second_lower = (
                lowers[1] if len(lowers) > 1
                else nearest_lower - 2 * (atr14 or current * 0.01)
            )
            half_zone = max((atr14 or current * 0.01) * 0.25, current * 0.0015)

            # Recent realized five-session excursions explain whether a nearby
            # technical level is likely to be tested. They are context, not a
            # probability forecast or a replacement for support/resistance.
            realized_up, realized_down = [], []
            lookback_start = max(0, len(closes) - 45)
            for origin in range(lookback_start, len(closes) - 5):
                base = float(closes[origin])
                if base <= 0:
                    continue
                realized_up.append(
                    (max(float(v) for v in highs[origin + 1:origin + 6]) / base - 1) * 100
                )
                realized_down.append(
                    (min(float(v) for v in lows[origin + 1:origin + 6]) / base - 1) * 100
                )

            def excursion_summary(values):
                if not values:
                    return {"median_pct": None, "p80_abs_pct": None, "samples": 0}
                series = pd.Series(values, dtype=float)
                return {
                    "median_pct": round(float(series.median()), 2),
                    "p80_abs_pct": round(float(series.abs().quantile(0.8)), 2),
                    "samples": len(values),
                }

            def zone(level):
                return {
                    "lower": round(level - half_zone, 2),
                    "upper": round(level + half_zone, 2),
                    "mid": round(level, 2),
                }
            latest_high = float(highs[-1])
            latest_low = float(lows[-1])
            close_location = (
                (current - latest_low) / (latest_high - latest_low)
                if latest_high > latest_low else 0.5
            )
            touched_upper = latest_high >= nearest_upper - half_zone
            if touched_upper and close_location <= 0.45:
                structure = "触及压力区后回落"
            elif current >= nearest_upper + half_zone:
                structure = "已向上脱离原区间"
            elif current <= nearest_lower - half_zone:
                structure = "已向下跌破原区间"
            elif ma.get("MA20") is not None and current >= ma["MA20"]:
                structure = "区间内偏强运行"
            else:
                structure = "区间内偏弱运行"
            range_outlook = {
                "method": "两级技术路标+近期5日实际波动",
                "current": round(float(current), 2),
                "support_zone": zone(nearest_lower),
                "resistance_zone": zone(nearest_upper),
                "secondary_support_zone": zone(second_lower),
                "secondary_resistance_zone": zone(second_upper),
                "upside_to_resistance_pct": round((nearest_upper / current - 1) * 100, 2),
                "downside_to_support_pct": round((nearest_lower / current - 1) * 100, 2),
                "upside_to_secondary_resistance_pct": round((second_upper / current - 1) * 100, 2),
                "downside_to_secondary_support_pct": round((second_lower / current - 1) * 100, 2),
                "realized_5d_excursion": {
                    "upside": excursion_summary(realized_up),
                    "downside": excursion_summary(realized_down),
                    "usage": "用于判断技术路标是否容易被测试，不是未来收益概率",
                },
                "atr14": round(atr14, 2) if atr14 is not None else None,
                "close_location_in_day": round(close_location, 2),
                "structure": structure,
                "upside_confirmation": "收盘站上压力区上沿且成交额连续放大",
                "downside_confirmation": "收盘跌破支撑区下沿且市场广度同步转弱",
                "interpretation_boundary": "第一路标用于观察反应；穿越后转看第二路标，不把两者解释为保证止跌或见顶",
            }

            # V2 is shadow-only.  A malformed candidate calculation must not
            # erase the stable V1 technical output for the same index.
            structure_timestamp = datetime.now().astimezone().isoformat()
            try:
                market_structure_v2 = build_market_structure_v2(
                    df, index_name=name,
                    as_of_timestamp=structure_timestamp,
                    previous_snapshot=_load_previous_structure_v2(
                        name, str(df.iloc[-1]["trade_date"])
                    ),
                )
            except Exception as structure_error:
                market_structure_v2 = {
                    "schema_version": "2.0-shadow.1",
                    "mode": "shadow",
                    "status": "error",
                    "as_of_timestamp": structure_timestamp,
                    "error": str(structure_error),
                    "fallback": "V1 range_outlook remains authoritative",
                }
            out[name] = {
                "current": current,
                **ma,
                **resistance,
                **support,
                "swing_origin_近期(20日)": swing_short,
                "swing_origin_更大级别(60日)": swing_long,
                "range_outlook": range_outlook,
                "market_structure_v2": market_structure_v2,
            }
        except Exception as e:
            out[name] = {"source": f"error: {e}"}
    return out


def _find_swing_origin(closes, dates, lookback=60):
    """
    找"本轮涨跌起点"——不是任意窗口的最高/最低价(压力/支撑位那种)，是真正的
    转折点：最近一次趋势反转发生在哪里，反转前的那个高点/低点是多少。

    做法：先在trailing lookback天内找最低点L(反弹起点候选)；再往前找L之前
    lookback天内的最高点H(下跌起点候选)。哪个更晚发生决定"当前是从下跌转反弹"
    还是"从上涨转下跌"——用L和H的相对位置判断当前所处的阶段。
    """
    n = len(closes)
    if n < lookback + 5:
        return None
    recent = closes[-lookback:]
    low_offset = recent.index(min(recent))
    low_idx = n - lookback + low_offset
    low_price = closes[low_idx]
    low_date = dates[low_idx]

    pre_window_start = max(0, low_idx - lookback)
    pre_window = closes[pre_window_start:low_idx + 1] if low_idx > pre_window_start else closes[:low_idx + 1]
    if not pre_window:
        return None
    high_offset = pre_window.index(max(pre_window))
    high_idx = pre_window_start + high_offset
    high_price = closes[high_idx]
    high_date = dates[high_idx]

    current = closes[-1]
    if high_idx < low_idx:
        # 高点先于低点出现：这是一次"下跌起点(前高)→反弹起点(前低)"的完整回撤路径
        decline_pct = round((low_price / high_price - 1) * 100, 2)
        rebound_pct = round((current / low_price - 1) * 100, 2)
        return {
            "本轮下跌起点(前高)": {"date": high_date, "price": high_price},
            "本轮反弹起点(前低)": {"date": low_date, "price": low_price},
            "本轮跌幅": decline_pct,
            "反弹起点至今涨幅": rebound_pct,
        }
    return {
        "本轮反弹起点(前低)": {"date": low_date, "price": low_price},
        "说明": "未在窗口内找到明确的前置高点，可能仍处于单边趋势中",
    }


def fetch_style_index_comparison(pro) -> dict:
    """6个风格指数近5日/20日涨跌幅对比，用于market-state-machine.md第一节判断3'风格状态'。"""
    today = datetime.now()
    start = (today - timedelta(days=45)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    out = {}
    for name, code in STYLE_INDICES.items():
        try:
            df = pro.index_daily(ts_code=code, start_date=start, end_date=end)
            if df is None or df.empty:
                out[name] = {"pct_5d": None, "pct_20d": None, "source": "empty"}
                continue
            df = df.sort_values("trade_date")
            closes = df["close"].tolist()
            pct_today = round(float(df["pct_chg"].iloc[-1]), 2) if "pct_chg" in df.columns else None
            pct_5d = round((closes[-1] / closes[-5] - 1) * 100, 2) if len(closes) >= 5 else None
            pct_20d = round((closes[-1] / closes[-20] - 1) * 100, 2) if len(closes) >= 20 else None
            ma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else None
            above_ma20 = (closes[-1] > ma20) if ma20 is not None else None
            out[name] = {
                "pct_today": pct_today, "pct_5d": pct_5d, "pct_20d": pct_20d,
                "latest_close": closes[-1], "ma20": ma20, "above_ma20": above_ma20,
            }
        except Exception as e:
            out[name] = {"pct_5d": None, "pct_20d": None, "source": f"error: {e}"}
    return out


def fetch_divergence_ratio(pro, days: int = 10) -> dict:
    """
    背离占比：过去N个交易日中regime_label为'抱团/集中'或'权重股压制'的天数占比。
    market-state-machine.md v1.1核心指标——替代原六层Layer0"分化期"判断（那个判断
    从未被验证过，"连续N天背离"定义太严格在A股历史上极度稀有；改用滚动窗口占比后
    样本充分、效果清晰）。

    实现：循环调用stock_data_fetcher.fetch_market_breadth(as_of_date=X)，
    这个函数本身已支持指定历史交易日，不需要新写数据获取逻辑。
    """
    today = datetime.now()
    cal = pro.trade_cal(exchange="SSE",
                         start_date=(today - timedelta(days=days * 3)).strftime("%Y%m%d"),
                         end_date=today.strftime("%Y%m%d"))
    open_days = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist(), reverse=True)
    # 若今天数据还没发布(盘中/发布延迟)，跳过今天，从最近一个已收盘交易日开始——
    # 用实际探测数据是否存在来判断，不猜测收盘时间(沙盒系统时钟不等于北京时间，
    # 且A股实际15:00收盘不是16:00，"hour<16"这个猜测本身就不可靠)
    if open_days and open_days[0] == today.strftime("%Y%m%d"):
        probe = pro.daily(trade_date=open_days[0], fields="ts_code")
        if probe is None or probe.empty:
            open_days = open_days[1:]
    target_days = open_days[:days]

    daily_regimes = []
    divergence_count = 0
    for d in target_days:
        breadth = fetch_market_breadth(as_of_date=d)
        label = breadth.get("regime_label", "unknown")
        is_divergent = label.startswith("抱团/集中") or label.startswith("权重股压制")
        daily_regimes.append({"trade_date": d, "regime_label": label, "divergent": is_divergent})
        if is_divergent:
            divergence_count += 1

    ratio = round(divergence_count / len(target_days) * 100, 1) if target_days else None
    return {
        "window_days": len(target_days),
        "divergent_days": divergence_count,
        "divergence_ratio_pct": ratio,
        "daily_detail": daily_regimes,
    }


def _fetch_basket_returns(pro, codes: dict, trade_date: str) -> dict:
    """给一批{ts_code: name}算当日/5日/20日涨跌幅，逐只pro.daily()拉取（同日只拉一次，
    调用方之间可以共享结果做去重，避免同一只股票在多个篮子里被重复请求）。"""
    end_dt = datetime.strptime(trade_date, "%Y%m%d")
    start_str = (end_dt - timedelta(days=45)).strftime("%Y%m%d")
    out = {}
    for ts_code in codes:
        try:
            df = pro.daily(ts_code=ts_code, start_date=start_str, end_date=trade_date,
                            fields="trade_date,close,pct_chg,amount")
            if df is None or df.empty:
                continue
            df = df.sort_values("trade_date")
            closes = df["close"].tolist()
            pct_today = round(float(df["pct_chg"].iloc[-1]), 2)
            pct_5d = round((closes[-1] / closes[-5] - 1) * 100, 2) if len(closes) >= 5 else None
            pct_20d = round((closes[-1] / closes[-20] - 1) * 100, 2) if len(closes) >= 20 else None
            amounts = pd.to_numeric(df.get("amount"), errors="coerce").dropna()
            prior_amounts = amounts.iloc[-21:-1] if len(amounts) >= 21 else amounts.iloc[:-1]
            amount_ratio_20d = None
            if len(amounts) and len(prior_amounts) and float(prior_amounts.mean()) > 0:
                amount_ratio_20d = round(float(amounts.iloc[-1] / prior_amounts.mean()), 2)
            out[ts_code] = {
                "pct_today": pct_today, "pct_5d": pct_5d,
                "pct_20d": pct_20d, "amount_ratio_20d": amount_ratio_20d,
            }
        except Exception:
            continue
    return out


def _fetch_basket_returns_bulk(pro, codes: set[str], trade_date: str) -> dict:
    """Fetch basket history by trading day, avoiding one request per stock."""
    end_dt = datetime.strptime(trade_date, "%Y%m%d")
    start_str = (end_dt - timedelta(days=45)).strftime("%Y%m%d")
    calendar = pro.trade_cal(
        exchange="SSE", start_date=start_str, end_date=trade_date
    )
    open_days = sorted(
        calendar.loc[calendar["is_open"] == 1, "cal_date"].astype(str)
    )[-25:]
    code_set = set(codes)
    histories = {code: [] for code in code_set}
    for day in open_days:
        try:
            frame = pro.daily(
                trade_date=day,
                fields="ts_code,trade_date,open,high,low,close,pct_chg,amount",
            )
        except Exception:
            continue
        if frame is None or frame.empty:
            continue
        subset = frame[frame["ts_code"].isin(code_set)]
        for row in subset.to_dict("records"):
            histories[row["ts_code"]].append(row)
    out = {}
    for code, rows in histories.items():
        if not rows:
            continue
        frame = pd.DataFrame(rows).sort_values("trade_date")
        closes = pd.to_numeric(frame["close"], errors="coerce").tolist()
        amounts = pd.to_numeric(frame["amount"], errors="coerce").dropna()
        prior_amounts = amounts.iloc[-21:-1] if len(amounts) >= 21 else amounts.iloc[:-1]
        amount_ratio_20d = None
        if len(amounts) and len(prior_amounts) and float(prior_amounts.mean()) > 0:
            amount_ratio_20d = round(float(amounts.iloc[-1] / prior_amounts.mean()), 2)
        history = []
        prior_amount_mean = float(prior_amounts.mean()) if len(prior_amounts) else None
        for record in frame.to_dict("records"):
            amount = pd.to_numeric(record.get("amount"), errors="coerce")
            close = float(record["close"])
            history.append({
                "trade_date": str(record["trade_date"]),
                "open": float(record.get("open", close)),
                "high": float(record.get("high", close)),
                "low": float(record.get("low", close)),
                "close": close,
                "pct_chg": float(record["pct_chg"]),
                "amount_ratio_20d": (
                    round(float(amount) / prior_amount_mean, 2)
                    if pd.notna(amount) and prior_amount_mean and prior_amount_mean > 0
                    else None
                ),
            })
        out[code] = {
            "pct_today": round(float(frame["pct_chg"].iloc[-1]), 2),
            "pct_5d": (
                round((closes[-1] / closes[-5] - 1) * 100, 2)
                if len(closes) >= 5 else None
            ),
            "pct_20d": (
                round((closes[-1] / closes[-20] - 1) * 100, 2)
                if len(closes) >= 20 else None
            ),
            "amount_ratio_20d": amount_ratio_20d,
            "history": history,
        }
    return out


def load_external_basket_supplement(
    path: str = EXTERNAL_BASKET_SUPPLEMENT,
    as_of_date: str = None,
) -> tuple[list[dict], list[dict]]:
    """Load reviewed external basket rows without mutating the event map.

    Only approved, non-expired rows enter calculations. Candidate/rejected or
    malformed rows stay visible in the review queue.
    """
    if not os.path.exists(path):
        return [], []
    frame = pd.read_csv(path, dtype=str).fillna("")
    required = {
        "sector_id", "sub_sector", "stock_code", "company_name",
        "basket_role", "source_type", "source_name", "source_url",
        "inclusion_reason", "business_relevance", "verified_on",
        "review_status", "valid_until",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"外部篮子补充表缺少字段: {','.join(missing)}")
    check_date = as_of_date or datetime.now().strftime("%Y%m%d")
    approved, review = [], []
    for raw in frame.to_dict("records"):
        row = {key: str(value).strip() for key, value in raw.items()}
        digits = "".join(ch for ch in row["stock_code"] if ch.isdigit())
        if len(digits) != 6 or not row["sector_id"]:
            row["review_reason"] = "invalid_sector_or_stock_code"
            review.append(row)
            continue
        row["stock_code"] = digits
        row["ts_code"] = f"{digits}.SH" if digits.startswith("6") else f"{digits}.SZ"
        valid_until = row.get("valid_until", "").replace("-", "")
        expired = bool(valid_until and valid_until < check_date)
        if row["review_status"] == "approved" and not expired:
            approved.append(row)
        else:
            row["review_reason"] = "expired" if expired else row["review_status"]
            review.append(row)
    return approved, review


def _basket_stats(values: list[dict]) -> dict:
    if not values:
        return {"n_stocks": 0, "coverage_confidence": "missing"}
    n_stocks = len(values)
    vals_5d = [v["pct_5d"] for v in values if v.get("pct_5d") is not None]
    vals_20d = [v["pct_20d"] for v in values if v.get("pct_20d") is not None]
    amount_ratios = [
        v["amount_ratio_20d"] for v in values
        if v.get("amount_ratio_20d") is not None
    ]
    return {
        "n_stocks": n_stocks,
        "coverage_confidence": (
            "normal" if n_stocks >= 8 else
            "limited" if n_stocks >= 5 else "low"
        ),
        "pct_today": round(sum(v["pct_today"] for v in values) / n_stocks, 2),
        "pct_5d": round(sum(vals_5d) / len(vals_5d), 2) if vals_5d else None,
        "pct_20d": round(sum(vals_20d) / len(vals_20d), 2) if vals_20d else None,
        "median_today": round(float(pd.Series([
            v["pct_today"] for v in values
        ]).median()), 2),
        "median_5d": round(float(pd.Series(vals_5d).median()), 2) if vals_5d else None,
        "median_20d": round(float(pd.Series(vals_20d).median()), 2) if vals_20d else None,
        "advance_ratio_today": round(
            sum(v["pct_today"] > 0 for v in values) / n_stocks * 100, 1
        ),
        "advance_ratio_5d": (
            round(sum(v > 0 for v in vals_5d) / len(vals_5d) * 100, 1)
            if vals_5d else None
        ),
        "median_amount_ratio_20d": (
            round(float(pd.Series(amount_ratios).median()), 2)
            if amount_ratios else None
        ),
    }


def fetch_subsector_basket_momentum(pro, trade_date: str = None) -> dict:
    """Layer2：SQLite主库sectors_status的signal_stock_code篮子等权涨跌幅。

    2026-07-22新增。动机：申万31个一级行业（Layer1）颗粒度太粗——今天贵金属+6~8%
    这种动作会被"有色金属"大类（混了工业金属）稀释成+2.9%，"算力租赁/Token工厂"
    这种主题申万里根本没有独立分类。sectors_status的子赛道恰好卡在中间
    颗粒度，且signal_stock_code这一列2026-07-22已经通过sync_sectors_status()
    自动同步补齐了大部分（此前从建档起近一个月没更新），直接拿来当篮子用，
    不用新建任何东西。用pro.daily()逐只现算，不依赖任何"预算好的指数"
    （sw_daily/ths_daily这类指数接口今天已确认有1个交易日的发布延迟）。
    """
    df, _ = query_status(source="tech")
    if df is None or df.empty:
        return {"error": "SQLite主库sectors_status不可用"}

    if trade_date is None:
        cal = pro.trade_cal(exchange="SSE",
                             start_date=(datetime.now() - timedelta(days=10)).strftime("%Y%m%d"),
                             end_date=datetime.now().strftime("%Y%m%d"))
        open_days = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist(), reverse=True)
        trade_date = open_days[0] if open_days else datetime.now().strftime("%Y%m%d")
        probe = pro.daily(trade_date=trade_date, fields="ts_code")
        if (probe is None or probe.empty) and len(open_days) > 1:
            trade_date = open_days[1]

    baskets = {}
    all_codes = set()
    for _, row in df.iterrows():
        sid = str(row.get("sector_id", "")).strip()
        codes_raw = row.get("signal_stock_code")
        if not sid or pd.isna(codes_raw):
            continue
        codes = []
        for c in str(codes_raw).split(";"):
            c = c.strip()
            if len(c) == 6 and c.isdigit():
                ts_code = f"{c}.SH" if c[0] == "6" else f"{c}.SZ"
                codes.append(ts_code)
                all_codes.add(ts_code)
        if codes:
            baskets[sid] = {
                "sub_sector": row.get("sub_sector", sid),
                "event_codes": set(codes), "external_rows": [],
            }

    approved_external, review_queue = load_external_basket_supplement(
        as_of_date=trade_date
    )
    for row in approved_external:
        sid = row["sector_id"]
        basket = baskets.setdefault(sid, {
            "sub_sector": row["sub_sector"],
            "event_codes": set(), "external_rows": [],
        })
        basket["external_rows"].append(row)
        all_codes.add(row["ts_code"])

    _log(f"Layer2篮子动量: {len(baskets)}个子赛道，共{len(all_codes)}只不重复个股，"
         f"数据日期{trade_date}")
    price_data = _fetch_basket_returns_bulk(pro, all_codes, trade_date)
    benchmark_start = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=45)).strftime("%Y%m%d")
    try:
        benchmark_frame = pro.index_daily(
            ts_code="000300.SH", start_date=benchmark_start, end_date=trade_date,
            fields="ts_code,trade_date,pct_chg",
        )
        benchmark_history = (
            benchmark_frame.sort_values("trade_date").to_dict("records")
            if benchmark_frame is not None and not benchmark_frame.empty else []
        )
    except Exception:
        benchmark_history = []

    results = []
    for sid, b in baskets.items():
        event_codes = b["event_codes"]
        external_codes = {row["ts_code"] for row in b["external_rows"]}
        panorama_codes = event_codes | external_codes
        event_vals = [price_data[c] for c in event_codes if c in price_data]
        panorama_vals = [price_data[c] for c in panorama_codes if c in price_data]
        if not panorama_vals:
            continue
        event_stats = _basket_stats(event_vals)
        panorama_stats = _basket_stats(panorama_vals)
        preheat_features = build_sector_preheat_features(
            panorama_vals, benchmark_history
        )
        external_by_code = {row["ts_code"]: row for row in b["external_rows"]}
        active_response = []
        for code in panorama_codes:
            value = price_data.get(code)
            if not value:
                continue
            if (
                value["pct_today"] >= 3
                or (value.get("pct_5d") is not None and value["pct_5d"] >= 5)
                or (value.get("amount_ratio_20d") or 0) >= 1.8
            ):
                ext = external_by_code.get(code, {})
                active_response.append({
                    "ts_code": code,
                    "company_name": ext.get("company_name", ""),
                    "source_role": (
                        ext.get("basket_role") or "event_map_signal"
                    ),
                    **value,
                })
        active_response.sort(
            key=lambda row: (row["pct_today"], row.get("pct_5d") or -999),
            reverse=True,
        )
        results.append({
            "sector_id": sid,
            "sub_sector": b["sub_sector"],
            # Compatibility fields use the panorama basket from now on.
            **panorama_stats,
            "event_core": event_stats,
            "market_panorama": panorama_stats,
            "preheat_features": preheat_features,
            "input_counts": {
                "event_map": len(event_codes),
                "external_approved": len(external_codes),
                "external_pending_total": sum(
                    row.get("sector_id") == sid for row in review_queue
                ),
            },
            "external_sources": sorted({
                row["source_name"] for row in b["external_rows"]
            }),
            "active_response": active_response[:10],
        })

    results.sort(key=lambda x: x["pct_today"], reverse=True)
    return {
        "trade_date": trade_date, "n_baskets": len(results),
        "input_model": "event_map_plus_reviewed_external",
        "external_registry": EXTERNAL_BASKET_SUPPLEMENT,
        "external_approved_count": len(approved_external),
        "external_review_queue": review_queue,
        "baskets": results,
    }


def _latest_open_trade_date(pro, requested: str = None) -> str:
    if requested:
        return requested
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d")
    cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end)
    days = sorted(
        cal.loc[cal["is_open"] == 1, "cal_date"].astype(str).tolist(),
        reverse=True,
    )
    return days[0] if days else end


def _latest_available_board_frame(pro, api_name: str, requested: str):
    """Use the newest published board date, which can lag the trading calendar."""
    start = (datetime.strptime(requested, "%Y%m%d") - timedelta(days=14)).strftime("%Y%m%d")
    cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=requested)
    candidates = sorted(
        cal.loc[cal["is_open"] == 1, "cal_date"].astype(str).tolist(),
        reverse=True,
    )
    api = getattr(pro, api_name)
    for candidate in candidates:
        frame = api(trade_date=candidate)
        if frame is not None and not frame.empty:
            return frame, candidate
    return pd.DataFrame(), requested


def _safe_number(value, default: float = 0.0) -> float:
    number = pd.to_numeric(value, errors="coerce")
    return default if pd.isna(number) else float(number)


def _member_preheat_for_hotspots(pro, rows: list[dict], trade_date: str, limit: int = 8) -> None:
    """Attach the existing reusable preheat contract to the leading boards."""
    memberships = {}
    all_codes = set()
    for row in rows[:limit]:
        try:
            if row["source"] == "ths":
                members = pro.ths_member(ts_code=row["board_code"])
                column = "con_code"
            else:
                parameter = f"{row['classification_level'].lower()}_code"
                members = pro.index_member_all(**{parameter: row["board_code"], "is_new": "Y"})
                column = "ts_code"
        except Exception as exc:
            row["member_error"] = str(exc)
            continue
        if members is None or members.empty or column not in members:
            row["member_error"] = "empty"
            continue
        codes = {
            str(code) for code in members[column]
            if isinstance(code, str) and code.endswith((".SH", ".SZ"))
        }
        memberships[row["board_code"]] = codes
        all_codes.update(codes)
    if not all_codes:
        return
    price_data = _fetch_basket_returns_bulk(pro, all_codes, trade_date)
    benchmark_start = (
        datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=45)
    ).strftime("%Y%m%d")
    benchmark_frame = pro.index_daily(
        ts_code="000300.SH", start_date=benchmark_start, end_date=trade_date,
        fields="ts_code,trade_date,pct_chg",
    )
    benchmark_history = (
        benchmark_frame.sort_values("trade_date").to_dict("records")
        if benchmark_frame is not None and not benchmark_frame.empty else []
    )
    for row in rows[:limit]:
        values = [
            price_data[code] for code in memberships.get(row["board_code"], set())
            if code in price_data
        ]
        row["member_stats"] = _basket_stats(values)
        row["preheat_features"] = build_sector_preheat_features(
            values, benchmark_history
        )
        # Reuse the equal-weight constituent history as the medium-horizon
        # measure. This avoids one rate-limited history request per board.
        if row.get("pct_5d") is None:
            row["pct_5d"] = row["member_stats"].get("pct_5d")
        if row.get("pct_20d") is None:
            row["pct_20d"] = row["member_stats"].get("pct_20d")
        row["medium_horizon_basis"] = "成分股等权均值"


def fetch_ths_hotspot_momentum(pro, trade_date: str = None, top: int = 25) -> dict:
    """Discover market themes from THS; this is market heat, not business purity."""
    requested = _latest_open_trade_date(pro, trade_date)
    catalogue = pro.ths_index(exchange="A")
    latest, published_date = _latest_available_board_frame(
        pro, "ths_daily", requested
    )
    if catalogue is None or catalogue.empty or latest is None or latest.empty:
        return {"status": "unavailable", "trade_date": requested, "boards": []}
    latest["trade_date"] = latest["trade_date"].astype(str)
    actual = latest["trade_date"].max() if "trade_date" in latest else published_date
    latest = latest[latest["trade_date"] == actual].copy()
    meta = catalogue[["ts_code", "name", "type", "count"]].drop_duplicates("ts_code")
    latest = latest.merge(meta, on="ts_code", how="inner")
    latest = latest[
        latest["type"].astype(str).isin({"N", "I"})
        & (pd.to_numeric(latest["count"], errors="coerce") >= 3)
    ]
    # Trading-behaviour boards are useful for sentiment, but they are not
    # sector rotation candidates and would crowd out actual industries/themes.
    behaviour_tokens = (
        "昨日", "复牌", "连板", "首板", "涨停", "炸板", "换手",
        "振幅", "异动", "上市首",
    )
    latest = latest[
        ~latest["name"].astype(str).map(
            lambda name: any(token in name for token in behaviour_tokens)
        )
    ]
    latest["pct_change"] = pd.to_numeric(latest["pct_change"], errors="coerce")
    latest = latest.dropna(subset=["pct_change"]).sort_values("pct_change", ascending=False)
    rows = []
    for _, item in latest.head(top).iterrows():
        code = str(item["ts_code"])
        high = float(item["high"])
        low = float(item["low"])
        close = float(item["close"])
        rows.append({
            "source": "ths", "board_code": code,
            "board_name": str(item["name"]), "board_type": str(item["type"]),
            "constituent_count": int(_safe_number(item.get("count"))),
            "trade_date": actual, "pct_today": round(float(item["pct_change"]), 2),
            "pct_5d": None,
            "pct_20d": None,
            "turnover_rate": round(_safe_number(item.get("turnover_rate")), 2),
            "close_location": round((close - low) / (high - low), 2) if high > low else 0.5,
            "interpretation_boundary": "同花顺概念只代表市场热度，不代表主营纯度",
        })
    _member_preheat_for_hotspots(pro, rows, actual)
    return {"status": "ok", "trade_date": actual, "boards": rows}


def fetch_sw_subindustry_momentum(pro, trade_date: str = None, top: int = 25) -> dict:
    """Add SW L2/L3 as a stable, finer-grained rotation discovery layer."""
    requested = _latest_open_trade_date(pro, trade_date)
    latest, published_date = _latest_available_board_frame(
        pro, "sw_daily", requested
    )
    if latest is None or latest.empty:
        return {"status": "unavailable", "trade_date": requested, "boards": []}
    latest["trade_date"] = latest["trade_date"].astype(str)
    actual = latest["trade_date"].max() if "trade_date" in latest else published_date
    latest = latest[latest["trade_date"] == actual].copy()
    classes = []
    for level in ("L2", "L3"):
        frame = pro.index_classify(level=level, src="SW2021")
        if frame is None or frame.empty:
            continue
        frame = frame[["index_code", "industry_name"]].copy()
        frame["classification_level"] = level
        classes.append(frame)
    if not classes:
        return {"status": "unavailable", "trade_date": actual, "boards": []}
    class_frame = pd.concat(classes, ignore_index=True).drop_duplicates("index_code")
    latest = latest.merge(class_frame, left_on="ts_code", right_on="index_code", how="inner")
    latest["pct_change"] = pd.to_numeric(latest["pct_change"], errors="coerce")
    latest = latest.dropna(subset=["pct_change"]).sort_values("pct_change", ascending=False)
    rows = []
    for _, item in latest.head(top).iterrows():
        code = str(item["ts_code"])
        high = float(item["high"])
        low = float(item["low"])
        close = float(item["close"])
        rows.append({
            "source": "sw_subindustry", "board_code": code,
            "board_name": str(item["industry_name"]),
            "classification_level": str(item["classification_level"]),
            "trade_date": actual, "pct_today": round(float(item["pct_change"]), 2),
            "pct_5d": None,
            "pct_20d": None,
            "amount": round(_safe_number(item.get("amount")), 2),
            "close_location": round((close - low) / (high - low), 2) if high > low else 0.5,
        })
    _member_preheat_for_hotspots(pro, rows, actual)
    return {"status": "ok", "trade_date": actual, "boards": rows}


def fetch_macro_hedge_baskets(pro, trade_date: str = None) -> dict:
    """Layer3：事件地图覆盖范围外的宏观避险篮子（目前只有贵金属/黄金）。

    2026-07-22新增。跟Layer2用同一套等权计算逻辑，篮子清单见MACRO_HEDGE_BASKETS，
    手工维护，不接事件地图/公司池同步（这类资金流向本来就不是产业催化驱动的）。
    """
    if trade_date is None:
        cal = pro.trade_cal(exchange="SSE",
                             start_date=(datetime.now() - timedelta(days=10)).strftime("%Y%m%d"),
                             end_date=datetime.now().strftime("%Y%m%d"))
        open_days = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist(), reverse=True)
        trade_date = open_days[0] if open_days else datetime.now().strftime("%Y%m%d")
        probe = pro.daily(trade_date=trade_date, fields="ts_code")
        if (probe is None or probe.empty) and len(open_days) > 1:
            trade_date = open_days[1]

    all_codes = set()
    for basket in MACRO_HEDGE_BASKETS.values():
        all_codes.update(basket.keys())
    price_data = _fetch_basket_returns(pro, all_codes, trade_date)

    results = {}
    for name, codes in MACRO_HEDGE_BASKETS.items():
        vals = [price_data[c] for c in codes if c in price_data]
        if not vals:
            continue
        vals_5d = [v["pct_5d"] for v in vals if v["pct_5d"] is not None]
        vals_20d = [v["pct_20d"] for v in vals if v["pct_20d"] is not None]
        results[name] = {
            "n_stocks": len(vals),
            "pct_today": round(sum(v["pct_today"] for v in vals) / len(vals), 2),
            "pct_5d": round(sum(vals_5d) / len(vals_5d), 2) if vals_5d else None,
            "pct_20d": round(sum(vals_20d) / len(vals_20d), 2) if vals_20d else None,
        }
    return {"trade_date": trade_date, "baskets": results}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--skip-baskets", action="store_true",
                     help="跳过Layer2/3篮子动量计算（涉及上百只个股逐一拉取，比其余字段慢，"
                          "调试其他字段时可以跳过）")
    args = ap.parse_args()

    pro = _get_pro()

    _log("拉取市场广度(今日)...")
    breadth_today = fetch_market_breadth()

    _log("拉取北向资金20日净流入...")
    north_bound = fetch_north_bound_flow_20d()

    _log("拉取两融余额趋势...")
    margin_trend = fetch_margin_trend(pro)

    _log("计算两融余额滚动250日分位+预警窗口...")
    margin_rolling_signal = fetch_margin_rolling_signal(pro)

    _log("拉取全市场大单/小单资金流代理...")
    market_moneyflow = fetch_market_moneyflow_dc(pro)

    _log("拉取风格指数对比(科创50/创业板/沪深300/中证1000/银行/红利)...")
    style_comparison = fetch_style_index_comparison(pro)

    _log("计算过去10个交易日背离占比...")
    divergence = fetch_divergence_ratio(pro, days=10)

    _log("拉取宽基/科技ETF份额变化(护盘资金代理指标)...")
    national_team_proxy = fetch_national_team_proxy(pro)

    _log("拉取两市合计成交额趋势...")
    total_turnover = fetch_total_turnover(pro)

    _log("拉取关键指数均线+压力支撑位...")
    technical_levels = fetch_index_technical_levels(pro)

    subsector_basket_momentum = {}
    macro_hedge_baskets = {}
    ths_hotspot_momentum = {}
    sw_subindustry_momentum = {}
    if not args.skip_baskets:
        _log("计算Layer2子赛道篮子动量(sectors_status.csv signal_stock_code)...")
        subsector_basket_momentum = fetch_subsector_basket_momentum(pro)
        _log("计算Layer3宏观避险篮子动量(贵金属等，事件地图范围外)...")
        macro_hedge_baskets = fetch_macro_hedge_baskets(pro)
        _log("扫描同花顺热点概念（市场热度发现层，非主营纯度标签）...")
        ths_hotspot_momentum = fetch_ths_hotspot_momentum(pro)
        _log("扫描申万二/三级行业轮动...")
        sw_subindustry_momentum = fetch_sw_subindustry_momentum(pro)

    result = {
        "fetch_time": datetime.now().isoformat(),
        "breadth_today": breadth_today,
        "north_bound": north_bound,
        "margin_trend": margin_trend,
        "margin_rolling_signal": margin_rolling_signal,
        "market_moneyflow": market_moneyflow,
        "style_index_comparison": style_comparison,
        "divergence_ratio": divergence,
        "national_team_proxy": national_team_proxy,
        "total_turnover": total_turnover,
        "technical_levels": technical_levels,
        "subsector_basket_momentum": subsector_basket_momentum,
        "macro_hedge_baskets": macro_hedge_baskets,
        "ths_hotspot_momentum": ths_hotspot_momentum,
        "sw_subindustry_momentum": sw_subindustry_momentum,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n市场广度: {breadth_today.get('regime_label')} (指数{breadth_today.get('index_pct_chg')}%, "
              f"涨{breadth_today.get('advancers')}/跌{breadth_today.get('decliners')}, "
              f"涨停{breadth_today.get('limit_up')}/跌停{breadth_today.get('limit_down')})")
        print(f"北向资金20日净流入: {north_bound.get('net_flow_20d')}亿 ({north_bound.get('direction')})")
        print(f"两融余额: {margin_trend.get('direction')}, {margin_trend.get('consecutive_days')}天, "
              f"6年历史分位{margin_trend.get('historical_percentile_6y')}%")
        print(f"两融滚动250日分位: {margin_rolling_signal.get('rolling_percentile_250d')}%  "
              f"预警窗口内: {margin_rolling_signal.get('in_warning_window')}"
              + (f"（首次突破日:{margin_rolling_signal.get('fresh_cross_date')}）" if margin_rolling_signal.get('fresh_cross_date') else ""))
        print(f"全市场资金: {market_moneyflow.get('pattern')}")
        print(f"\n风格指数(今日/5日/20日涨跌幅，MA20位置):")
        for name, v in style_comparison.items():
            ma20_flag = "上方" if v.get("above_ma20") else ("下方" if v.get("above_ma20") is False else "N/A")
            print(f"  {name:<8} 今日{v.get('pct_today')}%  5日{v.get('pct_5d')}%  20日{v.get('pct_20d')}%  MA20{ma20_flag}")
        print(f"\n背离占比(近{divergence['window_days']}日): {divergence['divergence_ratio_pct']}% "
              f"({divergence['divergent_days']}/{divergence['window_days']}天)")
        print(f"\n两市合计成交额: {total_turnover.get('today_amount_yi')}亿 "
              f"(前{total_turnover.get('avg_prior_days_yi') and len(total_turnover.get('daily_detail', []))-1}日均值"
              f"{total_turnover.get('avg_prior_days_yi')}亿, {total_turnover.get('trend')} "
              f"{total_turnover.get('chg_vs_avg_pct')}%)")
        print(f"\n护盘资金代理指标(ETF份额变化，非直接确认'国家队'身份；注意各ETF latest_date"
              f"不一定同一天——上交所.SH挂牌ETF的fund_share接口比深交所.SZ更新慢约1天):")
        for name, v in national_team_proxy.items():
            if "source" in v:
                print(f"  {name:<8} 数据不可用({v['source']})")
                continue
            print(f"  {name:<8} 区间累计{v['period_chg_pct']}%  单日最大跳升{v['max_single_day_chg_pct']}%"
                  f"({v['max_single_day_date']})  数据截至{v['latest_date']}")
        print(f"\n关键指数均线+压力支撑位:")
        for name, v in technical_levels.items():
            if "source" in v:
                print(f"  {name:<8} 数据不可用({v['source']})")
                continue
            print(f"  {name:<8} 现价{v['current']}  MA5{v['MA5']}/MA10{v['MA10']}/MA20{v['MA20']}/MA60{v['MA60']}")
            for w in [20, 60, 120]:
                r = v.get(f"压力位_{w}日")
                s = v.get(f"支撑位_{w}日")
                if r:
                    print(f"      压力位({w}日): {r['level']}（{r['status']}）", end="  ")
                if s:
                    print(f"支撑位({w}日): {s['level']}（{s['status']}）")
            for label in ["swing_origin_近期(20日)", "swing_origin_更大级别(60日)"]:
                swing = v.get(label)
                if not swing:
                    continue
                print(f"      [{label.split('_')[-1]}]", end="  ")
                for k, val in swing.items():
                    if isinstance(val, dict):
                        print(f"{k}: {val['price']}（{val['date']}）", end="  ")
                    else:
                        print(f"{k}: {val}%", end="  ")
                print()


if __name__ == "__main__":
    main()
