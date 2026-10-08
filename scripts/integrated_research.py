#!/usr/bin/env python3
"""Small review-first session gate and tool-call ledger; no investment scoring."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import daily_decision_journal as journal

DEFAULT_ROOT = journal.WORKSPACE_ROOT / '技能数据/运行记录/综合研究'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def calendar_dates(path):
    data = read(path)
    values = data.get('calendar', []) if isinstance(data, dict) else data
    if values and isinstance(values[0], dict):
        values = [r['cal_date'] for r in values if int(r['is_open']) == 1]
    dates = sorted(set(str(v).replace('-', '') for v in values))
    for date in dates:
        datetime.strptime(date, '%Y%m%d')
    if not dates:
        raise ValueError('交易日历为空，不能用自然日代替交易日')
    return dates


def due_reviews(rows, dates, as_of):
    """Use actual freeze date, not an old data date, for forward horizons."""
    completed = {(r.get('review_id'), r.get('horizon_trading_days'))
                 for r in rows if r.get('event_type') == 'outcome_review'}
    result = []
    for row in rows:
        if row.get('event_type') != 'model_review':
            continue
        snapshot = row.get('review_snapshot', {})
        integrated = snapshot.get('integrated_research', {})
        times = [row.get('recorded_at'), snapshot.get('generated_at'),
                 snapshot.get('pilot_generated_at')]
        freeze = max([str(v)[:10].replace('-', '') for v in times if v]
                     + [str(row['trade_date'])])
        explicit = integrated.get('review_dates', {})
        future = [d for d in dates if d > freeze]
        for horizon in (3, 5, 10):
            if (row['review_id'], horizon) in completed:
                continue
            target = explicit.get(str(horizon))
            if target:
                target = str(target).replace('-', '')
                datetime.strptime(target, '%Y%m%d')
                if target <= freeze:
                    raise ValueError('原复核日期早于实际冻结时间，需要核对，不能当事前预测')
            elif len(future) >= horizon:
                target = future[horizon - 1]
            else:
                # If enough open days have passed, a truncated calendar must not
                # silently erase overdue work. Otherwise this horizon is future.
                if dates[-1] >= as_of and len([d for d in future if d <= as_of]) < horizon:
                    continue
                target = None
            if target is None or target <= as_of:
                result.append({'key': f"{row['review_id']}:{horizon}",
                               'review_id': row['review_id'], 'horizon': horizon,
                               'data_date': row['trade_date'], 'freeze_date': freeze,
                               'due_date': target,
                               'status': 'due' if target else 'calendar_missing'})
    return sorted(result, key=lambda x: (x['due_date'] or '', x['key']))


def start(run_dir, journal_path, calendar, as_of):
    run_dir = Path(run_dir)
    if (run_dir / 'session.json').exists():
        raise ValueError('该会话已存在；继续原会话，不覆盖启动记录')
    datetime.strptime(as_of, '%Y%m%d')
    rows = journal._rows(Path(journal_path))
    due = due_reviews(rows, calendar_dates(calendar), as_of)
    state = {'run_id': run_dir.name, 'started_at': datetime.now().astimezone().isoformat(),
             'as_of': as_of, 'journal': str(Path(journal_path).resolve()),
             'calendar': str(Path(calendar).resolve()), 'due_reviews': due,
             'gate': 'review_required' if due else 'ready',
             'review_result': '待复核' if due else '没有未完成的到期项'}
    state['previous_evidence_gaps'] = [
        {'review_id': r.get('review_id'), 'horizon': r.get('horizon_trading_days'),
         'prediction_result': r.get('prediction_result'), 'evidence_gaps': r['evidence_gaps']}
        for r in rows if r.get('event_type') == 'outcome_review' and r.get('evidence_gaps')
    ]
    write(run_dir / 'session.json', state)
    return state


def complete_review(run_dir, payload):
    state = read(Path(run_dir) / 'session.json')
    dispositions = {r['key']: r for r in payload['items']}
    outcomes = {(r.get('review_id'), r.get('horizon_trading_days'))
                for r in journal._rows(Path(state['journal']))
                if r.get('event_type') == 'outcome_review'}
    for item in state['due_reviews']:
        disposition = dispositions.get(item['key'], {})
        status = disposition.get('status')
        if status == 'recorded':
            if (item['review_id'], item['horizon']) not in outcomes:
                raise ValueError(f"结果尚未写入原账本: {item['key']}")
        elif status == 'pending_missing_data':
            if not disposition.get('reason') or not disposition.get('next_step'):
                raise ValueError('缺数项须记录原因和下次补证动作，不能标成复核完成')
        else:
            raise ValueError(f"到期项尚未处理: {item['key']}")
    write(Path(run_dir) / 'review_check.json', payload)
    state['gate'] = 'ready'
    state['review_checked_at'] = datetime.now().astimezone().isoformat()
    state['review_result'] = ('复核存在缺数待补' if any(
        r['status'] == 'pending_missing_data' for r in payload['items']) else '到期复核已入账')
    state['review_dispositions'] = payload['items']
    write(Path(run_dir) / 'session.json', state)
    return state


def call_tool(run_dir, tool, stage, purpose, command, inputs=(), timeout=1800):
    run_dir = Path(run_dir)
    state = read(run_dir / 'session.json')
    if stage != 'review' and state['gate'] != 'ready':
        raise ValueError('必须先处理到期复核，再调用新的分析工具')
    if not command:
        raise ValueError('缺少工具命令')
    call_id = uuid.uuid4().hex[:12]
    base = run_dir / 'calls' / call_id
    base.parent.mkdir(parents=True, exist_ok=True)
    started, clock = datetime.now().astimezone().isoformat(), time.monotonic()
    error = None
    with base.with_suffix('.stdout').open('w', encoding='utf-8') as stdout, \
            base.with_suffix('.stderr').open('w', encoding='utf-8') as stderr:
        try:
            result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                                    stdout=stdout, stderr=stderr, timeout=timeout, check=False)
            returncode = result.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            error, returncode = str(exc), -1
    row = {'event_id': f'call:{call_id}', 'event_type': 'tool_call', 'call_id': call_id,
           'run_id': state['run_id'], 'tool': tool, 'stage': stage, 'purpose': purpose,
           'command': command, 'input_paths': list(inputs), 'started_at': started,
           'duration_seconds': round(time.monotonic() - clock, 3), 'returncode': returncode,
           'status': 'success' if returncode == 0 else 'failed', 'error': error,
           'stdout_path': str(base.with_suffix('.stdout').resolve()),
           'stderr_path': str(base.with_suffix('.stderr').resolve()),
           'contribution': 'unassessed', 'provenance': 'live_wrapper'}
    journal._append(run_dir / 'tool_calls.jsonl', row)
    return row


def assess(run_dir, call_id, effect, note, used_in):
    path = Path(run_dir) / 'tool_calls.jsonl'
    if not any(r.get('call_id') == call_id and r['event_type'] == 'tool_call'
               for r in journal._rows(path)):
        raise ValueError('找不到该调用；不补造未运行工具')
    row = {'event_id': f'assessment:{uuid.uuid4().hex}', 'event_type': 'tool_assessment',
           'call_id': call_id, 'contribution': effect, 'note': note, 'used_in': used_in,
           'recorded_at': datetime.now().astimezone().isoformat()}
    journal._append(path, row)
    return row


def external_call(run_dir, tool, stage, purpose, evidence, duration=None):
    run_dir = Path(run_dir)
    state = read(run_dir / 'session.json')
    if stage != 'review' and state['gate'] != 'ready':
        raise ValueError('必须先处理到期复核，再记录新的分析调用')
    if not Path(evidence).is_file():
        raise ValueError('非脚本调用必须保存真实结果或失败证据')
    if duration is not None and duration < 0:
        raise ValueError('耗时不能为负')
    call_id = uuid.uuid4().hex[:12]
    row = {'event_id': f'call:{call_id}', 'event_type': 'tool_call', 'call_id': call_id,
           'run_id': state['run_id'], 'tool': tool, 'stage': stage, 'purpose': purpose,
           'command': None, 'input_paths': [], 'started_at': None,
           'recorded_at': datetime.now().astimezone().isoformat(),
           'duration_seconds': duration, 'returncode': None, 'status': 'external_recorded',
           'evidence_path': str(Path(evidence).resolve()),
           'contribution': 'unassessed', 'provenance': 'external_evidence'}
    journal._append(run_dir / 'tool_calls.jsonl', row)
    return row


def usage_summary(root):
    groups = defaultdict(list)
    for path in Path(root).rglob('tool_calls.jsonl'):
        rows = journal._rows(path)
        assessments = {r['call_id']: r for r in rows if r['event_type'] == 'tool_assessment'}
        for row in rows:
            if row['event_type'] == 'tool_call':
                groups[row['tool']].append((row, assessments.get(row['call_id'], {})))
    return {'tools': [{
        'tool': tool, 'calls': len(values),
        'failed': sum(r['status'] == 'failed' for r, _ in values),
        'total_seconds': round(sum(r['duration_seconds'] or 0 for r, _ in values), 3),
        'unknown_duration_calls': sum(r['duration_seconds'] is None for r, _ in values),
        'contributions': dict(Counter(a.get('contribution', 'unassessed') for _, a in values)),
        'evidence': [{'run_id': r['run_id'], 'call_id': r['call_id'], 'purpose': r['purpose'],
                      'note': a.get('note'), 'used_in': a.get('used_in')} for r, a in values],
    } for tool, values in sorted(groups.items())],
            'boundary': '使用少不等于鸡肋；否决候选和有效空名单也有价值；不计算虚假收益贡献评分'}


def main():
    parser = argparse.ArgumentParser(description='综合研究：先复核，记录工具调用与贡献')
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('start')
    p.add_argument('--run-dir', required=True)
    p.add_argument('--journal', default=str(journal.DEFAULT_JOURNAL))
    p.add_argument('--calendar', required=True, help='已获取的交易日列表或含calendar字段的JSON')
    p.add_argument('--as-of', required=True, help='本次已完成收盘日YYYYMMDD')
    p = sub.add_parser('reviewed')
    p.add_argument('--run-dir', required=True)
    p.add_argument('--payload', required=True)
    p = sub.add_parser('call')
    p.add_argument('--run-dir', required=True)
    p.add_argument('--tool', required=True)
    p.add_argument('--stage', choices=('review', 'market', 'sector', 'event', 'stock', 'freeze'), required=True)
    p.add_argument('--purpose', required=True)
    p.add_argument('--input', action='append', default=[])
    p.add_argument('--timeout', type=int, default=1800)
    p.add_argument('command', nargs=argparse.REMAINDER)
    p = sub.add_parser('assess')
    p.add_argument('--run-dir', required=True)
    p.add_argument('--call-id', required=True)
    p.add_argument('--effect', choices=('changed', 'supported', 'duplicate', 'no_increment', 'empty_useful', 'failed'), required=True)
    p.add_argument('--note', required=True)
    p.add_argument('--used-in', required=True, help='影响了哪项判断/否决/复核，或未采用原因')
    p = sub.add_parser('summary')
    p.add_argument('--root', default=str(DEFAULT_ROOT))
    p = sub.add_parser('external')
    p.add_argument('--run-dir', required=True)
    p.add_argument('--tool', required=True)
    p.add_argument('--stage', choices=('review', 'market', 'sector', 'event', 'stock', 'freeze'), required=True)
    p.add_argument('--purpose', required=True)
    p.add_argument('--evidence', required=True)
    p.add_argument('--duration', type=float)
    args = parser.parse_args()
    if args.cmd == 'start':
        result = start(args.run_dir, args.journal, args.calendar, args.as_of)
    elif args.cmd == 'reviewed':
        result = complete_review(args.run_dir, read(args.payload))
    elif args.cmd == 'call':
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        result = call_tool(args.run_dir, args.tool, args.stage, args.purpose, command, args.input, args.timeout)
    elif args.cmd == 'assess':
        result = assess(args.run_dir, args.call_id, args.effect, args.note, args.used_in)
    elif args.cmd == 'external':
        result = external_call(args.run_dir, args.tool, args.stage, args.purpose, args.evidence, args.duration)
    else:
        result = usage_summary(args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.cmd == 'call' and result['status'] == 'failed':
        sys.exit(1)


if __name__ == '__main__':
    main()
