import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills/stock-analysis/scripts/market_state_fetcher.py"
)
SPEC = importlib.util.spec_from_file_location("market_state_fetcher", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MarketStateFetcherImportTests(unittest.TestCase):
    def test_script_imports_on_supported_python(self):
        self.assertTrue(callable(MODULE.fetch_ths_hotspot_momentum))
        self.assertTrue(callable(MODULE.fetch_sw_subindustry_momentum))

    def test_technical_roadmap_has_two_levels_and_realized_context(self):
        class FakePro:
            def index_daily(self, **kwargs):
                rows = []
                for index in range(140):
                    close = 1000 + index * 1.5 + (index % 7 - 3) * 2
                    rows.append({
                        "trade_date": f"2026{(index // 28) + 1:02d}{(index % 28) + 1:02d}",
                        "open": close - 2, "high": close + 8,
                        "low": close - 8, "close": close,
                    })
                return pd.DataFrame(rows)

        levels = MODULE.fetch_index_technical_levels(FakePro(), "20260818")
        roadmap = levels["上证指数"]["range_outlook"]
        structure = levels["上证指数"]["market_structure_v2"]
        self.assertIn("secondary_support_zone", roadmap)
        self.assertIn("secondary_resistance_zone", roadmap)
        self.assertGreater(
            roadmap["secondary_resistance_zone"]["mid"],
            roadmap["resistance_zone"]["mid"],
        )
        self.assertGreater(roadmap["realized_5d_excursion"]["upside"]["samples"], 0)
        self.assertEqual(structure["mode"], "shadow")
        self.assertIn("level_map", structure)
        self.assertIn("provenance", structure["level_map"][0])

    def test_previous_structure_loader_ignores_future_records(self):
        records = [
            {
                "index_name": "上证指数", "trade_date": "20260827",
                "as_of_timestamp": "2026-08-27T15:00:00+08:00",
                "snapshot": {"mode": "shadow", "marker": "visible"},
            },
            {
                "index_name": "上证指数", "trade_date": "20260831",
                "as_of_timestamp": "2026-08-31T15:00:00+08:00",
                "snapshot": {"mode": "shadow", "marker": "future"},
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "versions.jsonl"
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
                encoding="utf-8",
            )
            result = MODULE._load_previous_structure_v2(
                "上证指数", "20260828", str(path)
            )
        self.assertEqual(result["marker"], "visible")


if __name__ == "__main__":
    unittest.main()
