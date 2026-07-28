import importlib.util
import unittest
from pathlib import Path

import pandas as pd


FETCHER_PATH = (
    Path(__file__).parents[1]
    / "skills"
    / "stock-analysis"
    / "references"
    / "stock_data_fetcher.py"
)
SPEC = importlib.util.spec_from_file_location("stock_data_fetcher", FETCHER_PATH)
FETCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FETCHER)


class FakePro:
    def margin(self, **_kwargs):
        rows = []
        for day in range(1, 11):
            trade_date = f"202607{day:02d}"
            # 两个交易所各一行，验证函数按交易日汇总，而不是逐行比较。
            rows.append({"trade_date": trade_date, "rzye": day * 1e8})
            rows.append({"trade_date": trade_date, "rzye": day * 2e8})
        return pd.DataFrame(rows)

    def moneyflow(self, **_kwargs):
        return pd.DataFrame([
            {
                "trade_date": "20260727",
                "buy_sm_amount": 100,
                "sell_sm_amount": 300,
                "buy_lg_amount": 900,
                "sell_lg_amount": 200,
                "buy_elg_amount": 500,
                "sell_elg_amount": 100,
                "net_mf_amount": 900,
            },
            {
                "trade_date": "20260727",
                "buy_sm_amount": 50,
                "sell_sm_amount": 150,
                "buy_lg_amount": 400,
                "sell_lg_amount": 100,
                "buy_elg_amount": 200,
                "sell_elg_amount": 50,
                "net_mf_amount": 350,
            },
            # 旧交易日必须被排除。
            {
                "trade_date": "20260726",
                "buy_sm_amount": 9999,
                "sell_sm_amount": 0,
                "buy_lg_amount": 0,
                "sell_lg_amount": 9999,
                "buy_elg_amount": 0,
                "sell_elg_amount": 9999,
                "net_mf_amount": -9999,
            },
        ])


class MarketStateHelperTests(unittest.TestCase):
    def test_margin_trend_aggregates_exchanges_by_date(self):
        result = FETCHER.fetch_margin_trend(FakePro())
        self.assertEqual(result["direction"], "up")
        self.assertEqual(result["latest_date"], "20260710")
        self.assertEqual(result["latest_balance_yi"], 30.0)
        self.assertEqual(result["source"], "tushare_margin")

    def test_moneyflow_uses_latest_date_and_correct_units(self):
        result = FETCHER.fetch_market_moneyflow_dc(FakePro())
        self.assertEqual(
            result["pattern"], "large_order_inflow_small_order_outflow"
        )
        self.assertEqual(result["latest_date"], "20260727")
        self.assertEqual(result["large_order_net_yi"], 0.16)
        self.assertEqual(result["small_order_net_yi"], -0.03)
        self.assertEqual(result["market_net_yi"], 0.12)
        self.assertIn("代理", result["identity_caveat"])


if __name__ == "__main__":
    unittest.main()
