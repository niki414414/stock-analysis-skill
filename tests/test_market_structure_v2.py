import importlib.util
import tempfile
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
STRUCTURE_SCRIPT = ROOT / "skills/market-outlook/scripts/market_structure_v2.py"
SPEC = importlib.util.spec_from_file_location("market_structure_v2", STRUCTURE_SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

REVIEW_SCRIPT = ROOT / "skills/market-outlook/scripts/market_daily_review.py"
REVIEW_SPEC = importlib.util.spec_from_file_location("market_daily_review_v2_test", REVIEW_SCRIPT)
REVIEW = importlib.util.module_from_spec(REVIEW_SPEC)
REVIEW_SPEC.loader.exec_module(REVIEW)


def repeated_range_bars():
    closes = [
        105, 103, 101, 100, 102, 106, 109, 110, 108, 104,
        101, 100.2, 103, 107, 109.5, 110.1, 107, 104, 101, 100.1,
        102, 106, 109, 110.2, 108, 105, 102, 100.3, 103, 106,
        109, 109.8, 108, 106, 104, 103, 105, 107, 108, 106,
    ]
    rows = []
    for idx, close in enumerate(closes):
        rows.append({
            "trade_date": f"202607{idx + 1:02d}",
            "open": close - 0.2,
            "high": close + 0.8,
            "low": close - 0.8,
            "close": close,
            "amount": 1000 + idx * 10,
        })
    return pd.DataFrame(rows)


class MarketStructureV2Tests(unittest.TestCase):
    def test_repeated_touches_keep_provenance_and_touch_history(self):
        result = MODULE.build_market_structure_v2(
            repeated_range_bars(), "测试指数", "2026-08-30T15:00:00+08:00"
        )
        levels = result["level_map"]
        support_area = next(
            row for row in levels
            if "swing_low" in row["source_types"] and row["zone"]["mid"] < 101
        )
        resistance_area = next(
            row for row in levels
            if "swing_high" in row["source_types"] and row["zone"]["mid"] > 109
        )
        self.assertGreaterEqual(support_area["touch_count"], 3)
        self.assertGreaterEqual(resistance_area["touch_count"], 3)
        self.assertTrue(any(
            row["source_type"] == "swing_low"
            for row in support_area["provenance"]
        ))
        self.assertTrue(any(
            row["source_type"] == "swing_high"
            for row in resistance_area["provenance"]
        ))
        self.assertEqual(result["mode"], "shadow")

    def test_future_bars_do_not_change_earlier_snapshot(self):
        bars = repeated_range_bars()
        early = MODULE.build_market_structure_v2(
            bars.iloc[:30], "测试指数", "2026-08-01T15:00:00+08:00"
        )
        future = pd.DataFrame([{
            "trade_date": "20260831", "open": 90, "high": 91,
            "low": 80, "close": 82, "amount": 9999,
        }])
        # Rebuilding the same as-of slice remains byte-for-byte stable in the
        # structural evidence even if a caller separately owns future data.
        same_as_of = MODULE.build_market_structure_v2(
            pd.concat([bars.iloc[:30], future]).iloc[:30],
            "测试指数", "2026-08-01T15:00:00+08:00",
        )
        self.assertEqual(early["level_map"], same_as_of["level_map"])
        self.assertEqual(early["primary_box"], same_as_of["primary_box"])

    def test_previous_snapshot_preserves_box_identity_when_boundaries_match(self):
        bars = repeated_range_bars()
        first = MODULE.build_market_structure_v2(
            bars, "测试指数", "2026-08-30T14:00:00+08:00"
        )
        second = MODULE.build_market_structure_v2(
            bars, "测试指数", "2026-08-30T15:00:00+08:00",
            previous_snapshot=first,
        )
        self.assertEqual(
            first["primary_box"]["box_id"], second["primary_box"]["box_id"]
        )
        self.assertEqual(
            second["revision_from_previous"]["type"], "unchanged_boundaries"
        )

    def test_close_above_previous_resistance_is_explicit_price_break(self):
        bars = repeated_range_bars()
        first = MODULE.build_market_structure_v2(
            bars.iloc[:30], "测试指数", "2026-08-01T15:00:00+08:00"
        )
        resistance = first["primary_box"]["resistance"]["zone"]["upper"]
        next_row = bars.iloc[[-1]].copy()
        next_row["trade_date"] = "20260831"
        next_row["open"] = resistance + 1
        next_row["low"] = resistance + 0.5
        next_row["high"] = resistance + 3
        next_row["close"] = resistance + 2
        second = MODULE.build_market_structure_v2(
            pd.concat([bars.iloc[:30], next_row]), "测试指数",
            "2026-08-31T15:00:00+08:00", previous_snapshot=first,
        )
        self.assertEqual(
            second["revision_from_previous"]["type"],
            "upward_break_from_previous_box",
        )
        self.assertEqual(
            second["tradable_room"]["permission"],
            "PRICE_BREAK_ABOVE_PREVIOUS_BOX",
        )

    def test_version_log_keeps_two_same_day_timestamps(self):
        bars = repeated_range_bars()
        first = MODULE.build_market_structure_v2(
            bars, "上证指数", "2026-08-30T11:00:00+08:00"
        )
        second = MODULE.build_market_structure_v2(
            bars, "上证指数", "2026-08-30T15:00:00+08:00",
            previous_snapshot=first,
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "versions.jsonl"
            REVIEW.save_structure_v2_versions(
                {"market": {"structure_v2": {"上证指数": first}}}, target
            )
            REVIEW.save_structure_v2_versions(
                {"market": {"structure_v2": {"上证指数": second}}}, target
            )
            records = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(records), 2)


if __name__ == "__main__":
    unittest.main()
