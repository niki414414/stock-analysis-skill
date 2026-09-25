import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "skills/market-outlook/scripts/market_brief_v2.py"
SPEC = importlib.util.spec_from_file_location("market_brief_v2", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def bars(base, drift=0.3, days=90, amount=2_500_000):
    rows = []
    previous = base
    for idx in range(days):
        wave = ((idx % 10) - 5) * 0.12
        close = base + idx * drift + wave
        open_price = previous
        high = max(open_price, close) + 1.0
        low = min(open_price, close) - 1.0
        rows.append({
            "trade_date": f"2026{(idx // 28) + 5:02d}{(idx % 28) + 1:02d}",
            "open": round(open_price, 2), "high": round(high, 2),
            "low": round(low, 2), "close": round(close, 2),
            "pct_chg": round((close / previous - 1) * 100, 2),
            "amount": amount * (1 + (idx % 7) * 0.08),
        })
        previous = close
    return rows


def bundle():
    definitions = [
        ("证券", "801193.SI", 1000, 0.20, "中信证券", "600030.SH"),
        ("小金属", "801054.SI", 900, 0.55, "中钨高新", "000657.SZ"),
        ("种植业", "801017.SI", 800, 0.38, "登海种业", "002041.SZ"),
        ("医药生物", "801150.SI", 700, -0.12, "恒瑞医药", "600276.SH"),
        ("通信", "801770.SI", 1100, 0.65, "杭电股份", "603618.SH"),
    ]
    sectors = []
    for name, code, base, drift, stock_name, stock_code in definitions:
        sectors.append({
            "name": name, "code": code, "kind": "sector", "bars": bars(base, drift),
            "breadth": {"advance_ratio": 62.0},
            "representatives": [{
                "name": stock_name, "code": stock_code, "kind": "stock",
                "price_basis": "qfq_latest_bar",
                "role": "容量核心", "bars": bars(base / 20, drift / 20, amount=2_500_000),
            }],
        })
    return {
        "market": {"name": "上证指数", "code": "000001.SH", "kind": "market", "bars": bars(3900, 0.5)},
        "market_indices": [
            {"name": "创业板指", "code": "399006.SZ", "kind": "market", "bars": bars(3000, 1.2)},
            {"name": "科创50", "code": "000688.SH", "kind": "market", "bars": bars(1000, 0.8)},
        ],
        "sectors": sectors,
        "market_context": {"turnover": {"latest_yi": 24500.0, "vs_5d_pct": 4.2}},
        "data_gaps": [],
    }


class MarketBriefV2Tests(unittest.TestCase):
    def test_sw_daily_fields_are_normalised(self):
        frame = MODULE._normalise_bars(pd.DataFrame([{
            "trade_date": "20260901", "open": 100, "high": 102,
            "low": 99, "close": 101, "pct_change": 1.25,
            "amount": 12345,
        }]))
        self.assertIn("pct_chg", frame.columns)
        self.assertNotIn("pct_change", frame.columns)
        self.assertEqual(frame.iloc[-1]["pct_chg"], 1.25)

    def test_industry_snapshot_uses_sw_daily(self):
        class FakePro:
            def __init__(self):
                self.sw_calls = []

            def sw_daily(self, trade_date):
                self.sw_calls.append(trade_date)
                close = {"20260901": 110, "20260821": 105, "20260806": 100}[trade_date]
                return pd.DataFrame([{
                    "ts_code": "801010.SI", "trade_date": trade_date,
                    "close": close, "pct_change": 1.0,
                }])

            def index_daily(self, **kwargs):
                raise AssertionError("申万截面不应再调用index_daily")

        benchmark = pd.DataFrame({
            "trade_date": [f"202608{day:02d}" for day in range(4, 26)] + ["20260901"],
            "close": range(23), "high": range(23), "low": range(23),
        })
        classes = pd.DataFrame([{
            "industry_name": "农林牧渔", "index_code": "801010.SI",
        }])
        pro = FakePro()
        rows = MODULE._batch_industry_candidates(pro, classes, benchmark)
        self.assertEqual(pro.sw_calls, ["20260901", "20260821", "20260806"])
        self.assertEqual(rows[0]["name"], "农林牧渔")

    def test_price_map_contains_exact_two_level_zones_and_provenance(self):
        result = MODULE.build_price_map(bundle()["market"])
        self.assertEqual(len(result["support_zones"]), 2)
        self.assertEqual(len(result["resistance_zones"]), 2)
        self.assertIn("level_map", result["structure"])
        first_available = next(
            level for level in result["structure"]["level_map"]
            if level.get("provenance")
        )
        self.assertTrue(first_available["touch_count"] >= 1)

    def test_article_and_evidence_share_values(self):
        result = MODULE.build_brief(bundle())
        article = result["article"]
        support = MODULE._zone_text(result["market"]["support_zones"][0])
        self.assertIn(support, article)
        self.assertIn("实操问答", article)
        self.assertGreaterEqual(len(result["sectors"]), 3)
        self.assertLessEqual(len(result["sectors"]), 5)
        for sector in result["sectors"]:
            self.assertIn(MODULE._zone_text(sector["price_map"]["support_zones"][0]), article)
            self.assertIn("已持有", article)
            self.assertIn("未持有", article)

    def test_parallel_headline_index_boxes_are_rendered_and_independent(self):
        base = bundle()
        result = MODULE.build_brief(base)
        self.assertEqual([row["name"] for row in result["market_indices"]], ["创业板指", "科创50"])
        for row in result["market_indices"]:
            self.assertEqual(len(row["support_zones"]), 2)
            self.assertEqual(len(row["resistance_zones"]), 2)
            self.assertIn(row["name"], result["article"])
            self.assertIn(MODULE._zone_text(row["support_zones"][0]), result["article"])
        self.assertNotEqual(
            result["market_indices"][0]["structure"]["primary_box"]["box_id"],
            result["market_indices"][1]["structure"]["primary_box"]["box_id"],
        )

    def test_missing_headline_index_marks_brief_degraded(self):
        base = bundle()
        base["market_indices"] = base["market_indices"][:1]
        result = MODULE.build_brief(base)
        self.assertEqual(result["status"], "degraded")
        self.assertIn("科创50指数数据缺失", "；".join(result["data_gaps"]))

    def test_historical_save_does_not_replace_newer_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            newer = MODULE.build_brief(bundle())
            newer["as_of_date"] = "20260922"
            MODULE.save_result(newer, output_dir)
            older = {**newer, "as_of_date": "20260828"}
            saved = MODULE.save_result(older, output_dir)
            self.assertFalse(saved["latest_updated"])
            self.assertEqual(json.loads((output_dir / "latest.json").read_text())["as_of_date"], "20260922")

    def test_volume_path_is_exposed_and_joined_to_box(self):
        result = MODULE.build_price_map(bundle()["market"])
        volume = result["volume"]
        self.assertGreaterEqual(len(volume["daily"]), 5)
        self.assertIn("amount_ratio_20d", volume["daily"][-1])
        self.assertIn("amount_change_1d_pct", volume)
        self.assertIn(result["volume_box"]["gate"], {
            "inside_box_neutral", "support_testing", "support_absorption",
            "resistance_testing", "resistance_distribution",
            "resistance_rejected",
            "breakout_pending_volume", "breakout_confirmed",
            "breakdown_pending_volume", "breakdown_confirmed",
        })
        self.assertIn("量能闸门", result["confirmation"])

    def test_close_below_touched_resistance_is_rejection_not_pending_test(self):
        frame = pd.DataFrame([
            {"trade_date": f"202609{day:02d}", "open": 100, "high": 102,
             "low": 98, "close": 100, "pct_chg": 0, "amount": 100}
            for day in range(1, 21)
        ] + [{"trade_date": "20260921", "open": 103, "high": 105,
              "low": 94, "close": 95, "pct_chg": -8, "amount": 80}])
        signal = MODULE._volume_box_context(
            frame, {"lower": 92, "upper": 94}, {"lower": 100, "upper": 102}
        )
        self.assertEqual(signal["gate"], "resistance_rejected")

    def test_v2_representative_stock_output_is_observation_not_trade_order(self):
        article = MODULE.build_brief(bundle())["article"]
        self.assertIn("个股买点须另做六层核验", article)
        self.assertIn("不是代表股的独立买卖指令", article)
        for phrase in ("试仓", "低吸区", "生死线", "减交易仓"):
            self.assertNotIn(phrase, article)

    def test_sector_roles_are_not_one_unified_ranking(self):
        selected = MODULE.select_sector_roles(bundle()["sectors"])
        roles = {row["brief_role"] for row in selected}
        self.assertIn("容量确认", roles)
        self.assertIn("已有趋势", roles)
        self.assertIn("弱势回避", roles)

    def test_non_actionable_composite_sector_is_not_auto_selected(self):
        rows = bundle()["sectors"] + [{
            "name": "综合", "code": "801230.SI", "kind": "sector",
            "bars": bars(1000, 3.0),
        }]
        self.assertNotIn("综合", {row["name"] for row in MODULE.select_sector_roles(rows)})

    def test_cli_offline_one_entry_outputs_article_and_json(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.json"
            input_path.write_text(json.dumps(bundle(), ensure_ascii=False), encoding="utf-8")
            article = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(input_path)],
                check=True, capture_output=True, text=True,
            ).stdout
            self.assertIn("市场可执行简报", article)
            evidence = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(input_path), "--json"],
                check=True, capture_output=True, text=True,
            ).stdout
            parsed = json.loads(evidence)
            self.assertEqual(parsed["contract_version"], MODULE.CONTRACT_VERSION)
            self.assertIn("article", parsed)

    def test_historical_run_ignores_future_latest_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            latest = Path(directory) / "latest.json"
            latest.write_text(json.dumps({"as_of_date": "20260828"}), encoding="utf-8")
            self.assertIsNone(MODULE._load_previous(latest, current_date="20260821"))
            self.assertIsNone(MODULE._load_previous(latest, current_date="20260828"))

    def test_lineage_uses_last_earlier_date_not_output_directory_or_same_day(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lineage_dir = root / "lineage"
            output_dir = root / "focused_output"
            lineage_dir.mkdir()
            output_dir.mkdir()
            for date in ("20260918", "20260922", "20260924", "20260928"):
                (lineage_dir / f"market_brief_{date}_180000.json").write_text(
                    json.dumps({"as_of_date": date}), encoding="utf-8"
                )
            (output_dir / "latest.json").write_text(
                json.dumps({"as_of_date": "20260918"}), encoding="utf-8"
            )
            previous, source = MODULE._load_previous_brief("20260924", lineage_dir)
            self.assertEqual(previous["as_of_date"], "20260922")
            self.assertEqual(source.parent, lineage_dir)
            self.assertNotEqual(source.parent, output_dir)


if __name__ == "__main__":
    unittest.main()
