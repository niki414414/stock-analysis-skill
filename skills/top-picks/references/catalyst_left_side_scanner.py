#!/usr/bin/env python3
"""
catalyst_left_side_scanner.py — 催化优先左侧扫描器 v2.0

逻辑：催化活跃 → 价格未动 → 启动就绪度 → 左侧窗口
与现有扫描器的区别：
  signal_scanner:    价格异动 → 检查有没有催化（右侧确认，已删除）
  prelaunch_scanner: 技术蓄力 → 检查有没有催化（技术右侧，已删除）
  本扫描器:          催化活跃 → 价格还没动 → 量能/支撑/收敛三维评估（催化左侧）

v2.0变化（2026-07-10）：催化新鲜度不再自己解析events.csv的静态"重要程度/当前状态"
标签打分，改为调用 event_map_query.py window --json（catalyst_window_model.py的
A-E衰减模型），事件是否还"活跃"以窗口模型的🔴🟡🟢🔵⚪判断为准，避免过期催化被
静态标签误判为"活跃"。见~/.claude/projects/-Users-niki/memory/project-leftside-rightside-unification.md

启动就绪度（0-10）：
  ① 量能方向（5分）：近5日量能趋势↑(+3) + vol_ratio>1.2(+2)
  ② 支撑位临近（3分）：贴近MA60 <5%(+3)，5-15%(+1)
  ③ 价格收敛（2分）：近5日振幅<3%(+2)，3-6%(+1)

用法:
  python3 catalyst_left_side_scanner.py
  python3 catalyst_left_side_scanner.py --top 20
  python3 catalyst_left_side_scanner.py --min-event-score 6
"""

import argparse
import json
import os
import re
import sys
import time
import logging
from datetime import datetime, timedelta

import pandas as pd

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", stream=sys.stderr)
log = logging.getLogger(__name__)

# ── 路径常量 ──────────────────────────────────────────────────────────────────
WORKSPACE_ROOT = os.path.abspath(os.path.expanduser(
    os.environ.get("TZ_CODEX_HOME", "~/Desktop/tz-codex")
))
REPO_ROOT = os.path.join(WORKSPACE_ROOT, "repo")
DATA_ROOT = os.path.join(WORKSPACE_ROOT, "技能数据")
COMPANY_POOL = os.path.join(DATA_ROOT, "公司.xlsx")
CODE_MAP_CSV = os.path.join(DATA_ROOT, "company_code_map.csv")
CSV_BASE_DIR = os.path.join(DATA_ROOT, "科技产业事件")

# ── 申万行业轮动雷达 ──────────────────────────────────────────────────────────
# 使用 SW2021 标准代码（via pro.index_classify(level='L1', src='SW2021')）
# tech=科技主线, non_tech=轮动候选
_SW_SECTORS = {
    "801080.SI": ("电子",     "tech"),
    "801750.SI": ("计算机",   "tech"),
    "801770.SI": ("通信",     "tech"),
    "801740.SI": ("国防军工", "tech"),    # 低空/商业航天科技属性
    "801890.SI": ("机械设备", "tech"),    # 机器人/工业母机
    "801730.SI": ("电力设备", "non_tech"),# 新能源/储能/光伏
    "801790.SI": ("非银金融", "non_tech"),
    "801780.SI": ("银行",     "non_tech"),
    "801010.SI": ("农林牧渔", "non_tech"),
    "801950.SI": ("煤炭",     "non_tech"),
    "801040.SI": ("钢铁",     "non_tech"),
    "801050.SI": ("有色金属", "non_tech"),
    "801030.SI": ("基础化工", "non_tech"),# 六氟化钨/氟化工/新材料
    "801150.SI": ("医药生物", "non_tech"),
    "801120.SI": ("食品饮料", "non_tech"),# 白酒/消费防御
    "801160.SI": ("公用事业", "non_tech"),
    "801210.SI": ("社会服务", "non_tech"),
    "801760.SI": ("传媒",     "non_tech"),
    "801880.SI": ("汽车",     "non_tech"),
    "801960.SI": ("石油石化", "non_tech"),
    "801170.SI": ("交通运输", "non_tech"),
    "801180.SI": ("房地产",   "non_tech"),
    # 2026-07-22补齐至申万31个一级行业全覆盖（此前22个，缺9个）
    "801110.SI": ("家用电器", "non_tech"),
    "801130.SI": ("纺织服饰", "non_tech"),
    "801140.SI": ("轻工制造", "non_tech"),
    "801200.SI": ("商贸零售", "non_tech"),
    "801230.SI": ("综合",     "non_tech"),
    "801710.SI": ("建筑材料", "non_tech"),
    "801720.SI": ("建筑装饰", "non_tech"),
    "801970.SI": ("环保",     "non_tech"),
    "801980.SI": ("美容护理", "non_tech"),
}

def rotation_check(pro) -> None:
    """申万行业20日/5日涨幅排行，嵌入 top-picks Step 0，自动运行。

    2026-07-22教训：`pro.sw_daily()`比`pro.index_daily()`慢至少1个交易日发布——
    同一个申万指数代码(如801050.SI有色金属)，`index_daily()`当天下午就有当日
    收盘数据，`sw_daily()`还停在前一交易日。实测当天用sw_daily复盘，把7/21的
    行业涨跌幅当成7/22的板块表现，把当天实际大涨的有色/贵金属说成还在跌。
    根因是sw_daily接口本身发布节奏慢，不是数据真的抓不到——换成index_daily
    （同样的申万指数代码，两个接口都支持）就能拿到当天数据，问题在换接口，
    不在数据源本身。仍保留新鲜度校验作为兜底：万一index_daily某天也滞后，
    照样会在标题里报警，不会静默拿旧数据当新数据用。
    """
    end   = datetime.now()
    start = end - timedelta(days=40)
    results = []
    data_max_date = None
    for ts_code, (name, kind) in _SW_SECTORS.items():
        try:
            df = pro.index_daily(
                ts_code=ts_code,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
            if df is None or len(df) < 5:
                continue
            df = df.sort_values("trade_date")
            closes = df["close"].tolist()
            latest_date = df["trade_date"].iloc[-1]
            if data_max_date is None or latest_date > data_max_date:
                data_max_date = latest_date
            p20 = round((closes[-1] / closes[-20] - 1) * 100, 1) if len(closes) >= 20 else None
            p5  = round((closes[-1] / closes[-5]  - 1) * 100, 1) if len(closes) >= 5  else None
            results.append({"name": name, "kind": kind, "p20": p20, "p5": p5})
        except Exception:
            continue

    if not results:
        return

    expected_latest = None
    try:
        cal = pro.trade_cal(exchange="SSE",
                             start_date=(end - timedelta(days=10)).strftime("%Y%m%d"),
                             end_date=end.strftime("%Y%m%d"))
        open_days = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist(), reverse=True)
        if open_days:
            expected_latest = open_days[0]
    except Exception:
        pass

    results.sort(key=lambda x: x["p20"] if x["p20"] is not None else -99, reverse=True)

    # 非科技异动：近5日发力 且 20日跌幅不深（-10%以内），可能是轮动启动
    anomalies = [
        r for r in results
        if r["kind"] == "non_tech"
        and r["p5"]  is not None and r["p5"]  > 3
        and r["p20"] is not None and r["p20"] > -10
    ]

    print(f"\n{'─'*54}")
    print(f"  板块轮动雷达（申万行业 20日/5日涨幅，数据截至{data_max_date}）")
    if expected_latest and data_max_date and data_max_date < expected_latest:
        print(f"  ⚠️ sw_daily数据滞后：最新交易日应为{expected_latest}，"
              f"接口只更新到{data_max_date}——以下涨跌幅不代表最近一个交易日的"
              f"真实表现，个股级别的今日异动请用pro.daily()直接核实，不要只看本雷达")
    print(f"{'─'*54}")
    for r in results:
        tag   = "🔵" if r["kind"] == "tech" else ("⚡" if r in anomalies else "  ")
        p20s  = f"{r['p20']:+.1f}%" if r["p20"] is not None else "  N/A "
        p5s   = f"5日{r['p5']:+.1f}%" if r["p5"] is not None else ""
        print(f"  {tag} {r['name']:<8}  20日{p20s}  {p5s}")
    if anomalies:
        names = "、".join(a["name"] for a in anomalies[:3])
        print(f"\n  ⚡ 非科技异动: {names} — 近5日发力，留意轮动方向")
    print(f"{'─'*54}\n")

# ── Tushare ───────────────────────────────────────────────────────────────────
def _get_pro():
    token = os.environ.get("TUSHARE_TOKEN", "")
    env_path = os.path.join(REPO_ROOT, ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            if "TUSHARE_TOKEN" in line:
                token = line.split("=", 1)[-1].strip()
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未配置；请写入 repo/.env 或环境变量")
    import tushare as ts
    return ts.pro_api(token)

# ── Step 1: 催化过滤（唯一权威来源=event_map_query.py window，见下方load_window_scores）─────
# 不再自己解析events.csv的"重要程度/当前状态"字段打分——那套静态标签不衰减，
# 会让过期几个月的催化一直显示"活跃"。新鲜度/是否还能操作全部交给window模型判断。

EVENT_MAP_QUERY_SCRIPT = os.path.expanduser(
    os.path.join(REPO_ROOT, "skills", "stock-analysis", "scripts", "event_map_query.py")
)

# bucket → 权重：🔴🟡按窗口模型原分值计（催化最紧迫），🟢按趋势配置期打6折，
# 🔵Wave2窗口打3.5折（低拥挤重入，值得看但不是当下重点），⚪直接排除（不操作/已退出）。
BUCKET_WEIGHT = {"red": 1.0, "yellow": 0.85, "green": 0.6, "blue": 0.35, "gray": 0.0}
BUCKET_ICON   = {"red": "🔴", "yellow": "🟡", "green": "🟢", "blue": "🔵", "gray": "⚪"}

def load_window_scores(source: str = "tech", top: int = 300) -> dict:
    """调用event_map_query.py window --json，取窗口模型对每个事件的最新判断。

    这是全局唯一的催化新鲜度计算入口，scanner不再自己算一遍——
    避免出现"scanner说预热，window模型说已经不操作"这种自相矛盾。
    """
    import subprocess
    result = subprocess.run(
        ["python3", EVENT_MAP_QUERY_SCRIPT, "window",
         "--source", source, "--json", "--top", str(top)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"window模型调用失败: {result.stderr[:500]}")
    events = json.loads(result.stdout)
    if isinstance(events, dict) and "error" in events:
        raise FileNotFoundError(events["error"])
    return {e["event_id"]: e for e in events}

def load_active_events() -> pd.DataFrame:
    """从window模型结果构建候选事件表，排除⚪(不操作/已退出)的事件。

    重要程度已经在window模型的score_event()里折算进final_score，
    这里不再单独按★数过滤。

    2026-07-16修复：此前只查tech源，nonfin源(创新药/有色/储能等)从未被扫描过，
    导致window模型评分很高(甚至🔴红桶)的nonfin事件永远进不了候选池——不是催化
    不够格，是代码没查那个库。现在tech+nonfin都查，用source字段区分。
    """
    rows = []
    for source in ("tech", "nonfin"):
        window_scores = load_window_scores(source=source)
        for eid, e in window_scores.items():
            weight = BUCKET_WEIGHT.get(e["bucket"], 0.0)
            if weight <= 0:
                continue
            rows.append({
                "事件ID":      eid,
                "事件名称":     e["event_name"],
                "当前状态":     e["action"],          # 用window模型的操作建议取代原始状态文本
                "bucket":      e["bucket"],
                "catalyst_score": round(e["final_score"] * weight, 1),
                "source":      source,
            })

    df = pd.DataFrame(rows)
    log.info(f"窗口模型活跃事件(已排除⚪不操作/已退出): {len(df)} 个 "
             f"(tech={sum(1 for r in rows if r['source']=='tech')}, "
             f"nonfin={sum(1 for r in rows if r['source']=='nonfin')})")
    return df

# ── Step 2: 公司映射 ──────────────────────────────────────────────────────────
def parse_event_ids(val) -> list:
    if pd.isna(val):
        return []
    return re.findall(r'[A-Z][A-Z0-9-]*-\d{4}-\d{3}', str(val))

def load_company_candidates(active_events: pd.DataFrame) -> pd.DataFrame:
    """从公司.xlsx找出关联活跃事件的A股公司，解析股票代码。"""
    active_ids = set(active_events["事件ID"].dropna())
    event_score_map = dict(zip(active_events["事件ID"], active_events["catalyst_score"]))
    event_name_map  = dict(zip(active_events["事件ID"], active_events["事件名称"].astype(str).str[:30]))
    event_status_map= dict(zip(active_events["事件ID"], active_events["当前状态"].astype(str)))
    event_bucket_map= dict(zip(active_events["事件ID"], active_events["bucket"].astype(str)))

    # 2026-07-16修复：此前只读科技公司池，非科技公司池(创新药/有色/储能等对应的
    # 公司)从未被纳入候选——即使active_events里已经有nonfin事件，也找不到公司。
    pool_sheets = []
    for sheet in ("科技公司池", "非科技公司池"):
        try:
            pool_sheets.append(pd.read_excel(COMPANY_POOL, sheet_name=sheet))
        except Exception as e:
            log.warning(f"  读取{sheet}失败: {e}")
    pool = pd.concat(pool_sheets, ignore_index=True) if pool_sheets else pd.DataFrame()
    a_pool = pool[pool["市场/属性"].astype(str).str.contains("A股", na=False)].copy()

    code_map = pd.read_csv(CODE_MAP_CSV)
    code_dict = dict(zip(code_map["name"], code_map["code"].astype(str).str.zfill(6)))
    
    rows = []
    for _, row in a_pool.iterrows():
        parsed = parse_event_ids(row["关联事件ID"])
        if not parsed:
            continue
        matched_ids = [eid for eid in parsed if eid in active_ids]
        if not matched_ids:
            continue
        name = str(row["公司名称"])
        code = code_dict.get(name)
        if not code:
            continue
        for matched_id in matched_ids:
            rows.append({
                "code":          code,
                "name":          name,
                "event_id":      matched_id,
                "event_name":    event_name_map.get(matched_id, ""),
                "event_status":  event_status_map.get(matched_id, ""),
                "event_bucket":  event_bucket_map.get(matched_id, "gray"),
                "catalyst_score": event_score_map.get(matched_id, 0),
                "sector":        str(row.get("一级赛道", "")),
                "sub_sector":    str(row.get("二级环节", "")),
            })

    if not rows:
        return pd.DataFrame()

    # 同一公司可能有多条产业角色和多项活跃催化。保留最高分催化作为主事件，
    # 同时聚合全部命中，避免旧逻辑“取第一个事件+按代码去重”静默丢信息。
    raw = pd.DataFrame(rows).sort_values("catalyst_score", ascending=False)
    aggregated = []
    for _, group in raw.groupby("code", sort=False):
        primary = group.iloc[0].to_dict()
        primary["event_ids"] = ";".join(dict.fromkeys(group["event_id"].astype(str)))
        primary["event_names"] = "；".join(dict.fromkeys(group["event_name"].astype(str)))
        primary["matched_catalyst_count"] = int(group["event_id"].nunique())
        primary["roles"] = "；".join(dict.fromkeys(
            (group["sector"].astype(str) + "/" + group["sub_sector"].astype(str)).tolist()
        ))
        aggregated.append(primary)

    df = pd.DataFrame(aggregated)
    log.info(f"候选公司: {len(df)} 家")
    return df

# ── Step 3: 价格未动验证 ──────────────────────────────────────────────────────
def fetch_price_batch(pro, codes: list, trade_date: str, n_days: int = 65) -> dict:
    """逐只拉取历史价格，返回 code -> bars列表。"""
    from datetime import datetime, timedelta
    end_dt   = datetime.strptime(trade_date, "%Y%m%d")
    start_dt = end_dt - timedelta(days=n_days * 2)
    start_str = start_dt.strftime("%Y%m%d")

    result = {}
    log.info(f"拉取 {len(codes)} 只股票历史({start_str}~{trade_date})...")
    for i, code in enumerate(codes):
        try:
            suffix = "SH" if code.startswith("6") or code.startswith("688") else "SZ"
            ts_code = f"{code}.{suffix}"
            df = pro.daily(ts_code=ts_code, start_date=start_str, end_date=trade_date,
                           fields="trade_date,open,high,low,close,pct_chg,vol")
            if df is None or df.empty:
                result[code] = []
                continue
            df = df.sort_values("trade_date")
            result[code] = [
                {"date": r["trade_date"], "open": float(r["open"]),
                 "high": float(r["high"]), "low": float(r["low"]),
                 "close": float(r["close"]), "pct_chg": float(r.get("pct_chg", 0)),
                 "vol": float(r.get("vol", 0))}
                for _, r in df.iterrows()
            ]
        except Exception as e:
            log.warning(f"  {code} 拉取失败: {e}")
            result[code] = []
        if (i + 1) % 20 == 0:
            log.info(f"  进度: {i+1}/{len(codes)} 只")
        time.sleep(0.06)
    return result

def _mean(arr):
    return sum(arr) / len(arr) if arr else 0

def _vol_trend_up(vols: list) -> bool:
    """近5日均量 vs 前5日均量，判断量能方向是否向上。"""
    if len(vols) < 10:
        return False
    recent = _mean(vols[-5:])
    prev   = _mean(vols[-10:-5])
    return recent > prev if prev > 0 else False

def _range_5d(bars: list) -> float:
    """近5日价格振幅 = (最高-最低)/最低，反映价格收敛程度。"""
    if len(bars) < 5:
        return 999.0
    highs = [b["high"] for b in bars[-5:]]
    lows  = [b["low"]  for b in bars[-5:]]
    low5  = min(lows)
    return (max(highs) - low5) / low5 * 100 if low5 > 0 else 999.0

def launch_readiness_score(close: float, ma60: float, vol_ratio: float,
                           vols: list, bars: list) -> tuple:
    """
    启动就绪度三维评分（0-10）：
      ① 量能方向（max 5）：近5日量能趋势↑(+3) + vol_ratio>1.2(+2)
      ② 支撑位临近（max 3）：dist_ma60 <5%(+3)，5-15%(+1)
      ③ 价格收敛（max 2）：近5日振幅<3%(+2)，3-6%(+1)
    返回 (score, label, detail_str)
    """
    score = 0
    details = []

    # ① 量能方向
    vol_up = _vol_trend_up(vols)
    if vol_up:
        score += 3
    if vol_ratio >= 1.2:
        score += 2
    vol_arrow = "↑" if vol_up else "→" if vol_ratio >= 0.9 else "↓"
    details.append(f"量{vol_arrow}({vol_ratio:.2f}x)")

    # ② 支撑位临近（dist_ma60 = (close-ma60)/ma60*100，已知>0因为above_ma60通过）
    dist_ma60 = (close - ma60) / ma60 * 100 if ma60 > 0 else 99
    if dist_ma60 < 5:
        score += 3
        details.append(f"贴近MA60(+{dist_ma60:.1f}%)")
    elif dist_ma60 < 15:
        score += 1
        details.append(f"近MA60(+{dist_ma60:.1f}%)")
    else:
        details.append(f"远MA60(+{dist_ma60:.1f}%)")

    # ③ 价格收敛
    rng = _range_5d(bars)
    if rng < 3:
        score += 2
        details.append(f"收敛({rng:.1f}%)")
    elif rng < 6:
        score += 1
        details.append(f"轻震({rng:.1f}%)")
    else:
        details.append(f"震荡({rng:.1f}%)")

    label = "就绪" if score >= 8 else ("预热" if score >= 5 else "观望")
    return score, label, "  ".join(details)

def analyze_price_freshness(bars: list) -> dict:
    """判断价格是否"未充分交易"，并计算启动就绪度。"""
    if len(bars) < 20:
        return None

    closes = [b["close"] for b in bars]
    vols   = [b["vol"]   for b in bars]
    latest = bars[-1]
    close  = latest["close"]

    # 均线
    ma20 = _mean(closes[-20:])
    ma60 = _mean(closes[-60:]) if len(closes) >= 60 else _mean(closes)

    # 近30日涨幅
    close_30d_ago = closes[-30] if len(closes) >= 30 else closes[0]
    pct_30d = (close - close_30d_ago) / close_30d_ago * 100 if close_30d_ago > 0 else 0

    # 距60日高点
    high_60d = max(b["high"] for b in bars[-60:]) if len(bars) >= 60 else max(b["high"] for b in bars)
    dist_60d_high = (close - high_60d) / high_60d * 100 if high_60d > 0 else 0

    # 量能
    vol_3d  = _mean(vols[-3:])
    vol_20d = _mean(vols[-20:])
    vol_ratio = vol_3d / vol_20d if vol_20d > 0 else 1.0

    # 价格新鲜度过滤条件
    above_ma60  = close > ma60
    not_at_peak = dist_60d_high < -5
    not_surged  = pct_30d < 25

    # 价格新鲜度分 (0-10)
    freshness = 0
    if above_ma60:   freshness += 3
    if not_at_peak:  freshness += max(0, min(4, int(abs(dist_60d_high) / 5)))
    if not_surged:   freshness += max(0, min(3, int((25 - pct_30d) / 8)))

    passes = above_ma60 and not_at_peak and not_surged

    # 启动就绪度（仅在passes时计算）
    lr_score, lr_label, lr_detail = (0, "观望", "")
    if passes:
        lr_score, lr_label, lr_detail = launch_readiness_score(
            close, ma60, vol_ratio, vols, bars)

    return {
        "close":          round(close, 2),
        "ma20":           round(ma20, 2),
        "ma60":           round(ma60, 2),
        "pct_30d":        round(pct_30d, 1),
        "high_60d":       round(high_60d, 2),
        "dist_60d_high":  round(dist_60d_high, 1),
        "vol_ratio":      round(vol_ratio, 2),
        "above_ma60":     above_ma60,
        "not_at_peak":    not_at_peak,
        "not_surged":     not_surged,
        "freshness":      freshness,
        "passes":         passes,
        "lr_score":       lr_score,
        "lr_label":       lr_label,
        "lr_detail":      lr_detail,
    }

# ── Step 4: 合并评分 ──────────────────────────────────────────────────────────
def score_candidates(candidates: pd.DataFrame, price_map: dict) -> pd.DataFrame:
    rows = []
    for _, row in candidates.iterrows():
        code = row["code"]
        bars = price_map.get(code, [])
        price = analyze_price_freshness(bars)
        if price is None or not price["passes"]:
            continue
        
        final_score = row["catalyst_score"] * price["freshness"]
        rows.append({**row.to_dict(), **price, "final_score": final_score})
    
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values("final_score", ascending=False)
    return result

# ── Step 4b: 前瞻记录（2026-07-16新增）────────────────────────────────────────
PROSPECTIVE_LOG = os.path.join(DATA_ROOT, "market_daily_snapshot", "toppicks_log.csv")
_LOG_FIELDS = ["scan_date", "trade_date", "code", "name", "sector", "event_id",
               "event_bucket", "catalyst_score", "lr_score", "lr_label",
               "close", "ma60", "dist_60d_high", "pct_30d", "final_score"]

def log_prospective(results: pd.DataFrame, trade_date: str):
    """把本次扫描的全部候选（不只是展示的top N）追加进持久化CSV，供以后回头验证
    "标就绪/预热的票后续实际表现如何"——不是回测，是前瞻记录，今天写不出结论，
    攒够时间后才有用。按(trade_date, code)去重，同一天重复跑不会重复写。
    """
    import csv
    if results.empty:
        return
    os.makedirs(os.path.dirname(PROSPECTIVE_LOG), exist_ok=True)
    scan_date = datetime.now().strftime("%Y%m%d")

    existing_keys = set()
    if os.path.exists(PROSPECTIVE_LOG):
        with open(PROSPECTIVE_LOG, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                existing_keys.add((r.get("trade_date"), r.get("code")))

    new_rows = []
    for _, row in results.iterrows():
        key = (trade_date, row["code"])
        if key in existing_keys:
            continue
        new_rows.append({
            "scan_date": scan_date, "trade_date": trade_date,
            "code": row["code"], "name": row["name"], "sector": row.get("sector", ""),
            "event_id": row["event_id"], "event_bucket": row.get("event_bucket", ""),
            "catalyst_score": row["catalyst_score"], "lr_score": row["lr_score"],
            "lr_label": row["lr_label"], "close": row["close"], "ma60": row["ma60"],
            "dist_60d_high": row["dist_60d_high"], "pct_30d": row["pct_30d"],
            "final_score": round(row["final_score"], 1),
        })
        existing_keys.add(key)

    if not new_rows:
        log.info("[前瞻记录] 无新增（今天已经记过）")
        return
    exists = os.path.exists(PROSPECTIVE_LOG)
    with open(PROSPECTIVE_LOG, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_LOG_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerows(new_rows)
    log.info(f"[前瞻记录] 已追加{len(new_rows)}条到 {PROSPECTIVE_LOG}")


# ── Step 5: 输出 ──────────────────────────────────────────────────────────────
READINESS_ICON = {"就绪": "🟢", "预热": "🟡", "观望": "⚪"}

def print_results(df: pd.DataFrame, top_n: int = 20):
    if df.empty:
        print("暂无左侧窗口候选")
        return

    # 按 lr_score 降序二次排序（催化分相同时，就绪度高的优先）
    df = df.sort_values(["final_score", "lr_score"], ascending=[False, False])

    print(f"\n{'='*72}")
    print(f"  催化左侧扫描结果  共 {len(df)} 只  (Top {min(top_n, len(df))})")
    print(f"  启动就绪度：🟢就绪(8-10) 🟡预热(5-7) ⚪观望(<5)")
    print(f"{'='*72}")

    for i, (_, row) in enumerate(df.head(top_n).iterrows()):
        icon  = READINESS_ICON.get(row["lr_label"], "⚪")
        bucket_icon = BUCKET_ICON.get(row.get("event_bucket", "gray"), "⚪")
        print(f"\n[{i+1:02d}] {row['name']} ({row['code']})  "
              f"综合分:{row['final_score']:.0f}  "
              f"{icon}启动就绪:{row['lr_score']}/10 [{row['lr_label']}]")
        print(f"  催化: {row['event_name']}  [{row['event_id']}  催化分:{row['catalyst_score']}  {bucket_icon}窗口:{row.get('event_bucket','?')}]")
        print(f"  操作建议: {row['event_status']}")
        print(f"  赛道: {row['sector']} → {row['sub_sector']}")
        print(f"  价格: {row['close']}  MA60:{row['ma60']}  近30日:{row['pct_30d']:+.1f}%  距高:{row['dist_60d_high']:.1f}%")
        print(f"  量价: {row['lr_detail']}")

    # 赛道汇总
    print(f"\n{'─'*52}")
    print("按赛道分布（含就绪度）:")
    top_df = df.head(top_n)
    for sector, grp in top_df.groupby("sector"):
        ready = grp[grp["lr_label"] == "就绪"]
        warm  = grp[grp["lr_label"] == "预热"]
        names = "/".join(grp["name"].tolist()[:3])
        tag   = f"🟢{len(ready)}" if len(ready) else (f"🟡{len(warm)}" if len(warm) else "⚪")
        print(f"  {sector}: {len(grp)}家 {tag}  ({names}{'...' if len(grp)>3 else ''})")

# ── MAIN ──────────────────────────────────────────────────────────────────────
def resolve_trade_date(pro) -> str:
    """最近一个已收盘交易日：不用"hour<15"猜测收盘时间——沙盒系统时钟不一定
    等于北京时间，2026-07-22实测sandbox显示14:43时该猜测已经把已收盘的当天
    数据误判成"未收盘"，导致扫描器拿前一交易日的候选跑了一整天。改为直接探测
    当天数据是否已发布，发布了就用，没发布再退回前一交易日（同
    stock_data_fetcher.py的fetch_market_breadth()已用过的修复方式）。
    """
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    try:
        start = (now - timedelta(days=10)).strftime("%Y%m%d")
        cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=today)
        days = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist(), reverse=True)
        if not days:
            return today
        candidate = days[0]
        probe = pro.daily(trade_date=candidate, fields="ts_code")
        if probe is None or probe.empty:
            return days[1] if len(days) > 1 else candidate
        return candidate
    except Exception:
        pass
    return today

def main():
    parser = argparse.ArgumentParser(description="催化优先左侧扫描器 v2.0（催化打分改由window模型统一计算）")
    parser.add_argument("--top",              type=int,   default=20,  help="输出前N个")
    parser.add_argument("--min-event-score",  type=int,   default=3,   help="最低催化分（window模型final_score×bucket权重后的值）")
    parser.add_argument("--json",             action="store_true",      help="JSON输出")
    args = parser.parse_args()

    pro = _get_pro()
    trade_date = resolve_trade_date(pro)
    log.info(f"=== 催化左侧扫描 trade_date={trade_date} ===")

    # Step 0b: 板块轮动雷达（非JSON模式自动输出）
    if not args.json:
        rotation_check(pro)

    # Step 1: 活跃事件（唯一权威来源=window模型，见load_window_scores）
    active_events = load_active_events()
    active_events = active_events[active_events["catalyst_score"] >= args.min_event_score]
    log.info(f"催化分≥{args.min_event_score} 的事件: {len(active_events)} 个")

    # Step 2: 公司映射
    candidates = load_company_candidates(active_events)
    if candidates.empty:
        print("无候选公司")
        return

    # Step 3: 价格数据
    codes = candidates["code"].tolist()
    price_map = fetch_price_batch(pro, codes, trade_date, n_days=65)

    # Step 4: 评分过滤
    results = score_candidates(candidates, price_map)
    log.info(f"通过价格筛选: {len(results)} 家")

    # Step 4b: 前瞻记录（全部通过价格筛选的候选都记，不只是展示的top N）
    log_prospective(results, trade_date)

    # Step 5: 输出
    if args.json:
        out = results.head(args.top).to_dict(orient="records")
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print_results(results, top_n=args.top)

if __name__ == "__main__":
    main()
