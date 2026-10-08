#!/usr/bin/env python3
"""No-lookahead replay helper for market_structure_v2.

Fetches one index history once, then rebuilds each requested trade date from a
strictly truncated slice.  It is an audit tool, not a trading backtest.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path


_CODE_ROOT = Path(__file__).resolve().parents[3]
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))
from skills.shared.paths import repo_root, workspace_root
from skills.shared.datasource import get_pro

WORKSPACE_ROOT = workspace_root()
REPO_ROOT = repo_root()
sys.path.insert(0, str(REPO_ROOT / "skills/market-outlook/scripts"))
from market_structure_v2 import build_market_structure_v2  # noqa: E402
from market_review_renderer_v2 import build_structure_decision_card  # noqa: E402


def compact(snapshot: dict) -> dict:
    box = snapshot.get("primary_box", {})

    def brief(level):
        if not level:
            return None
        return {
            "zone": level.get("zone"),
            "touch_count": level.get("touch_count"),
            "touch_dates": level.get("touch_dates"),
            "strength_score": level.get("strength_score"),
            "source_types": level.get("source_types"),
        }

    return {
        "trade_date": snapshot.get("trade_date"),
        "current": snapshot.get("current"),
        "atr14": snapshot.get("atr14"),
        "box_id": box.get("box_id"),
        "support": brief(box.get("support")),
        "resistance": brief(box.get("resistance")),
        "secondary_support": brief(box.get("secondary_support")),
        "secondary_resistance": brief(box.get("secondary_resistance")),
        "width_atr": box.get("width_atr"),
        "location_ratio": box.get("location_ratio"),
        "tradable_room": snapshot.get("tradable_room"),
        "revision_from_previous": snapshot.get("revision_from_previous"),
        "decision_card": build_structure_decision_card(snapshot),
    }


def replay(frame, index_name: str, dates: list[str]) -> list[dict]:
    history = frame.sort_values("trade_date").reset_index(drop=True)
    previous = None
    out = []
    for date in dates:
        visible = history[history["trade_date"].astype(str) <= str(date)]
        if visible.empty or str(visible.iloc[-1]["trade_date"]) != str(date):
            out.append({"trade_date": date, "status": "not_a_trade_date_or_missing"})
            continue
        snapshot = build_market_structure_v2(
            visible, index_name=index_name,
            as_of_timestamp=f"{date[:4]}-{date[4:6]}-{date[6:]}T15:00:00+08:00",
            previous_snapshot=previous,
        )
        out.append(compact(snapshot))
        previous = snapshot
    return out


def replay_many(frames: dict[str, object], dates: list[str]) -> dict[str, list[dict]]:
    """Run the same no-lookahead replay independently for several indices."""
    return {name: replay(frame, name, dates) for name, frame in frames.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="市场结构V2无前视逐日回放")
    parser.add_argument(
        "--ts-code", default="000001.SH",
        help="指数代码；多个代码用逗号分隔，需与--index-name一一对应",
    )
    parser.add_argument(
        "--index-name", default="上证指数",
        help="指数名称；多个名称用逗号分隔",
    )
    parser.add_argument("--dates", required=True, help="逗号分隔的YYYYMMDD交易日")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    dates = [item.strip() for item in args.dates.split(",") if item.strip()]
    earliest = datetime.strptime(min(dates), "%Y%m%d") - timedelta(days=420)
    latest = datetime.strptime(max(dates), "%Y%m%d")
    names = [item.strip() for item in args.index_name.split(",") if item.strip()]
    codes = [item.strip() for item in args.ts_code.split(",") if item.strip()]
    if len(names) != len(codes):
        raise ValueError("--ts-code 与 --index-name 的数量必须一致")
    pro = get_pro()
    frames = {
        name: pro.index_daily(
            ts_code=code,
            start_date=earliest.strftime("%Y%m%d"), end_date=latest.strftime("%Y%m%d"),
        )
        for name, code in zip(names, codes)
    }
    result = replay(frames[names[0]], names[0], dates) if len(names) == 1 else replay_many(frames, dates)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for row in result:
            print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
