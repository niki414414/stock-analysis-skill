#!/usr/bin/env python3
"""Persistent, auditable reactivation ledger for catalyst windows."""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from skills.shared.paths import workspace_root


WORKSPACE_ROOT = workspace_root()
DEFAULT_LEDGER = WORKSPACE_ROOT / "技能数据" / "catalyst_activation_ledger.json"


def load_ledger(path: Path | str = DEFAULT_LEDGER) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def confirmed_activation_dates(path: Path | str = DEFAULT_LEDGER) -> dict[tuple[str, str], date]:
    result: dict[tuple[str, str], date] = {}
    for row in load_ledger(path):
        if row.get("status") != "confirmed":
            continue
        try:
            parsed = date.fromisoformat(str(row.get("activation_date")))
        except ValueError:
            continue
        key = (str(row.get("source", "tech")), str(row.get("event_id", "")))
        if key[1] and (key not in result or parsed > result[key]):
            result[key] = parsed
    return result


def record_activation(record: dict[str, Any], path: Path | str = DEFAULT_LEDGER) -> bool:
    """Upsert one activation by source/event/date; return True when ledger changed."""
    target = Path(path)
    rows = load_ledger(target)
    normalized = {
        **record,
        "source": str(record.get("source", "tech")),
        "event_id": str(record.get("event_id", "")),
        "activation_date": str(record.get("activation_date", "")),
        "status": str(record.get("status", "confirmed")),
        "recorded_at": str(record.get("recorded_at") or datetime.now().isoformat(timespec="seconds")),
    }
    key = (normalized["source"], normalized["event_id"], normalized["activation_date"])
    changed = False
    for index, row in enumerate(rows):
        row_key = (str(row.get("source", "tech")), str(row.get("event_id", "")), str(row.get("activation_date", "")))
        if row_key == key:
            if row != normalized:
                rows[index] = normalized
                changed = True
            break
    else:
        rows.append(normalized)
        changed = True
    if changed:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, target)
    return changed
