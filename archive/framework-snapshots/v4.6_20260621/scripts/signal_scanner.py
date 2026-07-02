#!/usr/bin/env python3
"""
signal_scanner.py — 快速异动扫描器 v1.0
单一职责：扫描股票池中今日出现量价异动的标的，输出信号排行榜 + 赛道热度

输入：stock_universe.yaml + market_status.yaml
输出：JSON → stdout
   {
     "trade_date": "YYYYMMDD",
     "hs300_pct": float,
     "rs_threshold": float,
     "signals": [...],          # 有信号的标的，按 final_score 降序
     "sector_heat": {...},      # 各子赛道热度
     "non_tradeable_signals": [...],   # 科创板等仅供参考
     "errors": [...]
   }

用法：
  python3 signal_scanner.py
  python3 signal_scanner.py --date 20260527        # 指定日期（默认今日）
  python3 signal_scanner.py --top 30               # 输出前N个信号（默认20）
  python3 signal_scanner.py --min-score 30         # 最低分数过滤（默认20）
  python3 signal_scanner.py --json-compact         # 输出紧凑JSON（减少体积）
"""

import argparse
import json
import os
import sys
import time
import logging
from datetime import datetime, timedelta
from typing import Optional

import yaml

# ── 日志配置（INFO → stderr，不污染 stdout JSON）────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)

# ── 路径常量（使用 expanduser 确保从 /tmp 运行时路径仍正确）────────────────────
UNIVERSE_YAML = os.path.expanduser(
    "~/.claude/skills/top-picks/config/stock_universe.yaml"
)
MARKET_YAML = os.path.expanduser(
    "~/.claude/skills/stock-analysis/config/market_status.yaml"
)
EVENT_MAP_QUERY = os.path.expanduser(
    "~/.claude/skills/stock-analysis/scripts/event_map_query.py"
)

# 公司.xlsx 一级赛道 → signal_scanner theme_key
_SECTOR_TO_THEME = {
    "AI光通信与高速互联":      "ai_computing",
    "AI电源与元器件":          "ai_computing",
    "AI电源与数据中心基础设施":  "ai_computing",
    "PCB与高速材料":           "ai_computing",
    "先进封装与玻璃基板":       "semiconductor",
    "半导体制造与材料":         "semiconductor",
    "商业航天与6G":            "six_networks_emerging",
    "国产算力与AI基础设施":     "ai_computing",
    "机器人与Physical AI":     "robotics",
    "液冷与散热":              "ai_computing",
    "电池储能与固态/钠电":      "power_industry",
    "科技上游战略资源":         "semiconductor",
    "端侧AI与消费电子":        "ai_computing",
    "被动元件与高端薄膜":       "ai_computing",
    "无人驾驶":                "autonomous_driving",
    "电力产业链":              "power_industry",
    "六张网与新兴支柱":         "six_networks_emerging",
    "传媒互联网":              "six_networks_emerging",
    "公用事业":                "power_industry",
    "军工":                    "six_networks_emerging",
    "化工":                    "six_networks_emerging",
    "医药":                    "six_networks_emerging",
    "大宗能源":                "power_industry",
    "消费":                    "six_networks_emerging",
    "金融":                    "six_networks_emerging",
}

_THEME_LABELS = {
    "semiconductor":        "半导体全链",
    "ai_computing":         "AI算力算电",
    "robotics":             "机器人",
    "autonomous_driving":   "无人驾驶",
    "power_industry":       "电力产业链",
    "six_networks_emerging":"六张网+新兴支柱",
}

# ── 信号级别定义 ──────────────────────────────────────────────────────────────
SIGNAL_LEVELS = {
    "resonance":   ("🔴", "价量共振"),   # RS > thresh AND VA > 1.5
    "vol_burst":   ("🟠", "量能爆发"),   # VA > 2.0 AND RS > 0
    "price_lead":  ("🟡", "价格强势"),   # RS > thresh
    "mild_vol":    ("🟢", "温和放量"),   # VA > 1.2
    "no_signal":   ("⚪", "无异动"),
}

# ── 赛道热度标签 ──────────────────────────────────────────────────────────────
def heat_label(ratio: float) -> str:
    if ratio >= 0.6:  return "🔥🔥 极热"
    if ratio >= 0.4:  return "🔥 偏热"
    if ratio >= 0.2:  return "🌡️ 温热"
    return "❄️ 冷淡"


# ═══════════════════════════════════════════════════════════════════════════════
# PART 1: 数据加载
# ═══════════════════════════════════════════════════════════════════════════════

def _load_extended_pool() -> list[dict]:
    """从 event_map_query.py pool --json 加载公司.xlsx扩展池。"""
    import subprocess
    try:
        result = subprocess.run(
            [sys.executable, EVENT_MAP_QUERY, "pool", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.warning(f"扩展池加载失败: {result.stderr[:200]}")
            return []
        lines = result.stdout.strip().split("\n")
        return json.loads("\n".join(lines[1:]))
    except Exception as e:
        log.warning(f"扩展池加载异常: {e}")
        return []


def load_pool(universe_yaml: str, market_yaml: str) -> tuple[list, dict, dict]:
    """
    加载股票池 + 市场状态。
    数据源：stock_universe.yaml（精选池，含tier/subsector元数据）
          + 公司.xlsx扩展池（event_map_query.py pool，tier=3兜底）

    返回：(stocks_list, subsector_meta, market_cfg)
    """
    # 读取 market_status.yaml
    with open(market_yaml, encoding="utf-8") as f:
        market_cfg = yaml.safe_load(f)

    # 展开 subsector_meta
    subsector_meta: dict = {}
    themes = market_cfg.get("theme_framework", {})
    for theme_key, theme_val in themes.items():
        if not isinstance(theme_val, dict):
            continue
        subsectors = theme_val.get("sub_sectors", theme_val.get("subsectors", {}))
        for ss_key, ss_val in subsectors.items():
            if isinstance(ss_val, dict):
                subsector_meta[ss_key] = {
                    "label":           ss_val.get("label", ss_key),
                    "priority":        ss_val.get("priority", 3),
                    "stage":           ss_val.get("stage", ""),
                    "catalyst_quality":ss_val.get("catalyst_quality", ""),
                    "theme_key":       theme_key,
                    "theme_label":     theme_val.get("label", theme_key),
                    "style_position":  ss_val.get("style_position", ""),
                    "leader_stocks":   ss_val.get("leader_stocks", []),
                }

    stocks_out: list = []
    seen_pairs: set = set()
    seen_codes: set = set()

    # ── 第一层：yaml精选池（保留完整tier/subsector元数据）──────────
    try:
        with open(universe_yaml, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        log.warning(f"精选池不存在: {universe_yaml}，仅使用扩展池")
        raw = {"stocks": []}

    for s in raw.get("stocks", []):
        code     = str(s.get("code", "")).zfill(6)
        name     = str(s.get("name", ""))
        ss_key   = str(s.get("subsector_key", ""))
        theme_k  = str(s.get("theme_key", ""))
        tier     = int(s.get("tier", 2))
        tradeable= bool(s.get("tradeable", True))
        also_in  = s.get("also_in", [])

        all_sectors = [(ss_key, theme_k)]
        for ai in also_in:
            ai_ss  = str(ai.get("subsector_key", ""))
            ai_th  = str(ai.get("theme_key", theme_k))
            if ai_ss:
                all_sectors.append((ai_ss, ai_th))

        for sk, tk in all_sectors:
            pair = (code, sk)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            meta = subsector_meta.get(sk, {})
            stocks_out.append({
                "code":        code,
                "name":        name,
                "subsector_key":   sk,
                "subsector_label": meta.get("label", sk),
                "theme_key":   tk,
                "theme_label": meta.get("theme_label", tk),
                "priority":    meta.get("priority", tier),
                "tier":        tier,
                "stage":       meta.get("stage", ""),
                "catalyst_quality": meta.get("catalyst_quality", ""),
                "tradeable":   tradeable,
                "cross_sector": len(all_sectors) > 1,
                "is_leader":   code in meta.get("leader_stocks", []),
                "pool_source": "curated",
            })
        seen_codes.add(code)

    curated_count = len(stocks_out)

    # ── 第二层：公司.xlsx扩展池（yaml未覆盖的股票，tier=3）────────
    ext_pool = _load_extended_pool()
    ext_added = 0
    for rec in ext_pool:
        code = str(rec.get("code", "")).zfill(6)
        if code in seen_codes:
            continue
        seen_codes.add(code)

        sector = rec.get("sector", "")
        theme_k = _SECTOR_TO_THEME.get(sector, "six_networks_emerging")
        theme_label = _THEME_LABELS.get(theme_k, theme_k)
        ss_key = f"ext_{sector}"
        tradeable = not (code.startswith("688") or code.startswith("689"))

        stocks_out.append({
            "code":            code,
            "name":            rec.get("name", ""),
            "subsector_key":   ss_key,
            "subsector_label": rec.get("sub_sector", sector),
            "theme_key":       theme_k,
            "theme_label":     theme_label,
            "priority":        3,
            "tier":            3,
            "stage":           "",
            "catalyst_quality":"",
            "tradeable":       tradeable,
            "cross_sector":    False,
            "is_leader":       False,
            "pool_source":     "extended",
        })
        ext_added += 1

    log.info(f"股票池加载完成：精选 {curated_count} + 扩展 {ext_added} = {len(stocks_out)} 条，"
             f"可交易 {sum(1 for s in stocks_out if s['tradeable'])} 条")
    return stocks_out, subsector_meta, market_cfg


# ═══════════════════════════════════════════════════════════════════════════════
# PART 2: 数据获取（Tushare 主 → efinance 备 → akshare 兜底）
# ═══════════════════════════════════════════════════════════════════════════════

def _load_env_file():
    """加载 ~/.claude/skills/.env，将其中 KEY=VALUE 注入 os.environ（不覆盖已有变量）。"""
    env_path = os.path.expanduser("~/.claude/skills/.env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip()
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


# 模块加载时自动注入 .env
_load_env_file()


def _get_tushare_token() -> Optional[str]:
    # 1. ~/.tushare_token 文件
    try:
        cfg_path = os.path.expanduser("~/.tushare_token")
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                tok = f.read().strip()
                if tok:
                    return tok
    except Exception:
        pass
    # 2. 环境变量（含 .env 注入）
    return os.environ.get("TUSHARE_TOKEN") or None


def fetch_market_data_tushare(codes: list[str], trade_date: str) -> dict:
    """
    用 Tushare daily API 获取当日行情。
    返回：code → {pct_chg, volume_ratio, price, close, volume, turn_rate}
    注意：volume_ratio (量比) Tushare daily 没有，需要另外计算或通过 daily_basic 获取
    """
    import tushare as ts
    token = _get_tushare_token()
    if not token:
        raise RuntimeError("未找到 Tushare Token，请设置环境变量 TUSHARE_TOKEN 或写入 ~/.tushare_token")
    pro = ts.pro_api(token)

    result = {}
    batch_size = 200   # Tushare 每次最多查200

    for i in range(0, len(codes), batch_size):
        batch = codes[i: i + batch_size]
        ts_codes = [f"{c}.SH" if c.startswith("6") else f"{c}.SZ" for c in batch]
        try:
            df = pro.daily(
                ts_code=",".join(ts_codes),
                trade_date=trade_date
            )
            if df is None or df.empty:
                continue
            # 也拉 daily_basic 获取量比 (volume_ratio)
            df_basic = pro.daily_basic(
                ts_code=",".join(ts_codes),
                trade_date=trade_date,
                fields="ts_code,volume_ratio,pe,total_mv"
            )
            basic_map = {}
            if df_basic is not None and not df_basic.empty:
                for _, row in df_basic.iterrows():
                    c = row["ts_code"].split(".")[0]
                    basic_map[c] = {
                        "volume_ratio": float(row.get("volume_ratio") or 1.0),
                        "pe":           row.get("pe"),
                        "total_mv":     float(row.get("total_mv") or 0),
                    }
            for _, row in df.iterrows():
                c = row["ts_code"].split(".")[0]
                bm = basic_map.get(c, {})
                result[c] = {
                    "pct_chg":      float(row.get("pct_chg") or 0),
                    "close":        float(row.get("close") or 0),
                    "price":        float(row.get("close") or 0),
                    "volume_ratio": bm.get("volume_ratio", 1.0),
                    "pe":           bm.get("pe"),
                    "total_mv":     bm.get("total_mv", 0),
                }
        except Exception as e:
            log.warning(f"Tushare daily batch {i//batch_size+1} failed: {e}")

    return result


def fetch_market_data_akshare(codes: list[str]) -> dict:
    """
    akshare Tier2：东方财富全市场实时行情（一次性拉全量再筛，同 screener 做法）。
    含量比/市值，适合交易日。
    """
    import akshare as ak

    result = {}
    try:
        log.info("akshare: 拉全市场实时行情（stock_zh_a_spot_em）...")
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return result
        code_set = set(codes)
        df["代码"] = df["代码"].astype(str).str.zfill(6)
        df = df[df["代码"].isin(code_set)]
        for _, row in df.iterrows():
            c = str(row["代码"])
            try:
                result[c] = {
                    "pct_chg":      float(row.get("涨跌幅") or 0),
                    "price":        float(row.get("最新价") or 0),
                    "close":        float(row.get("最新价") or 0),
                    "volume_ratio": float(row.get("量比") or 1.0),
                    "name":         str(row.get("名称", "")),
                    "pe":           row.get("市盈率-动态"),
                    "total_mv":     float(row.get("总市值") or 0) / 1e8,  # → 亿元
                }
            except Exception:
                pass
        log.info(f"akshare: 获取 {len(result)}/{len(codes)} 只")
    except Exception as e:
        log.warning(f"akshare 实时行情失败: {e}")
    return result


def fetch_market_data_efinance_hist(codes: list[str]) -> dict:
    """
    efinance Tier3（非交易日降级）：批量拉历史K线，取最新1日。
    pct_chg 直接从 '涨跌幅' 列取；volume_ratio 用最近2日成交量比计算。
    """
    import efinance as ef

    result = {}
    batch_size = 50
    log.info(f"efinance: 历史K线降级（{len(codes)} 只，批量 {batch_size}）...")

    for i in range(0, len(codes), batch_size):
        batch = codes[i: i + batch_size]
        try:
            hist = ef.stock.get_quote_history(batch, klt=101)
            # 返回 dict{code: DataFrame} 或单 DataFrame（只传1只时）
            if isinstance(hist, dict):
                hist_dict = hist
            else:
                hist_dict = {batch[0]: hist} if hist is not None and not hist.empty else {}

            for code_raw, df in hist_dict.items():
                c = str(code_raw).zfill(6)
                if df is None or df.empty or len(df) < 2:
                    continue
                col_map = {
                    "日期": "date", "开盘": "open", "收盘": "close",
                    "最高": "high", "最低": "low", "成交量": "volume",
                    "涨跌幅": "pct_chg",
                }
                df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
                df = df.sort_values("date").tail(6)
                last = df.iloc[-1]
                # volume_ratio = 今日量 / 5日均量
                vols = df["volume"].apply(lambda x: float(x) if x else 0).tolist()
                avg5 = sum(vols[:-1]) / max(len(vols) - 1, 1) if len(vols) > 1 else 1
                va = vols[-1] / avg5 if avg5 > 0 else 1.0

                result[c] = {
                    "pct_chg":      float(last.get("pct_chg", 0) or 0),
                    "price":        float(last.get("close", 0) or 0),
                    "close":        float(last.get("close", 0) or 0),
                    "volume_ratio": round(va, 2),
                    "pe":           None,
                    "total_mv":     0,
                }
        except Exception as e:
            log.warning(f"efinance hist batch {i//batch_size+1} failed: {e}")

    log.info(f"efinance 历史降级: 获取 {len(result)}/{len(codes)} 只")
    return result


def fetch_market_data(codes: list[str], trade_date: str) -> dict:
    """
    三层降级获取行情数据。
    Tier1: Tushare（需 token，有量比）
    Tier2: akshare stock_zh_a_spot_em（全市场实时，有量比）
    Tier3: efinance get_quote_history（历史K线，无量比→自算）
    """
    unique_codes = list(set(codes))

    # 层1: Tushare
    try:
        data = fetch_market_data_tushare(unique_codes, trade_date)
        if len(data) >= len(unique_codes) * 0.5:
            log.info(f"行情来源: Tushare，获取 {len(data)}/{len(unique_codes)} 只")
            return data
        log.warning(f"Tushare 仅返回 {len(data)} 只，降级到 akshare")
    except Exception as e:
        log.warning(f"Tushare 失败: {e}，降级到 akshare")

    # 层2: akshare（全市场实时）
    data = fetch_market_data_akshare(unique_codes)
    if len(data) >= len(unique_codes) * 0.4:
        return data
    log.warning(f"akshare 仅返回 {len(data)} 只，降级到 efinance 历史")

    # 层3: efinance（历史K线）
    data2 = fetch_market_data_efinance_hist(unique_codes)
    # 合并：akshare 已有的优先
    for c, v in data2.items():
        if c not in data:
            data[c] = v
    log.info(f"行情最终（akshare+efinance混合）: {len(data)}/{len(unique_codes)} 只")
    return data


def fetch_hs300(trade_date: str) -> float:
    """
    获取沪深300当日涨跌幅。三层降级。
    """
    # 层1: Tushare
    try:
        import tushare as ts
        token = _get_tushare_token()
        if token:
            pro = ts.pro_api(token)
            df = pro.index_daily(ts_code="399300.SZ", trade_date=trade_date, fields="pct_chg")
            if df is not None and not df.empty:
                val = float(df.iloc[0]["pct_chg"])
                log.info(f"沪深300当日涨跌: {val:+.2f}%（Tushare）")
                return val
    except Exception as e:
        log.warning(f"沪深300 Tushare 失败: {e}")

    # 层2: efinance
    try:
        import efinance as ef
        df = ef.stock.get_quote_history("399300", beg=trade_date, end=trade_date, klt=101)
        if df is not None and not df.empty:
            row = df.iloc[-1]
            open_p = float(row.get("开盘", 0) or 0)
            close_p = float(row.get("收盘", 0) or 0)
            if open_p > 0:
                val = (close_p - open_p) / open_p * 100
                log.info(f"沪深300当日涨跌: {val:+.2f}%（efinance 推算）")
                return val
    except Exception as e:
        log.warning(f"沪深300 efinance 失败: {e}")

    # 层3: akshare
    try:
        import akshare as ak
        df = ak.stock_zh_index_spot_em(symbol="沪深300")
        if df is not None and not df.empty:
            val = float(df.iloc[0].get("涨跌幅", 0) or 0)
            log.info(f"沪深300当日涨跌: {val:+.2f}%（akshare）")
            return val
    except Exception as e:
        log.warning(f"沪深300 akshare 失败: {e}")

    log.warning("无法获取沪深300涨跌，默认使用 0.0%")
    return 0.0


def fetch_stock_names(codes: list[str], market_data: Optional[dict] = None) -> dict:
    """
    预加载股票名称（用于 ST 检测）。
    优先级：market_data 内嵌名称 → Tushare stock_basic → akshare spot。
    返回：code → name
    """
    result = {}

    # 层0: 从已有行情数据中提取（akshare spot 的 market_data 携带 name 字段）
    if market_data:
        for c, v in market_data.items():
            n = v.get("name", "")
            if n:
                result[c] = n
        if len(result) >= len(codes) * 0.8:
            log.info(f"名称来源: market_data 内嵌，{len(result)} 只")
            return result

    # 层1: Tushare stock_basic
    try:
        import tushare as ts
        token = _get_tushare_token()
        if token:
            pro = ts.pro_api(token)
            df = pro.stock_basic(fields="ts_code,name,list_status")
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    c = str(row["ts_code"]).split(".")[0]
                    result[c] = str(row.get("name", ""))
                log.info(f"名称来源: Tushare stock_basic，{len(result)} 只")
                return result
    except Exception as e:
        log.warning(f"Tushare stock_basic 失败: {e}")

    # 层2: akshare spot（全市场，含名称列）
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                c = str(row.get("代码", "")).zfill(6)
                result[c] = str(row.get("名称", ""))
            log.info(f"名称来源: akshare spot，{len(result)} 只")
            return result
    except Exception as e:
        log.warning(f"akshare 名称失败: {e}")

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# PART 3: 信号计算
# ═══════════════════════════════════════════════════════════════════════════════

def get_rs_threshold(hs300_pct: float) -> float:
    """
    市场自适应 RS 阈值。
    大盘强势时阈值低（选股更宽），大盘弱时阈值高（只选真正强股）。
    """
    if hs300_pct >= 1.5:
        return 1.0    # 大盘强势，RS > 1% 即算强势
    elif hs300_pct >= 0.3:
        return 1.5
    elif hs300_pct >= -0.3:
        return 2.0    # 大盘平盘，RS > 2% 才算强势
    elif hs300_pct >= -1.5:
        return 2.5
    else:
        return 3.0    # 大盘大跌，RS > 3% 才算真强势


def compute_rs(pct_chg: float, hs300_pct: float) -> float:
    """相对强度 = 个股涨跌幅 - 沪深300涨跌幅"""
    return pct_chg - hs300_pct


def classify_signal(rs: float, va: float, rs_thresh: float) -> tuple[str, str, str]:
    """
    信号分类。
    返回：(signal_key, emoji, label)
    """
    if rs > rs_thresh and va > 1.5:
        k = "resonance"
    elif va > 2.0 and rs > 0:
        k = "vol_burst"
    elif rs > rs_thresh:
        k = "price_lead"
    elif va > 1.2:
        k = "mild_vol"
    else:
        k = "no_signal"

    emoji, label = SIGNAL_LEVELS[k]
    return k, emoji, label


def compute_score(
    rs: float,
    va: float,
    signal_key: str,
    priority: int,
    stage: str,
    tier: int,
    cross_sector: bool,
    is_leader: bool,
) -> float:
    """
    信号综合评分（0-100）。
    rs_norm: RS 归一化，[-5%, +5%] → [0, 50]
    va_norm: 量比归一化，[0.5, 3.0] → [0, 50]
    bonus:   赛道/层级/跨赛道加成
    """
    # RS 归一化: [-5, +5] 映射到 [0, 50]
    rs_norm = max(0.0, min(50.0, (rs + 5.0) / 10.0 * 50.0))
    # VA 归一化: [0.5, 3.0] 映射到 [0, 50]
    va_norm = max(0.0, min(50.0, (va - 0.5) / 2.5 * 50.0))

    base = rs_norm * 0.4 + va_norm * 0.6

    # 优先级加成（赛道景气度）
    priority_bonus = {1: 10, 2: 5, 3: 0}.get(priority, 0)

    # 赛道阶段加成
    stage_bonus = {
        "主升期": 8,
        "启动期": 5,
        "整理期": 0,
        "调整期": -5,
    }.get(stage, 0)

    # 层级加成（Tier1龙头）
    tier_bonus = {1: 6, 2: 2, 3: 0}.get(tier, 0)

    # 跨赛道共振加成
    cross_bonus = 5 if cross_sector else 0

    # 龙头股加成
    leader_bonus = 4 if is_leader else 0

    # 无信号惩罚
    if signal_key == "no_signal":
        base *= 0.3

    total = base + priority_bonus + stage_bonus + tier_bonus + cross_bonus + leader_bonus
    return round(min(100.0, total), 1)


def is_st(code: str, name: str, names_map: dict) -> bool:
    """检测是否是 ST/退市预警股。"""
    real_name = names_map.get(code, name)
    return "ST" in real_name.upper() or "退市" in real_name


# ═══════════════════════════════════════════════════════════════════════════════
# PART 4: 主扫描逻辑
# ═══════════════════════════════════════════════════════════════════════════════

def scan(
    pool: list,
    market_data: dict,
    names_map: dict,
    hs300_pct: float,
    min_score: float = 20.0,
) -> tuple[list, list, dict]:
    """
    扫描股票池，输出信号列表 + 非可交易信号 + 赛道热度。

    返回：
      (signals, non_tradeable_signals, sector_heat)
      signals: 有信号的可交易股，按 final_score 降序
      non_tradeable_signals: 科创板等信号标的
      sector_heat: subsector_key → {total, flagged, ratio, heat_label, label}
    """
    rs_thresh = get_rs_threshold(hs300_pct)
    log.info(f"RS阈值: {rs_thresh:+.1f}%（HS300={hs300_pct:+.2f}%）")

    signals: list = []
    non_tradeable_signals: list = []

    # 赛道热度统计：按 subsector_key 分组，统计总数和异动数
    sector_counter: dict = {}   # sk → {total: int, flagged: int, label: str}

    # dedup：同一 (code, subsector_key) 只算一次
    seen_entry_keys: set = set()

    for entry in pool:
        code       = entry["code"]
        name       = entry["name"]
        ss_key     = entry["subsector_key"]
        tradeable  = entry["tradeable"]
        entry_key  = (code, ss_key)

        if entry_key in seen_entry_keys:
            continue
        seen_entry_keys.add(entry_key)

        # 初始化赛道计数
        if ss_key not in sector_counter:
            sector_counter[ss_key] = {
                "total":   0,
                "flagged": 0,
                "label":   entry.get("subsector_label", ss_key),
                "theme_label": entry.get("theme_label", ""),
                "priority": entry.get("priority", 3),
                "stage":   entry.get("stage", ""),
            }
        sector_counter[ss_key]["total"] += 1

        # 获取行情数据
        mkt = market_data.get(code)
        if not mkt:
            continue

        pct_chg = mkt.get("pct_chg", 0.0)
        va      = mkt.get("volume_ratio", 1.0)
        price   = mkt.get("price", 0.0)

        # ST 检测（用预加载名称）
        if is_st(code, name, names_map):
            log.info(f"[{code}] {names_map.get(code, name)} 识别为ST，跳过")
            continue

        # 价格合理性检查（过滤停牌/异常）
        if price <= 0 or price > 300:
            continue

        rs     = compute_rs(pct_chg, hs300_pct)
        signal_key, emoji, signal_label = classify_signal(rs, va, rs_thresh)

        # 赛道热度：有异动（非 no_signal）则计入
        if signal_key != "no_signal":
            sector_counter[ss_key]["flagged"] += 1

        # 非可交易（科创板等）：只记录赛道热度，信号单独归类
        if not tradeable:
            if signal_key != "no_signal":
                non_tradeable_signals.append({
                    "code":          code,
                    "name":          names_map.get(code, name),
                    "subsector_key": ss_key,
                    "subsector_label": entry.get("subsector_label", ss_key),
                    "theme_label":   entry.get("theme_label", ""),
                    "signal":        signal_label,
                    "signal_emoji":  emoji,
                    "pct_chg":       round(pct_chg, 2),
                    "rs":            round(rs, 2),
                    "volume_ratio":  round(va, 2),
                    "price":         price,
                    "note":          "不可交易（科创板）：赛道热度参考",
                })
            continue

        # 计算综合评分
        score = compute_score(
            rs          = rs,
            va          = va,
            signal_key  = signal_key,
            priority    = entry.get("priority", 3),
            stage       = entry.get("stage", ""),
            tier        = entry.get("tier", 2),
            cross_sector= entry.get("cross_sector", False),
            is_leader   = entry.get("is_leader", False),
        )

        if score < min_score and signal_key == "no_signal":
            continue    # 无信号低分直接丢弃

        signals.append({
            "code":            code,
            "name":            names_map.get(code, name),
            "subsector_key":   ss_key,
            "subsector_label": entry.get("subsector_label", ss_key),
            "theme_key":       entry.get("theme_key", ""),
            "theme_label":     entry.get("theme_label", ""),
            "priority":        entry.get("priority", 3),
            "stage":           entry.get("stage", ""),
            "signal_key":      signal_key,
            "signal_emoji":    emoji,
            "signal_label":    signal_label,
            "final_score":     score,
            "pct_chg":         round(pct_chg, 2),
            "rs":              round(rs, 2),
            "volume_ratio":    round(va, 2),
            "price":           price,
            "pe":              mkt.get("pe"),
            "total_mv_yi":     round(mkt.get("total_mv", 0), 1),
            "cross_sector":    entry.get("cross_sector", False),
            "is_leader":       entry.get("is_leader", False),
            "tier":            entry.get("tier", 2),
            "catalyst_quality":entry.get("catalyst_quality", ""),
            "pool_source":     entry.get("pool_source", "curated"),
        })

    # 排序：先按 signal_key 优先级，再按 final_score 降序
    signal_order = {"resonance": 0, "vol_burst": 1, "price_lead": 2, "mild_vol": 3, "no_signal": 4}
    signals.sort(key=lambda x: (signal_order.get(x["signal_key"], 9), -x["final_score"]))

    # 构建赛道热度输出
    sector_heat: dict = {}
    for sk, cnt in sector_counter.items():
        total   = cnt["total"]
        flagged = cnt["flagged"]
        ratio   = flagged / total if total > 0 else 0.0
        sector_heat[sk] = {
            "label":       cnt["label"],
            "theme_label": cnt["theme_label"],
            "priority":    cnt["priority"],
            "stage":       cnt["stage"],
            "total":       total,
            "flagged":     flagged,
            "ratio":       round(ratio, 2),
            "heat_label":  heat_label(ratio),
        }

    log.info(f"扫描完成：{len(signals)} 个有信号标的，{len(non_tradeable_signals)} 个科创板信号")
    return signals, non_tradeable_signals, sector_heat


# ═══════════════════════════════════════════════════════════════════════════════
# PART 5: 输出格式化（可选，供直接查看）
# ═══════════════════════════════════════════════════════════════════════════════

def format_sector_heat_table(sector_heat: dict) -> str:
    """生成赛道热度表（按热度降序）。"""
    rows = sorted(sector_heat.items(), key=lambda x: -x[1]["ratio"])
    lines = [
        "赛道热度排行（本日异动占比）",
        f"{'子赛道':<22} {'阶段':<6} {'异动/总数':<10} {'热度'}",
        "─" * 55,
    ]
    for sk, v in rows:
        if v["total"] == 0:
            continue
        bar = f"{v['flagged']}/{v['total']}({v['ratio']*100:.0f}%)"
        lines.append(
            f"{v['label']:<22} {v['stage']:<6} {bar:<10} {v['heat_label']}"
        )
    return "\n".join(lines)


def format_signal_table(signals: list, top_n: int = 20) -> str:
    """生成信号排行榜（紧凑文本）。"""
    lines = [
        f"异动信号 Top {top_n}（RS阈值自适应）",
        f"{'#':<3} {'代码':<8} {'名称':<8} {'子赛道':<16} {'信号':<8} {'涨跌%':<7} {'RS%':<7} {'量比':<5} {'分'}",
        "─" * 80,
    ]
    for i, s in enumerate(signals[:top_n], 1):
        lines.append(
            f"{i:<3} {s['code']:<8} {s['name']:<8} "
            f"{s['subsector_label'][:14]:<16} "
            f"{s['signal_emoji']}{s['signal_label']:<6} "
            f"{s['pct_chg']:>+6.2f}% {s['rs']:>+6.2f}% "
            f"{s['volume_ratio']:>4.1f}x {s['final_score']:>5.1f}"
        )
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_trade_date() -> str:
    """返回最近一个交易日（YYYYMMDD）。交易日15:00后返回当日，否则返回上一个。"""
    now = datetime.now()
    today = now.strftime("%Y%m%d")

    # 先尝试 tushare 交易日历（最准）
    try:
        import tushare as ts
        token = _get_tushare_token()
        if token:
            pro = ts.pro_api(token)
            end = today
            start = (now - timedelta(days=10)).strftime("%Y%m%d")
            df = pro.trade_cal(exchange="SSE", start_date=start, end_date=end,
                               fields="cal_date,is_open")
            if df is not None and not df.empty:
                open_days = df[df["is_open"] == 1]["cal_date"].sort_values(ascending=False)
                if not open_days.empty:
                    latest = open_days.iloc[0]
                    if latest == today and now.hour < 15:
                        latest = open_days.iloc[1] if len(open_days) > 1 else latest
                    log.info(f"交易日历: 最近交易日 {latest}")
                    return latest
    except Exception as e:
        log.warning(f"交易日历获取失败: {e}")

    # 兜底：跳过周末
    dt = now
    if dt.hour < 15:
        dt -= timedelta(days=1)
    while dt.weekday() >= 5:  # 5=Sat, 6=Sun
        dt -= timedelta(days=1)
    fallback = dt.strftime("%Y%m%d")
    log.info(f"交易日历兜底: {fallback}")
    return fallback


def main():
    parser = argparse.ArgumentParser(description="股票池快速异动扫描器 v1.0")
    parser.add_argument("--date",         default="",    help="交易日期 YYYYMMDD（默认今日）")
    parser.add_argument("--top",          type=int, default=20, help="输出前N个信号（默认20）")
    parser.add_argument("--min-score",    type=float, default=20.0, help="最低评分过滤（默认20）")
    parser.add_argument("--json-compact", action="store_true", help="输出紧凑JSON")
    parser.add_argument("--text",         action="store_true", help="同时输出可读文本（输出到stderr）")
    args = parser.parse_args()

    trade_date = args.date or _resolve_trade_date()
    errors: list = []
    t0 = time.time()

    # ── STEP 1: 加载股票池 ────────────────────────────────────────────────────
    log.info(f"=== 信号扫描器启动 trade_date={trade_date} ===")
    try:
        pool, subsector_meta, market_cfg = load_pool(UNIVERSE_YAML, MARKET_YAML)
    except FileNotFoundError as e:
        sys.exit(f"配置文件缺失: {e}")
    except Exception as e:
        sys.exit(f"股票池加载失败: {e}")

    # 提取唯一代码集（用于行情查询）
    all_codes = list({s["code"] for s in pool})
    log.info(f"唯一股票代码: {len(all_codes)} 只")

    # ── STEP 2: 获取沪深300涨跌幅 ────────────────────────────────────────────
    hs300_pct = 0.0
    try:
        hs300_pct = fetch_hs300(trade_date)
    except Exception as e:
        errors.append(f"HS300获取失败: {e}")

    rs_threshold = get_rs_threshold(hs300_pct)

    # ── STEP 3+4: 行情 + 名称（akshare 一次拉取同时含名称，合并处理）─────────
    try:
        market_data = fetch_market_data(all_codes, trade_date)
    except Exception as e:
        errors.append(f"行情获取失败: {e}")
        market_data = {}

    if not market_data:
        sys.exit("无法获取行情数据，请检查网络或 API token")

    # 名称：优先从 market_data 内嵌 name 字段提取，补充 Tushare/akshare
    names_map: dict = {}
    try:
        names_map = fetch_stock_names(all_codes, market_data=market_data)
    except Exception as e:
        errors.append(f"名称预加载失败: {e}")

    # ── STEP 5: 扫描信号 ──────────────────────────────────────────────────────
    signals, non_tradeable, sector_heat = scan(
        pool        = pool,
        market_data = market_data,
        names_map   = names_map,
        hs300_pct   = hs300_pct,
        min_score   = args.min_score,
    )

    elapsed = round(time.time() - t0, 1)
    log.info(f"总耗时: {elapsed}s")

    # ── STEP 5.5: 催化预警 ───────────────────────────────────────────────────
    catalyst_watchlist: list = []
    try:
        import subprocess
        cat_result = subprocess.run(
            [sys.executable, EVENT_MAP_QUERY, "catalyst", "--json", "--top", "50"],
            capture_output=True, text=True, timeout=30,
        )
        if cat_result.returncode == 0:
            cat_lines = cat_result.stdout.strip().split("\n")
            cat_data = json.loads("\n".join(cat_lines[1:]))

            signal_codes = {s["code"] for s in signals}
            for cand in cat_data:
                code = cand["code"]
                mkt = market_data.get(code, {})
                pct = mkt.get("pct_chg", 0)
                va = mkt.get("volume_ratio", 1.0)
                has_signal = code in signal_codes

                catalyst_watchlist.append({
                    "code":               code,
                    "name":               cand["name"],
                    "sector":             cand["sector"],
                    "sub_sector":         cand["sub_sector"],
                    "max_catalyst_score": cand["max_catalyst_score"],
                    "catalyst_count":     len(cand["catalysts"]),
                    "top_catalyst":       cand["catalysts"][0]["event"] if cand["catalysts"] else "",
                    "top_window":         cand["catalysts"][0]["window"] if cand["catalysts"] else "",
                    "top_expectation_gap":cand["catalysts"][0].get("expectation_gap", ""),
                    "pct_chg":            round(pct, 2),
                    "volume_ratio":       round(va, 2),
                    "has_price_signal":   has_signal,
                    "alert_type":         "逻辑+量价共振" if has_signal else "纯逻辑预警",
                })
            log.info(f"催化预警: {len(catalyst_watchlist)} 只候选，"
                     f"其中纯逻辑预警 {sum(1 for w in catalyst_watchlist if not w['has_price_signal'])} 只")
    except Exception as e:
        errors.append(f"催化预警加载失败: {e}")

    # ── STEP 6: 可选文本输出（stderr）────────────────────────────────────────
    if args.text:
        print("\n" + format_sector_heat_table(sector_heat), file=sys.stderr)
        print("\n" + format_signal_table(signals, top_n=args.top), file=sys.stderr)

    # ── STEP 7: JSON 输出（stdout）────────────────────────────────────────────
    output = {
        "trade_date":            trade_date,
        "hs300_pct":             round(hs300_pct, 2),
        "rs_threshold":          rs_threshold,
        "market_data_count":     len(market_data),
        "elapsed_seconds":       elapsed,
        "signals":               signals[:args.top],
        "catalyst_watchlist":    catalyst_watchlist,
        "sector_heat":           sector_heat,
        "non_tradeable_signals": non_tradeable,
        "errors":                errors,
        "meta": {
            "pool_size":   len(all_codes),
            "pool_entries":len(pool),
            "pool_curated": sum(1 for s in pool if s.get("pool_source") == "curated"),
            "pool_extended":sum(1 for s in pool if s.get("pool_source") == "extended"),
            "signal_count":len(signals),
            "catalyst_count": len(catalyst_watchlist),
            "top_n":       args.top,
            "min_score":   args.min_score,
        }
    }

    indent = None if args.json_compact else 2
    print(json.dumps(output, ensure_ascii=False, indent=indent))


if __name__ == "__main__":
    main()
