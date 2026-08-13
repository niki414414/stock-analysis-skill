#!/usr/bin/env python3
"""MA5稳升短线策略的事件回测。

信号在T日收盘后确认；T+1仅当开盘接近平开且位于次日MA5临界价上方
指定区间时成交。分别统计T+1、T+2收盘收益和期间最大有利/不利波动。
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


def fetch_history(pro, start: str, end: str, cache_dir: Path, warmup_days: int = 45) -> pd.DataFrame:
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
        tail5, tail10 = history.tail(5), history.tail(10)
        signal = bool(
            (tail5["adj_close"] > tail5["ma5"]).all()
            and (tail5["ma5"].diff().dropna() >= 0).all()
            and tail10["limit_up"].any()
        )
        if not signal:
            continue
        entry = bars.iloc[i + 1]
        exit2 = bars.iloc[i + 2]
        threshold = history.tail(4)["adj_close"].mean() / history.iloc[-1]["adj_factor"]
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
        }
        if executable:
            price = float(entry["open"])
            row.update({
                "return_1d_pct": round((float(entry["close"]) / price - 1) * 100, 3),
                "return_2d_pct": round((float(exit2["close"]) / price - 1) * 100, 3),
                "mfe_1d_pct": round((float(entry["high"]) / price - 1) * 100, 3),
                "mae_1d_pct": round((float(entry["low"]) / price - 1) * 100, 3),
                "mfe_2d_pct": round((max(float(entry["high"]), float(exit2["high"])) / price - 1) * 100, 3),
                "mae_2d_pct": round((min(float(entry["low"]), float(exit2["low"])) / price - 1) * 100, 3),
            })
        rows.append(row)
    return rows


def summarize(rows: pd.DataFrame) -> dict:
    executed = rows[rows["executable"]].copy() if not rows.empty else rows
    result = {"signals": int(len(rows)), "executed": int(len(executed))}
    result["execution_rate"] = round(len(executed) / len(rows), 4) if len(rows) else None
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


def compute_market_activity(bars: pd.DataFrame) -> pd.DataFrame:
    """仅用当日收盘后可见数据划分次日短线资金环境。"""
    frame = bars.copy()
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    frame["raw_return"] = pd.to_numeric(frame["close"], errors="coerce") / pd.to_numeric(frame["pre_close"], errors="coerce") - 1
    daily = frame.groupby("trade_date").agg(
        total_amount=("amount", "sum"),
        limit_up_count=("raw_return", lambda values: int((values >= 0.095).sum())),
        limit_down_count=("raw_return", lambda values: int((values <= -0.095).sum())),
    ).sort_index()
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
    return daily.reset_index()


def attach_market_activity(rows: pd.DataFrame, activity: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows
    fields = ["trade_date", "market_activity", "turnover_ratio_20d", "limit_up_count", "limit_down_count"]
    lookup = activity[fields].rename(columns={"trade_date": "signal_date"})
    result = rows.merge(lookup, on="signal_date", how="left")
    result["executable_with_gate"] = result["executable"] & result["market_activity"].isin(["active", "normal"])
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
        f"# MA5短线回测 {summary['start']}—{summary['end']}", "",
        f"- 收盘信号：{summary['signals']}次；符合次日开盘规则：{summary['executed']}次；执行率：{summary['execution_rate']}",
        f"- 持有1日：平均{one.get('mean_pct')}%，中位数{one.get('median_pct')}%，胜率{one.get('win_rate')}，平均MFE/MAE {one.get('mean_mfe_pct')}%/{one.get('mean_mae_pct')}%",
        f"- 持有2日：平均{two.get('mean_pct')}%，中位数{two.get('median_pct')}%，胜率{two.get('win_rate')}，平均MFE/MAE {two.get('mean_mfe_pct')}%/{two.get('mean_mae_pct')}%",
        "", "## 按市场资金活跃度", "",
    ]
    lines += ["|状态|可成交样本|持有1日均值|持有1日胜率|持有2日均值|持有2日胜率|", "|---|---:|---:|---:|---:|---:|"]
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
        for row in simulate_stock(frame, start, end, **kwargs):
            rows.append({"name": meta.get("name"), "industry": meta.get("industry"), **row})
    return pd.DataFrame(rows)


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
    import tushare as ts
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN未设置")
    out_dir = args.output_dir or root / "分析记录" / "策略回测"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = root / "技能数据" / "market_history_cache"
    pro = ts.pro_api(token)
    bars = fetch_history(pro, args.start, args.end, cache_dir)
    basics = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,industry,market,list_date")
    rows = run_backtest(
        bars, basics, args.start, args.end, gap_low=args.gap_low,
        gap_high=args.gap_high, threshold_premium=args.threshold_premium,
    )
    rows = attach_market_activity(rows, compute_market_activity(bars))
    summary = summarize(rows)
    summary["by_market_activity"] = summarize_by_market_activity(rows)
    summary.update({
        "start": args.start, "end": args.end,
        "entry_rule": {"gap_low": args.gap_low, "gap_high": args.gap_high, "threshold_premium": args.threshold_premium},
        "market_gate": "active=成交额不低于20日均值且涨停不少于50；frozen=跌停不少于50，或成交额低于20日均值80%且涨停少于30；其他为normal",
        "limitations": ["当前版本使用期末股票名称过滤ST，尚未重建逐日历史ST状态", "当前版本股票池存在一定生存者偏差", "收益未扣佣金、印花税和滑点", "涨跌停数量由日涨跌幅近似计算"],
    })
    stem = f"ma5_short_backtest_{args.start}_{args.end}"
    rows.to_csv(out_dir / f"{stem}.csv", index=False, encoding="utf-8-sig")
    (out_dir / f"{stem}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(summary, out_dir / f"{stem}.md")
    print(json.dumps({"summary": summary, "csv": str(out_dir / f'{stem}.csv'), "report": str(out_dir / f'{stem}.md')}, ensure_ascii=False))


if __name__ == "__main__":
    main()
