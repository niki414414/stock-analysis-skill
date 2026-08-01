#!/usr/bin/env python3
"""科技+非科技事件地图的通用催化时间轴。

用途：
1. 不按材料日期过滤，保留历史材料中指向未来的判断；
2. 分离“首次提出时间、目标窗口、当前验证状态”；
3. 支持任意起止日期，不限定8月/9月，也可延伸到2027-2028年；
4. 输出机器可读CSV和便于复核的Markdown报告。

示例：
  python catalyst_timeline.py --start 2026-08-01 --end 2026-12-31 \
      --output-dir outputs/catalyst_calendar/2026H2
  python catalyst_timeline.py --start 2027-01-01 --end 2028-12-31 \
      --output-dir outputs/catalyst_calendar/2027-2028
"""

from __future__ import annotations

import argparse
import calendar
import glob
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd


WORKSPACE_ROOT = Path(os.path.abspath(os.path.expanduser(
    os.environ.get("TZ_CODEX_HOME", "~/Desktop/tz-codex")
)))
DATA_ROOT = WORKSPACE_ROOT / "技能数据"
TECH_ROOT = DATA_ROOT / "科技产业事件"
NONFIN_ROOT = DATA_ROOT / "非科技产业事件地图"


@dataclass
class ParsedWindow:
    start: Optional[date]
    end: Optional[date]
    precision: str
    note: str = ""


def latest_dir(source: str) -> Path:
    if source == "tech":
        candidates = [Path(p) for p in glob.glob(str(TECH_ROOT / "csv*")) if Path(p).is_dir()]
    else:
        candidates = [
            Path(p) for p in glob.glob(str(NONFIN_ROOT / "非科技主线产业事件地图_CSV包_*"))
            if Path(p).is_dir()
        ]
    if not candidates:
        raise FileNotFoundError(f"未找到{source}事件地图目录")

    def embedded_date(path: Path) -> str:
        dates = []
        for f in path.glob("*.csv"):
            dates.extend(re.findall(r"_(20\d{6})", f.name))
        if dates:
            return max(dates)
        m = re.search(r"(20\d{6})", path.name)
        return m.group(1) if m else "00000000"

    return max(candidates, key=embedded_date)


def load_table(folder: Path, prefix: str, required: bool = True) -> pd.DataFrame:
    files = list(folder.glob(f"{prefix}_*.csv"))
    if not files:
        if required:
            raise FileNotFoundError(f"{folder} 下缺少 {prefix}_*.csv")
        return pd.DataFrame()
    return pd.read_csv(files[0]).fillna("")


def month_end(year: int, month: int) -> date:
    if month < 1 or month > 12:
        raise ValueError(f"非法月份: {year}-{month}")
    return date(year, month, calendar.monthrange(year, month)[1])


def safe_date(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_single_token(token: str, default_end: bool = False) -> Optional[date]:
    token = token.strip()
    patterns = [
        (r"^(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$", "day"),
        (r"^(20\d{2})(\d{2})(\d{2})$", "day"),
        (r"^(20\d{2})[-/.年](\d{1,2})月?$", "month"),
        (r"^(20\d{2})(\d{2})$", "month"),
    ]
    for pattern, kind in patterns:
        m = re.match(pattern, token)
        if not m:
            continue
        year, month = int(m.group(1)), int(m.group(2))
        if kind == "day":
            return safe_date(year, month, int(m.group(3)))
        return month_end(year, month) if default_end else date(year, month, 1)
    if re.match(r"^20\d{2}$", token):
        year = int(token)
        return date(year, 12, 31) if default_end else date(year, 1, 1)
    return None


def parse_window(text: str, origin: Optional[date]) -> ParsedWindow:
    raw = str(text or "").strip()
    compact = raw.replace("—", "-").replace("–", "-").replace("至", "-")
    compact = compact.replace("年", "-").replace("月", "").replace(" ", "")

    # 相对窗口只能以首次提出时间为锚，不能以今天为锚。
    relative = re.search(r"未来(\d+)[-~到—至]?(\d+)?(天|个月|月|年)", raw)
    if relative and origin:
        lo = int(relative.group(1))
        hi = int(relative.group(2) or lo)
        unit = relative.group(3)
        multiplier = 1 if unit == "天" else 30 if unit in ("个月", "月") else 365
        return ParsedWindow(origin, origin + timedelta(days=hi * multiplier), "relative", "以首次提出时间为锚")

    # 两位年份的半年/季度写法，如26H2至27Q2。
    short_cross = re.search(r"\b(\d{2})H([12]).*?(\d{2})Q([1-4])\b", compact, re.I)
    if short_cross:
        y1, h1 = 2000 + int(short_cross.group(1)), int(short_cross.group(2))
        y2, q2 = 2000 + int(short_cross.group(3)), int(short_cross.group(4))
        return ParsedWindow(date(y1, 1 if h1 == 1 else 7, 1), month_end(y2, q2 * 3), "quarter")

    # 半年/季度范围，如2026H2-2027、2026Q3-Q4。
    m = re.search(r"(20\d{2})[-]?H([12])(?:-(?:(20\d{2})[-]?)?H?([12])|-(20\d{2}))?", compact, re.I)
    if m:
        y1, h1 = int(m.group(1)), int(m.group(2))
        start = date(y1, 1 if h1 == 1 else 7, 1)
        if m.group(5):
            end = date(int(m.group(5)), 12, 31)
        else:
            y2 = int(m.group(3) or y1)
            h2 = int(m.group(4) or h1)
            end = date(y2, 6 if h2 == 1 else 12, 30 if h2 == 1 else 31)
        return ParsedWindow(start, end, "half-year")

    m = re.search(r"(20\d{2})[-]?Q([1-4])(?:-Q?([1-4])|-(20\d{2})[-]?Q?([1-4]))?", compact, re.I)
    if m:
        y1, q1 = int(m.group(1)), int(m.group(2))
        y2 = int(m.group(4) or y1)
        q2 = int(m.group(5) or m.group(3) or q1)
        sm, em = 3 * q1 - 2, 3 * q2
        return ParsedWindow(date(y1, sm, 1), month_end(y2, em), "quarter")

    # 从某月延续到另一年份，如2026-07至2030年、2026-10至2027初。
    m = re.search(r"(20\d{2})[-/.](\d{1,2})-(20\d{2})(?:初)?", compact)
    if m:
        y1, mo1, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo1 <= 12:
            end_month = 3 if "初" in raw else 12
            return ParsedWindow(date(y1, mo1, 1), month_end(y2, end_month), "year")

    # 纯跨年范围，如2026-2027、2027至2028。
    m = re.search(r"\b(20\d{2})-(20\d{2})\b", compact)
    if m:
        return ParsedWindow(date(int(m.group(1)), 1, 1), date(int(m.group(2)), 12, 31), "year")

    # 年月/日期范围。右侧缺年份时继承左侧年份。
    m = re.search(
        r"(20\d{2})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?"
        r"-(?:(20\d{2})[-/.])?(\d{1,2})(?:[-/.](\d{1,2}))?",
        compact,
    )
    if m:
        y1, mo1, d1 = int(m.group(1)), int(m.group(2)), m.group(3)
        y2, mo2, d2 = int(m.group(4) or y1), int(m.group(5)), m.group(6)
        if 1 <= mo1 <= 12 and 1 <= mo2 <= 12:
            start = safe_date(y1, mo1, int(d1)) if d1 else date(y1, mo1, 1)
            end = safe_date(y2, mo2, int(d2)) if d2 else month_end(y2, mo2)
            if start and end:
                return ParsedWindow(start, end, "day" if d1 and d2 else "month")

    # 单点日期、月份或年份。
    candidates = re.findall(r"20\d{2}(?:[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?)?|20\d{6}", raw)
    if candidates:
        token = candidates[0].replace("年", "-").replace("月", "").replace("日", "")
        start = parse_single_token(token, False)
        end = parse_single_token(token, True)
        if start and end:
            precision = "day" if start == end else "month" if start.year == end.year and start.month == end.month else "year"
            return ParsedWindow(start, end, precision)

    # 缺年份的月份描述，以首次提出年份为锚，如“8月暑期与中报披露”。
    month_only = re.search(r"(?<!\d)(\d{1,2})月", raw)
    if month_only and origin:
        month = int(month_only.group(1))
        if 1 <= month <= 12:
            return ParsedWindow(date(origin.year, month, 1), month_end(origin.year, month), "month", "年份取首次提出年份")

    return ParsedWindow(None, None, "unparsed", "需人工确定时间")


def extract_origin(identifier: str, fallback: str, version_date: date) -> date:
    m = re.search(r"(20\d{6})", str(identifier))
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            pass
    for value in (fallback,):
        m = re.search(r"(20\d{2})[-/]?(\d{2})[-/]?(\d{2})", str(value))
        if m:
            parsed = safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if parsed:
                return parsed
    return version_date


def version_date(folder: Path) -> date:
    dates = []
    for f in folder.glob("*.csv"):
        dates.extend(re.findall(r"_(20\d{6})", f.name))
    return datetime.strptime(max(dates), "%Y%m%d").date() if dates else date.today()


def star_score(value: str) -> int:
    text = str(value)
    stars = text.count("★")
    if stars:
        return stars
    m = re.search(r"([1-5])", text)
    return int(m.group(1)) if m else 3


def word_score(text: str, kind: str) -> int:
    text = str(text)
    maps = {
        "probability": [("已兑现", 5), ("高", 5), ("中高", 4), ("中", 3), ("低", 1)],
        "priority": [("极高", 5), ("高", 5), ("中高", 4), ("中", 3), ("低", 1)],
    }
    for word, score in maps[kind]:
        if word in text:
            return score
    return 3


def traded_penalty(text: str) -> int:
    text = str(text)
    if "充分" in text or "过热" in text:
        return 2
    if "部分" in text or "快速" in text:
        return 1
    return 0


def confidence_for_precision(precision: str) -> int:
    return {"day": 5, "month": 4, "quarter": 3, "half-year": 2, "year": 1, "relative": 2}.get(precision, 0)


def overlap(window: ParsedWindow, start: date, end: date) -> bool:
    return bool(window.start and window.end and window.start <= end and window.end >= start)


def bucket(window: ParsedWindow) -> str:
    if not window.start or not window.end:
        return "时间待定"
    if window.start == window.end:
        return window.start.isoformat()
    if window.precision == "month" and window.start.year == window.end.year and window.start.month == window.end.month:
        return window.start.strftime("%Y-%m")
    if window.precision == "quarter" and window.start.year == window.end.year:
        q = (window.start.month - 1) // 3 + 1
        q2 = (window.end.month - 1) // 3 + 1
        return f"{window.start.year}Q{q}" if q == q2 else f"{window.start.year}Q{q}-Q{q2}"
    if window.precision == "half-year" and window.start.year == window.end.year:
        return f"{window.start.year}H{1 if window.start.month == 1 else 2}"
    return f"{window.start.isoformat()}~{window.end.isoformat()}"


def normalize_tech(folder: Path) -> list[dict]:
    forward = load_table(folder, "forward")
    corrections = load_table(folder, "corrections", required=False)
    signals = load_table(folder, "signals_early", required=False)
    vdate = version_date(folder)
    rows = []
    for _, r in forward.iterrows():
        event_id = str(r.get("前瞻ID", ""))
        origin = extract_origin(event_id, r.get("更新时间", ""), vdate)
        target_text = str(r.get("时间", "") or r.get("事件窗口", ""))
        parsed = parse_window(target_text, origin)
        related_signal_count = 0
        if not signals.empty:
            related_signal_count = int(signals.astype(str).apply(
                lambda col: col.str.contains(re.escape(event_id), na=False)
            ).any(axis=1).sum())
        correction_count = 0
        if not corrections.empty and "对应事件ID" in corrections.columns:
            correction_count = int(corrections["对应事件ID"].astype(str).str.contains(re.escape(event_id), na=False).sum())
        score = (
            star_score(r.get("重要程度", "")) * 4
            + word_score(r.get("发生概率", ""), "probability") * 3
            + confidence_for_precision(parsed.precision) * 2
            + min(related_signal_count, 3)
            - traded_penalty(r.get("是否已被交易", "")) * 2
        )
        rows.append({
            "source": "tech", "id": event_id, "sector": r.get("一级赛道", ""),
            "event": r.get("事件", ""), "origin_date": origin.isoformat(),
            "target_text": target_text, "target_start": parsed.start.isoformat() if parsed.start else "",
            "target_end": parsed.end.isoformat() if parsed.end else "", "precision": parsed.precision,
            "window_bucket": bucket(parsed), "probability": r.get("发生概率", ""),
            "importance": r.get("重要程度", ""), "trade_status": r.get("是否已被交易", ""),
            "expectation_gap": r.get("预期差", ""), "validation": r.get("后续观察指标", ""),
            "benefit": r.get("可能受益方向", ""), "risk_or_loss": r.get("可能受损方向", ""),
            "signal_count": related_signal_count, "correction_count": correction_count,
            "timeline_score": score, "parse_note": parsed.note,
        })
    return rows


def normalize_nonfin(folder: Path) -> list[dict]:
    forward = load_table(folder, "forward")
    events = load_table(folder, "events")
    vdate = version_date(folder)
    event_lookup = events.set_index("事件ID").to_dict("index") if "事件ID" in events.columns else {}
    rows = []
    for _, r in forward.iterrows():
        event_id = str(r.get("事件ID", ""))
        ev = event_lookup.get(event_id, {})
        origin = extract_origin(event_id, ev.get("最新更新时间", ev.get("更新时间", "")), vdate)
        target_text = str(r.get("关键日期/窗口", "") or r.get("观察周期", ""))
        parsed = parse_window(target_text, origin)
        priority = r.get("跟踪优先级", "")
        score = (
            word_score(priority, "priority") * 4
            + confidence_for_precision(parsed.precision) * 2
            + word_score(ev.get("重要程度", priority), "priority") * 2
            - traded_penalty(ev.get("是否已被交易", "")) * 2
        )
        event_name = ev.get("事件名称", "") or ev.get("事件", "") or event_id
        if not event_name:
            event_name = "未登记事件：" + str(r.get("观察指标", ""))[:48]
        rows.append({
            "source": "nonfin", "id": event_id, "sector": ev.get("一级赛道", ""),
            "event": event_name, "origin_date": origin.isoformat(),
            "target_text": target_text, "target_start": parsed.start.isoformat() if parsed.start else "",
            "target_end": parsed.end.isoformat() if parsed.end else "", "precision": parsed.precision,
            "window_bucket": bucket(parsed), "probability": priority,
            "importance": ev.get("重要程度", priority), "trade_status": ev.get("是否已被交易", ""),
            "expectation_gap": r.get("备注", ""), "validation": r.get("观察指标", ""),
            "benefit": ev.get("主要影响方向", ev.get("受益方向", "")),
            "risk_or_loss": ev.get("风险点", ""), "signal_count": 0, "correction_count": 0,
            "timeline_score": score, "parse_note": parsed.note,
        })
    return rows


def markdown_report(df: pd.DataFrame, unresolved: pd.DataFrame, start: date, end: date, folders: dict[str, Path]) -> str:
    lines = [
        f"# 催化时间轴：{start.isoformat()} 至 {end.isoformat()}", "",
        f"> 数据版本：科技 `{folders['tech'].name}`；非科技 `{folders['nonfin'].name}`。", "",
        "> 评分只用于整理优先级，不构成投资建议；远期窗口精度越低，越应以里程碑验证替代具体日期。", "",
        "## 总览", "",
        f"- 窗口内记录：{len(df)}条（科技{int((df['source']=='tech').sum())}条，非科技{int((df['source']=='nonfin').sum())}条）",
        f"- 时间无法自动解析、需人工复核：{len(unresolved)}条", "",
    ]
    if df.empty:
        lines.append("当前区间没有可解析的催化记录。")
        return "\n".join(lines) + "\n"

    ordered = df.sort_values(["target_start", "timeline_score"], ascending=[True, False])
    for window_name, group in ordered.groupby("window_bucket", sort=False):
        lines.extend([f"## {window_name}", "", "|来源|赛道|事件|首次提出|目标窗口|优先分|交易状态|核心验证|", "|---|---|---|---|---|---:|---|---|"])
        for _, r in group.head(15).iterrows():
            clean = lambda v: str(v).replace("|", "/").replace("\n", " ")
            lines.append(
                f"|{clean(r['source'])}|{clean(r['sector'])}|{clean(r['event'])}|{clean(r['origin_date'])}|"
                f"{clean(r['target_text'])}|{int(r['timeline_score'])}|{clean(r['trade_status'])}|{clean(r['validation'])}|"
            )
        lines.append("")

    lines.extend(["## 使用说明", "", "- `首次提出`用于衡量市场预期形成时间，不等于事件发生时间。", "- 月/季度/年度窗口应拆成产品、订单、产能、收入和利润等连续验证节点。", "- 已充分交易的方向仍保留在历史时间轴，但优先分会下调。", "- 自动解析失败的记录保存在 `unresolved.csv`，不能静默丢弃。", ""])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="科技+非科技通用催化时间轴")
    parser.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--source", choices=["all", "tech", "nonfin"], default="all")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--top", type=int, default=0, help="按优先分只保留前N条；0为全部")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        raise SystemExit("--end 不能早于 --start")

    folders = {"tech": latest_dir("tech"), "nonfin": latest_dir("nonfin")}
    rows: list[dict] = []
    if args.source in ("all", "tech"):
        rows.extend(normalize_tech(folders["tech"]))
    if args.source in ("all", "nonfin"):
        rows.extend(normalize_nonfin(folders["nonfin"]))

    all_df = pd.DataFrame(rows)
    parsed_mask = all_df["target_start"].ne("") & all_df["target_end"].ne("")
    unresolved = all_df.loc[~parsed_mask].copy()
    parsed = all_df.loc[parsed_mask].copy()
    parsed["_start"] = pd.to_datetime(parsed["target_start"]).dt.date
    parsed["_end"] = pd.to_datetime(parsed["target_end"]).dt.date
    selected = parsed[(parsed["_start"] <= end) & (parsed["_end"] >= start)].copy()
    selected = selected.drop(columns=["_start", "_end"]).sort_values("timeline_score", ascending=False)
    if args.top > 0:
        selected = selected.head(args.top)

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    selected.to_csv(out / "catalyst_timeline.csv", index=False, encoding="utf-8-sig")
    unresolved.to_csv(out / "unresolved.csv", index=False, encoding="utf-8-sig")
    (out / "report.md").write_text(
        markdown_report(selected, unresolved, start, end, folders), encoding="utf-8"
    )
    print(f"已生成: {out / 'report.md'}")
    print(f"窗口内: {len(selected)} 条；时间待人工解析: {len(unresolved)} 条")


if __name__ == "__main__":
    main()
