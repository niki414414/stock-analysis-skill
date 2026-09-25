import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/daily_decision_journal.py"
spec = importlib.util.spec_from_file_location("daily_decision_journal", SCRIPT)
journal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(journal)


class DailyDecisionJournalTests(unittest.TestCase):
    def test_review_decision_and_outcome_stay_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "journal.jsonl"
            review_path = root / "review.json"
            review_path.write_text(json.dumps({
                "status": "ready",
                "data_dates": {"price_dates": ["20260814"]},
                "presentation": {"mode": "analysis"},
                "market": {"environment": {"label": "rotation_or_mixed"}},
                "sector_views": {
                    "trend": [{"sector": "创新药", "coverage_confidence": "normal"}],
                    "rotation": [{"sector": "有色"}],
                    "event_preheat": [{"sector": "机器人", "preheat_state": "early"}],
                },
                "decision_routes": {"strategy_guidance": {"short-ma5": {"mode": "enabled"}}},
                "checks": {"missing_required_sections": []},
            }, ensure_ascii=False), encoding="utf-8")
            review_row = journal.record_review(str(review_path), log)
            decision_row = journal.record_decision(Namespace(
                trade_date="20260814", review_id=review_row["review_id"],
                status="no_operation", action=[], note="条件未触发",
            ), log)
            payload = root / "outcome.json"
            payload.write_text(json.dumps({
                "prediction_result": "mixed",
                "execution_result": "not_applicable",
                "direction_outcomes": [{"sector": "创新药", "result": "continued"}],
            }, ensure_ascii=False), encoding="utf-8")
            outcome_row = journal.record_outcome(Namespace(
                review_id=review_row["review_id"], horizon=3,
                as_of_date="20260819", payload=str(payload),
            ), log)
            self.assertEqual(outcome_row["trade_date"], "20260814")
            result = journal.summary(log)
            self.assertEqual(result["model_reviews"], 1)
            self.assertEqual(result["user_decisions"], 1)
            self.assertEqual(result["outcome_reviews"], 1)
            self.assertEqual(journal.summary(log, "20260814")["outcome_reviews"], 1)
            self.assertEqual(journal.summary(log, "20260819")["outcome_reviews"], 1)
            self.assertEqual(decision_row["decision_status"], "no_operation")

    def test_unknown_decision_cannot_contain_inferred_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "只有status=operated"):
                journal.record_decision(Namespace(
                    trade_date="20260814", review_id=None, status="unknown",
                    action=["601138|工业富联|买|底仓|模型猜测"], note="",
                ), Path(tmp) / "journal.jsonl")

    def test_duplicate_review_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_path = root / "review.json"
            review_path.write_text(json.dumps({
                "data_dates": {"price_dates": ["20260814"]}
            }), encoding="utf-8")
            log = root / "journal.jsonl"
            journal.record_review(str(review_path), log)
            with self.assertRaisesRegex(ValueError, "记录已存在"):
                journal.record_review(str(review_path), log)

    def test_legacy_outcome_without_trade_date_remains_searchable(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "journal.jsonl"
            log.write_text(json.dumps({
                "event_type": "outcome_review", "review_id": "review:20260814:legacy",
                "as_of_date": "20260819", "horizon_trading_days": 3,
            }) + "\n", encoding="utf-8")
            self.assertEqual(journal.summary(log, "20260814")["outcome_reviews"], 1)
            self.assertEqual(journal.summary(log, "20260819")["outcome_reviews"], 1)


if __name__ == "__main__":
    unittest.main()
