import importlib.util
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills/market-outlook/scripts/market_framework_backtest.py"
)
spec = importlib.util.spec_from_file_location("market_framework_backtest", SCRIPT)
backtest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backtest)


class MarketFrameworkBacktestTests(unittest.TestCase):
    def test_anchor_selection_preserves_forward_window(self):
        days = [f"202601{day:02d}" for day in range(1, 31)]
        anchors = backtest.choose_anchors(days, samples=4, forward=10)
        self.assertEqual(len(anchors), 4)
        self.assertLessEqual(anchors[-1], days[-11])

    def test_single_anchor_does_not_divide_by_zero(self):
        days = [f"202601{day:02d}" for day in range(1, 21)]
        self.assertEqual(backtest.choose_anchors(days, 1, 10), [days[-11]])

    def test_diagnose_flags_wide_range_and_non_predictive_hotspot(self):
        index = {"summary": {"5": {
            "boundary_touch_rate_pct": 10, "containment_rate_pct": 90,
        }}}
        boards = {"summary": {"ths": {"5": {"mean_excess_pct": -0.2}}}}
        findings = backtest.diagnose(index, boards)
        self.assertEqual({row["module"] for row in findings}, {
            "index_range", "ths_discovery",
        })


if __name__ == "__main__":
    unittest.main()
