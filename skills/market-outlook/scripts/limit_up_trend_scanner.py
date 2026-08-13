#!/usr/bin/env python3
"""A股主板“涨停激活＋均线稳态/稳升”短线股票池扫描器。

入池条件：最近10日出现过涨停，且满足下列至少一种价格结构：
S1=最近5个交易日收盘均高于各自MA10（MA10稳态）；
S2=最近5个交易日收盘均高于各自MA5、且MA5连续不下降（MA5稳升）。
回踩、开盘位置和买卖点不属于本扫描器职责。

均线使用后复权比例等价序列（close * adj_factor）；涨停判断使用未复权
pre_close和交易所两位小数涨停价。脚本只生成研究观察池，不生成买入指令。
"""

from __future__ import annotations

import argparse
import json
import math
import os
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pandas as pd


MAIN_BOARD_PREFIXES = ("000", "001", "002", "003", "600", "601", "603", "605")
OUTPUT_COLUMNS = [
    "ts_code", "name", "industry", "trade_date", "close", "signal",
    "s1_ma10_stable", "s2_ma5_rising", "close_above_ma10_5d",
    "close_above_ma5_5d", "ma5_rising_5d", "ma10_rising_5d",
    "predicted_ma5", "predicted_ma10", "predicted_ma20", "next_ma5_threshold",
    "limit_up_count_10d", "last_limit_up_date",
    "days_since_limit_up", "bias_ma5_pct", "bias_ma10_pct", "return_5d_pct",
    "post_limit_peak_gain_pct", "volume_ratio_5_20", "avg_amount_5d_qianyuan",
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


def is_limit_up(close: float, pre_close: float) -> bool:
    if not all(math.isfinite(float(x)) and float(x) > 0 for x in (close, pre_close)):
        return False
    return float(close) >= china_price_limit(float(pre_close)) - 0.001


def prepare_bars(frame: pd.DataFrame) -> pd.DataFrame:
    bars = frame.copy().sort_values("trade_date").drop_duplicates("trade_date")
    numeric = ["open", "high", "low", "close", "pre_close", "vol", "amount", "adj_factor"]
    for col in numeric:
        bars[col] = pd.to_numeric(bars.get(col), errors="coerce")
    bars = bars.dropna(subset=["close", "pre_close", "adj_factor"])
    bars["adj_close"] = bars["close"] * bars["adj_factor"]
    bars["ma5"] = bars["adj_close"].rolling(5).mean()
    bars["ma10"] = bars["adj_close"].rolling(10).mean()
    bars["limit_up"] = [is_limit_up(c, p) for c, p in zip(bars["close"], bars["pre_close"])]
    return bars.reset_index(drop=True)


def analyze_stock(frame: pd.DataFrame, meta: dict) -> dict | None:
    bars = prepare_bars(frame)
    if len(bars) < 20:
        return None
    tail5, tail10 = bars.tail(5), bars.tail(10)
    close_above_ma10 = bool((tail5["adj_close"] > tail5["ma10"]).all())
    close_above_ma5 = bool((tail5["adj_close"] > tail5["ma5"]).all())
    ma5_rising = bool((tail5["ma5"].diff().dropna() >= 0).all())
    ma10_rising = bool((tail5["ma10"].diff().dropna() >= 0).all())
    s1_ma10_stable = close_above_ma10
    s2_ma5_rising = close_above_ma5 and ma5_rising
    limit_rows = tail10[tail10["limit_up"]]
    if not (s1_ma10_stable or s2_ma5_rising) or limit_rows.empty:
        return None

    latest = bars.iloc[-1]
    last_limit_idx = int(limit_rows.index[-1])
    post = bars.loc[last_limit_idx:]
    limit_close_adj = float(bars.loc[last_limit_idx, "adj_close"])
    peak_gain = (float(post["adj_close"].max()) / limit_close_adj - 1) * 100
    vol20 = bars.tail(20)["vol"].mean()
    vol_ratio = bars.tail(5)["vol"].mean() / vol20 if vol20 and math.isfinite(vol20) else None
    prior_close = bars["close"].shift(1)
    daily_ret = bars["close"] / prior_close - 1
    bearish = (bars["close"] < bars["open"]) & (daily_ret <= -0.05) & (bars["vol"] > bars["vol"].rolling(20).mean() * 1.2)
    bias5 = (latest["adj_close"] / latest["ma5"] - 1) * 100
    bias10 = (latest["adj_close"] / latest["ma10"] - 1) * 100
    ret5 = (latest["adj_close"] / bars.iloc[-6]["adj_close"] - 1) * 100
    next_ma5_threshold = bars.tail(4)["adj_close"].mean() / latest["adj_factor"]

    def predicted_flat_ma(days: int) -> float:
        # 假设次日价格等于最新收盘价时的次日动态均线。
        adjusted = bars.tail(days - 1)["adj_close"].sum() + latest["adj_close"]
        return adjusted / days / latest["adj_factor"]

    flags = []
    if bias5 > 7:
        flags.append("偏离MA5>7%")
    if peak_gain > 20:
        flags.append("涨停后最高涨幅>20%")
    if bool(bearish.tail(5).any()):
        flags.append("近5日放量大阴")
    avg_amount = bars.tail(5)["amount"].mean()
    if pd.notna(avg_amount) and avg_amount < 50000:
        flags.append("近5日平均成交额<5000万元")
    if s2_ma5_rising and not s1_ma10_stable:
        flags.append("仅S2，尚未形成MA10稳态")
    tier = "A_结构较好" if not flags else ("B_需等待" if len(flags) == 1 else "C_高风险")
    signal = "S1+S2" if s1_ma10_stable and s2_ma5_rising else (
        "S1_MA10_STABLE" if s1_ma10_stable else "S2_MA5_RISING"
    )

    return {
        "ts_code": meta.get("ts_code"), "name": meta.get("name"),
        "industry": meta.get("industry"), "trade_date": str(latest["trade_date"]),
        "close": round(float(latest["close"]), 2), "signal": signal,
        "s1_ma10_stable": s1_ma10_stable, "s2_ma5_rising": s2_ma5_rising,
        "close_above_ma10_5d": close_above_ma10,
        "close_above_ma5_5d": close_above_ma5, "ma5_rising_5d": ma5_rising,
        "ma10_rising_5d": ma10_rising,
        "predicted_ma5": round(float(predicted_flat_ma(5)), 2),
        "predicted_ma10": round(float(predicted_flat_ma(10)), 2),
        "predicted_ma20": round(float(predicted_flat_ma(20)), 2),
        "next_ma5_threshold": round(float(next_ma5_threshold), 2),
        "limit_up_count_10d": int(len(limit_rows)),
        "last_limit_up_date": str(limit_rows.iloc[-1]["trade_date"]),
        "days_since_limit_up": int(len(bars) - 1 - last_limit_idx),
        "bias_ma5_pct": round(float(bias5), 2), "bias_ma10_pct": round(float(bias10), 2),
        "return_5d_pct": round(float(ret5), 2), "post_limit_peak_gain_pct": round(float(peak_gain), 2),
        "volume_ratio_5_20": round(float(vol_ratio), 2) if vol_ratio is not None else None,
        "avg_amount_5d_qianyuan": round(float(avg_amount), 2) if pd.notna(avg_amount) else None,
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
    return frame.sort_values(
        ["observation_priority", "large_bearish_day_5d", *activity_keys],
        ascending=[True, True, *activity_ascending], na_position="last",
    ).reset_index(drop=True)


def scan_frames(bars: pd.DataFrame, stock_basic: pd.DataFrame, as_of: str) -> pd.DataFrame:
    eligible = stock_basic[stock_basic.apply(lambda row: eligible_stock(row, as_of), axis=1)]
    meta_map = eligible.set_index("ts_code").to_dict("index")
    universe = bars[bars["ts_code"].isin(meta_map)].copy()
    rows = []
    for code, frame in universe.groupby("ts_code", sort=False):
        result = analyze_stock(frame, {"ts_code": code, **meta_map[code]})
        if result:
            rows.append(result)
    if not rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    result = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    return sort_by_activity(result)


def fetch_data(pro, as_of: str, lookback_days: int = 45) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    calendar = pro.trade_cal(
        exchange="SSE", start_date=(pd.Timestamp(as_of) - pd.Timedelta(days=lookback_days)).strftime("%Y%m%d"),
        end_date=as_of, fields="cal_date,is_open",
    )
    dates = sorted(calendar.loc[calendar["is_open"].eq(1), "cal_date"].astype(str))
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
        frame = frame.merge(context, on="ts_code", how="left")
    for column in ("limit_theme", "limit_up_reason", "limit_status"):
        if column not in frame:
            frame[column] = ""
        frame[column] = frame[column].fillna("")
    return frame


def write_markdown(result: pd.DataFrame, path: Path, as_of: str) -> None:
    lines = [
        f"# {as_of} 涨停后均线稳态/稳升短线股票池", "",
        "> 入池=最近10日有涨停，且满足S1（连续5日收盘高于MA10）或S2（连续5日收盘高于MA5且MA5不下降）。买点由次日低吸规则决定。", "",
        f"共筛出 **{len(result)}** 只。", "",
        "|代码|名称|信号|行业|10日涨停|量能比|最近涨停|预测MA5/10/20|临界价|MA5乖离|参考标记|", "|---|---|---|---|---:|---:|---|---|---:|---:|---|",
    ]
    # Markdown按资金活跃度主排序展示前60；CSV/JSON保留全部候选。
    display = result.head(60)
    for _, row in display.iterrows():
        lines.append(
            f"|{row.ts_code}|{row['name']}|{row.signal}|{row.industry}|{row.limit_up_count_10d}|{row.volume_ratio_5_20}|"
            f"{row.last_limit_up_date}|{row.predicted_ma5:.2f}/{row.predicted_ma10:.2f}/{row.predicted_ma20:.2f}|"
            f"{row.next_ma5_threshold:.2f}|{row.bias_ma5_pct:.2f}%|{row.risk_flags or '无'}|"
        )
    lines += ["", "说明：报告按涨停次数、量能比、涨停新鲜度和成交额展示前60只；乖离率及预测均线不参与排序。CSV/JSON保留全量结果。"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="A股主板涨停激活＋MA10稳态/MA5稳升短线股票池")
    parser.add_argument("--as-of", help="截止日YYYYMMDD，默认最近可得交易日")
    parser.add_argument("--lookback-days", type=int, default=45)
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
    result = scan_frames(bars, basics, actual_as_of)
    result = attach_limit_context(result, fetch_limit_context(pro, actual_as_of))
    out_dir = args.output_dir or root / "分析记录" / "涨停趋势扫描"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"limit_up_trend_{actual_as_of}"
    result.to_csv(out_dir / f"{stem}.csv", index=False, encoding="utf-8-sig")
    (out_dir / f"{stem}.json").write_text(
        json.dumps(result.to_dict("records"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_markdown(result, out_dir / f"{stem}.md", actual_as_of)
    print(json.dumps({"trade_date": actual_as_of, "candidates": len(result), "output": str(out_dir / f'{stem}.md')}, ensure_ascii=False))


if __name__ == "__main__":
    main()
