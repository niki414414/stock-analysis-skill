import importlib.util
import json
import os
import sqlite3
import tempfile
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
    def test_nonfin_events_come_from_sqlite_primary_not_legacy_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "events.db"
            with sqlite3.connect(db_path) as con:
                con.execute("CREATE TABLE source_rows(source TEXT, table_name TEXT, row_key TEXT, ordinal INTEGER, row_json TEXT)")
                con.execute("INSERT INTO source_rows VALUES(?,?,?,?,?)", (
                    "nonfin", "events", "NEW-1", 1,
                    json.dumps({"事件ID": "NEW-1", "一级赛道": "航运", "事件名称": "新增事件"}, ensure_ascii=False),
                ))
                con.execute("INSERT INTO source_rows VALUES(?,?,?,?,?)", (
                    "nonfin", "mapping", "NEW-1", 1,
                    json.dumps({"事件ID": "NEW-1", "公司名称": "测试公司"}, ensure_ascii=False),
                ))
            events, mappings, source = radar.load_nonfin_tables(db_path)
            self.assertEqual(events.iloc[0]["事件ID"], "NEW-1")
            self.assertEqual(mappings.iloc[0]["公司名称"], "测试公司")
            self.assertEqual(source, db_path)

    def test_market_preheat_basket_enters_existing_preheat_view(self):
        state = {
            "style_index_comparison": {
                "沪深300": {"pct_today": 0, "pct_5d": 2, "pct_20d": 4}
            },
            "subsector_basket_momentum": {"baskets": [{
                "sector_id": "power_dc",
                "sub_sector": "800VDC",
                "n_stocks": 8,
                "pct_today": 0.8,
                "pct_5d": 1.0,
                "pct_20d": 2.0,
                "preheat_features": {
                    "state": "market_testing",
                    "signal_count": 3,
                    "signals": [
                        "relative_resilience",
                        "relative_strength_acceleration",
                        "leader_ladder",
                    ],
                },
            }]},
        }
        views = radar.build_sector_views(
            opportunity_map=[], state=state, limit=10
        )
        row = views["event_preheat_watch"][0]
        self.assertEqual(row["sector"], "800VDC")
        self.assertEqual(row["discovery_source"], "market_preheat_features")
        self.assertIn("催化", row["interpretation"])

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

    def test_company_candidates_use_sqlite_relations(self):
        class Store:
            def companies_for_events(self, refs):
                self.refs = refs
                return [{
                    "company_name": "三七互娱", "stock_code": "002555",
                    "relation_status": "映射已验证", "benefit_tier": "明确映射",
                    "mapping_confidence": "高",
                }]

        store = Store()
        rows, gaps = radar.company_candidates(
            [{"event_id": "GAME-1", "company_text": "三七互娱"}],
            pd.DataFrame(),
            pd.DataFrame([{"name": "三七互娱", "code": "002555"}]),
            store=store,
        )
        self.assertEqual(store.refs, [("nonfin", "GAME-1")])
        self.assertEqual(rows[0]["company_source"], "sqlite")
        self.assertEqual(rows[0]["benefit_tier"], "明确映射")
        self.assertEqual(gaps, [])

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

    def test_sector_views_keep_trend_and_rotation_separate(self):
        state = {
            "style_index_comparison": {
                "沪深300": {"pct_today": 0.5, "pct_5d": 1.0, "pct_20d": 2.0}
            },
            "subsector_basket_momentum": {"baskets": [{
                "sector_id": "DRUG", "sub_sector": "创新药", "n_stocks": 12,
                "coverage_confidence": "normal", "pct_today": -0.2,
                "pct_5d": 8.0, "pct_20d": 12.0, "median_5d": 5.0,
                "advance_ratio_5d": 75.0,
            }]},
        }
        opportunity = [{
            "sector": "房地产", "active_events": [],
            "evidence": {
                "excess_today_vs_csi300": 3.0,
                "excess_5d_vs_csi300": 1.0,
                "excess_20d_vs_csi300": -2.0,
                "advance_excess_vs_market": 15.0,
                "active_catalyst": False,
                "gates": {"relative_breadth": True},
            },
        }]
        views = radar.build_sector_views(
            opportunity_map=opportunity, state=state
        )
        self.assertEqual(views["trend_watch"][0]["sector"], "创新药")
        self.assertEqual(views["rotation_watch"][0]["sector"], "房地产")
        self.assertNotIn("房地产", [r["sector"] for r in views["trend_watch"]])

    def test_low_coverage_trend_has_explicit_warning(self):
        state = {
            "style_index_comparison": {"沪深300": {
                "pct_today": 0, "pct_5d": 0, "pct_20d": 0,
            }},
            "subsector_basket_momentum": {"baskets": [{
                "sector_id": "DRUG", "sub_sector": "创新药", "n_stocks": 2,
                "coverage_confidence": "low", "pct_today": 1,
                "pct_5d": 8, "pct_20d": 11, "median_5d": 8,
                "advance_ratio_5d": 100,
            }]},
        }
        row = radar.build_sector_views(
            opportunity_map=[], state=state
        )["trend_watch"][0]
        self.assertIn("少于5只", row["warning"])

    def test_low_coverage_warns_but_does_not_hide_strong_signal(self):
        state = {
            "style_index_comparison": {"沪深300": {
                "pct_today": 0, "pct_5d": 1, "pct_20d": 2,
            }},
            "subsector_basket_momentum": {"baskets": [
                {
                    "sector_id": "DRUG", "sub_sector": "创新药",
                    "n_stocks": 2, "coverage_confidence": "low",
                    "pct_today": 0, "pct_5d": 9, "pct_20d": 13,
                    "median_5d": 8, "advance_ratio_5d": 100,
                },
                {
                    "sector_id": "OTHER", "sub_sector": "普通趋势",
                    "n_stocks": 12, "coverage_confidence": "normal",
                    "pct_today": 0, "pct_5d": 4, "pct_20d": 8,
                    "median_5d": 2, "advance_ratio_5d": 70,
                },
            ]},
        }
        rows = radar.build_sector_views(
            opportunity_map=[], state=state
        )["trend_watch"]
        self.assertEqual(rows[0]["sector"], "创新药")
        self.assertEqual(rows[0]["coverage_confidence"], "low")

    def test_trend_view_exposes_core_panorama_divergence(self):
        state = {
            "style_index_comparison": {"沪深300": {
                "pct_today": 0, "pct_5d": 0, "pct_20d": 0,
            }},
            "subsector_basket_momentum": {"baskets": [{
                "sector_id": "X", "sub_sector": "测试板块", "n_stocks": 10,
                "coverage_confidence": "normal", "pct_today": 1,
                "pct_5d": 5, "pct_20d": 6, "median_5d": 3,
                "advance_ratio_5d": 70,
                "event_core": {"n_stocks": 2, "pct_5d": -1},
            }]},
        }
        row = radar.build_sector_views(
            opportunity_map=[], state=state
        )["trend_watch"][0]
        self.assertEqual(
            row["evidence"]["core_panorama_alignment"],
            "panorama_only_rotation",
        )


if __name__ == "__main__":
    unittest.main()
