#!/usr/bin/env python3
"""历史回放版信息漏检雷达，不修改正式事件库。"""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def detect_market_anomalies(
    sectors: Iterable[dict],
    universe_size: int,
    min_pct: float = 3.0,
    min_volume_ratio: float = 1.2,
    top_quantile: float = 0.2,
) -> list[dict]:
    max_rank = max(1, round(universe_size * top_quantile))
    return [
        row
        for row in sectors
        if float(row["pct_chg"]) >= min_pct
        and float(row["volume_ratio"]) >= min_volume_ratio
        and int(row["rank"]) <= max_rank
    ]


def read_event_rows(event_root: Path) -> list[str]:
    parts: list[str] = []
    if not event_root.exists():
        return []
    for path in sorted(event_root.glob("*.csv")):
        if not path.name.startswith(("events_", "signals_early_", "forward_", "corrections_")):
            continue
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.reader(handle):
                parts.append(" ".join(row))
    return parts


def _dates_in_row(row: str) -> list[datetime]:
    found = []
    for year, month, day in re.findall(r"(20\d{2})[-/]?(\d{2})[-/]?(\d{2})", row):
        try:
            found.append(datetime(int(year), int(month), int(day)))
        except ValueError:
            continue
    return found


def has_recent_explanation(
    rows: Iterable[str],
    entity_keywords: Iterable[str],
    change_keywords: Iterable[str],
    trade_date: str,
    lookback_days: int = 3,
) -> bool:
    cutoff = datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=lookback_days)
    entities = [value.lower() for value in entity_keywords]
    changes = [value.lower() for value in change_keywords]
    for row in rows:
        normalized = row.lower()
        if not any(value in normalized for value in entities):
            continue
        if not any(value in normalized for value in changes):
            continue
        dates = _dates_in_row(row)
        if dates and max(dates) >= cutoff:
            return True
    return False


def audit(replay: dict, rules: dict, event_rows: Iterable[str]) -> list[dict]:
    anomalies = detect_market_anomalies(
        replay["sectors"], int(replay.get("universe_size", 31))
    )
    anomaly_by_name = {row["name"]: row for row in anomalies}
    findings: list[dict] = []

    for signal in replay.get("signals", []):
        rule = rules.get(signal.get("variable"))
        if not rule:
            continue
        covered = has_recent_explanation(
            event_rows,
            rule["entity_keywords"],
            rule["change_keywords"],
            replay["trade_date"],
        )
        for sector in rule["target_sectors"]:
            market = anomaly_by_name.get(sector)
            if not market:
                continue
            findings.append(
                {
                    "trade_date": replay["trade_date"],
                    "signal_id": signal["signal_id"],
                    "signal_title": signal["title"],
                    "variable": signal["variable"],
                    "sector": sector,
                    "sector_rank": market["rank"],
                    "pct_chg": market["pct_chg"],
                    "volume_ratio": market["volume_ratio"],
                    "event_map_covered": covered,
                    "coverage_gap": not covered,
                    "mechanism": rule["mechanism"],
                    "target_themes": rule["target_themes"],
                    "verification": signal.get("verification", ""),
                }
            )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--event-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    findings = audit(
        load_json(args.replay),
        load_json(args.rules),
        read_event_rows(args.event_root),
    )
    rendered = json.dumps(findings, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if findings else 2


if __name__ == "__main__":
    raise SystemExit(main())
