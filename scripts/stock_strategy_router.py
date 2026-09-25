#!/usr/bin/env python3
"""三类交易任务的统一扫描入口。

任务：
  short-ma5       推土机：涨停激活＋自适应趋势分层＋低吸执行，计划隔夜，不进入六层。
  catalyst-swing  催化趋势波段，计划持有15-30天，候选进入六层队列。
  quality-core    质量复利底仓，计划持有3-6个月以上，候选进入六层队列。

路由器只编排已有扫描器并统一结果协议，不跨策略计算总排名。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
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
    "短线": "short-ma5", "短线低吸": "short-ma5", "推土机": "short-ma5",
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
    for line in completed.stderr.splitlines():
        if "[WARNING]" in line or "[ERROR]" in line:
            print(f"{Path(command[1]).name}: {line}", file=sys.stderr)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"扫描器没有返回合法JSON：{completed.stdout[-1000:]}") from exc


def short_reference_bid(row: dict) -> dict:
    """Give a quotable example inside the existing experimental 50%-75% band.

    This is a display aid, not an additional stock selection or fill signal.
    """
    if not row.get("auction_band_valid"):
        return {"reference_bid": None, "confirmation_low": None, "confirmation_high": None}
    low = Decimal(str(row["auction_reference_low"]))
    high = Decimal(str(row["auction_reference_high"]))
    tick = Decimal("0.01")
    if high < low:
        return {"reference_bid": None, "confirmation_low": None, "confirmation_high": None}
    width = high - low
    band_low = low + width * Decimal("0.50")
    band_high = low + width * Decimal("0.75")
    first = (band_low / tick).to_integral_value(rounding=ROUND_CEILING) * tick
    last = (band_high / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    if first > last:
        return {"reference_bid": None, "confirmation_low": None, "confirmation_high": None}
    target = low + width * Decimal("0.625")
    bid = (target / tick).to_integral_value(rounding=ROUND_HALF_UP) * tick
    bid = min(max(bid, first), last)
    return {"reference_bid": float(bid), "confirmation_low": float(first), "confirmation_high": float(last)}


def short_execution_plan(row: dict) -> str:
    """Return scenario-based auction guidance; never print NaN price levels."""
    band_valid = row.get("auction_band_valid")
    if band_valid is None:
        band_valid = row.get("auction_low25") is not None or row.get("auction_reference_low") is not None
    if not band_valid:
        return (
            "昨收与次日MA5临界价之间没有合规报价空间，保留为趋势观察标的；"
            "不倒置区间机械挂单；9:25核对结果，未成交委托在可撤时发起撤单并等待确认"
        )
    quote = short_reference_bid(row)
    price_text = (
        f"试验限价买入报价上限{quote['reference_bid']:.2f}元；可报价确认带"
        f"{quote['confirmation_low']:.2f}—{quote['confirmation_high']:.2f}元；"
        if quote["reference_bid"] is not None else "确认带内无可报价价位，竞价不预埋；"
    )
    return (
        price_text +
        f"理论临界价{row.get('next_ma5_threshold')}元，昨收{row.get('close')}元；"
        "该报价是买入限价，不保证按此价格成交；若集合竞价成交价更低，仍可能以更低价成交；"
        "默认先观察9:25开盘结果，9:30后重算动态MA5并确认承接，再决定是否委托；收盘后报价不能机械照搬。"
        "只有主动接受低价成交及撤单延迟风险时，才考虑9:20—9:25预埋；"
        "未成交单可能进入连续竞价，撤单须以券商回报为准"
    )


def normalize_short(rows: list[dict]) -> list[dict]:
    normalized = []
    for row in rows:
        hints = short_chart_teaching(row)
        normalized.append({
        "trade_date": row.get("trade_date"), "task": "short-ma5",
        "code": row.get("ts_code"), "name": row.get("name"),
        "candidate_reason": "推土机双通道：" + str(row.get("selection_lane") or "待分层"),
        "holding_period": "隔夜；买入后的下一交易日闭环", "market_gate": "推土机市场闸门待盘前确认",
        "strategy_score": None,
        "current_state": f"{row.get('continuity_tier') or '持续性待分层'}；{row.get('signal') or '—'}；{row.get('trend_stage') or '—'}；{row.get('risk_tier') or '—'}；活跃排名{row.get('activity_rank') or '—'}",
        "next_action": short_execution_plan(row),
        "needs_six_layer": False, "risk_flags": row.get("risk_flags", ""),
        "chart_teaching": hints,
        "execution_state": "待人工确认",
        "price_plan": short_reference_bid(row),
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
    trend_stage = row.get("trend_stage")
    trend_days = row.get("consecutive_days_above_ma5")
    lane = row.get("selection_lane")
    if lane == "完全均线型":
        structure = "均线发散、贴MA5且推进较均匀；仍需肉眼排查异常K线"
    elif lane == "活跃兼顾型":
        structure = "满足共同上升底线并偏重成交与振幅；均线连续性弱于完全均线型"
    elif lane == "双通道":
        structure = "同时进入完全均线与活跃通道，结构和活跃度相对均衡"
    elif signal == "E2_MA5_EARLY":
        structure = "MA5上方仅形成早期结构，属于边缘观察"
    elif signal == "S1+S2":
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
        activity += f"；{row.get('volume_observation') or '倍量状态待核验'}"
    else:
        activity = "量能活跃度待核验"
    risk = row.get("risk_flags") or "未触发脚本风险标记；仍需看板块和分时承接"
    theme = row.get("limit_theme") or row.get("industry") or "待人工核验"
    reason = row.get("limit_up_reason") or "数据源未提供，盘前需人工核验"
    if isinstance(trend_days, int):
        duration = f"连续{trend_days}日站在MA5上，阶段={trend_stage or '待分层'}；2/5/8/16日均非固定公式"
    else:
        duration = "趋势持续日数待核验"
    low = row.get("auction_reference_low")
    high = row.get("auction_reference_high")
    quote = short_reference_bid(row)
    if row.get("auction_band_valid"):
        auction = (
            f"合规报价区间{low}—{high}；"
            + (f"试验参考报价{quote['reference_bid']:.2f}元，确认带{quote['confirmation_low']:.2f}—{quote['confirmation_high']:.2f}元；"
               if quote["reference_bid"] is not None else "确认带内无可报价价位；")
            + "默认先看9:25开盘结果，9:30后重算动态MA5并确认承接；预埋限价单可能低于报价成交，未成交单可能进入连续竞价"
        )
    else:
        auction = "昨收与次日MA5临界价之间没有合规报价空间；不能倒置区间机械挂单"
    return {
        "形态": structure, "趋势阶段": duration, "启动": activation, "位置": position, "量能": activity,
        "竞价计划": auction,
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
        "trade_date": row.get("trade_date"), "task": "catalyst-swing",
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
            early_experimental = details.get("signal") == "E2_MA5_EARLY"
            close = details.get("close")
            low_price = isinstance(close, (int, float)) and close <= 20
            activity_tier, activity_reason = classify_short_activity(details)
            continuity_tier = {
                "A_持续强势": 0,
                "B_结构合格待确认": 1,
                "C_活跃观察": 2,
            }.get(details.get("continuity_tier"), 2)
            if just_limit_up:
                activity_tier, shape_tier = 3, 3
                reason = "当日刚涨停，不列为次日低吸优先；等待分歧后的新机会"
            elif early_experimental:
                activity_tier, shape_tier = max(activity_tier, 2), 2
                reason = "两日早期试验层；仅在非退潮环境小仓观察，不与5/8/16日成熟趋势同档"
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
            tier = (continuity_tier, activity_tier, shape_tier, activity_rank, price_tiebreak)
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
        "short-ma5": "推土机短线池（涨停激活＋自适应趋势分层）",
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
                "收盘后生成次日MA5临界价、可报价确认带和一个具体的试验报价。报价是限价买入上限，不是保证成交价。默认9:25观察开盘结果，9:30后确认止跌承接再决定是否委托；若9:20—9:25预埋，须事先接受集合竞价可能以低于报价的价格成交且该时段不能撤单。未成交委托可能进入连续竞价；撤单只有收到券商确认才算完成。黄色环境不做预埋，红色停止新开仓。确认带和报价仍需更长样本及竞价数据验证。", "",
            ]
            lines += ["### 前5图形教学提示", ""]
            for row in focus.get(task, []):
                hints = row.get("chart_teaching") or {}
                lines += [f"- {row['name']}（{row['code']}）：" + "；".join(f"{key}：{value}" for key, value in hints.items())]
            lines.append("")
            lines += [
                "### 30秒盘中确认卡", "",
                "- □ 默认等9:25开盘结果，9:30后再按承接决定；预埋只限昨日绿灯且今早跌停未扩散，并接受低价成交与不可撤单风险。",
                "- □ 候选所属题材相对市场更强且有正常成交的跟随；不机械以‘至少两只上涨’替代扩散判断，独立事件另行核验。",
                "- □ 参考报价严格低于昨收、高于次日MA5临界价；它是买入限价上限，不保证成交价或避免更低价成交。",
                "- □ 未成交单若需撤销，已收到券商撤单确认；确认前不得下第二笔。",
                "- □ 原版只认低开下杀；止跌收回次日MA5临界价，第一次回踩不破临界价或前低，并出现成交改善或相对强势。",
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


def write_short_html_report(rows: list[dict], focus: list[dict], path: Path, generated_at: str) -> None:
    """Readable copy of the same short-list evidence, without changing ranking."""
    esc = lambda value: html.escape(str(value if value is not None else "—"))
    cards = []
    for row in focus:
        plan = row.get("price_plan") or {}
        bid = plan.get("reference_bid")
        price = f"{bid:.2f} 元" if bid is not None else "无有效参考报价"
        cards.append(
            f"<article><h2>{esc(row['name'])} <small>{esc(row['code'])}</small></h2>"
            f"<p class='price'>{esc(price)}</p>"
            f"<p><strong>筛选状态：</strong>{esc(row.get('current_state'))}</p>"
            f"<p><strong>关注原因：</strong>{esc(row.get('focus_reason'))}</p>"
            f"<p><strong>价格与执行：</strong>{esc(row.get('next_action'))}</p></article>"
        )
    all_rows = "".join(
        f"<tr><td>{esc(row['code'])}</td><td>{esc(row['name'])}</td>"
        f"<td>{esc((row.get('price_plan') or {}).get('reference_bid'))}</td>"
        f"<td>{esc(row.get('current_state'))}</td></tr>"
        for row in rows
    )
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>推土机短线观察池</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;background:#f3f5f6;color:#182530;margin:0;padding:25px 16px}}
main{{max-width:900px;margin:auto}}h1{{font-size:29px}}.meta{{color:#65747d}}.notice,article,.list{{background:white;border-radius:12px;padding:20px 24px;margin:16px 0;box-shadow:0 5px 20px #2030400d}}
.notice{{border-left:4px solid #188458}}article h2{{margin:0}}small{{color:#687780;font-size:14px}}.price{{color:#126e55;font-weight:700;font-size:25px;margin:10px 0}}
p{{line-height:1.7}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #e5eaed;vertical-align:top}}@media(max-width:650px){{td,th{{font-size:13px}}}}</style></head><body><main>
<h1>推土机短线观察池</h1><p class="meta">生成时间 {esc(generated_at)} · 数据日期 {esc(rows[0]['trade_date'] if rows else '—')} · 共 {len(rows)} 只</p>
<div class="notice"><strong>先看结论：</strong>以下价格是收盘后生成的试验买入限价上限，不是成交保证。默认观察集合竞价，9:25核对开盘，9:30后重算动态MA5并确认承接，再决定是否委托。未成交单可能进入连续竞价，撤单以券商确认回报为准。</div>
<h2>盘前重点核查</h2>{''.join(cards)}<div class="list"><h2>完整观察池</h2>
<table><thead><tr><th>代码</th><th>名称</th><th>试验参考价（元）</th><th>当前状态</th></tr></thead><tbody>{all_rows}</tbody></table></div>
</main></body></html>"""
    path.write_text(page, encoding="utf-8")


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
    run_dir = args.output_dir or WORKSPACE_ROOT / "技能数据" / "运行记录" / "策略任务" / generated_at
    # 子扫描器在 REPO_ROOT 下执行；相对输出路径会被错误写到repo目录，
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
    html_path = run_dir / "strategy_tasks.html"
    if task == "short-ma5":
        write_short_html_report(results[task], focus[task], html_path, generated_at)
    print(json.dumps({
        "generated_at": generated_at,
        "counts": {key: len(value) for key, value in results.items()},
        "focus_counts": {key: len(value) for key, value in focus.items()},
        "report": str(report_path), "html": str(html_path) if task == "short-ma5" else None,
        "json": str(json_path),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
