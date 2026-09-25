import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills" / "stock-analysis" / "scripts" / "market_state_fetcher.py"
)
spec = importlib.util.spec_from_file_location("market_state_fetcher", SCRIPT)
fetcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetcher)


HEADER = (
    "sector_id,sub_sector,stock_code,company_name,basket_role,source_type,"
    "source_name,source_url,inclusion_reason,business_relevance,verified_on,"
    "review_status,valid_until,note\n"
)


class SubsectorBasketInputTests(unittest.TestCase):
    def test_subsector_baskets_read_sqlite_status_query(self):
        with mock.patch.object(
            fetcher, "query_status",
            return_value=(pd.DataFrame(), "sqlite:///tmp/event.db#tech/sectors_status"),
        ) as query:
            result = fetcher.fetch_subsector_basket_momentum(object(), "20260908")

        query.assert_called_once_with(source="tech")
        self.assertEqual(result, {"error": "SQLite主库sectors_status不可用"})

    def test_only_approved_non_expired_rows_enter_calculation(self):
        rows = [
            "drug,创新药,600276,恒瑞医药,market_representative,official_index,"
            "index,https://example.com,top10,high,2026-08-01,approved,2026-12-31,",
            "drug,创新药,688235,百济神州,market_representative,official_index,"
            "index,https://example.com,top10,high,2026-08-01,candidate,2026-12-31,",
            "drug,创新药,002422,科伦药业,market_representative,official_index,"
            "index,https://example.com,top10,high,2025-01-01,approved,2025-12-31,",
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tmp:
            tmp.write(HEADER + "\n".join(rows))
            path = tmp.name
        approved, review = fetcher.load_external_basket_supplement(
            path, as_of_date="20260813"
        )
        self.assertEqual([row["stock_code"] for row in approved], ["600276"])
        self.assertEqual(
            {row["review_reason"] for row in review}, {"candidate", "expired"}
        )

    def test_basket_stats_report_median_breadth_and_activity(self):
        result = fetcher._basket_stats([
            {"pct_today": 5, "pct_5d": 10, "pct_20d": 20,
             "amount_ratio_20d": 2},
            {"pct_today": -1, "pct_5d": -2, "pct_20d": 4,
             "amount_ratio_20d": 1},
        ])
        self.assertEqual(result["n_stocks"], 2)
        self.assertEqual(result["median_5d"], 4)
        self.assertEqual(result["advance_ratio_5d"], 50)
        self.assertEqual(result["median_amount_ratio_20d"], 1.5)

    def test_bulk_fetch_calculates_returns_for_requested_codes_only(self):
        class Pro:
            def trade_cal(self, **kwargs):
                return pd.DataFrame({
                    "cal_date": [f"202601{i:02d}" for i in range(1, 26)],
                    "is_open": [1] * 25,
                })

            def daily(self, trade_date, fields):
                day = int(trade_date[-2:])
                return pd.DataFrame([
                    {"ts_code": "600276.SH", "trade_date": trade_date,
                     "close": day, "pct_chg": 1, "amount": 100 + day},
                    {"ts_code": "000001.SZ", "trade_date": trade_date,
                     "close": day, "pct_chg": 9, "amount": 999},
                ])

        rows = fetcher._fetch_basket_returns_bulk(
            Pro(), {"600276.SH"}, "20260125"
        )
        self.assertEqual(set(rows), {"600276.SH"})
        self.assertEqual(rows["600276.SH"]["pct_today"], 1)
        self.assertIsNotNone(rows["600276.SH"]["pct_20d"])


if __name__ == "__main__":
    unittest.main()
