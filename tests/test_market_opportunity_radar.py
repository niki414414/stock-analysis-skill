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

    def test_food_alias_does_not_match_generic_consumer_events(self):
        events = pd.DataFrame([{
            "事件ID": "GAME-1",
            "一级赛道": "消费/出口",
            "二级事件": "游戏版号",
            "事件名称": "游戏版号下发",
            "主要影响方向": "游戏公司",
        }])
        active = {
            "GAME-1": {"bucket": "red", "final_score": 5, "action": "观察"}
        }
        matches = radar.find_matching_events(
            "食品饮料",
            events,
            active,
            {"食品饮料": ["消费/白酒", "白酒"]},
        )
        self.assertEqual(matches, [])

    def test_home_appliance_alias_does_not_match_export_consumer_events(self):
        events = pd.DataFrame([{
            "事件ID": "EXPORT-1",
            "一级赛道": "消费/出口",
            "二级事件": "跨境出口",
            "事件名称": "消费电子出口改善",
            "主要影响方向": "消费电子公司",
        }])
        active = {
            "EXPORT-1": {"bucket": "red", "final_score": 5, "action": "观察"}
        }
        matches = radar.find_matching_events(
            "家用电器", events, active, {"家用电器": ["家电"]}
        )
        self.assertEqual(matches, [])

    def test_auto_alias_does_not_treat_storage_battery_as_sector_catalyst(self):
        events = pd.DataFrame([{
            "事件ID": "BAT-1",
            "一级赛道": "电池储能",
            "二级事件": "储能集采",
            "事件名称": "储能系统集采",
            "主要影响方向": "储能电池公司",
        }])
        active = {
            "BAT-1": {"bucket": "red", "final_score": 5, "action": "观察"}
        }
        matches = radar.find_matching_events(
            "汽车",
            events,
            active,
            {"汽车": ["汽车", "新能源车"]},
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

    def test_sector_evidence_uses_relative_not_absolute_breadth(self):
        state, detail = radar.classify_sector_evidence(
            excess_5d=4.0,
            advance_ratio=80.0,
            market_advance_ratio=78.0,
            positive_flow_days_5d=4,
            cumulative_flow_yi_5d=20.0,
            latest_flow_yi=5.0,
            active_catalyst=False,
        )
        self.assertEqual(state, "research_candidate")
        self.assertFalse(detail["gates"]["relative_breadth"])
        self.assertTrue(detail["gates"]["persistent_flow"])

    def test_missing_moneyflow_never_becomes_zero_or_persistent(self):
        state, detail = radar.classify_sector_evidence(
            excess_5d=5.0,
            advance_ratio=90.0,
            market_advance_ratio=70.0,
            positive_flow_days_5d=None,
            cumulative_flow_yi_5d=None,
            latest_flow_yi=None,
            active_catalyst=False,
        )
        self.assertEqual(state, "research_candidate")
        self.assertFalse(detail["gates"]["persistent_flow"])

    def test_candidate_labels_require_independent_sources(self):
        rows = radar.merge_candidate_labels(
            quality_rows=[{
                "code": "600660",
                "name": "福耀玻璃",
                "tags": ["quality_core"],
            }],
            catalyst_rows=[],
            response_rows=[{
                "code": "600660.SH",
                "name": "福耀玻璃",
                "net_mf_yi": 4.2,
                "tags": ["market_response"],
            }],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["tags"], ["market_response", "quality_core"]
        )
        self.assertEqual(rows[0]["next_action"], "six_layer_priority")


if __name__ == "__main__":
    unittest.main()
