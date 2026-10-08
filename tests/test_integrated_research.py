import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/integrated_research.py'
spec = importlib.util.spec_from_file_location('integrated_research', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class IntegratedResearchTests(unittest.TestCase):
    def test_freeze_date_and_explicit_pilot_dates(self):
        rows = [{'event_type': 'model_review', 'review_id': 'review:20260929:x',
                 'trade_date': '20260929', 'recorded_at': '2026-09-30T16:20:00',
                 'review_snapshot': {'integrated_research': {'review_dates': {
                     '3': '20261012', '5': '20261014', '10': '20261021'}}}}]
        self.assertEqual(module.due_reviews(rows, ['20260930', '20261008'], '20260930'), [])
        due = module.due_reviews(rows, ['20260930', '20261008'], '20261012')
        self.assertEqual([r['horizon'] for r in due], [3])
        rows.append({'event_type': 'outcome_review', 'review_id': rows[0]['review_id'],
                     'horizon_trading_days': 3})
        self.assertEqual(module.due_reviews(rows, ['20260930', '20261008'], '20261012'), [])

    def test_gate_cannot_skip_due_or_claim_unrecorded_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log, cal, run = root / 'journal.jsonl', root / 'calendar.json', root / 'run'
            row = {'event_id': 'r1', 'event_type': 'model_review', 'review_id': 'review:20260901:x',
                   'trade_date': '20260901', 'recorded_at': '2026-09-01T18:00:00'}
            log.write_text(json.dumps(row) + '\n')
            module.write(cal, ['20260901', '20260902', '20260903', '20260904',
                               '20260907', '20260908', '20260909', '20260910'])
            state = module.start(run, log, cal, '20260904')
            self.assertEqual(state['gate'], 'review_required')
            with self.assertRaises(ValueError):
                module.call_tool(run, 'probe', 'market', 'new analysis', [sys.executable, '-c', 'pass'])
            with self.assertRaises(ValueError):
                module.complete_review(run, {'items': []})
            key = state['due_reviews'][0]['key']
            with self.assertRaises(ValueError):
                module.complete_review(run, {'items': [{'key': key, 'status': 'recorded'}]})
            module.complete_review(run, {'items': [{'key': key, 'status': 'pending_missing_data',
                                                    'reason': 'historical basket unavailable',
                                                    'next_step': 'retrieve original constituents'}]})
            # Missing evidence is still overdue next time, not silently completed.
            next_state = module.start(root / 'next', log, cal, '20260904')
            self.assertEqual(next_state['gate'], 'review_required')
            result = module.call_tool(run, 'probe', 'market', 'verify logging',
                                      [sys.executable, '-c', 'print("evidence")'])
            module.assess(run, result['call_id'], 'supported', 'reused evidence', 'market view')
            result2 = module.call_tool(run, 'probe', 'stock', 'verify failure logging',
                                       [sys.executable, '-c', 'raise SystemExit(7)'])
            self.assertEqual(result2['returncode'], 7)
            summary = module.usage_summary(root)
            self.assertEqual(summary['tools'][0]['calls'], 2)
            self.assertEqual(summary['tools'][0]['failed'], 1)
            self.assertEqual(summary['tools'][0]['contributions']['supported'], 1)

    def test_truncated_calendar_is_not_silent_no_due(self):
        rows = [{'event_type': 'model_review', 'review_id': 'review:20260901:x',
                 'trade_date': '20260901', 'recorded_at': '2026-09-01T18:00:00'}]
        due = module.due_reviews(rows, ['20260901', '20260902'], '20260930')
        self.assertEqual(len(due), 3)
        self.assertTrue(all(r['status'] == 'calendar_missing' for r in due))

    def test_recorded_result_unlocks_and_external_duration_stays_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log, run = root / 'journal.jsonl', root / 'run'
            key = 'review:20260901:x:3'
            module.write(run / 'session.json', {'run_id': 'run', 'journal': str(log),
                                               'gate': 'review_required', 'due_reviews': [
                                                   {'key': key, 'review_id': 'review:20260901:x', 'horizon': 3}]})
            log.write_text(json.dumps({'event_type': 'outcome_review',
                                       'review_id': 'review:20260901:x', 'horizon_trading_days': 3}) + '\n')
            state = module.complete_review(run, {'items': [{'key': key, 'status': 'recorded'}]})
            self.assertEqual(state['gate'], 'ready')
            evidence = root / 'web.json'
            module.write(evidence, {'source': 'official announcement'})
            row = module.external_call(run, 'web', 'event', 'verify catalyst', evidence)
            self.assertIsNone(row['duration_seconds'])
            self.assertEqual(module.usage_summary(root)['tools'][0]['unknown_duration_calls'], 1)


if __name__ == '__main__':
    unittest.main()
