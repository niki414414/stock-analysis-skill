import importlib.util
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STRUCTURE = load(
    "market_structure_v2_for_renderer",
    ROOT / "skills/market-outlook/scripts/market_structure_v2.py",
)
RENDERER = load(
    "market_review_renderer_v2",
    ROOT / "skills/market-outlook/scripts/market_review_renderer_v2.py",
)


class MarketReviewRendererV2Tests(unittest.TestCase):
    def test_card_has_concise_action_scenarios_and_derivation(self):
        closes = [100, 98, 96, 95, 97, 101, 104, 105, 103, 99] * 4
        bars = pd.DataFrame([{
            "trade_date": f"2026{index // 28 + 1:02d}{index % 28 + 1:02d}",
            "open": close, "high": close + 1, "low": close - 1,
            "close": close,
        } for index, close in enumerate(closes)])
        snapshot = STRUCTURE.build_market_structure_v2(
            bars, "测试指数", "2026-08-30T15:00:00+08:00"
        )
        card = RENDERER.build_structure_decision_card(snapshot)
        self.assertEqual(card["status"], "ready")
        self.assertIn("测试指数", card["headline"])
        self.assertIn("up", card["scenarios"])
        self.assertIn("down", card["scenarios"])
        self.assertTrue(card["derivation"]["raw_structure_available"])
        self.assertEqual(
            card["validation_status"],
            "frozen_shadow_output_waiting_for_blogger_and_market_outcome",
        )

    def test_error_snapshot_degrades_to_v1(self):
        card = RENDERER.build_structure_decision_card({
            "status": "error", "error": "boom"
        })
        self.assertEqual(card["status"], "degraded")
        self.assertIn("V1", card["headline"])


if __name__ == "__main__":
    unittest.main()
