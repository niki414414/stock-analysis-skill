#!/usr/bin/env python3
"""推土机短线策略的日线代理回测。

信号在T日收盘后确认；T+1仅当开盘接近平开且位于次日MA5临界价上方
指定区间时成交。T+1收盘只作为买入当日盯市值；遵守A股T+1后，
最早可执行退出为T+2开盘或其后。竞价预埋成交需要独立竞价数据验证。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path

import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("limit_scanner", SCRIPT_DIR / "limit_up_trend_scanner.py")
SCANNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCANNER)


def fetch_history(pro, start: str, end: str, cache_dir: Path, warmup_days: int = 100) -> pd.DataFrame:
    fetch_start = (pd.Timestamp(start) - pd.Timedelta(days=warmup_days)).strftime("%Y%m%d")
    calendar = pro.trade_cal(exchange="SSE", start_date=fetch_start, end_date=end, fields="cal_date,is_open")
    dates = sorted(calendar.loc[calendar["is_open"].eq(1), "cal_date"].astype(str))
    cache_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    fields = "ts_code,trade_date,open,high,low,close,pre_close,vol,amount"
    for trade_date in dates:
        cached = cache_dir / f"{trade_date}.csv.gz"
        if cached.exists():
            frames.append(pd.read_csv(cached, dtype={"trade_date": str}))
            continue
        daily = pro.daily(trade_date=trade_date, fields=fields)
        adj = pro.adj_factor(trade_date=trade_date, fields="ts_code,trade_date,adj_factor")
        if daily is None or daily.empty or adj is None or adj.empty:
            continue
        merged = daily.merge(adj, on=["ts_code", "trade_date"], how="inner")
        merged.to_csv(cached, index=False, compression="gzip")
        frames.append(merged)
    if not frames:
        raise RuntimeError("回测区间没有行情数据")
    return pd.concat(frames, ignore_index=True)


def simulate_stock(
    frame: pd.DataFrame,
    start: str,
    end: str,
    gap_low: float = -0.01,
    gap_high: float = 0.01,
    threshold_premium: float = 0.01,
    meta: dict | None = None,
) -> list[dict]:
    bars = SCANNER.prepare_bars(frame)
    rows = []
    if len(bars) < 22:
        return rows
    for i in range(19, len(bars) - 2):
        signal_date = str(bars.iloc[i]["trade_date"])
        if signal_date < start or signal_date > end:
            continue
        history = bars.iloc[: i + 1]
        identity = {
            "ts_code": str(frame.iloc[0].get("ts_code", "")),
            "name": (meta or {}).get("name", ""),
            "industry": (meta or {}).get("industry", ""),
        }
        candidate = SCANNER.analyze_stock(history, identity)
        if candidate is None:
            continue
        entry = bars.iloc[i + 1]
        exit2 = bars.iloc[i + 2]
        threshold = float(candidate["next_ma5_threshold"])
        gap = entry["open"] / entry["pre_close"] - 1
        premium = entry["open"] / threshold - 1
        executable = gap_low <= gap <= gap_high and 0 <= premium <= threshold_premium
        row = {
            "ts_code": str(entry.get("ts_code", frame.iloc[0].get("ts_code", ""))),
            "signal_date": signal_date, "entry_date": str(entry["trade_date"]),
            "entry_open": round(float(entry["open"]), 3),
            "next_ma5_threshold": round(float(threshold), 3),
            "open_gap_pct": round(float(gap * 100), 3),
            "threshold_premium_pct": round(float(premium * 100), 3),
            "executable": bool(executable),
            "signal": candidate["signal"],
            "selection_lane": candidate["selection_lane"],
            "full_ma_lane": candidate["full_ma_lane"],
            "active_lane": candidate["active_lane"],
            "shape_score": candidate["shape_score"],
            "activity_score": candidate["activity_score"],
            "sustained_strength": candidate["sustained_strength"],
            "continuity_tier": candidate["continuity_tier"],
            "risk_tier": candidate["risk_tier"],
        }
        if executable:
            price = float(entry["open"])
            row.update({
                "same_day_mark_pct": round((float(entry["close"]) / price - 1) * 100, 3),
                "next_day_open_exit_pct": round((float(exit2["open"]) / price - 1) * 100, 3),
                "next_day_close_exit_pct": round((float(exit2["close"]) / price - 1) * 100, 3),
                # Legacy aliases retained for old result readers.
                "return_1d_pct": round((float(entry["close"]) / price - 1) * 100, 3),
                "return_2d_pct": round((float(exit2["close"]) / price - 1) * 100, 3),
                "mfe_1d_pct": round((float(entry["high"]) / price - 1) * 100, 3),
                "mae_1d_pct": round((float(entry["low"]) / price - 1) * 100, 3),
                "mfe_2d_pct": round((max(float(entry["high"]), float(exit2["high"])) / price - 1) * 100, 3),
                "mae_2d_pct": round((min(float(entry["low"]), float(exit2["low"])) / price - 1) * 100, 3),
            })
        rows.append(row)
    return rows


def summarize(rows: pd.DataFrame, execution_column: str | None = None) -> dict:
    column = execution_column or (
        "executable_with_gate" if "executable_with_gate" in rows else "executable"
    )
    executed = rows[rows[column].fillna(False).astype(bool)].copy() if not rows.empty else rows
    result = {"signals": int(len(rows)), "executed": int(len(executed)),
              "execution_column": column, "return_basis": "gross_daily_bar_proxy"}
    result["execution_rate"] = round(len(executed) / len(rows), 4) if len(rows) else None
    for key, column in (
        ("same_day_mark", "same_day_mark_pct"),
        ("next_day_open_exit", "next_day_open_exit_pct"),
        ("next_day_close_exit", "next_day_close_exit_pct"),
    ):
        values = pd.to_numeric(executed.get(column), errors="coerce").dropna() if column in executed else pd.Series(dtype=float)
        result[key] = {
            "count": int(len(values)), "mean_pct": round(float(values.mean()), 3),
            "median_pct": round(float(values.median()), 3),
            "win_rate": round(float((values > 0).mean()), 4),
        } if len(values) else {}
    for horizon in (1, 2):
        col = f"return_{horizon}d_pct"
        if executed.empty or col not in executed:
            result[f"holding_{horizon}d"] = {}
            continue
        values = pd.to_numeric(executed[col], errors="coerce").dropna()
        result[f"holding_{horizon}d"] = {
            "count": int(len(values)), "mean_pct": round(float(values.mean()), 3),
            "median_pct": round(float(values.median()), 3),
            "win_rate": round(float((values > 0).mean()), 4),
            "mean_mfe_pct": round(float(executed[f"mfe_{horizon}d_pct"].mean()), 3),
            "mean_mae_pct": round(float(executed[f"mae_{horizon}d_pct"].mean()), 3),
        }
    return result


def market_limit_rate(ts_code: str) -> float:
    """Board-aware regular price limit; historical ST/listing-day exceptions remain external."""
    symbol = str(ts_code).split(".", 1)[0]
    if symbol.startswith(("300", "301", "688", "689")):
        return 0.20
    if symbol.startswith(("4", "8", "92")):
        return 0.30
    return 0.10


def compute_market_activity(bars: pd.DataFrame) -> pd.DataFrame:
    """仅用当日收盘后可见数据划分次日短线资金环境。"""
    frame = bars.copy()
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    frame["raw_return"] = pd.to_numeric(frame["close"], errors="coerce") / pd.to_numeric(frame["pre_close"], errors="coerce") - 1
    if "ts_code" not in frame:
        frame["ts_code"] = "600000.SH"
    frame["limit_rate"] = frame["ts_code"].map(market_limit_rate)
    frame["is_limit_up"] = frame["raw_return"] >= frame["limit_rate"] - 0.005
    frame["is_limit_down"] = frame["raw_return"] <= -frame["limit_rate"] + 0.005
    daily = frame.groupby("trade_date").agg(
        total_amount=("amount", "sum"),
        advancers=("raw_return", lambda values: int((values > 0).sum())),
        decliners=("raw_return", lambda values: int((values < 0).sum())),
        limit_up_count=("is_limit_up", "sum"),
        limit_down_count=("is_limit_down", "sum"),
    ).sort_index()
    daily["advance_pct"] = daily["advancers"] / (daily["advancers"] + daily["decliners"]) * 100
    daily["amount_ma20"] = daily["total_amount"].rolling(20).mean()
    daily["turnover_ratio_20d"] = daily["total_amount"] / daily["amount_ma20"]

    def classify(row):
        ratio = row["turnover_ratio_20d"]
        if pd.isna(ratio):
            return "insufficient_history"
        if row["limit_down_count"] >= 50:
            return "frozen"
        if ratio >= 1.0 and row["limit_up_count"] >= 50:
            return "active"
        if ratio < 0.8 and row["limit_up_count"] < 30:
            return "frozen"
        return "normal"

    daily["market_activity"] = daily.apply(classify, axis=1)
    daily["pushdozer_gate"] = daily["limit_down_count"].map(
        lambda count: "green" if count <= 10 else "yellow" if count < 20 else "red"
    )
    return daily.reset_index()


def attach_market_activity(rows: pd.DataFrame, activity: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows
    fields = ["trade_date", "market_activity", "pushdozer_gate", "advance_pct", "turnover_ratio_20d", "limit_up_count", "limit_down_count"]
    lookup = activity[fields].rename(columns={"trade_date": "signal_date"})
    result = rows.merge(lookup, on="signal_date", how="left")
    result["executable_with_gate"] = result["executable"] & result["pushdozer_gate"].isin(["green", "yellow"])
    return result


def summarize_by_market_activity(rows: pd.DataFrame) -> dict:
    output = {}
    if rows.empty or "market_activity" not in rows:
        return output
    for state, group in rows.groupby("market_activity", dropna=False):
        output[str(state)] = summarize(group)
    return output


def write_markdown(summary: dict, path: Path) -> None:
    one, two = summary.get("holding_1d", {}), summary.get("holding_2d", {})
    lines = [
        f"# 推土机短线日线代理回测 {summary['start']}—{summary['end']}", "",
        f"- 收盘信号：{summary['signals']}次；开盘条件及市场闸门通过：{summary['executed']}次；执行率：{summary['execution_rate']}",
        f"- 未应用市场闸门的对照成交数：{summary.get('without_market_gate', {}).get('executed', '未提供')}",
        "- 收益口径：未扣费日线代理，不是账户可实现净收益；红灯或闸门缺失不计入主结果。",
        f"- 买入当日收盘盯市（不可执行退出）：{summary.get('same_day_mark', {})}",
        f"- 下一交易日开盘退出代理：{summary.get('next_day_open_exit', {})}",
        f"- 下一交易日收盘退出代理：{summary.get('next_day_close_exit', {})}",
        "", "## 按市场资金活跃度", "",
    ]
    lines += ["|状态|闸门通过样本|买入当日盯市均值（不可卖）|当日盯市胜率|次日收盘退出均值|次日收盘退出胜率|", "|---|---:|---:|---:|---:|---:|"]
    for state, stats in summary.get("by_market_activity", {}).items():
        h1, h2 = stats.get("holding_1d", {}), stats.get("holding_2d", {})
        lines.append(f"|{state}|{stats.get('executed')}|{h1.get('mean_pct')}%|{h1.get('win_rate')}|{h2.get('mean_pct')}%|{h2.get('win_rate')}|")
    lines += ["", "## 限制", ""]
    lines.extend(f"- {item}" for item in summary.get("limitations", []))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_backtest(bars: pd.DataFrame, basics: pd.DataFrame, start: str, end: str, **kwargs) -> pd.DataFrame:
    eligible = basics[basics.apply(lambda row: SCANNER.eligible_stock(row, end), axis=1)]
    codes = set(eligible["ts_code"])
    rows = []
    for code, frame in bars[bars["ts_code"].isin(codes)].groupby("ts_code", sort=False):
        meta = eligible[eligible["ts_code"] == code].iloc[0]
        for row in simulate_stock(frame, start, end, meta=meta.to_dict(), **kwargs):
            rows.append({"name": meta.get("name"), "industry": meta.get("industry"), **row})
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    selected_days = []
    for _, group in result.groupby("signal_date", sort=True):
        selected_days.append(SCANNER.select_dual_lane(group, per_lane=5, total=10))
    return pd.concat(selected_days, ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="MA5稳升短线低吸1/2日事件回测")
    parser.add_argument("--start", required=True, help="YYYYMMDD")
    parser.add_argument("--end", required=True, help="YYYYMMDD")
    parser.add_argument("--gap-low", type=float, default=-0.01)
    parser.add_argument("--gap-high", type=float, default=0.01)
    parser.add_argument("--threshold-premium", type=float, default=0.01)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = SCANNER.load_workspace_env()
    from skills.shared.datasource import get_pro
    out_dir = args.output_dir or root / "技能数据" / "运行记录" / "策略回测"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = root / "技能数据" / "market_history_cache"
    pro = get_pro()
    bars = fetch_history(pro, args.start, args.end, cache_dir)
    basics = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,industry,market,list_date")
    rows = run_backtest(
        bars, basics, args.start, args.end, gap_low=args.gap_low,
        gap_high=args.gap_high, threshold_premium=args.threshold_premium,
    )
    rows = attach_market_activity(rows, compute_market_activity(bars))
    summary = summarize(rows)
    summary["without_market_gate"] = summarize(rows, execution_column="executable")
    summary["by_market_activity"] = summarize_by_market_activity(rows)
    summary.update({
        "start": args.start, "end": args.end,
        "entry_rule": {"gap_low": args.gap_low, "gap_high": args.gap_high, "threshold_premium": args.threshold_premium},
        "market_gate": "推土机：跌停≤10绿灯；11—19黄灯；≥20红灯。上涨占比只分环境，不作为机械买点",
        "limitations": ["当前版本使用期末股票名称过滤ST，尚未重建逐日历史ST状态", "当前版本股票池存在一定生存者偏差", "收益未扣佣金、印花税和滑点", "涨跌停已按板块常规10%/20%/30%区分，但历史ST与上市初期无涨跌幅限制仍待重建", "无竞价逐笔数据，不能验证9:25前预埋是否成交；全天最低价不得替代竞价成交"],
    })
    stem = f"ma5_short_backtest_{args.start}_{args.end}"
    rows.to_csv(out_dir / f"{stem}.csv", index=False, encoding="utf-8-sig")
    (out_dir / f"{stem}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(summary, out_dir / f"{stem}.md")
    print(json.dumps({"summary": summary, "csv": str(out_dir / f'{stem}.csv'), "report": str(out_dir / f'{stem}.md')}, ensure_ascii=False))


if __name__ == "__main__":
    main()
