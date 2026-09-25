#!/usr/bin/env python3
"""催化窗口期模型 v1.0

从reviews.csv实证校准的A-E五类催化类型参数，用于计算每个活跃事件的当前窗口位置。

类型定义（基于15条实证数据校准）：
  A类：发布会/展会/季节性      峰值短暂，5-10天情绪窗口
  B类：IPO里程碑/重大订单      三阶段扩散，30-90天主窗口
  C类：市场结构/政策管制       Wave1情绪7-14天，Wave2季报验证60天后
  D类：涨价传导型              Wave1 14-42天，Wave2季报验证60-180天
  E类：叙事/早期信号           操作窗口稀疏，不以时间窗口为主要依据
"""

import re
from datetime import date, datetime, timedelta
from typing import Optional, Tuple


STRONG_EVIDENCE_KEYWORDS = (
    "重大合同", "长期供货", "供货协议", "正式合同", "订单", "中标",
    "业绩预告", "财报", "批量交付", "正式投产", "客户认证", "涨价函",
    "公告实施", "正式发布", "获得许可",
)
WEAK_OR_REPLAY_KEYWORDS = ("传闻", "转述", "尚缺原始", "待核验", "小作文", "未证实")


def qualify_evidence_activation(
    latest_update: str,
    evidence_text: str,
    today: Optional[date] = None,
    max_age_days: int = 14,
) -> dict:
    """Identify a recent high-grade evidence upgrade; price confirmation happens later."""
    today = today or date.today()
    update_date = parse_event_date(latest_update)
    text = str(evidence_text or "")
    strong = [token for token in STRONG_EVIDENCE_KEYWORDS if token in text]
    weak = [token for token in WEAK_OR_REPLAY_KEYWORDS if token in text]
    age = (today - update_date).days if update_date else None
    eligible = bool(update_date and strong and age is not None and 0 <= age <= max_age_days)
    # Weak wording does not veto a separately disclosed formal contract/order.
    if weak and not any(token in text for token in ("重大合同", "正式合同", "供货协议", "业绩预告", "正式发布")):
        eligible = False
    return {
        "eligible": eligible,
        "evidence_date": update_date,
        "evidence_age_days": age,
        "strong_matches": strong,
        "weak_matches": weak,
    }

# ── 校准参数（基于reviews.csv实证数据）──────────────────────────────────
CATALYST_TYPES = {
    "A": {
        "name": "发布会/展会/季节性",
        "emoji": "🎭",
        "entry_days": (-3, 0),       # 相对峰值的入场窗口（天）
        "peak_offset": 0,            # 峰值相对事件起点的偏移
        "half_life": 5,              # 半衰期（天）
        "wave1_end": 10,             # Wave1结束
        "has_wave2": False,
        "total_days": 14,
        "hold_action": "发布当日离场，无需持仓",
        "phase_labels": {
            1: "发布会预热（建仓期）",
            2: "发布后情绪消退（出清）",
            3: "无效区间",
        },
        "exit_signal": "发布会结束即出清",
        "keywords": ["发布会", "展会", "WAIC", "MWC", "大会", "论坛", "峰会", "迎峰度夏", "季节"],
    },
    "B": {
        "name": "IPO里程碑/重大订单/首发",
        "emoji": "🚀",
        "entry_days": (0, 7),        # 事件发生后7天内入场
        "peak_offset": 5,
        "half_life": 15,
        "wave1_end": 30,
        "has_wave2": True,
        "wave2_start": 30,
        "wave2_end": 90,
        "total_days": 90,
        "hold_action": "Phase1持30天，Phase2轻仓跟踪，Phase3不追",
        "phase_labels": {
            1: "直接受益核心标的（T+0到T+30）",
            2: "供应链扩散（T+30到T+90）",
            3: "影子/补涨（T+90+，风险最高）",
        },
        "exit_signal": "注册迟滞/订单无跟进/影子股退潮/连续破位",
        "keywords": ["IPO", "过会", "注册", "里程碑", "0-1", "首单", "首台", "首批", "发射", "量产"],
    },
    "C": {
        "name": "政策管制/市场结构事件",
        "emoji": "⚖️",
        "entry_days": (-1, 3),
        "peak_offset": 3,
        "half_life": 7,
        "wave1_end": 14,
        "has_wave2": True,
        "wave2_start": 60,           # Wave2=季报验证
        "wave2_end": 90,
        "total_days": 90,
        "hold_action": "Wave1情绪7-14天（轻仓），Wave2等季报验证再看",
        "phase_labels": {
            1: "情绪Wave1（7-14天快进快出）",
            2: "消退等待期（不操作）",
            3: "Wave2季报验证（低拥挤重入）",
        },
        "exit_signal": "核心股连续破位/政策放松/替代路线出现",
        "keywords": ["管制", "政策", "出口限制", "审查", "反制", "战略", "拥挤", "去杠杆", "落地", "实施"],
    },
    "D": {
        "name": "涨价传导型（产业链）",
        "emoji": "📈",
        "entry_days": (0, 14),
        "peak_offset": 7,
        "half_life": 21,
        "wave1_end": 42,
        "has_wave2": True,
        "wave2_start": 60,
        "wave2_end": 180,
        "total_days": 180,
        "hold_action": "Wave1涨价函后14天建仓，Wave2等中报/季报验证利润",
        "phase_labels": {
            1: "涨价函+核心品种（T+0到T+14）",
            2: "下游传导受益（T+14到T+42）",
            3: "中报/季报利润验证（T+60到T+180）",
        },
        "exit_signal": "涨价函停止/毛利率未改善/库存转高/龙头放量回落",
        "keywords": ["涨价", "价格上行", "供给冲击", "缺货", "短缺", "价格周期", "供需缺口", "减产"],
    },
    "E": {
        "name": "叙事/早期信号型",
        "emoji": "🔭",
        "entry_days": (0, 14),
        "peak_offset": 14,
        "half_life": 21,
        "wave1_end": 30,
        "has_wave2": True,
        "wave2_start": 90,
        "wave2_end": 360,
        "total_days": 360,
        "hold_action": "情绪窗口轻仓试探，等产品/订单节点再加仓",
        "phase_labels": {
            1: "叙事情绪窗口（轻仓，T+0到T+30）",
            2: "产品发布/客户认证等催化（T+30到T+90）",
            3: "商业化验证（T+90到T+360）",
        },
        "exit_signal": "无订单/商业化验证失败/叙事被证伪/核心标的高位破位",
        "keywords": ["前瞻", "验证中", "AI应用", "Agent", "算力租赁", "商业化", "数字化", "SaaS", "软件"],
    },
}

# 优先级更高的关键词（同时含多类特征时，此列表中的优先匹配）
PRIORITY_KEYWORDS = {
    "B": ["IPO", "过会", "注册", "发射", "首台", "首批量产", "PPA落地"],
    "D": ["涨价", "缺货", "停接新单", "减产", "价格上行"],
    "C": ["管制", "出口限制", "审查", "7月1日实施"],
}


def classify_catalyst_type(event_name: str, event_status: str = "", event_time: str = "") -> str:
    """根据事件名称+状态自动分类催化类型。返回 A/B/C/D/E。"""
    text = f"{event_name} {event_status} {event_time}"

    # 优先级检查（B/D/C 特征词优先）
    for t, keywords in PRIORITY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return t

    # 常规关键词匹配
    for t, cfg in CATALYST_TYPES.items():
        if any(kw in text for kw in cfg["keywords"]):
            return t

    # 默认：若有明确时间节点 → B，否则 → E
    if re.search(r'\d{4}-\d{2}-\d{2}', event_time):
        return "B"
    return "E"


def parse_event_date(event_time: str) -> Optional[date]:
    """解析事件时间字段为 date 对象，尽力取最早的可操作日期。"""
    if not event_time or str(event_time).strip() in ("nan", ""):
        return None

    s = str(event_time).strip()

    # 精确日期：2026-07-10
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # 日期范围：取第一个日期
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})至', s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # 8位纯数字日期：20260703 → 2026-07-03（必须在6位之前，否则会误匹配后6位）
    m = re.match(r'^(\d{4})(\d{2})(\d{2})$', s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # 年月或月份级窗口：2026-07 / 2026-07起 / 2026-04至2026-06 → 月初
    # 原始文本仍保存在事件库；这里只提供窗口计算所需的近似起点。
    m = re.match(r'^(\d{4})-(\d{1,2})(?:月)?(?:起|开始|以来|至.*)?$', s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)

    # 年月无横线：202607
    m = re.search(r'(\d{4})(\d{2})$', s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)

    # 年+月中文：2026年7月 → 2026-07-01
    m = re.search(r'(\d{4})年(\d{1,2})月', s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)

    # 半年：H1=1月, H2=7月
    m = re.search(r'(\d{4})[年]?H([12])', s, re.IGNORECASE)
    if m:
        h_month = {1: 1, 2: 7}[int(m.group(2))]
        return date(int(m.group(1)), h_month, 1)

    # 季度：Q1=1月, Q2=4月, Q3=7月, Q4=10月
    m = re.search(r'(\d{4})[年]?Q([1-4])', s)
    if m:
        q_month = {1: 1, 2: 4, 3: 7, 4: 10}[int(m.group(2))]
        return date(int(m.group(1)), q_month, 1)

    # 年份
    m = re.search(r'(\d{4})年', s)
    if m:
        return date(int(m.group(1)), 1, 1)

    # 仅年份
    m = re.match(r'^(\d{4})$', s)
    if m:
        return date(int(m.group(1)), 7, 1)

    return None


def compute_window_position(
    event_date: date,
    catalyst_type: str,
    today: Optional[date] = None,
    escalation_date: Optional[date] = None,
) -> dict:
    """计算事件的当前窗口位置。

    escalation_date: 最近一次corrections修正日期（来自corrections表的更新日期）。
    与event_date组成双时钟：event_date反映趋势成熟度，escalation_date反映催化新鲜度。
    """
    if today is None:
        today = date.today()

    cfg = CATALYST_TYPES[catalyst_type]
    t_current = (today - event_date).days  # 正值=事件后，负值=事件前

    # 双时钟：升级新鲜度
    t_escalation = (today - escalation_date).days if escalation_date else None
    escalation_fresh = t_escalation is not None and t_escalation <= 7

    # 确定当前阶段
    wave1_end = cfg["wave1_end"]
    has_wave2 = cfg.get("has_wave2", False)
    wave2_start = cfg.get("wave2_start", 999)
    wave2_end = cfg.get("wave2_end", 999)
    total_days = cfg["total_days"]

    entry_lo, entry_hi = cfg["entry_days"]
    peak = cfg["peak_offset"]

    # 相对峰值的距离（t_current - peak_offset）
    t_to_peak = peak - t_current  # 正值=峰值还没到，负值=已过峰值

    if t_current < entry_lo:
        phase = 0
        phase_label = "太早，等待入场窗口"
        action = "观察"
        urgency = max(0, 10 - abs(t_current - entry_lo) // 3)
    elif t_current <= wave1_end:
        phase = 1
        phase_label = cfg["phase_labels"][1]
        if t_to_peak >= 0:
            action = "建仓"
            urgency = min(10, 5 + (5 - t_to_peak // 2))
        else:
            action = "持仓/观察出清"
            urgency = max(3, 8 - abs(t_to_peak) // 3)
    elif has_wave2 and t_current >= wave2_start:
        if t_current <= wave2_end:
            phase = 3
            phase_label = cfg["phase_labels"].get(3, "Wave2验证期")
            action = "低拥挤重入/跟踪"
            urgency = 4
        else:
            phase = 4
            phase_label = "窗口结束"
            action = "退出"
            urgency = 1
    elif t_current > wave1_end:
        phase = 2
        phase_label = cfg["phase_labels"].get(2, "Wave1衰退/等待期")
        action = "持轻仓/等Wave2"
        urgency = 3
    else:
        phase = 4
        phase_label = "窗口外"
        action = "不操作"
        urgency = 0

    # 升级新鲜度加成（D/C/E类有意义；B/A类催化本身即为单一节点，无需）
    # 成熟趋势(phase 2/3) + 最近7天有新escalation → 重新激活，urgency拉升到8
    if escalation_fresh and catalyst_type in ("D", "C", "E"):
        if phase in (2, 3):
            urgency = max(urgency, 8)
            action = "成熟趋势+新催化（重点关注）"
        elif phase == 1:
            urgency = min(10, urgency + 2)

    return {
        "t_current": t_current,
        "t_escalation": t_escalation,
        "escalation_fresh": escalation_fresh,
        "t_to_peak": t_to_peak,
        "phase": phase,
        "phase_label": phase_label,
        "action": action,
        "urgency": urgency,          # 0-10，越高越紧迫
        "exit_signal": cfg["exit_signal"],
        "hold_action": cfg["hold_action"],
        "has_wave2": has_wave2,
        "wave2_days_away": max(0, wave2_start - t_current) if has_wave2 else None,
    }


def score_event(
    event_id: str,
    event_name: str,
    event_time: str,
    event_status: str,
    importance: str,
    expectation_gap: str,
    today: Optional[date] = None,
    latest_correction_date: Optional[date] = None,
    latest_update: str = "",
    evidence_text: str = "",
    confirmed_activation_date: Optional[date] = None,
) -> dict:
    """对单个事件打分，返回完整窗口评估结果。

    latest_correction_date: 该事件在corrections表中最近一次更新的日期，
    用于双时钟模型（趋势成熟度 vs 催化新鲜度）。
    """
    if today is None:
        today = date.today()

    ctype = classify_catalyst_type(event_name, event_status, event_time)
    edate = parse_event_date(event_time)

    # 重要程度 → 数值
    imp_map = {"★★★★★": 5, "★★★★": 4, "★★★": 3, "★★": 2, "★": 1, "高": 4}
    imp_score = imp_map.get(str(importance).strip(), 3)

    # 预期差修正
    gap_bonus = 1 if any(k in str(expectation_gap) for k in ["是", "有", "大"]) else 0

    if edate is None:
        return {
            "event_id": event_id,
            "event_name": event_name,
            "catalyst_type": ctype,
            "event_date": None,
            "position": None,
            "base_score": imp_score + gap_bonus,
            "final_score": imp_score + gap_bonus,
            "urgency": 0,
            "action": "无法计算时间位置",
            "note": "事件时间无法解析",
        }

    evidence = qualify_evidence_activation(latest_update, evidence_text, today=today)
    effective_date = edate
    if confirmed_activation_date and confirmed_activation_date > edate:
        effective_date = confirmed_activation_date
    pos = compute_window_position(effective_date, ctype, today, escalation_date=latest_correction_date)
    final_score = (imp_score + gap_bonus) * (pos["urgency"] / 10 + 0.5)

    return {
        "event_id": event_id,
        "event_name": event_name[:50],
        "catalyst_type": ctype,
        "event_date": edate,
        "effective_date": effective_date,
        "confirmed_activation_date": confirmed_activation_date,
        "activation_candidate": evidence["eligible"] and confirmed_activation_date is None,
        "evidence_date": evidence["evidence_date"],
        "evidence_age_days": evidence["evidence_age_days"],
        "evidence_matches": evidence["strong_matches"],
        "position": pos,
        "base_score": imp_score + gap_bonus,
        "final_score": round(final_score, 1),
        "urgency": pos["urgency"],
        "action": pos["action"],
        "t_current": pos["t_current"],
        "t_to_peak": pos["t_to_peak"],
        "t_escalation": pos.get("t_escalation"),
        "escalation_fresh": pos.get("escalation_fresh", False),
        "phase_label": pos["phase_label"],
    }


def classify_bucket(e: dict) -> str:
    """将score_event()的输出归到五档窗口分类：red/yellow/green/blue/gray。

    这是"事件当前是否还能操作"的唯一判定入口——scanner打分、window日历显示、
    stock-analysis第二层催化预处理都必须调这个函数，不能各自重新发明分类规则。
    """
    if e.get("position") is None:
        return "gray"

    t = e["t_current"]
    phase = e["position"]["phase"]
    t_peak = e.get("t_to_peak")
    t_esc = e.get("t_escalation")
    esc_fresh = e.get("escalation_fresh", False)

    if t_peak is not None and -7 <= t_peak <= 7:
        return "red"
    if esc_fresh and isinstance(t_esc, int) and t_esc <= 3 and phase in (2, 3):
        return "red"
    if isinstance(t, int) and 0 <= t <= 21 and phase == 1:
        return "yellow"
    if esc_fresh and isinstance(t_esc, int) and t_esc <= 7 and phase in (2, 3):
        return "yellow"
    if isinstance(t, int) and 21 < t <= 90 and phase in (1, 2):
        return "green"
    if phase == 3:
        return "blue"
    return "gray"


BUCKET_LABELS = {
    "red":    "🔴 峰值临近 T-7到T+7（立即操作）",
    "yellow": "🟡 窗口进行中 T+0到T+21（持续跟踪）",
    "green":  "🟢 趋势配置 T+21到T+90（月度跟踪）",
    "blue":   "🔵 Wave2窗口（季报验证，低拥挤重入）",
    "gray":   "⚪ 等待/观察（暂不操作）",
}


def format_calendar_output(scored_events: list, today: Optional[date] = None) -> str:
    """格式化输出催化窗口日历。"""
    if today is None:
        today = date.today()

    lines = []
    lines.append(f"\n{'━'*60}")
    lines.append(f"  催化窗口日历 — {today.strftime('%Y-%m-%d')}")
    lines.append(f"  共分析 {len(scored_events)} 个活跃事件")
    lines.append(f"{'━'*60}\n")

    # 分组（唯一分类入口：classify_bucket，禁止在这里重新写判断规则）
    groups = {label: [] for label in BUCKET_LABELS.values()}
    for e in scored_events:
        bucket = classify_bucket(e)
        groups[BUCKET_LABELS[bucket]].append(e)

    for group_name, events in groups.items():
        if not events:
            continue
        lines.append(f"{group_name}")
        events_sorted = sorted(events, key=lambda x: -x.get("urgency", 0))
        for e in events_sorted[:8]:  # 每组最多8条
            ctype = e["catalyst_type"]
            cfg = CATALYST_TYPES.get(ctype, {})
            emoji = cfg.get("emoji", "")
            t = e.get("t_current", "?")
            t_peak = e.get("t_to_peak")
            t_esc = e.get("t_escalation")
            esc_fresh = e.get("escalation_fresh", False)
            peak_str = f"T{'+' if t_peak >= 0 else ''}{t_peak}到峰" if isinstance(t_peak, int) else ""

            edate = e.get("event_date")
            date_str = edate.strftime("%m/%d") if edate else "?"

            # 双时钟显示：原始事件天数 + 升级新鲜度
            t_str = f"T{'+' if isinstance(t, int) and t >= 0 else ''}{t}天"
            if esc_fresh and isinstance(t_esc, int) and t_esc < t:
                esc_label = f"  ⚡最新催化:{t_esc}天前"
            elif isinstance(t_esc, int) and t_esc < t:
                esc_label = f"  最新修正:{t_esc}天前"
            else:
                esc_label = ""

            lines.append(
                f"  {emoji}[{ctype}类] {e['event_id']:<22} "
                f"事件起:{date_str}({t_str}){esc_label}  {peak_str}"
            )
            lines.append(f"       {e['event_name']}")
            lines.append(f"       → {e['action']}  |  {e['phase_label']}")

            # 输出关联公司（来自公司池）
            companies = e.get('companies', [])
            if companies:
                # Phase1优先（核心受益）
                core = [c for c in companies if '核心' in c.get('role', '') or '直接' in c.get('role', '')]
                others = [c for c in companies if c not in core]
                show = (core + others)[:6]
                co_str = '  '.join(
                    f"{c['code'] or '?'} {c['name']}" for c in show
                )
                lines.append(f"       📌 {co_str}")
                if len(companies) > 6:
                    lines.append(f"          +{len(companies)-6}家（pool总计{len(companies)}家）")
        lines.append("")

    lines.append(f"{'━'*60}")
    lines.append("  操作原则：🔴=立即决策  🟡=持续跟踪  🟢=月度复盘  🔵=等季报  ⚪=不操作")
    lines.append(f"{'━'*60}\n")

    return "\n".join(lines)
