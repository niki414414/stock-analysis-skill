import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "skills/market-outlook/scripts/market_daily_review.py"
spec = importlib.util.spec_from_file_location("market_daily_review", SCRIPT)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class MarketDailyReviewTests(unittest.TestCase):
    def test_compose_keeps_three_views_separate_and_checks_dates(self):
        state = {
            "breadth_today": {"trade_date": "20260812", "advance_pct": 76.3},
            "total_turnover": {"chg_vs_avg_pct": -15.2},
            "style_index_comparison": {"沪深300": {"pct_5d": 0.85}},
            "technical_levels": {"上证指数": {"range_outlook": {"current": 4000}}},
            "subsector_basket_momentum": {
                "trade_date": "20260812", "input_model": "dual",
                "external_approved_count": 49, "external_review_queue": [],
            },
            "ths_hotspot_momentum": {"trade_date": "20260812", "boards": [{
                "source": "ths", "board_code": "885877.TI", "board_name": "转基因",
                "pct_today": 3.1, "pct_5d": 5.2,
                "preheat_features": {"state": "early_improvement"},
                "interpretation_boundary": "同花顺概念只代表市场热度，不代表主营纯度",
            }]},
            "sw_subindustry_momentum": {"trade_date": "20260812", "boards": [{
                "source": "sw_subindustry", "board_code": "850111.SI",
                "board_name": "种植业", "classification_level": "L3", "pct_today": 2.2,
            }]},
        }
        radar = {
            "trade_date": "20260812",
            "market_environment": {"label": "broad_rebound_without_increment"},
            "sector_views": {
                "trend_watch": [{"sector": "创新药", "evidence": {
                    "core_panorama_alignment": "core_and_panorama_aligned"
                }}],
                "rotation_watch": [{"sector": "房地产"}],
                "event_preheat_watch": [{"sector": "机器人"}],
            },
            "opportunity_snapshot_meta": {
                "price_date": "20260812",
                "moneyflow": {"latest_trade_date": "20260811"},
            },
        }
        result = review.compose_review(state, radar)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["sector_views"]["trend"][0]["sector"], "创新药")
        self.assertEqual(result["sector_views"]["rotation"][0]["sector"], "房地产")
        self.assertTrue(result["data_dates"]["moneyflow_lagged"])
        self.assertEqual(result["market"]["range_outlook"]["上证指数"]["current"], 4000)
        discovery = result["sector_views"]["hotspot_discovery"]
        self.assertEqual(discovery["ths_market_hotspots"][0]["board_name"], "转基因")
        self.assertEqual(discovery["sw_l2_l3_rotation"][0]["classification_level"], "L3")
        self.assertEqual(discovery["rotation_spine"][0]["board_name"], "种植业")
        self.assertEqual(discovery["rotation_spine"][0]["ths_context"][0]["board_name"], "转基因")
        self.assertIn(
            "默认用户输出只保留盘面与板块、现有持仓、下一步策略三块",
            result["interpretation_contract"],
        )
        self.assertIn(
            "限制模块数量而非证据颗粒度；重点板块逐项展示核心/全景、相对强弱、风险、确认和失效",
            result["interpretation_contract"],
        )

    def test_missing_required_section_degrades_instead_of_inventing(self):
        result = review.compose_review({}, {})
        self.assertEqual(result["status"], "degraded")
        self.assertIn("breadth_today", result["checks"]["missing_required_sections"])

    def test_analysis_and_decision_modes_share_evidence_but_change_presentation(self):
        state = {
            "breadth_today": {"trade_date": "20260812", "advance_pct": 60},
            "total_turnover": {"chg_vs_avg_pct": 0},
            "style_index_comparison": {"沪深300": {"pct_5d": 1}},
            "technical_levels": {"上证指数": {"range_outlook": {}}},
            "subsector_basket_momentum": {"trade_date": "20260812"},
        }
        radar = {
            "trade_date": "20260812",
            "market_environment": {"label": "rotation_or_mixed"},
            "sector_views": {
                "trend_watch": [{"sector": "创新药"}],
                "rotation_watch": [{"sector": "有色"}],
                "event_preheat_watch": [{"sector": "机器人"}],
            },
        }
        analysis = review.compose_review(state, radar, output_mode="analysis")
        decision = review.compose_review(state, radar, output_mode="decision")
        self.assertEqual(analysis["sector_views"], decision["sector_views"])
        self.assertEqual(analysis["market"], decision["market"])
        self.assertEqual(analysis["presentation"]["mode"], "analysis")
        self.assertEqual(decision["presentation"]["mode"], "decision")
        self.assertIn("最多3个关键方向及其动作/确认/失效条件",
                      decision["presentation"]["required_sections"])
        self.assertIn("今天实际有操作吗", decision["interaction_contract"]["ask_after_review"])

    def test_legacy_radar_snapshot_reuses_current_view_builder(self):
        state = {
            "breadth_today": {"trade_date": "20260812", "advance_pct": 70},
            "total_turnover": {"chg_vs_avg_pct": -10},
            "style_index_comparison": {"沪深300": {
                "pct_today": 0, "pct_5d": 0, "pct_20d": 0,
            }},
            "technical_levels": {"上证指数": {"range_outlook": {}}},
            "subsector_basket_momentum": {"trade_date": "20260812", "baskets": []},
        }
        radar = {
            "trade_date": "20260812",
            "sector_opportunity_map": [{
                "sector": "房地产", "active_events": [],
                "evidence": {
                    "excess_today_vs_csi300": 3,
                    "excess_5d_vs_csi300": 1,
                    "excess_20d_vs_csi300": -1,
                    "advance_excess_vs_market": 10,
                    "active_catalyst": False,
                    "gates": {"relative_breadth": True},
                },
            }],
        }
        result = review.compose_review(state, radar)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["sector_views"]["rotation"][0]["sector"], "房地产")

    def test_decision_routes_keep_strategies_separate_and_queue_holdings(self):
        state = {
            "breadth_today": {"trade_date": "20260812", "advance_pct": 76},
            "total_turnover": {"chg_vs_avg_pct": -15},
            "style_index_comparison": {"沪深300": {"pct_5d": 1}},
            "technical_levels": {"上证指数": {"range_outlook": {}}},
            "subsector_basket_momentum": {"trade_date": "20260812"},
        }
        radar = {
            "trade_date": "20260812",
            "market_environment": {"label": "broad_rebound_without_increment"},
            "sector_views": {
                "trend_watch": [{
                    "sector": "创新药", "coverage_confidence": "normal",
                    "evidence": {"core_panorama_alignment": "core_and_panorama_aligned"},
                }],
                "rotation_watch": [{"sector": "房地产"}],
                "event_preheat_watch": [],
            },
        }
        holdings = [{
            "account": "A", "code": "601138", "name": "工业富联",
            "asset_type": "stock", "shares": "300", "cost": "50",
            "role": "底仓",
        }]
        result = review.compose_review(state, radar, holdings, {
            "601138": {
                "status": "ok", "trade_date": "20260812",
                "latest_price": 55, "pct_today": 1.2,
                "ma20": 53, "ma60": 50,
                "above_ma20": True, "above_ma60": True,
            }
        })
        routes = result["decision_routes"]
        self.assertEqual(
            routes["strategy_guidance"]["short-ma5"]["mode"],
            "enabled_selective",
        )
        self.assertEqual(
            routes["strategy_guidance"]["catalyst-swing"]["preferred_sectors"],
            ["创新药"],
        )
        self.assertTrue(routes["strategy_guidance"]["no_cross_strategy_ranking"])
        self.assertEqual(routes["holding_review_queue"][0]["strategy_identity"], "底仓")
        self.assertEqual(
            routes["holding_review_queue"][0]["market_data"]["pnl_pct_vs_cost"],
            10.0,
        )
        self.assertEqual(
            routes["holding_review_queue"][0]["market_data"]["trend_brief"],
            "MA20上 / MA60上",
        )

    def test_zero_share_holding_is_not_queued(self):
        rows = review.build_holding_review_queue([
            {"code": "000001", "shares": "0", "role": "短线"}
        ], {})
        self.assertEqual(rows, [])

    def test_swing_route_skips_low_coverage_theme(self):
        state = {
            "breadth_today": {"advance_pct": 60},
            "total_turnover": {"chg_vs_avg_pct": 0},
        }
        radar = {
            "market_environment": {"label": "rotation_or_mixed"},
            "sector_views": {
                "trend_watch": [
                    {"sector": "单股样本", "coverage_confidence": "low",
                     "evidence": {"core_panorama_alignment": "core_and_panorama_aligned"}},
                    {"sector": "创新药", "coverage_confidence": "normal",
                     "evidence": {"core_panorama_alignment": "core_and_panorama_aligned"}},
                ],
                "rotation_watch": [], "event_preheat_watch": [],
            },
        }
        guidance = review.strategy_guidance(state, radar)
        self.assertEqual(
            guidance["catalyst-swing"]["preferred_sectors"], ["创新药"]
        )

    def test_pushdozer_limit_down_gate_controls_short_mode(self):
        radar = {
            "market_environment": {"label": "incremental_broad_rally"},
            "sector_views": {"trend_watch": [], "rotation_watch": [{"sector": "机械"}]},
        }
        green = review.strategy_guidance({
            "breadth_today": {"advance_pct": 60, "limit_down": 8},
            "total_turnover": {"chg_vs_avg_pct": 5},
        }, radar)
        yellow = review.strategy_guidance({
            "breadth_today": {"advance_pct": 60, "limit_down": 15},
            "total_turnover": {"chg_vs_avg_pct": 5},
        }, radar)
        red = review.strategy_guidance({
            "breadth_today": {"advance_pct": 60, "limit_down": 20},
            "total_turnover": {"chg_vs_avg_pct": 5},
        }, radar)
        self.assertEqual(green["short-ma5"]["mode"], "enabled")
        self.assertEqual(yellow["short-ma5"]["mode"], "enabled_reduced")
        self.assertEqual(red["short-ma5"]["mode"], "defensive_or_off")

    def test_observation_snapshot_is_idempotent_by_trade_date(self):
        payload = {
            "generated_at": "2026-08-18T18:00:00",
            "data_dates": {"price_dates": ["20260818"]},
            "market": {
                "environment": {"label": "rotation_or_mixed"},
                "range_outlook": {"上证指数": {"current": 4000}},
            },
            "sector_views": {
                "trend": [{"sector": "种子"}], "event_preheat": [],
                "hotspot_discovery": {
                    "rotation_spine": [{"board_name": "种子"}],
                    "supplemental_concepts": [{"board_name": "转基因"}],
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.jsonl"
            review.save_observation_snapshot(payload, path)
            payload["market"]["range_outlook"]["上证指数"]["current"] = 4010
            review.save_observation_snapshot(payload, path)
            records = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["index_roadmaps"]["上证指数"]["current"], 4010)


if __name__ == "__main__":
    unittest.main()
