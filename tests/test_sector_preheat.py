import unittest

from skills.shared.sector_preheat import build_sector_preheat_features


class SectorPreheatFeatureTests(unittest.TestCase):
    def test_detects_early_market_testing_without_future_data(self):
        dates = [f"202601{i:02d}" for i in range(1, 9)]
        members = []
        for offset in (0.0, 0.2, 0.4):
            history = []
            close = 10 + offset
            for idx, date in enumerate(dates):
                pct = -0.4 if idx < 5 else 1.2 + offset
                close *= 1 + pct / 100
                history.append({
                    "trade_date": date,
                    "open": close * 0.995,
                    "high": close * 1.005,
                    "low": close * 0.99,
                    "close": close,
                    "pct_chg": pct,
                    "amount_ratio_20d": 1.5 if idx == 7 else 0.9,
                })
            members.append({"history": history})
        benchmark = [
            {"trade_date": date, "pct_chg": -0.8 if idx >= 3 else 0.2}
            for idx, date in enumerate(dates)
        ]

        result = build_sector_preheat_features(members, benchmark)

        self.assertEqual(result["state"], "market_testing")
        self.assertTrue(result["gates"]["relative_resilience"])
        self.assertTrue(result["gates"]["relative_strength_acceleration"])
        self.assertTrue(result["gates"]["breadth_inflection"])
        self.assertGreaterEqual(result["leader_count"], 2)

    def test_missing_history_is_explicit_not_zero(self):
        result = build_sector_preheat_features([], [])
        self.assertEqual(result["status"], "insufficient_history")
        self.assertEqual(result["signals"], [])


if __name__ == "__main__":
    unittest.main()
