#!/usr/bin/env python3
"""
daily_notify.py v3.0 — 板块异动 + 龙虎榜机构 + 自选池轻量日扫

监控四件事：
  1. 重点板块今日涨幅 / 换手异动（ETF代理）
  2. 异动板块内涨幅居前个股（快速定位）
  3. 龙虎榜机构席位净买入
  4. 自选池61只股票轨迹日扫（⚡信号 / 👁状态变化 / 板块概况）

周一至周五：
  8:30  开盘前推送（基于昨日收盘数据）
  15:30 收盘后推送（基于今日数据）

用法：
  python3 daily_notify.py           # 自动判断时段
  python3 daily_notify.py --morning # 强制开盘前
  python3 daily_notify.py --close   # 强制收盘后
  python3 daily_notify.py --test    # 测试（跳过交易日检查）
  python3 daily_notify.py --scan    # 只跑自选池日扫（不推送，用于调试）
"""

import argparse
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

import requests

_CODE_ROOT = Path(__file__).resolve().parents[2]
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))
from skills.shared.paths import workspace_root, config_file
from skills.shared.datasource import load_env as _shared_load_env, get_pro, get_token

# ── 路径 ───────────────────────────────────────────────────────────────────────
SKILL_DIR = Path(__file__).parent
WORKSPACE_ROOT = workspace_root()
ENV_FILE  = config_file()
LOG_PATH  = SKILL_DIR / "notify.log"

# ── 板块ETF映射（用ETF涨跌代理板块表现，tushare fund_daily 接口，稳定可靠）────────
# code: tushare格式（XXXXXX.SH / .SZ）
ETF_SECTORS = {
    "AI算力":   ("159819.SZ", "人工智能ETF"),
    "半导体":   ("512480.SH", "半导体ETF"),
    "机器人":   ("159258.SZ", "机器人ETF"),
    "消费电子": ("159732.SZ", "消费电子ETF"),
    "通信/铜连":("515880.SH", "通信ETF"),    # 覆盖铜缆高速连接方向
    "电力":     ("512140.SH", "电力ETF"),
    "固态电池": ("159757.SZ", "电池ETF"),
}

# ── 异动阈值 ───────────────────────────────────────────────────────────────────
SECTOR_HOT_PCT  = 2.0   # ETF涨幅 >= 2% 标🔴
SECTOR_WARM_PCT = 1.0   # ETF涨幅 >= 1% 标🟡

# ── 自选池（61只，v1.0 2026-06-06）─────────────────────────────────────────────
WATCHLIST: dict[str, tuple[str, str]] = {
    # code: (名称, 赛道)
    # ── 半导体&晶圆材料 ──────────────────────────────────────
    "688126": ("沪硅产业",  "半导体"),
    "605358": ("立昂微",    "半导体"),
    "603986": ("兆易创新",  "半导体"),
    "301308": ("江波龙",    "半导体"),
    "002156": ("通富微电",  "半导体"),
    "600584": ("长电科技",  "半导体"),
    "000021": ("深科技",    "半导体"),
    "688981": ("中芯国际",  "半导体"),
    "688347": ("华虹公司",  "半导体"),
    "002747": ("大族激光",  "半导体"),
    "601208": ("东材科技",  "半导体"),
    "603077": ("和邦生物",  "半导体"),
    "600183": ("生益科技",  "半导体"),
    "605006": ("中国玻纤",  "半导体"),
    # ── 光通信 ────────────────────────────────────────────────
    "601869": ("长飞光纤",  "光通信"),
    "600487": ("亨通光电",  "光通信"),
    "600105": ("永鼎股份",  "光通信"),
    "600522": ("中天科技",  "光通信"),
    "300308": ("中际旭创",  "光通信"),
    "002281": ("光迅科技",  "光通信"),
    "000988": ("华工科技",  "光通信"),
    "003031": ("中瓷电子",  "光通信"),
    "300903": ("科翔股份",  "光通信"),
    "600498": ("烽火通信",  "光通信"),
    "688008": ("澜起科技",  "光通信"),
    # ── MLCC/被动元件 ─────────────────────────────────────────
    "300408": ("三环集团",  "MLCC"),
    "002859": ("洁美科技",  "MLCC"),
    "000636": ("风华高科",  "MLCC"),
    # ── PCB产业链 ─────────────────────────────────────────────
    "301377": ("鼎泰高科",  "PCB"),
    "600549": ("厦门钨业",  "PCB"),
    "002916": ("深南电路",  "PCB"),
    "002463": ("沪电股份",  "PCB"),
    "600176": ("中国巨石",  "PCB"),
    "002080": ("中材科技",  "PCB"),
    # ── 算力&服务器 ───────────────────────────────────────────
    "002837": ("英维克",    "算力"),
    "300499": ("高澜股份",  "算力"),
    "002176": ("申菱环境",  "算力"),
    "002947": ("恒铭达",    "算力"),
    "300857": ("协创数据",  "算力"),
    "601138": ("工业富联",  "算力"),
    # ── 锂电&储能 ─────────────────────────────────────────────
    "300750": ("宁德时代",  "储能"),
    "300450": ("先导智能",  "储能"),
    "603659": ("璞泰来",    "储能"),
    "000973": ("佛塑科技",  "储能"),
    "002407": ("多氟多",    "储能"),
    # ── 电力设备/SST ──────────────────────────────────────────
    "688676": ("金盘科技",  "电力"),
    "601126": ("四方股份",  "电力"),
    "002851": ("麦格米特",  "电力"),
    "002364": ("中恒电气",  "电力"),
    "600886": ("国投电力",  "电力"),
    "000338": ("潍柴动力",  "电力"),
    "600406": ("国电南瑞",  "电力"),
    # ── 机器人 ────────────────────────────────────────────────
    "002050": ("三花智控",  "机器人"),
    "601689": ("拓普集团",  "机器人"),
    "300124": ("汇川技术",  "机器人"),
    # ── 有色/航天/医药/核电 ───────────────────────────────────
    "603993": ("洛阳钼业",  "有色"),
    "002342": ("巨力索具",  "航天"),
    "603259": ("药明康德",  "医药"),
    "601985": ("中国核电",  "核电"),
    "600089": ("特变电工",  "电力"),   # 调整期，保留监控
    "002460": ("赣锋锂业",  "储能"),
}


# ── 环境变量 ───────────────────────────────────────────────────────────────────
def load_env():
    _shared_load_env(ENV_FILE)


load_env()
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")


# ── 工具函数 ───────────────────────────────────────────────────────────────────
def is_trading_day() -> bool:
    return date.today().weekday() < 5


def last_trading_day() -> str:
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def ref_date(is_morning: bool) -> str:
    """开盘前用昨日，收盘后用今日"""
    return last_trading_day() if is_morning else date.today().strftime("%Y%m%d")


def pct_emoji(pct: float) -> str:
    if pct >= SECTOR_HOT_PCT:  return "🔴"
    if pct >= SECTOR_WARM_PCT: return "🟡"
    if pct <= -SECTOR_HOT_PCT: return "🔵"
    return "⚪"


# ── 微信推送（Server酱）──────────────────────────────────────────────────────────
def send_wechat(title: str, content: str) -> bool:
    if not SERVERCHAN_KEY:
        print("[ERROR] SERVERCHAN_KEY 未设置，请检查 repo/.env")
        return False
    try:
        resp = requests.post(
            f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send",
            data={"title": title, "desp": content},
            timeout=15,
        )
        result = resp.json()
        if result.get("data", {}).get("errno") == 0:
            print(f"[OK] 微信推送成功：{title}")
            return True
        print(f"[WARN] Server酱返回：{result}")
        return False
    except Exception as e:
        print(f"[ERROR] 推送异常：{e}")
        return False


# ── 板块数据获取（ETF代理法）─────────────────────────────────────────────────────
def fetch_sector_data(rdate: str) -> list[dict]:
    """
    用板块ETF的涨跌幅代理板块表现，通过 tushare fund_daily 获取。
    返回按涨跌幅降序的列表：
    [{"display": str, "etf_code": str, "etf_name": str, "pct": float,
      "vol_ratio": float, "error": str|None}]
    """
    import pandas as pd

    pro = get_pro()
    # 计算量比需要近5日数据
    prev5_start = (
        datetime.strptime(rdate, "%Y%m%d") - timedelta(days=10)
    ).strftime("%Y%m%d")

    results = []
    for display, (code, etf_name) in ETF_SECTORS.items():
        try:
            df = pro.fund_daily(ts_code=code, start_date=prev5_start, end_date=rdate)
            if df is None or df.empty:
                results.append({"display": display, "etf_code": code, "error": "无数据"})
                continue

            df = df.sort_values("trade_date")
            today_row = df[df["trade_date"] == rdate]
            if today_row.empty:
                # 用最新一行
                today_row = df.tail(1)

            pct = float(today_row.iloc[0]["pct_chg"])
            vol_today = float(today_row.iloc[0]["vol"])

            # 量比：今日成交量 / 前5日均量
            prev5 = df[df["trade_date"] < rdate].tail(5)
            avg_vol = float(prev5["vol"].mean()) if len(prev5) > 0 else vol_today
            vol_ratio = round(vol_today / avg_vol, 2) if avg_vol > 0 else 1.0

            results.append({
                "display":   display,
                "etf_code":  code,
                "etf_name":  etf_name,
                "pct":       pct,
                "vol_ratio": vol_ratio,
                "error":     None,
            })
        except Exception as e:
            print(f"[WARN] {display}({code}) 数据获取失败: {e}")
            results.append({"display": display, "etf_code": code, "error": str(e)[:40]})

    results.sort(key=lambda x: -(x.get("pct", -99)))
    return results


# ── 龙虎榜机构数据 ─────────────────────────────────────────────────────────────
def fetch_lhb_institutions(rdate: str) -> list[dict]:
    """
    返回当日龙虎榜中机构净买入的标的，按净买入降序：
    [{"code": str, "name": str, "net_buy_yi": float, "buy_cnt": int, "sell_cnt": int}]
    """
    import akshare as ak
    import pandas as pd

    try:
        # stock_lhb_jgmmtj_em: 龙虎榜机构买卖明细汇总
        # 列：代码, 名称, 机构买入总额, 机构卖出总额, 机构买入净额, 买方机构数, 卖方机构数
        df = ak.stock_lhb_jgmmtj_em(start_date=rdate, end_date=rdate)
        if df is None or df.empty:
            return []

        df["机构买入净额"] = pd.to_numeric(df["机构买入净额"], errors="coerce").fillna(0)
        # 只保留净买入为正的
        df_buy = df[df["机构买入净额"] > 0].copy()
        df_buy = df_buy.sort_values("机构买入净额", ascending=False)

        result = []
        seen = set()
        for _, row in df_buy.iterrows():
            code = str(row.get("代码", "")).zfill(6)
            if code in seen:
                continue
            seen.add(code)
            result.append({
                "code":       code,
                "name":       str(row.get("名称", "")),
                "net_buy_yi": float(row["机构买入净额"]) / 1e8,
                "buy_cnt":    int(row.get("买方机构数", 0) or 0),
                "sell_cnt":   int(row.get("卖方机构数", 0) or 0),
            })
            if len(result) >= 8:
                break
        return result

    except Exception as e:
        print(f"[WARN] 龙虎榜机构数据获取失败: {e}")
        return []


# ── 消息格式化 ─────────────────────────────────────────────────────────────────
def format_message(
    is_morning: bool,
    sectors: list[dict],
    lhb: list[dict],
    rdate: str,
    scan_section: str = "",
) -> tuple[str, str]:
    today_str    = datetime.now().strftime("%m-%d")
    rdate_disp   = f"{rdate[4:6]}-{rdate[6:8]}"
    tag          = "开盘前" if is_morning else "收盘复盘"
    title        = f"{'📊' if is_morning else '📈'} {today_str} {tag}｜板块+机构"

    lines = []

    # ── 板块异动 ──────────────────────────────────────────────────────
    label = "昨收" if is_morning else "今日"
    lines.append(f"**━━ 板块温度（{label}）━━**")
    hot_names = []

    for s in sectors:
        if s.get("error"):
            lines.append(f"⚠️ {s['display']}：{s['error']}")
            continue

        pct       = s["pct"]
        vol_ratio = s.get("vol_ratio", 1.0)
        em        = pct_emoji(pct)
        vr_str = ""
        if vol_ratio >= 1.5:
            vr_str = f"  量比{vol_ratio:.1f}x🔺"
        elif vol_ratio <= 0.7:
            vr_str = f"  量比{vol_ratio:.1f}x🔻"
        etf_name = s.get("etf_name", "")
        lines.append(f"{em} **{s['display']}**  {pct:+.2f}%{vr_str}  _{etf_name}_")

        if pct >= SECTOR_HOT_PCT:
            hot_names.append(s["display"])

    lines.append("")

    if is_morning:
        # ── 开盘前：读昨日日扫缓存，提醒未处理的⚡信号 ─────────────
        lines.append("**━━ 昨日待处理信号 ━━**")
        cache_path = SKILL_DIR / "last_scan_alerts.txt"
        if cache_path.exists():
            cached = cache_path.read_text().strip()
            lines.append(cached if cached else "　昨日无⚡信号")
        else:
            lines.append("　（暂无缓存，收盘后日扫会自动生成）")
    else:
        # ── 收盘后：完整龙虎榜机构 ───────────────────────────────────
        lines.append(f"**━━ 龙虎榜·机构席位（{rdate_disp}）━━**")
        if lhb:
            for item in lhb[:6]:
                net = item["net_buy_yi"]
                net_str = f"{net:.2f}亿" if net >= 0.1 else f"{net*100:.0f}百万"
                bc = item.get("buy_cnt", 0)
                sc = item.get("sell_cnt", 0)
                lines.append(f"🏦 {item['code']} {item['name']}  净买入{net_str}  ({bc}买/{sc}卖)")
        else:
            lines.append("　今日暂无机构净买入记录")

    lines.append("")

    # ── 自选池日扫（仅收盘后）────────────────────────────────────
    if scan_section:
        lines.append("")
        lines.append(scan_section)

    # ── 底部提示 ──────────────────────────────────────────────────────
    lines.append("")
    if is_morning:
        hint = f"→ 重点关注：{'、'.join(hot_names)}" if hot_names else "→ 今日板块偏弱，观望为主"
    else:
        hint = f"→ 明日留意：{'、'.join(hot_names)}" if hot_names else "→ 今日无强信号，明日继续观望"
    lines.append(hint)

    return title, "\n".join(lines)


# ── 自选池轻量日扫 ─────────────────────────────────────────────────────────────

def _ma(closes: list, n: int):
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n

def _ma_slope(closes: list, n: int, lb: int = 5):
    if len(closes) < n + lb:
        return None
    cur  = sum(closes[-n:]) / n
    prev = sum(closes[-(n + lb):-lb]) / n
    return (cur - prev) / prev * 100 if prev > 0 else None

def _pct(a, b):
    if not a or not b or b == 0:
        return 0.0
    return round((a - b) / b * 100, 2)

def _classify(closes: list) -> str:
    """轨迹分类：A / A⚠️ / B / C / C+ / D / D→C / E / E回踩"""
    if len(closes) < 65:
        return "数据不足"
    p    = closes[-1]
    c60  = _pct(p, closes[-61])
    c20  = _pct(p, closes[-21] if len(closes) >= 21 else closes[0])
    c5   = _pct(p, closes[-6]  if len(closes) >= 6  else closes[0])
    ma5  = _ma(closes, 5);  ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20); ma60 = _ma(closes, 60)
    sl60 = _ma_slope(closes, 60)
    window = closes[-120:-20] if len(closes) >= 120 else closes[:-20]
    hb   = max(window) if window else None
    ma60_up   = sl60 is not None and sl60 > 0
    above_ma60 = ma60 and p > ma60 * 0.97

    if hb and p > hb * 1.10:
        pb = max(closes[-6:-1]) if len(closes) >= 6 else p
        return "E回踩" if (pb - p) / pb * 100 >= 5 else "E"
    if hb and hb * 0.92 <= p <= hb * 1.10 and c60 > 15:
        rh = max(closes[-21:-1]) if len(closes) >= 21 else p
        return "D→C" if (rh - p) / rh * 100 >= 10 else "D"
    in_down = c60 < 3 and c5 < 5
    below60 = ma60 and p < ma60 and sl60 is not None and sl60 < 0
    if in_down or below60:
        if c60 < 5 and c5 > 5:  return "A⚠️"
        if c20 > 0:              return "A⚠️"
        return "A"
    if c60 > 3 and c20 < -5 and ma20 and 0.95 * ma20 <= p <= 1.05 * ma20:
        return "B"
    if ma60_up and above_ma60:
        if ma5  and 0.97 * ma5  <= p <= 1.03 * ma5:  return "C"
        if ma10 and 0.97 * ma10 <= p <= 1.03 * ma10: return "C"
        if ma20 and p < ma20:
            return "C(MA20下)" if (ma20 - p) / ma20 * 100 <= 15 else "C(破位)"
        if c20 > 5: return "C+"
        return "C"
    if c60 > 10 and ma60_up: return "C"
    return "A"


def fetch_watchlist_scan(rdate: str) -> list[dict]:
    """
    用 Tushare pro.daily() 批量拉取自选池数据，计算轨迹。
    rdate: YYYYMMDD 格式
    """
    import pandas as pd
    import time

    token = get_token()
    if not token:
        print("[WARN] 无 TUSHARE_TOKEN，跳过日扫")
        return []

    pro = get_pro()
    start = (datetime.strptime(rdate, "%Y%m%d") - timedelta(days=150)).strftime("%Y%m%d")
    results = []

    for i, (code, (name, sector)) in enumerate(WATCHLIST.items()):
        ts_code = (
            f"{code}.SH" if code.startswith(("600", "601", "603", "605", "688"))
            else f"{code}.SZ"
        )
        try:
            df = pro.daily(ts_code=ts_code, start_date=start, end_date=rdate)
            if df is None or df.empty or len(df) < 20:
                continue
            df = df.sort_values("trade_date").reset_index(drop=True)

            closes   = df["close"].tolist()
            traj     = _classify(closes)
            today_pct = float(df.iloc[-1].get("pct_chg", 0) or 0)

            # 量比：今日量 / 近5日均量
            vols = df["vol"].tolist()
            avg5 = sum(vols[-6:-1]) / 5 if len(vols) >= 6 else vols[-1]
            vol_ratio = round(vols[-1] / avg5, 2) if avg5 > 0 else 1.0

            c60 = _pct(closes[-1], closes[-61] if len(closes) >= 61 else closes[0])

            results.append({
                "code": code, "name": name, "sector": sector,
                "traj": traj, "today_pct": today_pct,
                "vol_ratio": vol_ratio, "chg60": c60,
                "price": closes[-1],
            })
        except Exception as e:
            print(f"[WARN] {code} {name} 日扫失败: {e}")

        # Tushare 免费账号限速保护
        if (i + 1) % 10 == 0:
            time.sleep(0.5)

    return results


def format_scan_section(scan_results: list[dict]) -> str:
    """将日扫结果格式化为推送文本段落"""
    if not scan_results:
        return "**━━ 自选池日扫 ━━**\n　数据获取失败，跳过\n"

    # 信号分级
    alerts  = [r for r in scan_results if r["traj"] == "C"  and r["vol_ratio"] >= 1.2]
    alerts += [r for r in scan_results if r["traj"] == "C+" and r["vol_ratio"] >= 1.5
               and r not in alerts]
    watches = [r for r in scan_results
               if r["traj"] in ("B", "A⚠️", "D→C", "E回踩")
               and r not in alerts]

    lines = [f"**━━ 自选池日扫（{len(scan_results)}只）━━**"]

    # ⚡ 立即分析
    alert_lines = []
    if alerts:
        lines.append(f"\n⚡ **今日信号（{len(alerts)}只）**")
        for r in sorted(alerts, key=lambda x: -x["vol_ratio"])[:6]:
            line = (
                f"  {r['code']} {r['name']}　{r['sector']}　"
                f"{r['traj']}　{r['today_pct']:+.1f}%　量比{r['vol_ratio']:.1f}x"
            )
            lines.append(line)
            alert_lines.append(line.strip())
    else:
        lines.append("\n⚡ 今日无入场信号")

    # 写入缓存供明早推送读取
    cache_path = SKILL_DIR / "last_scan_alerts.txt"
    if alert_lines:
        cache_path.write_text("\n".join(alert_lines))
    else:
        cache_path.write_text("")

    # 👁 需关注
    if watches:
        lines.append(f"\n👁 **状态关注（{len(watches)}只）**")
        for r in sorted(watches, key=lambda x: -abs(x["today_pct"]))[:6]:
            lines.append(
                f"  {r['code']} {r['name']}　{r['traj']}　{r['today_pct']:+.1f}%"
            )

    # 板块概况（按赛道统计轨迹分布）
    from collections import Counter
    sector_map: dict[str, list] = {}
    for r in scan_results:
        sector_map.setdefault(r["sector"], []).append(r["traj"])

    lines.append("\n📊 **板块概况**")
    for sector, trajs in sorted(sector_map.items()):
        cnt = Counter(trajs)
        # 用简短符号：C/C+→✅  B/A⚠️/D→C/E回踩→👁  E/D→⚠️  A→—
        hot  = cnt.get("C", 0) + cnt.get("C+", 0)
        watch = (cnt.get("B", 0) + cnt.get("A⚠️", 0) +
                 cnt.get("D→C", 0) + cnt.get("E回踩", 0))
        high  = cnt.get("E", 0) + cnt.get("D", 0)
        cold  = cnt.get("A", 0)
        tag = ""
        if hot >= 2:    tag = "🔴"
        elif hot >= 1:  tag = "🟡"
        elif high >= len(trajs) // 2: tag = "⚠️"
        elif cold >= len(trajs) // 2: tag = "🔵"
        else: tag = "⚪"
        parts = []
        if hot:   parts.append(f"C×{hot}")
        if watch: parts.append(f"👁×{watch}")
        if high:  parts.append(f"高位×{high}")
        if cold:  parts.append(f"A×{cold}")
        lines.append(f"  {tag} {sector}({len(trajs)}): {'  '.join(parts) or '─'}")

    return "\n".join(lines)


# ── 主入口 ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--morning", action="store_true")
    parser.add_argument("--close",   action="store_true")
    parser.add_argument("--test",    action="store_true")
    parser.add_argument("--scan",    action="store_true", help="只跑自选池日扫，不推送")
    args = parser.parse_args()

    if not args.test and not args.scan and not is_trading_day():
        print("今日非交易日，跳过推送")
        return

    hour       = datetime.now().hour
    is_morning = args.morning or (not args.close and hour < 12)
    rdate_val  = ref_date(is_morning)

    print(f"[INFO] 模式：{'开盘前' if is_morning else '收盘后'}  参考日期：{rdate_val}")

    # ── 仅日扫调试模式 ─────────────────────────────────────────
    if args.scan:
        print(f"[INFO] 自选池日扫（{len(WATCHLIST)}只）...")
        scan = fetch_watchlist_scan(rdate_val)
        print("\n" + format_scan_section(scan))
        return

    print("[INFO] 获取板块数据...")
    sectors = fetch_sector_data(rdate_val)

    print(f"[INFO] 获取龙虎榜（{rdate_val}）...")
    lhb = fetch_lhb_institutions(rdate_val)

    # 收盘后才跑日扫（开盘前数据不完整）
    scan_section = ""
    if not is_morning:
        print(f"[INFO] 自选池日扫（{len(WATCHLIST)}只）...")
        scan = fetch_watchlist_scan(rdate_val)
        scan_section = format_scan_section(scan)

    title, content = format_message(is_morning, sectors, lhb, rdate_val, scan_section)

    print("\n" + "="*40)
    print(content)
    print("="*40 + "\n")

    send_wechat(title, content)

    with open(LOG_PATH, "a") as f:
        f.write(f"{datetime.now().isoformat()}  {'morning' if is_morning else 'close'}  {title}\n")


if __name__ == "__main__":
    main()
