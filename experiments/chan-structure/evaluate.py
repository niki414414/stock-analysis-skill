"""Frozen-boundary prediction audit, not a trading backtest or full Chan engine."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'skills/market-outlook/scripts'))
from market_structure_v2 import build_market_structure_v2, generate_level_candidates, DEFAULT_CONFIG

ASSETS = {
    '上证指数': ('000001.SH', 'index'), '创业板指': ('399006.SZ', 'index'),
    '科创50': ('000688.SH', 'index'), '银行': ('801780.SI', 'nontech'),
    '房地产': ('801180.SI', 'nontech'), '医药生物': ('801150.SI', 'nontech'),
    '通信': ('801770.SI', 'tech'), '食品饮料': ('801120.SI', 'nontech'),
    '有色金属': ('801050.SI', 'nontech'), '电子': ('801080.SI', 'tech'),
    '电力设备': ('801730.SI', 'tech'),
}

def token():
    value = os.getenv('TUSHARE_TOKEN')
    if not value:
        for line in (ROOT / '.env').read_text().splitlines():
            if line.strip().startswith('TUSHARE_TOKEN='):
                value = line.split('=', 1)[1].strip().strip('\"\'')
                break
    if not value:
        raise RuntimeError('Tushare token missing')
    return value

def fetch(out):
    import tushare as ts
    pro = ts.pro_api(token())
    failures = []
    for name, (code, group) in ASSETS.items():
        path = out / f'{code}.csv'
        if path.exists():
            print(name, 'cached', flush=True)
            continue
        try:
            method = pro.index_daily if group == 'index' else pro.sw_daily
            chunks = [method(ts_code=code, start_date=f'{year}0101',
                             end_date=min(f'{year}1231', '20260930'))
                      for year in range(2022, 2027)]
            frame = pd.concat(chunks).drop_duplicates('trade_date').sort_values('trade_date')
            if len(frame) < 700:
                raise ValueError(f'insufficient history: {len(frame)}')
            frame.to_csv(path, index=False)
            print(name, len(frame), frame.trade_date.min(), frame.trade_date.max(), flush=True)
        except Exception as exc:
            failures.append({'asset': name, 'type': type(exc).__name__})
            print(name, 'FAILED', type(exc).__name__, flush=True)
    (out / 'fetch_failures.json').write_text(json.dumps(failures, ensure_ascii=False))
    if failures:
        raise RuntimeError('history fetch incomplete; see fetch_failures.json')

def pivots(frame):
    """Directional containment merge, strict fractals, confirmed-at right bar.

    Fixed proxy: alternating pivots at least four merged bars apart. Each
    prefix is rebuilt; terminal pivots can change later, never historical decisions.
    """
    merged = []
    direction = 1
    for row in frame.itertuples():
        bar = {'high': float(row.high), 'low': float(row.low), 'date': str(row.trade_date)}
        if merged:
            last = merged[-1]
            contains = ((bar['high'] <= last['high'] and bar['low'] >= last['low']) or
                        (bar['high'] >= last['high'] and bar['low'] <= last['low']))
            if contains:
                fun = max if direction > 0 else min
                last['high'] = fun(last['high'], bar['high'])
                last['low'] = fun(last['low'], bar['low'])
                last['date'] = bar['date']
                continue
            direction = 1 if bar['high'] > last['high'] else -1
        merged.append(bar)
    result = []
    for i in range(1, len(merged) - 1):
        a, b, c = merged[i-1:i+2]
        kind = ('high' if b['high'] > max(a['high'], c['high']) and b['low'] > max(a['low'], c['low'])
                else 'low' if b['low'] < min(a['low'], c['low']) and b['high'] < min(a['high'], c['high'])
                else None)
        if kind is None:
            continue
        point = {'kind': kind, 'price': b[kind], 'date': b['date'],
                 'confirmed_at': c['date'], 'position': i}
        if result and result[-1]['kind'] == kind:
            more_extreme = point['price'] > result[-1]['price'] if kind == 'high' else point['price'] < result[-1]['price']
            if more_extreme:
                result[-1] = point
        elif not result or i - result[-1]['position'] >= 4:
            result.append(point)
    return result

def structure_filter(frame):
    points = pivots(frame.tail(120))
    highs = [p for p in points if p['kind'] == 'high']
    lows = [p for p in points if p['kind'] == 'low']
    return bool(len(highs) >= 2 and len(lows) >= 2 and
                highs[-1]['price'] > highs[-2]['price'] and
                lows[-1]['price'] > lows[-2]['price'])

def simple_swing_filter(frame):
    """Supplementary baseline: V2-style 2+2 pivots, no containment/pen rules."""
    highs, lows = [], []
    frame = frame.tail(120).reset_index(drop=True)
    for i in range(2, len(frame)-2):
        other = frame.iloc[i-2:i+3].drop(index=i)
        if frame.high.iloc[i] > other.high.max():
            highs.append(float(frame.high.iloc[i]))
        if frame.low.iloc[i] < other.low.min():
            lows.append(float(frame.low.iloc[i]))
    return bool(len(highs) >= 2 and len(lows) >= 2 and highs[-1] > highs[-2] and lows[-1] > lows[-2])

def frozen_resistance(frame):
    """V2's exact nearest-resistance rule without unused touch-score rendering.

    The experiment verifies parity with the full engine before evaluation.
    Scoring does not participate in V2's nearest-level selection.
    """
    candidates, atr = generate_level_candidates(frame)
    current = float(frame.close.iloc[-1])
    cfg = DEFAULT_CONFIG
    tolerance = max(atr*cfg['cluster_atr_fraction'], current*cfg['cluster_price_fraction'])
    half_width = max(atr*cfg['zone_atr_fraction'], current*cfg['zone_price_fraction'])
    clusters = []
    for item in sorted(candidates, key=lambda r: r['price']):
        center = sum(x['price']*x['weight'] for x in clusters[-1])/sum(x['weight'] for x in clusters[-1]) if clusters else None
        if center is not None and abs(item['price']-center) <= tolerance:
            clusters[-1].append(item)
        else:
            clusters.append([item])
    centers = [sum(x['price']*x['weight'] for x in group)/sum(x['weight'] for x in group) for group in clusters]
    resistance = [c for c in centers if c > current+half_width]
    return round(min(resistance)+half_width, 2) if resistance else None

def summary(rows):
    if not rows:
        return {'n': 0}
    return {'n': len(rows), 'false_break_pct': round(100*np.mean([x['false_break'] for x in rows]), 2),
            'return_5d_pct': round(np.mean([x['ret5'] for x in rows]), 3),
            'return_10d_pct': round(np.mean([x['ret10'] for x in rows]), 3),
            'positive_10d_pct': round(100*np.mean([x['ret10'] > 0 for x in rows]), 2)}

def bootstrap(rows):
    """Resample signal months jointly across assets to retain common shocks."""
    months = sorted({r['date'][:6] for r in rows})
    blocks = {m: [r for r in rows if r['date'][:6] == m] for m in months}
    rng = np.random.default_rng(20261005)
    differences = []
    for _ in range(2000):
        sample = [r for m in rng.choice(months, len(months)) for r in blocks[m]]
        b = [r['false_break'] for r in sample if r['simple']]
        c = [r['false_break'] for r in sample if r['structure']]
        if len(b) >= 5 and len(c) >= 5:
            differences.append(100*(np.mean(b)-np.mean(c)))
    return list(np.round(np.quantile(differences, [.025, .975]), 2)) if differences else None

def evaluate(out):
    begin = time.monotonic()
    rows, manifest, parity_checks = [], [], 0
    for name, (code, group) in ASSETS.items():
        path = out / f'{code}.csv'
        frame = pd.read_csv(path, dtype={'trade_date': str}).sort_values('trade_date').reset_index(drop=True)
        manifest.append({'asset': name, 'code': code, 'rows': len(frame),
                         'first': frame.trade_date.iloc[0], 'last': frame.trade_date.iloc[-1],
                         'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        for end in np.linspace(121, len(frame), 5, dtype=int):
            sample = frame.iloc[end-120:end]
            full = build_market_structure_v2(sample, name, sample.trade_date.iloc[-1])['primary_box'].get('resistance')
            expected = full['zone']['upper'] if full else None
            if frozen_resistance(sample) != expected:
                raise AssertionError(f'V2 boundary mismatch: {name} {end}')
            parity_checks += 1
        last_event = -100
        for i in range(120, len(frame) - 10):
            if frame.trade_date.iloc[i] < '20230101':
                continue
            # Resistance zones are strictly above t-1 close; a non-rising
            # close cannot cross them. Embargoed events are never evaluated.
            if i-last_event < 10 or frame.close.iloc[i] <= frame.close.iloc[i-1]:
                continue
            visible = frame.iloc[max(0, i-120):i+1]
            # Previous boundary is built only from data through t-1.
            boundary = frozen_resistance(visible.iloc[:-1])
            if boundary is None:
                continue
            current = float(frame.close.iloc[i])
            if current <= boundary:
                continue
            last_event = i
            close = visible.close
            simple = bool(current > close.tail(20).mean() and
                          close.tail(20).mean() > close.iloc[-21:-1].mean())
            future = frame.iloc[i+1:i+11]
            rows.append({'asset': name, 'group': group, 'date': frame.trade_date.iloc[i],
                         'split': 'development' if frame.trade_date.iloc[i] < '20250101' else 'holdout',
                         'boundary': boundary, 'current': current, 'simple': simple,
                         'structure': structure_filter(visible),
                         'simple_swing': simple_swing_filter(visible),
                         'false_break': bool(float(future.close.iloc[-1]) <= boundary),
                         'ret5': 100*(float(future.close.iloc[4])/current-1),
                         'ret10': 100*(float(future.close.iloc[-1])/current-1)})
        print(name, 'evaluated', flush=True)
    report = {'manifest': manifest, 'evaluation': {}, 'by_asset': {}, 'v2_boundary_parity_checks': parity_checks,
              'elapsed_seconds': round(time.monotonic()-begin, 2)}
    for split in ['development', 'holdout']:
        subset = [r for r in rows if r['split'] == split]
        variants = {'v2': subset, 'simple': [r for r in subset if r['simple']],
                    'structure': [r for r in subset if r['structure']],
                    'simple_swing': [r for r in subset if r['simple_swing']]}
        report['evaluation'][split] = {k: summary(v) for k, v in variants.items()}
        success = [r for r in subset if not r['false_break']]
        report['evaluation'][split]['success_retention_pct'] = round(100*np.mean([r['structure'] for r in success]), 2) if success else None
        report['evaluation'][split]['simple_minus_structure_false_break_ci95_pp'] = bootstrap(subset)
    for name in ASSETS:
        subset = [r for r in rows if r['asset'] == name and r['split'] == 'holdout']
        report['by_asset'][name] = {k: summary([r for r in subset if k == 'v2' or r[k]]) for k in ['v2', 'simple', 'structure']}
    report['by_group'] = {}
    for group in ['index', 'tech', 'nontech']:
        subset = [r for r in rows if r['group'] == group and r['split'] == 'holdout']
        report['by_group'][group] = {k: summary([r for r in subset if k == 'v2' or r[k]]) for k in ['v2', 'simple', 'structure']}
    d, h = report['evaluation']['development'], report['evaluation']['holdout']
    ci = h['simple_minus_structure_false_break_ci95_pp']
    enough = h['v2']['n'] >= 100 and h['structure']['n'] >= 40
    comparisons = [v['structure'].get('false_break_pct', 100) < v['v2'].get('false_break_pct', 0) for v in report['by_group'].values()]
    report['gate'] = {
        'enough_samples': enough,
        'improvement_vs_v2': enough and h['v2']['false_break_pct']-h['structure']['false_break_pct'] >= 5,
        'improvement_vs_simple': enough and h['simple'].get('false_break_pct',0)-h['structure']['false_break_pct'] >= 3,
        'group_consistency': sum(comparisons) >= 2,
        'success_retention': (h['success_retention_pct'] or 0) >= 60,
        'development_same_direction': d['structure']['n'] > 0 and d['v2']['false_break_pct'] > d['structure']['false_break_pct'],
        'bootstrap': bool(ci and ci[0] > 0),
    }
    report['approved_for_decision'] = all(report['gate'].values())
    pd.DataFrame(rows).to_csv(out / 'events.csv', index=False)
    (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                              default=lambda value: value.item()))
    print(json.dumps(report['evaluation'], ensure_ascii=False, indent=2), flush=True)
    print('GATE', report['gate'], 'APPROVED', report['approved_for_decision'], flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['fetch', 'evaluate'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (fetch if args.mode == 'fetch' else evaluate)(args.output)
