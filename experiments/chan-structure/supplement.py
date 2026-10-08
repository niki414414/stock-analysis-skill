"""Post-primary robustness comparison, does not alter the preregistered gate."""
import argparse
import json
from pathlib import Path
import pandas as pd
from evaluate import ASSETS, simple_swing_filter, summary

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
out = args.output
events = pd.read_csv(out/'events.csv', dtype={'date': str})
for name, (code, _) in ASSETS.items():
    frame = pd.read_csv(out/f'{code}.csv', dtype={'trade_date': str})
    for index, row in events[events.asset == name].iterrows():
        events.loc[index, 'simple_swing'] = simple_swing_filter(frame[frame.trade_date <= row.date].tail(121))
events['simple_swing'] = events['simple_swing'].astype(bool)
events.to_csv(out/'events.csv', index=False)
report = json.loads((out/'report.json').read_text())
report['supplementary_comparison'] = {}
for split in ['development', 'holdout']:
    subset = events[events.split == split]
    kept = subset[subset.simple_swing]
    result = summary(kept.to_dict('records'))
    result['success_retention_pct'] = round(100*subset[~subset.false_break].simple_swing.mean(), 2)
    report['supplementary_comparison'][split] = result
report['supplementary_comparison']['note'] = 'Added after primary results, descriptive only; no threshold tuning or gate changes.'
(out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report['supplementary_comparison'], ensure_ascii=False, indent=2))
