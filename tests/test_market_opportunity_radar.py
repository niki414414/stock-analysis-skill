import importlib.util
import json
import os
import unittest
from pathlib import Path

import pandas as pd


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "market-outlook"
    / "scripts"
    / "market_opportunity_radar.py"
)
spec = importlib.util.spec_from_file_location("market_opportunity_radar", SCRIPT)
radar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(radar)


class MarketOpportunityRadarTests(unittest.TestCase):
    def test_normalize_code_preserves_leading_zero(self):
        self.assertEqual(radar.normalize_code("2602"), "002602")
        self.assertEqual(radar.normalize_code(2028.0), "002028")
        self.assertEqual(radar.normalize_code("603444"), "603444")

    def test_event_match_uses_category_not_incidental_text(self):
        events = pd.DataFrame([
            {
                "事件ID": "GAME-1",
                "一级赛道": "游戏",
                "二级事件": "版号发放",
                "事件名称": "游戏版号",
                "主要影响方向": "游戏公司",
                "备注": "",
            },
            {
                "事件ID": "EXPORT-1",
                "一级赛道": "消费/出口",
                "二级事件": "跨境出口",
                "事件名称": "出口改善",
                "主要影响方向": "纺织服饰可能间接受益",
                "备注": "",
            },
        ])
        active = {
            "GAME-1": {"bucket": "red", "final_score": 5, "action": "观察"},
            "EXPORT-1": {"bucket": "red", "final_score": 5, "action": "观察"},
        }
        aliases = {"传媒": ["游戏", "版号发放"], "纺织服饰": ["纺织", "服饰"]}
        media = radar.find_matching_events("传媒", events, active, aliases)
        textile = radar.find_matching_events("纺织服饰", events, active, aliases)
        self.assertEqual([row["event_id"] for row in media], ["GAME-1"])
        self.assertEqual(textile, [])

    def test_sector_name_only_matches_primary_category(self):
        events = pd.DataFrame([{
            "事件ID": "BAT-1",
            "一级赛道": "电池储能",
            "二级事件": "环保型电解液",
            "事件名称": "电解液涨价",
            "主要影响方向": "材料公司",
        }])
        active = {
            "BAT-1": {"bucket": "red", "final_score": 5, "action": "观察"}
        }
        matches = radar.find_matching_events(
            "环保", events, active, {"环保": ["环保"]}
        )
        self.assertEqual(matches, [])

    def test_abnormal_structure_separates_structure_from_cause(self):
        state = {
            "breadth_today": {"trade_date": "20260728", "advance_pct": 48.5},
            "style_index_comparison": {
                "沪深300": {"pct_today": -2.83},
                "科创50": {"pct_today": -6.33},
                "创业板指": {"pct_today": -7.35},
            },
            "total_turnover": {"chg_vs_avg_pct": -14.2},
        }
        result = radar.diagnose_abnormal_structure(state)
        self.assertTrue(result["triggered"])
        self.assertEqual(result["structure_label"], "科技风格冲击结构")
        self.assertIn("不是新闻原因", result["causal_warning"])

    def test_company_candidates_mark_text_fallback(self):
        matches = [{
            "event_id": "GAME-1",
            "company_text": "世纪华通、三七互娱",
        }]
        pool = pd.DataFrame([
            {"公司名称": "世纪华通", "关联事件ID": "", "置信度": "中"},
            {"公司名称": "三七互娱", "关联事件ID": "GAME-1", "置信度": "高"},
        ])
        code_map = pd.DataFrame([
            {"name": "世纪华通", "code": "002602"},
            {"name": "三七互娱", "code": "002555"},
        ])
        rows, _ = radar.company_candidates(matches, pool, code_map)
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(
            by_name["世纪华通"]["link_source"], "event_text_fallback"
        )
        self.assertEqual(
            by_name["三七互娱"]["link_source"], "structured_event_link"
        )


if __name__ == "__main__":
    unittest.main()
