#!/usr/bin/env python3
"""Tushare-first earnings forecast radar.

The radar discovers companies from announcements first, then measures whether
the market had already priced the forecast.  It outputs research candidates,
not buy recommendations.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

import sys
_CODE_ROOT = Path(__file__).resolve().parents[3]
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))
from skills.shared.paths import config_file
from skills.shared.datasource import load_env as _shared_load_env, get_pro as _shared_get_pro, get_token as _shared_token


POSITIVE_TYPES = {"预增", "略增", "扭亏", "续盈"}
ONE_OFF_TERMS = (
    "非经常性", "出售", "处置", "股权转让", "公允价值", "政府补助",
    "投资收益", "汇兑收益", "减值转回", "冲回", "授权收入", "首付款",
)
OPERATING_TERMS = (
    "主营", "营业收入", "销量", "销售增长", "销售规模", "毛利",
    "价格上涨", "产品价格", "产能", "产量", "订单", "市场需求",
    "降本", "成本下降", "产能利用率", "业务增长",
)


def load_workspace_env():
    _shared_load_env(config_file())
    return config_file()


def finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def date_strings(start_date: str, end_date: str) -> list[str]:
    return [
        day.strftime("%Y%m%d")
        for day in pd.date_range(
            datetime.strptime(start_date, "%Y%m%d"),
            datetime.strptime(end_date, "%Y%m%d"),
        )
    ]


def fetch_forecasts(pro, start_date: str, end_date: str) -> pd.DataFrame:
    fields = (
        "ts_code,ann_date,end_date,type,p_change_min,p_change_max,"
        "net_profit_min,net_profit_max,last_parent_net,summary,change_reason"
    )
    frames = []
    for ann_date in date_strings(start_date, end_date):
        frame = pro.forecast(ann_date=ann_date, fields=fields)
        if frame is not None and not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=fields.split(","))
    result = pd.concat(frames, ignore_index=True)
    result = result.sort_values(["ts_code", "end_date", "ann_date"])
    return result.drop_duplicates(
        subset=["ts_code", "ann_date", "end_date", "type", "p_change_min",
                "p_change_max", "net_profit_min", "net_profit_max"],
        keep="last",
    )


def prefilter_forecasts(
    forecasts: pd.DataFrame,
    report_period: str,
    min_growth: float,
    min_profit_wan: float,
) -> pd.DataFrame:
    if forecasts.empty:
        return forecasts.copy()
    frame = forecasts[forecasts["end_date"].astype(str) == report_period].copy()
    frame = frame[frame["type"].astype(str).isin(POSITIVE_TYPES)]
    for col in (
        "p_change_min", "p_change_max", "net_profit_min", "net_profit_max",
        "last_parent_net",
    ):
        if col not in frame:
            frame[col] = None
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    growth_ok = frame["p_change_min"].ge(min_growth)
    turnaround = frame["type"].eq("扭亏")
    profit_ok = frame["net_profit_min"].ge(min_profit_wan)
    frame = frame[(growth_ok | turnaround) & profit_ok]
    return (
        frame.sort_values(["ts_code", "ann_date"])
        .drop_duplicates("ts_code", keep="last")
        .reset_index(drop=True)
    )


def fetch_market_bars(pro, start_date: str, end_date: str) -> pd.DataFrame:
    calendar = pro.trade_cal(
        exchange="SSE", start_date=start_date, end_date=end_date,
        fields="cal_date,is_open",
    )
    open_days = calendar.loc[calendar["is_open"].eq(1), "cal_date"].astype(str)
    frames = []
    fields = "ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount"
    for trade_date in sorted(open_days):
        frame = pro.daily(trade_date=trade_date, fields=fields)
        if frame is not None and not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=fields.split(","))
    return pd.concat(frames, ignore_index=True)


def fetch_reference_data(pro, as_of: str):
    stock_basic = pro.stock_basic(
        exchange="", list_status="L",
        fields="ts_code,symbol,name,area,industry,market,list_date",
    )
    daily_basic = pro.daily_basic(
        trade_date=as_of,
        fields="ts_code,trade_date,close,pe_ttm,pb,total_mv,dv_ttm",
    )
    return stock_basic, daily_basic


def fetch_benchmark_bars(
    pro, start_date: str, end_date: str, code: str = "000300.SH"
) -> pd.DataFrame:
    return pro.index_daily(
        ts_code=code,
        start_date=start_date,
        end_date=end_date,
        fields="ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount",
    )


def close_series(frame: pd.DataFrame) -> pd.Series:
    ordered = frame.sort_values("trade_date").drop_duplicates("trade_date")
    return pd.Series(
        pd.to_numeric(ordered["close"], errors="coerce").values,
        index=ordered["trade_date"].astype(str),
        dtype=float,
    ).dropna()


def return_pct(series: pd.Series, start_pos: int, end_pos: int) -> float | None:
    if series.empty or abs(start_pos) > len(series) or abs(end_pos) > len(series):
        return None
    start, end = series.iloc[start_pos], series.iloc[end_pos]
    if not start:
        return None
    return round((end / start - 1) * 100, 2)


def price_metrics(stock_bars: pd.DataFrame, ann_date: str) -> dict:
    series = close_series(stock_bars)
    if series.empty:
        return {}
    before = series[series.index < str(ann_date)]
    if before.empty:
        return {}
    baseline_date = before.index[-1]
    baseline_pos = series.index.get_loc(baseline_date)
    latest = series.iloc[-1]
    baseline = series.loc[baseline_date]
    pre_start = max(0, baseline_pos - 20)
    pre20 = (baseline / series.iloc[pre_start] - 1) * 100
    post = (latest / baseline - 1) * 100
    tail20 = series.tail(20)
    tail60 = series.tail(60)

    ordered = stock_bars.sort_values("trade_date").drop_duplicates("trade_date")
    volumes = pd.to_numeric(ordered["vol"], errors="coerce").dropna()
    vol5 = volumes.tail(5).mean() if len(volumes) >= 5 else None
    vol20 = volumes.tail(20).mean() if len(volumes) >= 20 else None
    vol_ratio = vol5 / vol20 if vol5 is not None and vol20 else None

    return {
        "baseline_date": baseline_date,
        "baseline_close": round(float(baseline), 3),
        "latest_close": round(float(latest), 3),
        "pre20_return_pct": round(float(pre20), 2),
        "post_announcement_return_pct": round(float(post), 2),
        "recent_5d_return_pct": return_pct(series, -6, -1),
        "ma20": round(float(tail20.mean()), 3) if len(tail20) == 20 else None,
        "ma60": round(float(tail60.mean()), 3) if len(tail60) == 60 else None,
        "above_ma20": bool(latest >= tail20.mean()) if len(tail20) == 20 else None,
        "above_ma60": bool(latest >= tail60.mean()) if len(tail60) == 60 else None,
        "volume_ratio_5_20": round(float(vol_ratio), 2) if vol_ratio else None,
    }


def contains_any(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if term in text]


def classify_candidate(row: dict) -> dict:
    text = f"{row.get('summary') or ''} {row.get('change_reason') or ''}"
    one_off_hits = contains_any(text, ONE_OFF_TERMS)
    operating_hits = contains_any(text, OPERATING_TERMS)
    growth_min = finite(row.get("p_change_min"))
    last_profit = finite(row.get("last_parent_net"))
    pre20_absolute = finite(row.get("pre20_return_pct"))
    post_absolute = finite(row.get("post_announcement_return_pct"))
    pre20 = finite(row.get("pre20_excess_vs_csi300_pct"))
    post = finite(row.get("post_excess_vs_csi300_pct"))
    pre20 = pre20 if pre20 is not None else pre20_absolute
    post = post if post is not None else post_absolute
    above_ma20 = row.get("above_ma20")
    low_base = bool(
        growth_min is not None
        and growth_min >= 300
        and (last_profit is None or abs(last_profit) < 5000)
    )
    strong_quality = bool(operating_hits and not one_off_hits and not low_base)
    already_traded = bool(
        (pre20 is not None and pre20 > 25)
        or (post is not None and post > 20)
    )
    negative_divergence = bool(
        strong_quality
        and (
            (post is not None and post < -8)
            or above_ma20 is False
        )
    )

    if already_traded:
        category = "D_业绩较强但股价已充分交易"
    elif negative_divergence:
        category = "E_主营预增但走势背离待查"
    elif strong_quality and (pre20 is None or pre20 <= 10) and (
        post is None or -8 <= post <= 8
    ):
        category = "A_主营预增且相对市场尚未定价"
    elif strong_quality and post is not None and 8 < post <= 20:
        category = "B_主营预增且市场开始响应"
    else:
        category = "C_增长质量或定价状态待核实"

    score = 0
    profit_min = finite(row.get("net_profit_min"))
    if profit_min is not None:
        score += 2 if profit_min >= 50000 else 1
    if growth_min is not None:
        score += 2 if growth_min >= 100 else 1
    if operating_hits:
        score += 2
    if one_off_hits:
        score -= 3
    if low_base:
        score -= 1
    if pre20 is not None:
        if -5 <= pre20 <= 5:
            score += 2
        elif -10 <= pre20 <= 10:
            score += 1
    if post is not None:
        if post < -8:
            score -= 3
        elif -3 <= post <= 3:
            score += 3
        elif -8 <= post <= 8:
            score += 2
        elif post <= 15:
            score += 1
    if above_ma20 is False:
        score -= 1
    pe_ttm = finite(row.get("pe_ttm"))
    if pe_ttm is not None and pe_ttm > 80:
        score -= 1
    if already_traded:
        score -= 2

    return {
        "category": category,
        "expectation_gap_score": score,
        "operating_evidence": operating_hits,
        "one_off_risk": one_off_hits,
        "low_base_risk": low_base,
        "research_action": (
            "进入六层人工核验"
            if category.startswith(("A_", "B_"))
            else "保留观察，不自动推荐"
        ),
    }


def build_candidates(
    forecasts: pd.DataFrame,
    bars: pd.DataFrame,
    stock_basic: pd.DataFrame,
    daily_basic: pd.DataFrame,
    benchmark_bars: pd.DataFrame | None = None,
) -> list[dict]:
    names = (
        stock_basic.set_index("ts_code").to_dict("index")
        if not stock_basic.empty else {}
    )
    valuations = (
        daily_basic.set_index("ts_code").to_dict("index")
        if not daily_basic.empty else {}
    )
    grouped_bars = {code: frame for code, frame in bars.groupby("ts_code")}
    benchmark_bars = (
        benchmark_bars if benchmark_bars is not None else pd.DataFrame()
    )
    results = []
    for raw in forecasts.to_dict("records"):
        code = str(raw["ts_code"])
        row = {
            key: (None if pd.isna(value) else value)
            for key, value in raw.items()
        }
        row.update(names.get(code, {}))
        row.update({
            key: value for key, value in valuations.get(code, {}).items()
            if key not in {"ts_code", "trade_date", "close"}
        })
        if code in grouped_bars:
            row.update(price_metrics(grouped_bars[code], str(row["ann_date"])))
        if not benchmark_bars.empty:
            benchmark = price_metrics(benchmark_bars, str(row["ann_date"]))
            pre_stock = finite(row.get("pre20_return_pct"))
            pre_benchmark = finite(benchmark.get("pre20_return_pct"))
            post_stock = finite(row.get("post_announcement_return_pct"))
            post_benchmark = finite(benchmark.get("post_announcement_return_pct"))
            if pre_stock is not None and pre_benchmark is not None:
                row["pre20_excess_vs_csi300_pct"] = round(
                    pre_stock - pre_benchmark, 2
                )
            if post_stock is not None and post_benchmark is not None:
                row["post_excess_vs_csi300_pct"] = round(
                    post_stock - post_benchmark, 2
                )
        row.update(classify_candidate(row))
        results.append(row)
    return sorted(
        results,
        key=lambda item: (
            item["category"][0],
            -item["expectation_gap_score"],
            -(finite(item.get("net_profit_min")) or 0),
        ),
    )


def markdown_report(meta: dict, candidates: list[dict], top: int) -> str:
    counts = pd.Series([row["category"] for row in candidates]).value_counts()
    lines = [
        f"# {meta['as_of'][:4]}年中报预增候选模拟扫描",
        "",
        f"> 数据截止：{meta['as_of']}  ",
        f"> 预告公告区间：{meta['scan_start']}—{meta['as_of']}  ",
        "> 数据源：Tushare（forecast/daily/daily_basic/stock_basic）  ",
        "> 定位：研究候选发现，不是自动推荐",
        "",
        "## 扫描摘要",
        "",
        f"- 中报预告原始记录：{meta['forecast_rows']}条；",
        f"- 达到增长及利润门槛：{len(candidates)}家公司；",
    ]
    for category, count in counts.items():
        lines.append(f"- {category}：{count}家；")
    lines.extend([
        "",
        "## 优先人工核验候选",
        "",
        "|分类|代码|公司|行业|预告类型|增长下限|利润下限(亿元)|公告前20日超额|公告后超额|PE(TTM)|分数|",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    priority = [
        row for row in candidates if row["category"].startswith(("A_", "B_"))
    ][:top]
    for row in priority:
        def show(value, digits=1):
            number = finite(value)
            return "NA" if number is None else f"{number:.{digits}f}"
        lines.append(
            f"|{row['category'][:1]}|{row.get('ts_code','')}|"
            f"{row.get('name','')}|{row.get('industry','')}|{row.get('type','')}|"
            f"{show(row.get('p_change_min'))}%|"
            f"{show((finite(row.get('net_profit_min')) or 0) / 10000, 2)}|"
            f"{show(row.get('pre20_excess_vs_csi300_pct'))}%|"
            f"{show(row.get('post_excess_vs_csi300_pct'))}%|"
            f"{show(row.get('pe_ttm'))}|{row['expectation_gap_score']}|"
        )
    lines.extend([
        "",
        "## 使用限制",
        "",
        "- 增长原因采用公告文本关键词做第一轮分组，进入六层前必须阅读原公告；",
        "- 表中公告前后走势均为相对沪深300的超额收益；",
        "- A类表示“主营增长证据较强、相对市场尚未充分定价且未明显破坏趋势”，不等于可以买入；",
        "- E类单列业绩与走势负背离，必须先核查市场为何不认可，不能视为便宜；",
        "- 日线使用未复权收盘价，发生分红送转的公司需在个股核验时复算；",
        "- 首版没有用未来正式中报数据，避免把事后信息带回公告时点。",
        "",
    ])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="全市场业绩预告预期差模拟扫描")
    parser.add_argument("--as-of", required=True, help="数据截止日 YYYYMMDD")
    parser.add_argument("--scan-start", required=True, help="公告扫描起日 YYYYMMDD")
    parser.add_argument("--report-period", default="20260630")
    parser.add_argument("--min-growth", type=float, default=50.0)
    parser.add_argument("--min-profit-wan", type=float, default=10000.0)
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--json-output")
    parser.add_argument("--csv-output")
    parser.add_argument("--markdown-output")
    args = parser.parse_args()

    load_workspace_env()
    token = _shared_token()
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未配置")
    pro = _shared_get_pro()

    forecasts = fetch_forecasts(pro, args.scan_start, args.as_of)
    selected = prefilter_forecasts(
        forecasts, args.report_period, args.min_growth, args.min_profit_wan,
    )
    history_start = (
        datetime.strptime(args.as_of, "%Y%m%d") - timedelta(days=args.lookback_days)
    ).strftime("%Y%m%d")
    bars = fetch_market_bars(pro, history_start, args.as_of)
    if not selected.empty:
        bars = bars[bars["ts_code"].isin(selected["ts_code"])]
    stock_basic, daily_basic = fetch_reference_data(pro, args.as_of)
    benchmark_bars = fetch_benchmark_bars(pro, history_start, args.as_of)
    candidates = build_candidates(
        selected, bars, stock_basic, daily_basic, benchmark_bars,
    )
    meta = {
        "as_of": args.as_of,
        "scan_start": args.scan_start,
        "report_period": args.report_period,
        "forecast_rows": int(len(forecasts)),
        "selected_companies": len(candidates),
        "thresholds": {
            "min_growth_pct": args.min_growth,
            "min_profit_wan": args.min_profit_wan,
        },
    }
    payload = {"meta": meta, "candidates": candidates}

    if args.json_output:
        Path(args.json_output).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        )
    if args.csv_output:
        pd.DataFrame(candidates).to_csv(args.csv_output, index=False)
    report = markdown_report(meta, candidates, args.top)
    if args.markdown_output:
        Path(args.markdown_output).write_text(report)
    print(report)


if __name__ == "__main__":
    main()
