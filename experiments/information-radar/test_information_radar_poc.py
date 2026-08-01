import importlib.util
import json
import unittest
from pathlib import Path


HERE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location("information_radar_poc", HERE / "information_radar_poc.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class InformationRadarPocTest(unittest.TestCase):
    def setUp(self):
        self.replay = json.loads((HERE / "replay_20260731.json").read_text())
        self.rules = json.loads((HERE / "transmission_rules.json").read_text())

    def test_market_anomaly_requires_price_volume_and_rank(self):
        rows = MODULE.detect_market_anomalies(self.replay["sectors"], 31)
        self.assertEqual([row["name"] for row in rows], ["传媒", "计算机", "通信", "机械设备"])

    def test_price_signal_maps_to_computer_and_media_gaps(self):
        findings = MODULE.audit(self.replay, self.rules, ["2026-07-30 AI应用产品发布，没有成本价格线索"])
        self.assertEqual({row["sector"] for row in findings}, {"计算机", "传媒"})
        self.assertTrue(all(row["coverage_gap"] for row in findings))

    def test_existing_price_event_closes_gap(self):
        findings = MODULE.audit(self.replay, self.rules, ["2026-07-30 OpenAI API降价，模型价格下调"])
        self.assertTrue(findings)
        self.assertTrue(all(not row["coverage_gap"] for row in findings))

    def test_old_related_event_does_not_hide_new_gap(self):
        findings = MODULE.audit(self.replay, self.rules, ["2026-07-01 OpenAI价格减半的优化方法"])
        self.assertTrue(all(row["coverage_gap"] for row in findings))


if __name__ == "__main__":
    unittest.main()
