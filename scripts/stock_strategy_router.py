#!/usr/bin/env python3
"""三类交易任务的统一扫描入口。

任务：
  short-ma5       涨停激活＋MA10稳态/MA5稳升，计划持有1-2天，不进入六层。
  catalyst-swing  催化趋势波段，计划持有15-30天，候选进入六层队列。
  quality-core    质量复利底仓，计划持有3-6个月以上，候选进入六层队列。

路由器只编排已有扫描器并统一结果协议，不跨策略计算总排名。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = Path(os.environ.get("TZ_CODEX_HOME", REPO_ROOT.parent)).expanduser()
SHORT_SCRIPT = REPO_ROOT / "skills/market-outlook/scripts/limit_up_trend_scanner.py"
SWING_SCRIPT = REPO_ROOT / "skills/top-picks/references/catalyst_left_side_scanner.py"
CORE_SCRIPT = REPO_ROOT / "skills/quality-compounder/references/quality_compounder_screener.py"

TASK_ALIASES = {
    "short-ma5": "short-ma5", "short": "short-ma5", "ma5": "short-ma5",
    "短线": "short-ma5", "短线低吸": "short-ma5",
    "catalyst-swing": "catalyst-swing", "swing": "catalyst-swing",
    "波段": "catalyst-swing", "催化波段": "catalyst-swing",
    "quality-core": "quality-core", "core": "quality-core",
    "底仓": "quality-core", "长期": "quality-core",
    "all": "all", "全部": "all",
}


def task_command(task: str, as_of: str | None, top: int, output_dir: Path) -> list[str]:
    if task == "short-ma5":
        cmd = [sys.executable, str(SHORT_SCRIPT), "--output-dir", str(output_dir)]
        if as_of:
            cmd += ["--as-of", as_of]
        return cmd
    if task == "catalyst-swing":
        return [sys.executable, str(SWING_SCRIPT), "--top", str(top), "--json"]
    if task == "quality-core":
        return [sys.executable, str(CORE_SCRIPT), "--phase", "screen", "--json"]
    raise ValueError(f"未知任务：{task}")


def run_json_command(command: list[str], timeout: int = 1800) -> Any:
    env = os.environ.copy()
    env.setdefault("TZ_CODEX_HOME", str(WORKSPACE_ROOT))
    env.setdefault("PYTHONPYCACHEPREFIX", "/tmp/tz_codex_pycache")
    completed = subprocess.run(
        command, cwd=REPO_ROOT, env=env, text=True, capture_output=True,
        timeout=timeout, check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"扫描器失败（exit={completed.returncode}）：{detail[-2000:]}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"扫描器没有返回合法JSON：{completed.stdout[-1000:]}") from exc


def normalize_short(rows: list[dict]) -> list[dict]:
    normalized = []
    for row in rows:
        hints = short_chart_teaching(row)
        normalized.append({
        "trade_date": row.get("trade_date"), "task": "short-ma5",
        "code": row.get("ts_code"), "name": row.get("name"),
        "candidate_reason": "近期涨停激活＋MA10稳态/MA5稳升：" + str(row.get("signal") or "未标记"),
        "holding_period": "1-2天", "market_gate": "短线情绪闸门待盘前确认",
        "strategy_score": None,
        "current_state": f"{row.get('signal') or '—'}；{row.get('risk_tier') or '—'}；活跃排名{row.get('activity_rank') or '—'}",
        "next_action": f"按低吸规则观察，次日MA5临界价约{row.get('next_ma5_threshold')}；盘中确认后才授权",
        "needs_six_layer": False, "risk_flags": row.get("risk_flags", ""),
        "chart_teaching": hints,
        "execution_state": "待人工确认",
        "source_script": SHORT_SCRIPT.name,
        "details": row,
        })
    return normalized


def short_chart_teaching(row: dict) -> dict[str, str]:
    """把扫描字段翻译成看图提示，不制造新的综合分。"""
    signal = row.get("signal")
    days = row.get("days_since_limit_up")
    bias = row.get("bias_ma5_pct")
    volume_ratio = row.get("volume_ratio_5_20")
    if signal == "S1+S2":
        structure = "MA5稳升且连续站在MA10上，属于推土机式稳定推进形态"
    elif signal == "S1_MA10_STABLE":
        structure = "仍守MA10平台，但MA5推进不足；看回踩后能否重新提速"
    else:
        structure = "主要靠MA5短趋势维持，稳定性弱于S1+S2"
    activation = f"最近涨停距今{days}个交易日" if isinstance(days, int) else "涨停新鲜度待核验"
    if days == 0:
        activation += "，当天已加速，不把涨停板本身当低吸买点"
    if isinstance(bias, (int, float)):
        position = f"距MA5 {bias:+.1f}%：" + ("位置较贴近，重点看承接" if abs(bias) <= 3 else "位置偏离，等回踩不追价")
    else:
        position = "MA5距离待核验"
    if isinstance(volume_ratio, (int, float)):
        activity = f"近5日/20日均量比{volume_ratio:.2f}倍：" + ("资金活跃" if volume_ratio >= 1.2 else "活跃度一般，盘中需看到放量")
    else:
        activity = "量能活跃度待核验"
    risk = row.get("risk_flags") or "未触发脚本风险标记；仍需看板块和分时承接"
    theme = row.get("limit_theme") or row.get("industry") or "待人工核验"
    reason = row.get("limit_up_reason") or "数据源未提供，盘前需人工核验"
    return {
        "形态": structure, "启动": activation, "位置": position, "量能": activity,
        "板块身份": str(theme), "涨停原因": str(reason), "风险": risk,
    }


def classify_short_activity(details: dict) -> tuple[int, str]:
    """活跃度只分档，不计算看似精确的综合分。"""
    ratio = details.get("volume_ratio_5_20")
    amount = details.get("avg_amount_5d_qianyuan")
    days = details.get("days_since_limit_up")
    bearish = bool(details.get("large_bearish_day_5d"))
    liquid = isinstance(amount, (int, float)) and amount >= 100000  # 约1亿元/日
    expanded = isinstance(ratio, (int, float)) and ratio >= 1.2
    fresh = isinstance(days, int) and 1 <= days <= 6
    if liquid and expanded and fresh and not bearish:
        return 0, "活跃确认：涨停新鲜、量能放大且流动性充足"
    if liquid and not bearish and (expanded or fresh):
        return 1, "个股活跃：流动性充足，但量能或涨停新鲜度尚不完整"
    return 2, "形态候选：图形入池，活跃度仍需盘中证明"


def normalize_swing(rows: list[dict]) -> list[dict]:
    return [{
        "trade_date": None, "task": "catalyst-swing",
        "code": row.get("code"), "name": row.get("name"),
        "candidate_reason": row.get("event_name") or "活跃催化关联候选",
        "holding_period": "15-30天",
        "market_gate": f"市场响应：{row.get('market_response_state', row.get('sector_repair_state', 'no_data'))}",
        "strategy_score": row.get("final_score"),
        "current_state": row.get("lr_label"),
        "next_action": row.get("six_layer_action") or "进入六层候选队列，核验催化、市场响应和定价程度",
        "needs_six_layer": True, "risk_flags": "",
        "source_script": SWING_SCRIPT.name,
        "details": row,
    } for row in rows]


def normalize_core(payload: dict) -> list[dict]:
    trade_date = payload.get("trade_date")
    return [{
        "trade_date": trade_date, "task": "quality-core",
        "code": row.get("ts_code"), "name": row.get("name"),
        "candidate_reason": "沪深300十年质量与估值硬门槛通过",
        "holding_period": "3-6个月以上", "market_gate": "长期配置不使用短线情绪闸门",
        "strategy_score": row.get("cagr_np"), "current_state": "待六层验证",
        "next_action": "进入六层候选队列，核验估值、长期趋势与未来催化",
        "needs_six_layer": True, "risk_flags": "",
        "source_script": CORE_SCRIPT.name,
        "details": row,
    } for row in payload.get("candidates", [])]


def execute_task(task: str, as_of: str | None, top: int, run_dir: Path) -> list[dict]:
    task_dir = run_dir / task
    task_dir.mkdir(parents=True, exist_ok=True)
    command = task_command(task, as_of, top, task_dir)
    payload = run_json_command(command)
    if task == "short-ma5":
        summary = payload
        result_path = task_dir / f"limit_up_trend_{summary['trade_date']}.json"
        rows = json.loads(result_path.read_text(encoding="utf-8"))
        return normalize_short(rows)[:top]
    if task == "catalyst-swing":
        return normalize_swing(payload)
    return normalize_core(payload)[:top]


def select_focus(task: str, rows: list[dict], limit: int = 5) -> list[dict]:
    """从原始候选中生成盘前核查池，不改变原池成员与排序。

    这里仍然只使用收盘数据。结果不是买入建议；短线标的必须在次日由板块强度、
    开盘位置和价格承接完成最后确认。
    """
    ranked: list[tuple[int, int, dict, str]] = []
    for index, row in enumerate(rows):
        details = row.get("details") or {}
        if task == "short-ma5":
            is_a = details.get("risk_tier") == "A_结构较好"
            no_large_bearish = not bool(details.get("large_bearish_day_5d"))
            bias = details.get("bias_ma5_pct")
            near_ma5 = isinstance(bias, (int, float)) and abs(bias) <= 3
            days_since_limit = details.get("days_since_limit_up")
            just_limit_up = days_since_limit == 0
            close = details.get("close")
            low_price = isinstance(close, (int, float)) and close <= 20
            activity_tier, activity_reason = classify_short_activity(details)
            if just_limit_up:
                activity_tier, shape_tier = 3, 3
                reason = "当日刚涨停，不列为次日低吸优先；等待分歧后的新机会"
            elif is_a and no_large_bearish and near_ma5:
                shape_tier = 0
                reason = activity_reason + "；A档且贴近MA5"
            elif is_a and no_large_bearish:
                shape_tier = 1
                reason = activity_reason + "；A档但离MA5较远，等待回踩"
            else:
                shape_tier = 2
                reason = activity_reason + "；结构风险需人工复核"
            activity_rank = details.get("activity_rank")
            activity_rank = activity_rank if isinstance(activity_rank, int) else 9999
            price_tiebreak = 0 if low_price else 1
            tier = (activity_tier, shape_tier, activity_rank, price_tiebreak)
        elif task == "catalyst-swing":
            state = row.get("current_state")
            repair = details.get("sector_repair_state")
            lr_score = details.get("lr_score") or 0
            response = details.get("market_response_state", repair)
            active_bucket = details.get("event_bucket") in {"red", "yellow"}
            if active_bucket and state == "就绪" and response == "confirmed" and lr_score >= 6:
                tier, reason = 0, "活跃催化、个股就绪且市场响应已确认，优先进入六层"
            elif active_bucket and state == "预热" and response == "confirmed" and lr_score >= 6:
                tier, reason = 1, "活跃催化且市场响应确认，个股处于预热"
            elif active_bucket and response == "early":
                tier, reason = 2, "催化有效但市场仅早期响应，保留观察并等待扩散"
            else:
                tier, reason = 3, "事件候选补位，市场响应不足或数据不足"
        else:
            tier, reason = 0, "原始质量排序靠前，进入六层验证"

        focused = dict(row)
        focused["focus_reason"] = reason
        ranked.append((tier, index, focused, reason))
    return [item[2] for item in sorted(ranked, key=lambda item: (item[0], item[1]))[:limit]]


def write_report(
    results: dict[str, list[dict]], focus: dict[str, list[dict]], path: Path, generated_at: str,
) -> None:
    labels = {
        "short-ma5": "涨停激活＋MA10稳态/MA5稳升短线池",
        "catalyst-swing": "催化趋势波段池",
        "quality-core": "质量复利底仓池",
    }
    lines = [
        f"# 交易任务扫描总览 — {generated_at}", "",
        "> 三套策略独立输出，不计算跨策略总排名。盘前核查池只压缩注意力，不是买入建议；盘中仍须完成人工确认。", "",
    ]
    for task, rows in results.items():
        lines += [f"## {labels[task]}", "", f"候选：{len(rows)}只；计划持有：{rows[0]['holding_period'] if rows else '—'}。", ""]
        if not rows:
            lines += ["本次无候选或任务未产生结果。", ""]
            continue
        lines += ["### 盘前重点核查池（最多5只）", "", "|代码|名称|收敛原因|当前状态|下一步|", "|---|---|---|---|---|"]
        for row in focus.get(task, []):
            lines.append(
                f"|{row['code']}|{row['name']}|{row['focus_reason']}|{row['current_state'] or '—'}|{row['next_action']}|"
            )
        if task == "short-ma5":
            lines += [
                "", "### 次日人工确认（当前数据不能替代）", "",
                "只在目标板块没有退潮、个股未高开超过计划上限、回踩MA5/MA10有承接并重新站回分时均价时，才允许小仓试错。无法确认则不交易。", "",
            ]
            lines += ["### 前5图形教学提示", ""]
            for row in focus.get(task, []):
                hints = row.get("chart_teaching") or {}
                lines += [f"- {row['name']}（{row['code']}）：" + "；".join(f"{key}：{value}" for key, value in hints.items())]
            lines.append("")
            lines += [
                "### 30秒盘中确认卡", "",
                "- □ 市场未进入明显退潮，跌停没有快速扩散。",
                "- □ 候选所属题材至少有两只以上同步转强，不是单股孤涨。",
                "- □ 个股没有超过计划高开上限，也不是直线拉升后追价。",
                "- □ 计划位置出现承接，并在承接之后重新站回分时均价。",
                "- □ 已写好失效价、首笔仓位和最迟退出日。",
                "任一项不能确认：不下单。", "",
                "### 次日退出三情景", "",
                "|次日状态|观察|动作|", "|---|---|---|",
                "|强|高开后承接良好，保持分时强势|先兑现一半；剩余不涨停也须在收盘前退出|",
                "|平|平开或小幅波动，不能快速转强|反弹分批退出，不等待基本面理由|",
                "|弱|低开、跌破前日承接低点或板块退潮|优先退出，不补仓、不改成波段|", "",
            ]
        lines += ["", "### 扩展观察池（前20只，保留原始排序）", "", "|代码|名称|候选原因|市场/板块闸门|当前状态|下一步|六层|", "|---|---|---|---|---|---|---|"]
        for row in rows[:20]:
            lines.append(
                f"|{row['code']}|{row['name']}|{row['candidate_reason']}|{row['market_gate']}|{row['current_state'] or '—'}|"
                f"{row['next_action']}|{'是' if row['needs_six_layer'] else '否'}|"
            )
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="短线、催化波段、质量底仓统一交易任务入口")
    parser.add_argument("--task", required=True, choices=sorted(TASK_ALIASES))
    parser.add_argument("--as-of", help="YYYYMMDD；短线任务支持历史截止日，其他任务当前使用最新数据")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true", help="只展示将调用的扫描器")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    task = TASK_ALIASES[args.task]
    tasks = ["short-ma5", "catalyst-swing", "quality-core"] if task == "all" else [task]
    if args.as_of and any(item != "short-ma5" for item in tasks):
        raise SystemExit("目前--as-of只对short-ma5生效；催化波段和质量底仓使用各自最新数据。")

    generated_at = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = args.output_dir or WORKSPACE_ROOT / "分析记录" / "策略任务" / generated_at
    # 子扫描器在 REPO_ROOT 下执行；相对输出路径会被错误写到 repo/分析记录，
    # 而路由器随后从工作区根目录读取。统一转成绝对路径，保证写入与读取同址。
    if not run_dir.is_absolute():
        run_dir = (WORKSPACE_ROOT / run_dir).resolve()
    if args.dry_run:
        print(json.dumps({item: task_command(item, args.as_of, args.top, run_dir / item) for item in tasks}, ensure_ascii=False, indent=2))
        return

    run_dir.mkdir(parents=True, exist_ok=True)
    results = {item: execute_task(item, args.as_of, args.top, run_dir) for item in tasks}
    focus = {item: select_focus(item, rows) for item, rows in results.items()}
    payload = {"generated_at": generated_at, "tasks": results, "focus": focus}
    json_path = run_dir / "strategy_tasks.json"
    report_path = run_dir / "strategy_tasks.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(results, focus, report_path, generated_at)
    print(json.dumps({
        "generated_at": generated_at,
        "counts": {key: len(value) for key, value in results.items()},
        "focus_counts": {key: len(value) for key, value in focus.items()},
        "report": str(report_path), "json": str(json_path),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
