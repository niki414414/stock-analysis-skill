#!/usr/bin/env python3
"""Compare current pushdozer rules with an effective-volume trigger.

This is a daily-bar proxy.  A planned buy limit is placed inside the completed
signal day's MA5-to-close interval.  The next day's official open is used as
the auction clearing-price proxy; intraday lows are never used to infer fills.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import pandas as pd

import ma5_short_backtest as base


def stock_events(frame: pd.DataFrame, start: str, end: str, meta: dict | None = None) -> list[dict]:
    bars = base.SCANNER.prepare_bars(frame)
    rows: list[dict] = []
    if len(bars) < 22:
        return rows
    for i in range(19, len(bars) - 2):
        signal_date = str(bars.iloc[i]["trade_date"])
        if not start <= signal_date <= end:
            continue
        history = bars.iloc[: i + 1]
        identity = {
            "ts_code": str(frame.iloc[0].get("ts_code", "")),
            "name": (meta or {}).get("name", ""),
            "industry": (meta or {}).get("industry", ""),
        }
        candidate = base.SCANNER.analyze_stock(history, identity)
        if candidate is None:
            continue

        latest, entry, exit_bar = history.iloc[-1], bars.iloc[i + 1], bars.iloc[i + 2]
        ma5_threshold = float(candidate["next_ma5_threshold"])
        auction_low = candidate.get("auction_reference_low")
        auction_high = candidate.get("auction_reference_high")
        close = float(candidate["close"])
        valid_band = bool(candidate["auction_band_valid"])
        bias5 = float(candidate["bias_ma5_pct"])
        limit_idx = int(history.index[history["trade_date"].astype(str).eq(candidate["last_limit_up_date"])][-1])
        peak_gain = float(candidate["post_limit_peak_gain_pct"])
        vol_prev = float(history.iloc[-2]["vol"])
        vol_median5 = float(history.iloc[:-1].tail(5)["vol"].median())
        vol_ratio_prev = float(latest["vol"]) / vol_prev if vol_prev > 0 else math.nan
        vol_ratio_med5 = float(latest["vol"]) / vol_median5 if vol_median5 > 0 else math.nan
        day_range = float(latest["high"] - latest["low"])
        close_location = (float(latest["close"]) - float(latest["low"])) / day_range if day_range > 0 else 0.5
        bullish = float(latest["close"]) > float(latest["open"])
        def volume_trigger_at(pos: int, day_ratio: float) -> bool:
            bar = history.iloc[pos]
            if pos < 5 or float(history.iloc[pos - 1]["vol"]) <= 0:
                return False
            median5 = float(history.iloc[pos - 5:pos]["vol"].median())
            bar_range = float(bar["high"] - bar["low"])
            location = (float(bar["close"]) - float(bar["low"])) / bar_range if bar_range > 0 else 0.5
            bar_bias = (float(bar["adj_close"]) / float(bar["ma5"]) - 1) * 100
            return bool(
                float(bar["vol"]) / float(history.iloc[pos - 1]["vol"]) >= day_ratio
                and median5 > 0 and float(bar["vol"]) / median5 >= 1.5
                and float(bar["close"]) > float(bar["open"])
                and location >= 0.60 and float(bar["adj_close"]) > float(bar["ma5"])
                and bar_bias <= 7
            )

        effective_volume = volume_trigger_at(len(history) - 1, 1.8)
        volume_trigger_15 = volume_trigger_at(len(history) - 1, 1.5)
        recent_volume_3d = any(volume_trigger_at(pos, 1.8) for pos in range(len(history) - 3, len(history)))
        recent_volume_3d_15 = any(volume_trigger_at(pos, 1.5) for pos in range(len(history) - 3, len(history)))
        prior_close = history["close"].shift(1)
        daily_ret = history["close"] / prior_close - 1
        bearish = (history["close"] < history["open"]) & (daily_ret <= -0.05) & (
            history["vol"] > history["vol"].rolling(20).mean() * 1.2
        )
        avg_amount = float(candidate["avg_amount_5d_qianyuan"])
        volume_ratio_5_20 = float(candidate["volume_ratio_5_20"])
        risk_clean = candidate["risk_tier"] == "A_结构较好"
        row = {
            "ts_code": str(entry["ts_code"]), "signal_date": signal_date,
            "entry_date": str(entry["trade_date"]), "exit_date": str(exit_bar["trade_date"]),
            "signal": candidate["signal"], "selection_lane": candidate["selection_lane"],
            "full_ma_lane": candidate["full_ma_lane"], "active_lane": candidate["active_lane"],
            "shape_score": candidate["shape_score"], "activity_score": candidate["activity_score"],
            "sustained_strength": candidate["sustained_strength"],
            "continuity_tier": candidate["continuity_tier"],
            "trend_stage": candidate["trend_stage"], "valid_band": valid_band,
            "next_ma5_threshold": ma5_threshold, "close": close,
            "bias_ma5_pct": bias5, "post_limit_peak_gain_pct": peak_gain,
            "limit_up_count_10d": candidate["limit_up_count_10d"],
            "days_since_limit_up": candidate["days_since_limit_up"],
            "volume_ratio_5_20": volume_ratio_5_20,
            "avg_amount_5d_qianyuan": avg_amount,
            "large_bearish_day_5d": bool(bearish.tail(5).any()),
            "vol_ratio_prev": vol_ratio_prev, "vol_ratio_med5": vol_ratio_med5,
            "bullish": bullish, "close_location": close_location,
            "effective_volume": effective_volume, "risk_clean": risk_clean,
            "volume_trigger_15": volume_trigger_15,
            "recent_volume_3d": recent_volume_3d,
            "recent_volume_3d_15": recent_volume_3d_15,
            "entry_open": float(entry["open"]),
        }
        for label, fraction in (("low25", 0.25), ("mid50", 0.50), ("high75", 0.75)):
            bid = float(auction_low) + (float(auction_high) - float(auction_low)) * fraction if valid_band else math.nan
            mechanical = bool(valid_band and float(entry["open"]) <= bid)
            guarded = bool(mechanical and float(entry["open"]) >= ma5_threshold * 0.98)
            row[f"bid_{label}"] = bid
            row[f"fill_{label}"] = mechanical
            row[f"guarded_fill_{label}"] = guarded
        price = float(entry["open"])
        row["same_day_mark_pct"] = (float(entry["close"]) / price - 1) * 100
        row["next_day_open_exit_pct"] = (float(exit_bar["open"]) / price - 1) * 100
        row["next_day_close_exit_pct"] = (float(exit_bar["close"]) / price - 1) * 100
        rows.append(row)
    return rows


def describe(frame: pd.DataFrame, mask: pd.Series, fill_col: str) -> dict:
    selected = frame[mask & frame[fill_col]].copy()
    result = {"signals": int(mask.sum()), "fills": int(len(selected))}
    for col in ("same_day_mark_pct", "next_day_open_exit_pct", "next_day_close_exit_pct"):
        values = selected[col]
        result[col] = {
            "n": int(len(values)), "mean": round(float(values.mean()), 3) if len(values) else None,
            "median": round(float(values.median()), 3) if len(values) else None,
            "win_rate": round(float((values > 0).mean()), 4) if len(values) else None,
        }
    if len(selected):
        daily = selected.groupby("entry_date")["next_day_open_exit_pct"].mean()
        result["equal_weight_entry_days"] = {
            "days": int(len(daily)), "mean": round(float(daily.mean()), 3),
            "win_rate": round(float((daily > 0).mean()), 4),
        }
    else:
        result["equal_weight_entry_days"] = {"days": 0, "mean": None, "win_rate": None}
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--warmup-days", type=int, default=100)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = base.SCANNER.load_workspace_env()
    import tushare as ts
    pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])
    bars = base.fetch_history(
        pro, args.start, args.end, root / "技能数据" / "market_history_cache",
        warmup_days=args.warmup_days,
    )
    basics = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,industry,market,list_date")
    eligible = basics[basics.apply(lambda row: base.SCANNER.eligible_stock(row, args.end), axis=1)]
    events = []
    codes = set(eligible["ts_code"])
    for code, frame in bars[bars["ts_code"].isin(codes)].groupby("ts_code", sort=False):
        meta = eligible.loc[eligible["ts_code"].eq(code)].iloc[0].to_dict()
        events.extend({"name": meta.get("name"), **row} for row in stock_events(frame, args.start, args.end, meta))
    rows = pd.DataFrame(events)
    if rows.empty:
        raise RuntimeError("当前推土机规则在回测区间内没有产生候选")
    selected_days = []
    for _, group in rows.groupby("signal_date", sort=True):
        selected = base.SCANNER.select_dual_lane(group, per_lane=5, total=10).copy()
        # The pool is assembled by quota (shape lane, then activity lane).
        # This is an audit order, not a claim that row 1 is better than row 6.
        selected["pool_order"] = range(1, len(selected) + 1)
        selected_days.append(selected)
    rows = pd.concat(selected_days, ignore_index=True)
    activity = base.compute_market_activity(bars)
    market_cols = ["trade_date", "advancers", "decliners", "advance_pct", "limit_up_count", "limit_down_count", "pushdozer_gate"]
    rows = rows.merge(activity[market_cols].rename(columns={"trade_date": "signal_date"}), on="signal_date", how="left")
    rows["nonred_gate"] = rows["limit_down_count"] < 20
    rows["strict_3000_gate"] = (rows["limit_down_count"] <= 10) & (rows["advancers"] >= 3000)
    rows = rows.sort_values(["signal_date", "pool_order"])
    rows["volume_rank"] = pd.NA
    volume_rows = rows[rows["effective_volume"]].copy()
    volume_rows["volume_rank"] = volume_rows.groupby("signal_date").cumcount() + 1
    rows.loc[volume_rows.index, "volume_rank"] = volume_rows["volume_rank"]
    base_mask = rows["valid_band"] & rows["risk_clean"]
    variants = {}
    for fill in ("guarded_fill_low25", "guarded_fill_mid50", "guarded_fill_high75"):
        variants[fill] = {
            "current_nonred": describe(rows, base_mask & rows["nonred_gate"], fill),
            "current_nonred_full_ma_lane": describe(rows, base_mask & rows["nonred_gate"] & rows["full_ma_lane"], fill),
            "current_nonred_active_lane": describe(rows, base_mask & rows["nonred_gate"] & rows["active_lane"], fill),
            "current_strict_3000": describe(rows, base_mask & rows["strict_3000_gate"], fill),
            "volume_nonred": describe(rows, base_mask & rows["nonred_gate"] & rows["effective_volume"], fill),
            "volume_15_nonred": describe(rows, base_mask & rows["nonred_gate"] & rows["volume_trigger_15"], fill),
            "recent_volume_3d_nonred": describe(rows, base_mask & rows["nonred_gate"] & rows["recent_volume_3d"], fill),
            "recent_volume_3d_15_nonred": describe(rows, base_mask & rows["nonred_gate"] & rows["recent_volume_3d_15"], fill),
            "volume_strict_3000": describe(rows, base_mask & rows["strict_3000_gate"] & rows["effective_volume"], fill),
        }
    summary = {
        "start": args.start, "end": args.end, "event_count": int(len(rows)),
        "definitions": {
            "candidate_pool": "exact scanner parity: shared hard exclusions plus full-MA/activity dual-lane selection, at most 10 per day",
            "risk_clean": "scanner risk_tier=A after current pushdozer filters",
            "effective_volume": "Vt/Vt-1>=1.8, Vt/median(previous5)>=1.5, bullish, close_location>=0.60, close>MA5, bias<=7%",
            "guarded_fill": "next open<=planned limit and next open>=signal MA5*0.98",
            "strict_3000_gate": "limit-down<=10 and advancers>=3000 on signal day",
        },
        "variants": variants,
        "limitations": [
            "No 9:15-9:25 auction order-book data; official daily open is only a clearing-price proxy.",
            "No fees/slippage; current-listed stock universe causes survivorship and historical-ST bias.",
            "Half-month window is diagnostic, not statistically sufficient for parameter validation.",
        ],
    }
    out_dir = args.output_dir or root / "技能数据" / "运行记录" / "策略回测"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"pushdozer_variants_{args.start}_{args.end}"
    rows.to_csv(out_dir / f"{stem}.csv", index=False, encoding="utf-8-sig")
    (out_dir / f"{stem}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": summary, "csv": str(out_dir / f"{stem}.csv"), "json": str(out_dir / f"{stem}.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
