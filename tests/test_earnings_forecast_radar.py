import importlib.util
import unittest
from pathlib import Path

import pandas as pd


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "market-outlook"
    / "scripts"
    / "earnings_forecast_radar.py"
)
spec = importlib.util.spec_from_file_location("earnings_forecast_radar", SCRIPT)
radar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(radar)


class EarningsForecastRadarTests(unittest.TestCase):
    def test_prefilter_uses_growth_lower_bound_and_absolute_profit(self):
        frame = pd.DataFrame([
            {
                "ts_code": "000001.SZ", "ann_date": "20260701",
                "end_date": "20260630", "type": "预增",
                "p_change_min": 50, "p_change_max": 100,
                "net_profit_min": 10000, "net_profit_max": 12000,
            },
            {
                "ts_code": "000002.SZ", "ann_date": "20260701",
                "end_date": "20260630", "type": "预增",
                "p_change_min": 20, "p_change_max": 500,
                "net_profit_min": 50000, "net_profit_max": 60000,
            },
            {
                "ts_code": "000003.SZ", "ann_date": "20260701",
                "end_date": "20260630", "type": "预增",
                "p_change_min": 300, "p_change_max": 500,
                "net_profit_min": 1000, "net_profit_max": 2000,
            },
        ])
        result = radar.prefilter_forecasts(frame, "20260630", 50, 10000)
        self.assertEqual(result["ts_code"].tolist(), ["000001.SZ"])

    def test_turnaround_still_requires_absolute_profit(self):
        frame = pd.DataFrame([
            {
                "ts_code": "000001.SZ", "ann_date": "20260701",
                "end_date": "20260630", "type": "扭亏",
                "p_change_min": None, "p_change_max": None,
                "net_profit_min": 15000, "net_profit_max": 18000,
            },
            {
                "ts_code": "000002.SZ", "ann_date": "20260701",
                "end_date": "20260630", "type": "扭亏",
                "p_change_min": None, "p_change_max": None,
                "net_profit_min": 2000, "net_profit_max": 3000,
            },
        ])
        result = radar.prefilter_forecasts(frame, "20260630", 50, 10000)
        self.assertEqual(result["ts_code"].tolist(), ["000001.SZ"])

    def test_one_off_income_cannot_enter_priority_categories(self):
        result = radar.classify_candidate({
            "summary": "",
            "change_reason": "出售股权产生投资收益，营业收入有所增长",
            "p_change_min": 200,
            "last_parent_net": 10000,
            "net_profit_min": 50000,
            "pre20_return_pct": 0,
            "post_announcement_return_pct": 1,
        })
        self.assertTrue(result["category"].startswith("C_"))
        self.assertIn("出售", result["one_off_risk"])

    def test_operating_growth_and_underreaction_is_category_a(self):
        result = radar.classify_candidate({
            "summary": "",
            "change_reason": "产品销量增长，产能利用率提升，毛利率改善",
            "p_change_min": 80,
            "last_parent_net": 30000,
            "net_profit_min": 50000,
            "pre20_return_pct": 4,
            "post_announcement_return_pct": 3,
            "above_ma20": True,
        })
        self.assertTrue(result["category"].startswith("A_"))
        self.assertEqual(result["research_action"], "进入六层人工核验")

    def test_large_price_move_is_already_traded(self):
        result = radar.classify_candidate({
            "summary": "",
            "change_reason": "主营产品销量增长，订单充足",
            "p_change_min": 100,
            "last_parent_net": 30000,
            "net_profit_min": 50000,
            "pre20_return_pct": 35,
            "post_announcement_return_pct": 5,
            "above_ma20": True,
        })
        self.assertTrue(result["category"].startswith("D_"))

    def test_large_negative_divergence_is_not_expectation_gap_priority(self):
        result = radar.classify_candidate({
            "summary": "",
            "change_reason": "主营产品销量增长，订单充足",
            "p_change_min": 100,
            "last_parent_net": 30000,
            "net_profit_min": 50000,
            "pre20_excess_vs_csi300_pct": 1,
            "post_excess_vs_csi300_pct": -15,
            "above_ma20": False,
        })
        self.assertTrue(result["category"].startswith("E_"))
        self.assertEqual(result["research_action"], "保留观察，不自动推荐")


if __name__ == "__main__":
    unittest.main()
