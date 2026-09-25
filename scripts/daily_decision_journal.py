#!/usr/bin/env python3
"""Append-only journal for market judgments, user decisions and later outcomes."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(os.path.abspath(os.path.expanduser(
    os.environ.get("TZ_CODEX_HOME", "~/Desktop/tz-codex")
)))
DEFAULT_JOURNAL = WORKSPACE_ROOT / "技能数据" / "decision_journal.jsonl"


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        existing = {
            json.loads(line).get("event_id") for line in handle if line.strip()
        }
        if row["event_id"] in existing:
            raise ValueError(f"记录已存在，不重复追加: {row['event_id']}")
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _trade_date(review: dict, fallback: str | None = None) -> str:
    dates = review.get("data_dates", {}).get("price_dates", []) or []
    value = fallback or review.get("as_of_date") or (max(dates) if dates else "")
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 8:
        raise ValueError("无法确定8位交易日期，请提供 --trade-date YYYYMMDD")
    return digits


def _review_trade_date(review_id: str | None) -> str | None:
    parts = str(review_id or "").split(":")
    if len(parts) >= 3 and parts[0] == "review" and len(parts[1]) == 8 and parts[1].isdigit():
        return parts[1]
    return None


def _direction_rows(review: dict) -> list[dict]:
    if str(review.get("schema_version", "")).startswith("market-brief-"):
        return [{
            "view": row.get("role"), "rank_at_record": rank,
            "sector": row.get("name"),
            "state": row.get("price_map", {}).get("stage"),
            "confirmation": row.get("price_map", {}).get("confirmation"),
            "invalidation": row.get("price_map", {}).get("invalidation"),
            "evidence_snapshot": row,
        } for rank, row in enumerate(review.get("sectors", []), 1)]
    config = {
        "trend": ("趋势观察", "5-10日"),
        "rotation": ("轮动观察", "1-3日"),
        "event_preheat": ("预热观察", "3-5日"),
    }
    output = []
    for key, (label, horizon) in config.items():
        for rank, row in enumerate(review.get("sector_views", {}).get(key, []) or [], 1):
            output.append({
                "view": label,
                "horizon": horizon,
                "rank_at_record": rank,
                "sector": row.get("sector"),
                "state": row.get("state") or row.get("preheat_state"),
                "confidence": row.get("coverage_confidence") or row.get("confidence"),
                "confirmation": row.get("confirmation") or row.get("next_confirmation"),
                "invalidation": row.get("invalidation") or row.get("failure_condition"),
                "evidence_snapshot": row.get("evidence", {}),
            })
    return output


def record_review(review_path: str, journal: Path, trade_date: str | None = None) -> dict:
    review = _read_json(review_path)
    date = _trade_date(review, trade_date)
    digest = hashlib.sha256(
        json.dumps(review, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    review_id = f"review:{date}:{digest[:12]}"
    row = {
        "schema_version": "1.0",
        "event_id": review_id,
        "event_type": "model_review",
        "recorded_at": datetime.now().isoformat(),
        "trade_date": date,
        "review_id": review_id,
        "source_path": str(Path(review_path).resolve()),
        "source_sha256": digest,
        "output_mode": review.get("presentation", {}).get("mode", "market_brief_v2"
            if str(review.get("schema_version", "")).startswith("market-brief-") else "unknown"),
        "data_status": review.get("status"),
        "market_judgment": review.get("market", {}) if str(review.get("schema_version", "")).startswith("market-brief-")
            else review.get("market", {}).get("environment", {}),
        "directions": _direction_rows(review),
        "strategy_guidance": review.get("decision_routes", {}).get("strategy_guidance", {}),
        "missing_evidence": review.get("data_gaps", []) + review.get("checks", {}).get("missing_required_sections", []),
        "review_snapshot": review,
        "model_claim_boundary": "记录当时判断，不代表用户已操作，也不代表未来结果",
    }
    _append(journal, row)
    return row


def _parse_action(value: str) -> dict:
    parts = [part.strip() for part in value.split("|", 4)]
    if len(parts) < 4 or not parts[0] or not parts[2] or not parts[3]:
        raise ValueError("--action格式应为 代码|名称|买/卖/加/减|策略身份|原因（原因可省略）")
    while len(parts) < 5:
        parts.append("")
    return dict(zip(("code", "name", "action", "strategy_identity", "reason"), parts))


def record_decision(args, journal: Path) -> dict:
    actions = [_parse_action(value) for value in args.action]
    if args.status == "operated" and not actions:
        raise ValueError("status=operated时至少需要一条--action")
    if args.status != "operated" and actions:
        raise ValueError("只有status=operated时可以记录--action")
    date = _trade_date({}, args.trade_date)
    payload = {
        "schema_version": "1.0",
        "event_type": "user_decision",
        "recorded_at": datetime.now().isoformat(),
        "trade_date": date,
        "review_id": args.review_id,
        "decision_status": args.status,
        "actions": actions,
        "user_note": args.note or "",
        "inference_prohibited": args.status == "unknown",
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    payload["event_id"] = f"decision:{date}:{digest}"
    _append(journal, payload)
    return payload


def record_outcome(args, journal: Path) -> dict:
    payload = _read_json(args.payload)
    if args.horizon not in {3, 5, 10}:
        raise ValueError("结果观察窗口只允许3、5或10个交易日")
    row = {
        "schema_version": "1.0",
        "event_type": "outcome_review",
        "recorded_at": datetime.now().isoformat(),
        "review_id": args.review_id,
        "trade_date": _review_trade_date(args.review_id),
        "as_of_date": _trade_date({}, args.as_of_date),
        "horizon_trading_days": args.horizon,
        "prediction_result": payload.get("prediction_result", "unresolved"),
        "execution_result": payload.get("execution_result", "not_applicable"),
        "market_outcome": payload.get("market_outcome", {}),
        "direction_outcomes": payload.get("direction_outcomes", []),
        "decision_outcomes": payload.get("decision_outcomes", []),
        "evidence_gaps": payload.get("evidence_gaps", []),
        "notes": payload.get("notes", ""),
        "separation_rule": "预测对错与用户交易盈亏分开评价",
    }
    row["event_id"] = f"outcome:{args.review_id}:{args.horizon}d"
    _append(journal, row)
    return row


def summary(journal: Path, trade_date: str | None = None) -> dict:
    rows = _rows(journal)
    if trade_date:
        rows = [row for row in rows if (
            row.get("trade_date") == trade_date
            or row.get("as_of_date") == trade_date
            or (row.get("event_type") == "outcome_review"
                and _review_trade_date(row.get("review_id")) == trade_date)
        )]
    return {
        "journal": str(journal),
        "records": len(rows),
        "model_reviews": sum(row.get("event_type") == "model_review" for row in rows),
        "user_decisions": sum(row.get("event_type") == "user_decision" for row in rows),
        "outcome_reviews": sum(row.get("event_type") == "outcome_review" for row in rows),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="判断—实际决策—后续结果闭环账本")
    parser.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    sub = parser.add_subparsers(dest="cmd", required=True)
    review = sub.add_parser("record-review", help="保存统一复盘的原始判断快照")
    review.add_argument("--review", required=True)
    review.add_argument("--trade-date")
    decision = sub.add_parser("record-decision", help="保存用户实际决策；不得由模型猜测")
    decision.add_argument("--trade-date", required=True)
    decision.add_argument("--review-id")
    decision.add_argument("--status", choices=("operated", "no_operation", "unknown"), required=True)
    decision.add_argument("--action", action="append", default=[])
    decision.add_argument("--note")
    outcome = sub.add_parser("record-outcome", help="保存3/5/10交易日结果复核")
    outcome.add_argument("--review-id", required=True)
    outcome.add_argument("--horizon", type=int, choices=(3, 5, 10), required=True)
    outcome.add_argument("--as-of-date", required=True)
    outcome.add_argument("--payload", required=True)
    show = sub.add_parser("show", help="查看账本摘要或某日记录")
    show.add_argument("--trade-date")
    args = parser.parse_args()
    journal = Path(args.journal)
    if args.cmd == "record-review":
        result = record_review(args.review, journal, args.trade_date)
    elif args.cmd == "record-decision":
        result = record_decision(args, journal)
    elif args.cmd == "record-outcome":
        result = record_outcome(args, journal)
    else:
        result = summary(journal, args.trade_date)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
