import unittest
import pandas as pd
from evaluate import pivots, structure_filter, simple_swing_filter


def bars(values):
    return pd.DataFrame({'trade_date': pd.date_range('2024-01-01', periods=len(values)).strftime('%Y%m%d'),
                         'high': [v+1 for v in values], 'low': [v-1 for v in values],
                         'close': values})


class CausalStructureTests(unittest.TestCase):
    def test_nested_bars_do_not_invent_alternating_swings(self):
        frame = pd.DataFrame({'trade_date': ['20240101', '20240102', '20240103', '20240104'],
                              'high': [10, 9, 8, 11], 'low': [2, 3, 4, 5],
                              'close': [6, 6, 6, 8]})
        self.assertEqual(pivots(frame), [])
        self.assertFalse(structure_filter(frame))
        self.assertFalse(simple_swing_filter(frame))

    def test_fractal_requires_right_hand_bar(self):
        frame = bars([5, 3, 4])
        self.assertEqual(pivots(frame.iloc[:2]), [])
        found = pivots(frame)
        self.assertEqual(found[0]['kind'], 'low')
        self.assertEqual(found[0]['date'], '20240102')
        self.assertEqual(found[0]['confirmed_at'], '20240103')

    def test_future_changes_do_not_change_frozen_prefix(self):
        frame = bars([8, 7, 6, 5, 4, 5, 6, 7, 8, 9, 8, 7, 6, 5, 6, 7, 8, 9, 10, 11, 10])
        frozen = [structure_filter(frame.iloc[:i]) for i in range(3, 18)]
        changed = frame.copy()
        changed.loc[18:, ['high', 'low', 'close']] *= 10
        self.assertEqual(frozen, [structure_filter(changed.iloc[:i]) for i in range(3, 18)])
        for i in range(3, len(frame)+1):
            self.assertTrue(all(p['confirmed_at'] <= frame.trade_date.iloc[i-1] for p in pivots(frame.iloc[:i])))

    def test_requires_two_rising_highs_and_lows(self):
        frame = bars([8, 7, 6, 5, 4, 5, 6, 7, 8, 9, 8, 7, 6, 5, 6, 7, 8, 9, 10, 11, 10])
        self.assertFalse(structure_filter(frame.iloc[:10]))
        self.assertTrue(structure_filter(frame))
        down = frame.copy()
        down['high'], down['low'], down['close'] = 30-frame.low, 30-frame.high, 30-frame.close
        self.assertFalse(structure_filter(down))


if __name__ == '__main__':
    unittest.main()
