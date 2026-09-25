#!/usr/bin/env python3
"""Persistent research memory for catalyst lifecycles.

The event map describes *what* an event is.  This module keeps the longitudinal
record that must survive event-map rebuilds: thesis changes, evidence updates,
invalidations and price reactions anchored to a dated observation.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Any, Iterable


SCHEMA_SQL = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS catalyst_observations(
  observation_id INTEGER PRIMARY KEY,
  observation_key TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL,
  event_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN (
    'thesis','evidence','milestone','market_response','invalidation','outcome','inference'
  )),
  summary TEXT NOT NULL,
  source_grade TEXT NOT NULL CHECK(source_grade IN (
    'official','primary','secondary','market_data','user_material','inference'
  )),
  source_ref TEXT,
  related_codes_json TEXT NOT NULL DEFAULT '[]',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_catalyst_obs_event
  ON catalyst_observations(source,event_id,observed_at);

CREATE TABLE IF NOT EXISTS catalyst_price_responses(
  response_id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  event_id TEXT NOT NULL,
  anchor_label TEXT NOT NULL,
  anchor_date TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  company_name TEXT,
  benchmark_code TEXT NOT NULL,
  first_trade_date TEXT,
  as_of TEXT NOT NULL,
  response_json TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  UNIQUE(source,event_id,anchor_label,anchor_date,ts_code,as_of)
);
CREATE INDEX IF NOT EXISTS idx_catalyst_response_event
  ON catalyst_price_responses(source,event_id,anchor_date);

CREATE TABLE IF NOT EXISTS catalyst_intake_queue(
  intake_id INTEGER PRIMARY KEY,
  received_at TEXT NOT NULL,
  raw_summary TEXT NOT NULL,
  source_grade TEXT NOT NULL CHECK(source_grade IN (
    'official','primary','secondary','market_data','user_material','inference'
  )),
  source_ref TEXT,
  candidate_source TEXT,
  candidate_event_id TEXT,
  status TEXT NOT NULL CHECK(status IN ('pending','linked','new_event','rejected')),
  resolution_notes TEXT,
  created_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_catalyst_intake_status
  ON catalyst_intake_queue(status,received_at);
"""


KINDS = {"thesis", "evidence", "milestone", "market_response", "invalidation", "outcome", "inference"}
GRADES = {"official", "primary", "secondary", "market_data", "user_material", "inference"}


def connect(path: Path | str) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(target)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA_SQL)
    return con


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def record_observation(
    con: sqlite3.Connection,
    *,
    source: str,
    event_id: str,
    observed_at: str,
    kind: str,
    summary: str,
    source_grade: str,
    source_ref: str = "",
    related_codes: Iterable[str] = (),
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Append an immutable observation; exact duplicates are idempotent."""
    if kind not in KINDS:
        raise ValueError(f"unsupported observation kind: {kind}")
    if source_grade not in GRADES:
        raise ValueError(f"unsupported source grade: {source_grade}")
    date.fromisoformat(observed_at[:10])
    canonical = "\x1f".join((source, event_id, observed_at, kind, summary, source_ref))
    key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    cur = con.execute(
        """INSERT OR IGNORE INTO catalyst_observations(
             observation_key,source,event_id,observed_at,kind,summary,source_grade,
             source_ref,related_codes_json,metadata_json,created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (key, source, event_id, observed_at, kind, summary, source_grade, source_ref,
         _json(sorted(set(related_codes))), _json(metadata or {}),
         datetime.now().isoformat(timespec="seconds")),
    )
    con.commit()
    return cur.rowcount == 1


def observations(con: sqlite3.Connection, source: str, event_id: str) -> list[dict[str, Any]]:
    rows = con.execute(
        """SELECT observed_at,kind,summary,source_grade,source_ref,
                  related_codes_json,metadata_json
           FROM catalyst_observations
           WHERE source=? AND event_id=? ORDER BY observed_at,observation_id""",
        (source, event_id),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["related_codes"] = json.loads(item.pop("related_codes_json"))
        item["metadata"] = json.loads(item.pop("metadata_json"))
        out.append(item)
    return out


def record_intake(
    con: sqlite3.Connection, *, received_at: str, raw_summary: str,
    source_grade: str = "user_material", source_ref: str = "",
    candidate_source: str = "", candidate_event_id: str = "",
) -> int:
    """Preserve an unmatched or ambiguous material item for later attribution."""
    if source_grade not in GRADES:
        raise ValueError(f"unsupported source grade: {source_grade}")
    date.fromisoformat(received_at[:10])
    cur = con.execute(
        """INSERT INTO catalyst_intake_queue(
             received_at,raw_summary,source_grade,source_ref,candidate_source,
             candidate_event_id,status,created_at
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (received_at, raw_summary, source_grade, source_ref, candidate_source,
         candidate_event_id, "pending", datetime.now().isoformat(timespec="seconds")),
    )
    con.commit()
    return int(cur.lastrowid)


def intake_queue(con: sqlite3.Connection, status: str = "pending") -> list[dict[str, Any]]:
    return [dict(row) for row in con.execute(
        """SELECT * FROM catalyst_intake_queue WHERE status=?
           ORDER BY received_at,intake_id""", (status,)
    ).fetchall()]


def resolve_intake(
    con: sqlite3.Connection, intake_id: int, *, status: str,
    candidate_source: str = "", candidate_event_id: str = "",
    resolution_notes: str = "",
) -> bool:
    if status not in {"linked", "new_event", "rejected"}:
        raise ValueError("resolution status must be linked, new_event or rejected")
    cur = con.execute(
        """UPDATE catalyst_intake_queue
           SET status=?,candidate_source=?,candidate_event_id=?,resolution_notes=?,resolved_at=?
           WHERE intake_id=? AND status='pending'""",
        (status, candidate_source, candidate_event_id, resolution_notes,
         datetime.now().isoformat(timespec="seconds"), intake_id),
    )
    con.commit()
    return cur.rowcount == 1


def _ordered(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    clean = []
    for row in rows:
        try:
            close = float(row["close"])
        except (KeyError, TypeError, ValueError):
            continue
        clean.append({"trade_date": str(row["trade_date"]).replace("-", ""), "close": close})
    return sorted(clean, key=lambda x: x["trade_date"])


def calculate_price_response(
    stock_rows: Iterable[dict[str, Any]],
    benchmark_rows: Iterable[dict[str, Any]],
    anchor_date: str,
    horizons: tuple[int, ...] = (0, 1, 3, 5, 10, 20),
) -> dict[str, Any]:
    """Calculate close-to-close cumulative reaction from the pre-anchor close.

    T+0 is the first trading day on or after ``anchor_date``. Missing future
    horizons remain null; they are never filled with the latest available day.
    """
    anchor = anchor_date.replace("-", "")
    stock = _ordered(stock_rows)
    bench = _ordered(benchmark_rows)
    common = sorted(set(r["trade_date"] for r in stock) & set(r["trade_date"] for r in bench))
    start_candidates = [d for d in common if d >= anchor]
    if not start_candidates:
        return {"status": "not_started", "anchor_date": anchor_date, "horizons": {}}
    first = start_candidates[0]
    first_idx = common.index(first)
    if first_idx == 0:
        return {"status": "missing_baseline", "anchor_date": anchor_date, "first_trade_date": first, "horizons": {}}
    by_stock = {r["trade_date"]: r["close"] for r in stock}
    by_bench = {r["trade_date"]: r["close"] for r in bench}
    base_day = common[first_idx - 1]
    s0, b0 = by_stock[base_day], by_bench[base_day]
    values: dict[str, Any] = {}
    available_after = common[first_idx:]
    path_stock = []
    path_excess = []
    for offset in horizons:
        key = f"t{offset}"
        if offset >= len(available_after):
            values[key] = None
            continue
        day = available_after[offset]
        sr = (by_stock[day] / s0 - 1) * 100
        br = (by_bench[day] / b0 - 1) * 100
        values[key] = {"trade_date": day, "return_pct": round(sr, 4),
                       "benchmark_pct": round(br, 4), "excess_pct": round(sr - br, 4)}
    for day in available_after[: max(horizons) + 1]:
        sr = (by_stock[day] / s0 - 1) * 100
        br = (by_bench[day] / b0 - 1) * 100
        path_stock.append(sr)
        path_excess.append(sr - br)
    return {
        "status": "ok", "anchor_date": anchor_date, "baseline_trade_date": base_day,
        "first_trade_date": first, "observed_sessions": len(available_after),
        "horizons": values,
        "max_return_pct": round(max(path_stock), 4) if path_stock else None,
        "min_return_pct": round(min(path_stock), 4) if path_stock else None,
        "max_excess_pct": round(max(path_excess), 4) if path_excess else None,
        "min_excess_pct": round(min(path_excess), 4) if path_excess else None,
    }


def store_price_response(
    con: sqlite3.Connection, *, source: str, event_id: str, anchor_label: str,
    anchor_date: str, ts_code: str, company_name: str, benchmark_code: str,
    as_of: str, response: dict[str, Any],
) -> None:
    con.execute(
        """INSERT OR REPLACE INTO catalyst_price_responses(
             source,event_id,anchor_label,anchor_date,ts_code,company_name,
             benchmark_code,first_trade_date,as_of,response_json,recorded_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (source, event_id, anchor_label, anchor_date, ts_code, company_name,
         benchmark_code, response.get("first_trade_date"), as_of, _json(response),
         datetime.now().isoformat(timespec="seconds")),
    )
    con.commit()


def response_summary(con: sqlite3.Connection, source: str, event_id: str) -> list[dict[str, Any]]:
    rows = con.execute(
        """SELECT r.* FROM catalyst_price_responses r
           JOIN (
             SELECT source,event_id,anchor_label,anchor_date,ts_code,max(as_of) AS latest_as_of
             FROM catalyst_price_responses WHERE source=? AND event_id=?
             GROUP BY source,event_id,anchor_label,anchor_date,ts_code
           ) latest
           ON r.source=latest.source AND r.event_id=latest.event_id
          AND r.anchor_label=latest.anchor_label AND r.anchor_date=latest.anchor_date
          AND r.ts_code=latest.ts_code AND r.as_of=latest.latest_as_of
           ORDER BY r.anchor_date,r.anchor_label,r.ts_code""", (source, event_id)
    ).fetchall()
    return [{**dict(row), "response": json.loads(row["response_json"])} for row in rows]


def basket_medians(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for horizon in (0, 1, 3, 5, 10, 20):
        vals = []
        for row in rows:
            point = row.get("response", {}).get("horizons", {}).get(f"t{horizon}")
            if point is not None:
                vals.append(float(point["excess_pct"]))
        result[f"t{horizon}_median_excess_pct"] = round(median(vals), 4) if vals else None
    return result
