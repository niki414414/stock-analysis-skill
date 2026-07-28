#!/usr/bin/env python3
"""
Top Picks Screener — 四大主线漏斗选股
Phase 1 (universe): 从四大主线 concept boards 获取股票宇宙池（带7天缓存）
Phase 2 (screen):   快速过滤 + 评分 → Top 40 候选（主线均衡，每活跃主线≥8只；交易日用实时，非交易日历史降级）

Usage:
    python3 top_picks_screener.py --phase universe [--force-rebuild]
    python3 top_picks_screener.py --phase screen
"""

import os
import sys
import json
import argparse
import warnings
import math
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

WORKSPACE_ROOT = os.path.abspath(os.path.expanduser(
    os.environ.get("TZ_CODEX_HOME", "~/Desktop/tz-codex")
))
REPO_ROOT = os.path.join(WORKSPACE_ROOT, "repo")
sys.path.insert(0, os.path.join(REPO_ROOT, "skills", "stock-analysis", "scripts"))
from event_map_query import find_latest_csv_dir, load_csv as _load_event_map_csv  # noqa: E402


def load_sector_status() -> dict:
    """
    从事件地图 sectors_status.csv 读取赛道阶段/优先级/位置——
    这是赛道判断的唯一数据源（2026-07-12起，替代原 market_status.yaml sub_sectors，
    见 event_map_query.py 的 status 子命令，两者读同一份表）。
    返回 {sector_id: {label, theme, priority, stage, style_position}}。
    """
    df, _ = _load_event_map_csv(find_latest_csv_dir("tech"), "sectors_status")
    meta = {}
    for _, row in df.iterrows():
        sid = str(row.get("sector_id", "")).strip()
        if not sid:
            continue
        meta[sid] = {
            "label":          str(row.get("sub_sector", sid)),
            "theme":          str(row.get("theme", "")),
            "priority":       int(row.get("priority", 3)) if str(row.get("priority", "")).strip() else 3,
            "stage":          str(row.get("stage", "")) if str(row.get("stage", "")) != "nan" else "",
            "style_position": str(row.get("style_position", "")) if str(row.get("style_position", "")) != "nan" else "",
        }
    return meta


def _load_env_file():
    env_path = os.path.join(REPO_ROOT, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if key and val and key not in os.environ:
                os.environ[key] = val


_load_env_file()


def _log(msg):
    print(f"[INFO] {msg}", file=sys.stderr)


def _safe_float(val):
    if val is None:
        return None
    try:
        f = float(str(val).replace(",", "").replace("%", "").strip())
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    except (ValueError, TypeError):
        return None


def _with_timeout(fn, timeout=15, default=None):
    """在独立线程中执行 fn()，超时/异常时返回 default，避免外部 API 无限阻塞。"""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            _log(f"[TIMEOUT] call exceeded {timeout}s, returning default")
            return default
        except Exception as e:
            _log(f"[ERROR] in timed call: {e}")
            return default


# ============================================================
# SECTION 1: Sub-sector → Concept Board Keyword Mapping
# ============================================================

SUBSECTOR_KEYWORDS = {
    # 半导体全链
    "equipment_etch_cvd":     ["刻蚀", "薄膜沉积", "半导体设备"],
    "equipment_measure":      ["量测", "半导体设备"],
    "equipment_cmp_clean":    ["CMP", "清洗设备", "半导体设备"],
    "equipment_packaging":    ["封装设备", "测试设备", "半导体设备"],
    "material_wafer":         ["大硅片", "硅片"],
    "material_photoresist":   ["光刻胶"],
    "material_specialty_gas": ["电子特气", "特种气体"],
    "material_wet_chemicals": ["湿电子化学品", "电子化学品"],
    "material_photomask":     ["光掩模", "掩模版"],
    "material_precursor":     ["前驱体", "半导体材料"],
    "material_target":        ["靶材"],
    "material_cmp_slurry":    ["抛光液", "抛光垫", "半导体材料"],
    "material_quartz":        ["石英"],
    "material_substrate":     ["封装基板", "载板", "先进封装"],
    "material_bond_wire":     ["键合线"],
    "design_eda_ip":          ["EDA", "芯片国产替代"],
    "design_logic":           ["CPU", "GPU", "NPU", "芯片国产替代"],
    "design_memory_hbm":      ["存储芯片", "HBM"],
    "design_analog":          ["模拟芯片"],
    "design_fpga":            ["FPGA"],
    "foundry":                ["晶圆代工"],
    "packaging_advanced":     ["先进封装"],
    "hbm_ecosystem":          ["HBM", "存储封测", "HBM产业链", "先进封装"],
    "sic_gan":                ["第三代半导体", "碳化硅"],
    "diamond_substrate":      ["金刚石", "培育钻"],
    "precision_parts":        ["半导体零部件", "半导体精密"],

    # 机器人
    "humanoid_body":          ["人形机器人"],
    "humanoid_parts":         ["人形机器人", "谐波减速器"],
    "industrial_robot":       ["工业机器人", "机器人概念"],
    "machine_vision":         ["机器视觉"],
    "cnc_machine_tools":      ["工业母机", "数控机床", "机床"],

    # AI算力+算电协同+数据中心互联
    "ai_chip":                ["AI算力", "芯片国产替代"],
    "ai_chip_domestic":       ["国产AI芯片", "算力芯片", "AI芯片国产"],
    "ai_infra":               ["算力概念", "IDC概念", "AI服务器"],
    "ai_application":         ["人工智能", "AIGC", "大模型"],
    "physical_ai":            ["具身智能"],
    "power_grid":             ["特高压", "电力设备"],
    "power_ups_cooling":      ["液冷", "UPS概念"],
    "optical_module_cpo":     ["光模块", "CPO", "硅光"],
    "pcb_ai_server":          ["PCB概念", "覆铜板", "铜箔", "高速PCB"],
    "fiber_optical_dc":       ["光纤光缆", "光缆"],
    "ai_network_infra":       ["交换机", "高速连接", "连接器"],
    "power_nuclear":          ["核电", "核能", "小堆", "SMR"],
    "satellite_comms":        ["卫星互联网", "低轨卫星", "空天通信", "卫星通信"],
    "commercial_space":       ["商业航天", "航天军工", "卫星导航"],
    "passive_components":     ["MLCC", "被动元件", "电感", "片式电阻", "陶瓷电容"],

    # 无人驾驶
    "sensor_lidar_radar":     ["激光雷达", "毫米波雷达"],
    "domain_controller":      ["无人驾驶", "智能座舱"],
    "hd_map_v2x":             ["车联网", "V2X"],
    "robotaxi":               ["无人驾驶", "Robotaxi"],
}


# ============================================================
# SECTION 2: Trading Day Detection
# ============================================================

def is_trading_day() -> bool:
    """周一至周五视为潜在交易日（未排除法定节假日，实际遇到节假日会触发数据降级）。"""
    return datetime.now().weekday() < 5  # 0=Mon … 4=Fri


def get_last_trading_date_str() -> str:
    """返回最近一个工作日的日期字符串 YYYYMMDD。"""
    d = datetime.now()
    if d.weekday() == 5:    # 周六
        d -= timedelta(days=1)
    elif d.weekday() == 6:  # 周日
        d -= timedelta(days=2)
    return d.strftime("%Y%m%d")


# ============================================================
# SECTION 3: Universe Building（宇宙池构建）
#   优先路径：静态 stock_universe.yaml（无 API 调用，速度最快）
#   备选路径：动态爬取概念板块（仅 --force-rebuild 时触发，用于季度维护）
# ============================================================

def load_static_universe(static_path: str) -> list:
    """
    从静态 YAML 加载宇宙池，并与事件地图 sectors_status.csv 中的
    stage/priority/style_position 合并。无需任何外部 API 调用，执行时间 < 0.1 秒。
    """
    try:
        import yaml
    except ImportError:
        os.system("pip3 install pyyaml --quiet")
        import yaml

    with open(static_path) as f:
        static_data = yaml.safe_load(f)

    try:
        ss_meta = load_sector_status()
    except Exception as e:
        _log(f"sectors_status.csv load warning: {e}; using subsector defaults")
        ss_meta = {}

    # ── 构建宇宙池条目（允许同一股票跨多个子赛道出现）────────────
    seen_pairs: set = set()  # (code, ss_key) 去重
    result: list   = []

    def _make_entry(code, name, ss_key, theme_key, tier, tradeable):
        meta = ss_meta.get(ss_key, {})
        return {
            "code":            code,
            "name":            name,
            "subsector_key":   ss_key,
            "subsector_label": meta.get("label", ss_key),
            "theme":           theme_key,
            "theme_label":     meta.get("theme", theme_key),
            "priority":        meta.get("priority", 3),
            "stage":           meta.get("stage", ""),
            "style_position":  meta.get("style_position", ""),
            "tier":            tier,
            "matched_board":   "static_pool",
            "tradeable":       tradeable,  # False → 科创板/不可交易，仅用于赛道信号
        }

    for stock in static_data.get("stocks", []):
        code      = str(stock.get("code", "")).strip()
        name      = str(stock.get("name", code))
        ss_key    = str(stock.get("subsector_key", ""))
        theme_key = str(stock.get("theme_key", ""))
        tier      = int(stock.get("tier", 3))
        tradeable = bool(stock.get("tradeable", True))
        also_in   = stock.get("also_in", [])  # list of {subsector_key, theme_key}

        if not code:
            continue

        # 主赛道 + also_in 赛道 → 逐一建立条目
        all_sectors = [(ss_key, theme_key)] + [
            (str(ai.get("subsector_key", "")), str(ai.get("theme_key", theme_key)))
            for ai in also_in
            if ai.get("subsector_key", "")
        ]

        for sk, tk in all_sectors:
            if not sk:
                continue
            pair = (code, sk)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            result.append(_make_entry(code, name, sk, tk, tier, tradeable))

    unique_codes = len({e["code"] for e in result})
    tradeable_cnt = sum(1 for e in result if e.get("tradeable", True))
    _log(f"Static universe loaded: {len(result)} entries ({unique_codes} unique stocks, "
         f"{tradeable_cnt} tradeable) from {static_path.split('/')[-1]}")
    return result


def _universe_cache_valid(cache_path: str, max_days: int = 7) -> bool:
    """判断缓存文件是否存在且未超过 max_days 天。"""
    if not os.path.exists(cache_path):
        return False
    age = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(cache_path))).days
    return age < max_days


def fetch_all_concept_boards() -> list:
    try:
        import akshare as ak
        df = _with_timeout(lambda: ak.stock_board_concept_name_em(), timeout=15, default=None)
        if df is None or df.empty:
            return []
        name_col = next(
            (c for c in df.columns if "名称" in str(c) or "板块" in str(c)),
            df.columns[0],
        )
        names = df[name_col].astype(str).tolist()
        _log(f"Found {len(names)} concept boards")
        return names
    except Exception as e:
        _log(f"Concept board list failed: {e}")
        return []


def match_boards(all_boards: list, keywords: list) -> list:
    matched, seen = [], set()
    for kw in keywords:
        kw_lower = kw.lower()
        for board in all_boards:
            if board in seen:
                continue
            if kw_lower in board.lower() or board.lower() in kw_lower:
                matched.append(board)
                seen.add(board)
    return matched[:3]


def fetch_concept_stocks(board_name: str) -> list:
    try:
        import akshare as ak
        df = _with_timeout(
            lambda: ak.stock_board_concept_cons_em(symbol=board_name),
            timeout=10, default=None,
        )
        if df is None or df.empty:
            return []
        code_col = next((c for c in df.columns if "代码" in str(c)), df.columns[0])
        return df[code_col].astype(str).str.strip().tolist()
    except Exception as e:
        _log(f"  Board '{board_name}' failed: {e}")
        return []


def build_universe(force_rebuild: bool = False) -> list:
    """
    构建宇宙池。
    - 默认（推荐）：从静态 stock_universe.yaml 加载，无 API 调用，速度最快
    - --force-rebuild：动态爬取概念板块（季度维护时使用，比较慢）
    - 7天缓存：non-force-rebuild 且缓存有效时跳过构建
    """
    cache_path = UNIVERSE_DEFAULT

    # ── 优先：静态宇宙池（无 API，< 0.1s）──────────────────────
    if not force_rebuild and os.path.exists(STATIC_UNIVERSE_PATH):
        result = load_static_universe(STATIC_UNIVERSE_PATH)
        if len(result) >= 50:
            with open(cache_path, "w") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            _log(f"Universe from static pool → {cache_path}")
            return result
        _log("Static pool returned < 50 stocks, falling back to cache/dynamic build")

    # ── 次优：7 天有效缓存 ─────────────────────────────────────
    if not force_rebuild and _universe_cache_valid(cache_path):
        age = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(cache_path))).days
        _log(f"Using cached universe (age={age}d). Use --force-rebuild to refresh.")
        with open(cache_path) as f:
            cached = json.load(f)
        _log(f"Cached universe: {len(cached)} stocks")
        return cached

    # 重新构建：赛道元数据统一来自事件地图 sectors_status.csv
    ss_meta = load_sector_status()

    _log("Fetching all concept board names...")
    all_boards = fetch_all_concept_boards()

    universe: dict = {}

    for ss_key, meta in ss_meta.items():
        ss_label   = meta.get("label", ss_key)
        theme_key  = meta.get("theme", ss_key)
        priority   = meta.get("priority", 3)
        stage      = meta.get("stage", "")
        style_pos  = meta.get("style_position", "")

        keywords = SUBSECTOR_KEYWORDS.get(ss_key, [ss_label])
        matched  = match_boards(all_boards, keywords) if all_boards else []

        if not matched:
            _log(f"  [{ss_key}] no matching boards, skip")
            continue

        for board in matched[:2]:
            codes = fetch_concept_stocks(board)
            new_count = 0
            for code in codes:
                if code.startswith("688"):  # 科创板
                    continue
                if code not in universe:
                    universe[code] = {
                        "code": code,
                        "subsector_key": ss_key,
                        "subsector_label": ss_label,
                        "theme": theme_key,
                        "theme_label": theme_key,
                        "priority": priority,
                        "stage": stage,
                        "style_position": style_pos,
                        "matched_board": board,
                    }
                    new_count += 1
                else:
                    existing = universe[code]
                    if priority < existing.get("priority", 99):
                        existing.update({
                            "subsector_key": ss_key,
                            "subsector_label": ss_label,
                            "theme": theme_key,
                            "theme_label": theme_key,
                            "priority": priority,
                            "stage": stage,
                            "style_position": style_pos,
                            "matched_board": board,
                        })
            _log(f"  [{ss_key}] '{board}': {len(codes)} stocks, {new_count} new")

    result = list(universe.values())
    _log(f"Universe total after dedup: {len(result)} stocks")
    return result


# ============================================================
# SECTION 4: Weekend Fallback — Historical Data as Spot Proxy
# ============================================================

def fetch_stock_names(codes: list) -> dict:
    """
    获取股票名称（用于 ST 检测）。
    优先 tushare stock_basic（静态数据，周末可用），失败时返回空 dict。
    """
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        return {}
    try:
        import tushare as ts
        pro = ts.pro_api(token)
        df = pro.stock_basic(fields="ts_code,name,list_status")
        if df is None or df.empty:
            return {}
        names = {}
        for _, row in df.iterrows():
            tc   = str(row.get("ts_code", ""))
            name = str(row.get("name", ""))
            code = tc.split(".")[0]
            if code in codes:
                names[code] = name
        _log(f"Stock names from tushare: {len(names)} matched")
        return names
    except Exception as e:
        _log(f"Tushare stock names failed: {e}")
        return {}


def _connectivity_check() -> bool:
    """快速探测东方财富 API 是否可达（3 秒超时）。"""
    try:
        import requests
        r = requests.get(
            "https://push2.eastmoney.com/api/qt/stock/get?secid=1.600519&fields=f43",
            timeout=3,
        )
        return r.status_code == 200
    except Exception:
        return False


def _fetch_one_batch(batch):
    """子进程入口：拉单批 K 线，结果写 stdout pickle。"""
    import efinance as ef
    return ef.stock.get_quote_history(batch)


def fetch_hist_as_spot(codes: list):
    """
    非交易日降级：用 efinance 历史K线（最近10日）构建与 stock_zh_a_spot_em 等价的 DataFrame。
    - 连通性预检：API 不通直接返回 None，不做无效重试
    - 每批次用独立子进程执行，限时 8 秒；超时 kill 子进程，真正终止
    - 连续 3 批失败则提前终止整个降级流程
    """
    try:
        import pandas as pd
        import multiprocessing
    except ImportError:
        _log("pandas/multiprocessing not available")
        return None

    _log("Checking eastmoney API connectivity...")
    if not _connectivity_check():
        _log("ERROR: eastmoney API unreachable, skipping historical fallback")
        return None

    batch_size = 50
    all_rows = []
    n_batches = math.ceil(len(codes) / batch_size)
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAIL = 3

    for i in range(0, len(codes), batch_size):
        batch = codes[i: i + batch_size]
        batch_num = i // batch_size + 1
        _log(f"  Hist batch {batch_num}/{n_batches}: {len(batch)} stocks...")

        try:
            ctx = multiprocessing.get_context("spawn")
            pool = ctx.Pool(1)
            try:
                async_result = pool.apply_async(_fetch_one_batch, (batch,))
                try:
                    result = async_result.get(timeout=8)
                    consecutive_failures = 0
                except multiprocessing.TimeoutError:
                    _log(f"  Batch {batch_num} timed out (8s), skipping")
                    pool.terminate()
                    pool.join()
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_FAIL:
                        _log(f"  {MAX_CONSECUTIVE_FAIL} consecutive timeouts — aborting fallback")
                        return None
                    continue
            finally:
                try:
                    pool.terminate()
                    pool.join()
                except Exception:
                    pass
            # efinance 返回 dict{code: DataFrame} 或单个 DataFrame
            if isinstance(result, dict):
                hist_dict = result
            else:
                # 单只股票时直接返回 DataFrame
                hist_dict = {batch[0]: result} if not result.empty else {}

            for code, df in hist_dict.items():
                code = str(code).strip()
                if df is None or df.empty:
                    continue

                col_map = {
                    "日期": "date", "开盘": "open", "收盘": "close",
                    "最高": "high", "最低": "low", "成交量": "volume",
                    "成交额": "amount", "涨跌幅": "pct_chg", "换手率": "turnover",
                }
                df = df.rename(columns=col_map)
                df = df.sort_values("date").tail(10)
                if len(df) < 2:
                    continue

                last = df.iloc[-1]
                vols = df["volume"].apply(_safe_float).dropna().tolist()

                vol_today = _safe_float(last.get("volume"))
                vol_avg5  = (sum(vols[-6:-1]) / 5) if len(vols) >= 6 else None
                vol_ratio = (
                    round(vol_today / vol_avg5, 2)
                    if vol_today and vol_avg5 and vol_avg5 > 0
                    else 1.0
                )

                all_rows.append({
                    "代码":      code,
                    "名称":      code,          # 由 fetch_stock_names 补填
                    "最新价":    _safe_float(last.get("close")),
                    "涨跌幅":    _safe_float(last.get("pct_chg")),
                    "成交量":    _safe_float(last.get("volume")),
                    "量比":      vol_ratio,
                    "换手率":    _safe_float(last.get("turnover")),
                    "总市值":    None,           # 历史K线无市值，跳过市值过滤
                    "市盈率-动态": None,
                    "市净率":    None,
                })

        except Exception as e:
            _log(f"  Batch {batch_num} failed: {e}")
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAIL:
                _log(f"  {MAX_CONSECUTIVE_FAIL} consecutive failures — aborting fallback")
                break
            continue

    if not all_rows:
        _log("Historical fallback: no data rows collected")
        return None

    df_out = pd.DataFrame(all_rows)
    _log(f"Historical fallback: {len(df_out)} stocks with data (last trading day)")
    return df_out


# ============================================================
# SECTION 5: Fast Screening（量化粗筛）
# ============================================================

def _code_to_ts(code: str) -> str:
    """将6位纯数字代码转为 Tushare ts_code（6/688开头→SH，其余→SZ）。"""
    return f"{code}.SH" if code.startswith("6") else f"{code}.SZ"


def fetch_spot_via_tushare(ts_codes: list = None):
    """
    主数据源：Tushare pro.daily + daily_basic。
    - ts_codes 不为空时：精准查询指定股票（约 100 只），大幅减少 API 流量和响应时间
    - ts_codes 为空时：全市场兜底（备用逻辑，正常不走此路）
    - 所有外部调用均包裹 _with_timeout 防止无限阻塞
    """
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        _log("Tushare: no token, skipping")
        return None
    try:
        import tushare as ts
        import pandas as pd
        pro = ts.pro_api(token)
        trade_date = get_last_trading_date_str()

        # 构建 ts_code 过滤字符串（精准查询）
        ts_code_filter = None
        if ts_codes:
            ts_code_filter = ",".join(_code_to_ts(c) for c in ts_codes if c)
            _log(f"Tushare targeted: {len(ts_codes)} stocks, trade_date={trade_date}")
        else:
            _log(f"Tushare full market: trade_date={trade_date}")

        # ── daily（含过滤）────────────────────────────────────
        def _fetch_daily():
            kw = {"trade_date": trade_date, "fields": "ts_code,close,pct_chg,vol"}
            if ts_code_filter:
                kw["ts_code"] = ts_code_filter
            return pro.daily(**kw)

        df_daily = _with_timeout(_fetch_daily, timeout=25, default=None)

        if df_daily is None or df_daily.empty:
            # 如果精准查询返回空（Tushare 可能不支持长串），退化到全市场
            if ts_code_filter:
                _log("Tushare targeted returned empty, retrying full market...")
                df_daily = _with_timeout(
                    lambda: pro.daily(trade_date=trade_date,
                                      fields="ts_code,close,pct_chg,vol"),
                    timeout=30, default=None,
                )
            if df_daily is None or df_daily.empty:
                _log("Tushare daily: no data (non-trading day or API limit)")
                return None

        # ── daily_basic（含过滤）──────────────────────────────
        try:
            def _fetch_basic():
                kw = {"trade_date": trade_date,
                      "fields": "ts_code,turnover_rate,total_mv"}
                if ts_code_filter:
                    kw["ts_code"] = ts_code_filter
                return pro.daily_basic(**kw)

            df_basic = _with_timeout(_fetch_basic, timeout=20, default=None)
            if df_basic is not None and not df_basic.empty:
                df = df_daily.merge(df_basic, on="ts_code", how="left")
            else:
                df = df_daily
                df["turnover_rate"] = None
                df["total_mv"]      = None
        except Exception:
            df = df_daily
            df["turnover_rate"] = None
            df["total_mv"]      = None

        df["代码"]        = df["ts_code"].str.split(".").str[0]
        df["名称"]        = df["代码"]
        df["量比"]        = 1.0   # Tushare daily 不含量比，设中性值
        df["市盈率-动态"] = None
        df["市净率"]      = None
        df = df.rename(columns={
            "close":         "最新价",
            "pct_chg":       "涨跌幅",
            "vol":           "成交量",
            "turnover_rate": "换手率",
            "total_mv":      "总市值",
        })
        # 注：不在此处排除科创板(688)，由 hard_filter 依据 tradeable 字段处理
        # Tushare total_mv 单位是万元，screener 内部按元/1e8 换算成亿，需先×1e4
        if "总市值" in df.columns:
            df["总市值"] = pd.to_numeric(df["总市值"], errors="coerce") * 1e4
        cols = ["代码","名称","最新价","涨跌幅","成交量","量比","换手率","总市值","市盈率-动态","市净率"]
        df = df[[c for c in cols if c in df.columns]]
        _log(f"Tushare primary: {len(df)} stocks (trade_date={trade_date})")
        return df
    except Exception as e:
        _log(f"Tushare primary failed: {e}")
        return None


def fetch_spot_via_akshare():
    """备选数据源1：akshare 实时行情（全市场），含量比/换手/市值。超时 35s。"""
    try:
        import akshare as ak
        df = _with_timeout(lambda: ak.stock_zh_a_spot_em(), timeout=35, default=None)
        if df is None or df.empty:
            raise ValueError("Empty spot data (timeout or network issue)")
        _log(f"akshare fallback: {len(df)} stocks")
        return df
    except Exception as e:
        _log(f"akshare fallback failed: {e}")
        return None


def hard_filter(code, name, price, volume, market_cap_yi, change_pct,
                tradeable: bool = True) -> tuple:
    """
    Top-picks 轻过滤。市值为 None（历史降级时）跳过市值检查。
    tradeable=False 的科创板观测标的：不生成买入候选，仅供赛道热度信号使用。
    """
    if not tradeable:
        return False, "不可交易（科创板/账户限制）：仅用作赛道热度信号"
    if any(tag in name for tag in ["ST", "退", "*"]):
        return False, f"ST/退市: {name}"
    if price is None or price <= 0:
        return False, "停牌或价格异常"
    if volume is None or volume <= 0:
        return False, "成交量为零（停牌）"
    if price > 300:
        return False, f"价格{price}元超过300元上限"
    if price < 3:
        return False, f"价格{price}元低于3元下限"
    if market_cap_yi is not None and market_cap_yi < 30:
        return False, f"市值{market_cap_yi:.1f}亿低于30亿"
    return True, "通过"


def calc_quick_score(change_pct, vol_ratio, turnover) -> float:
    pct = change_pct or 0
    vr  = vol_ratio  or 1.0
    tr  = turnover   or 0
    momentum  = max(0.0, min(30.0, (pct + 5) / 15.0 * 30))
    vol_score = max(0.0, min(40.0, (vr  - 0.5) / 3.0  * 40))
    to_score  = max(0.0, min(30.0, (tr  - 0.3) / 4.7  * 30))
    return round(momentum + vol_score + to_score, 1)


def fetch_fund_flow_5d(code: str):
    try:
        import akshare as ak
        market = "sh" if code.startswith(("600", "601", "603")) else "sz"
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        if df is None or df.empty:
            return None
        main_col = None
        for col in df.columns:
            col_s = str(col)
            if "主力" in col_s and ("净额" in col_s or ("净" in col_s and "流入" in col_s)):
                main_col = col
                break
        if main_col is None:
            return None

        def to_yi(v):
            v = _safe_float(str(v).replace(",", ""))
            if v is None: return None
            if abs(v) > 1e7: return v / 1e8
            elif abs(v) > 1e3: return v / 1e4
            return v

        series = df[main_col].apply(to_yi).dropna().tail(5)
        if not len(series): return None
        total = float(series.sum())
        return round(total, 3) if abs(total) < 200 else None
    except Exception:
        return None


_ACTIVE_STAGES = {"主升期", "启动期"}


def _balanced_top40(scored: list) -> list:
    """
    从按 composite_score 降序排列的候选中选出均衡的 Top 40：
    每个活跃主线（stage=主升期/启动期）至少 8 只。
    若前 40 中某主线不足 8 只，从该主线剩余候选（非负面过滤已过）中补位。

    注：同一股票在不同子赛道的条目视为独立候选（跨赛道共振），
    去重键为 (code, ss_key)，不再以 code 单独去重。
    """
    MIN_PER_THEME = 8
    selected = list(scored[:40])
    # 以 (code, subsector_key) 为条目唯一标识
    selected_keys = {(c["code"], c.get("subsector_key", "")) for c in selected}

    active_themes = {c["theme"] for c in scored if c.get("stage", "") in _ACTIVE_STAGES}

    for theme in active_themes:
        in_selected = sum(1 for c in selected if c["theme"] == theme)
        if in_selected < MIN_PER_THEME:
            extras = [
                c for c in scored[40:]
                if c["theme"] == theme
                and (c["code"], c.get("subsector_key", "")) not in selected_keys
            ]
            need = MIN_PER_THEME - in_selected
            for extra in extras[:need]:
                selected.append(extra)
                selected_keys.add((extra["code"], extra.get("subsector_key", "")))

    selected.sort(key=lambda x: x["composite_score"], reverse=True)
    return selected


def fast_screen(universe_path: str, force_rescreen: bool = False) -> list:
    """
    量化粗筛：
    - 交易日：实时行情（akshare stock_zh_a_spot_em）
    - 非交易日：历史K线降级（efinance get_quote_history）
    - 当日缓存：同一自然日内第二次调用直接返回缓存，无需重跑 API
    """
    cache_path = _screen_cache_path()
    if not force_rescreen and _screen_cache_valid():
        _log(f"Using today's screen cache: {cache_path}")
        with open(cache_path) as f:
            return json.load(f)

    with open(universe_path) as f:
        universe = json.load(f)

    # 宇宙池中同一股票可能对应多个子赛道条目（also_in）
    universe_codes = {s["code"] for s in universe}
    # code → list of sub-sector entries（支持多赛道共振）
    universe_map_multi: dict = {}
    for s in universe:
        c = s["code"]
        universe_map_multi.setdefault(c, []).append(s)
    # 单代表条目（用于数据查询时的简单兜底）
    universe_map = {c: entries[0] for c, entries in universe_map_multi.items()}

    tradeable_count = sum(1 for s in universe if s.get("tradeable", True))
    _log(f"Universe loaded: {len(universe_codes)} unique stocks, "
         f"{len(universe)} entries ({tradeable_count} tradeable)")

    # ── 数据获取：Tushare（主）→ akshare（备1）→ efinance历史（备2）─
    trading = is_trading_day()
    spot_df = None
    names_map = {}

    # 主数据源：Tushare（精准查询宇宙池股票，避免拉取全市场 4000+ 条）
    spot_df = fetch_spot_via_tushare(ts_codes=list(universe_codes))

    # 备选1：akshare 实时（仅交易日有意义）
    if spot_df is None:
        _log("Tushare unavailable — trying akshare...")
        spot_df = fetch_spot_via_akshare()

    if spot_df is None:
        _log("akshare unavailable — switching to efinance historical fallback...")

        # 历史降级只拉 P1/P2 活跃主线，从 2000+ 只压缩到 ~300 只
        _FALLBACK_PRIOS   = {1, 2}
        _FALLBACK_STAGES  = {"主升期", "启动期"}
        fallback_codes = [
            s["code"] for s in universe
            if s.get("priority", 3) in _FALLBACK_PRIOS
            and s.get("stage", "") in _FALLBACK_STAGES
        ]
        if len(fallback_codes) < 80:   # 保底，防止 yaml 未更新时过度裁剪
            fallback_codes = list(universe_codes)
        _log(f"Fallback universe: {len(fallback_codes)} stocks "
             f"(P1/P2 active, reduced from {len(universe_codes)})")
        universe_codes_list = fallback_codes

        # 获取股票名称（用于 ST 检测，tushare 静态数据周末可用）
        _log("Fetching stock names for ST detection...")
        names_map = fetch_stock_names(universe_codes_list)

        spot_df = fetch_hist_as_spot(universe_codes_list)
        if spot_df is None:
            _log("ERROR: Both real-time and historical data unavailable")
            return []

        # 补填名称
        if names_map:
            spot_df["名称"] = spot_df["代码"].map(lambda c: names_map.get(c, c))

        _log(f"Using last-trading-day data ({get_last_trading_date_str()})")

    # ── 标准化代码列 ─────────────────────────────────────────
    code_col = next((c for c in spot_df.columns if c == "代码"), spot_df.columns[0])
    spot_df[code_col] = spot_df[code_col].astype(str).str.strip()

    in_universe = spot_df[spot_df[code_col].isin(universe_codes)].copy()
    _log(f"Universe stocks matched in data: {len(in_universe)}")

    # ── 硬过滤 + 快速评分 ─────────────────────────────────────
    # 同一股票可能在多个子赛道中出现（also_in），逐赛道建立候选条目
    seen_entry_keys: set = set()  # (code, ss_key) 去重
    candidates = []

    for _, row in in_universe.iterrows():
        code         = str(row.get(code_col, "")).strip()
        name         = str(row.get("名称", code))
        price        = _safe_float(row.get("最新价"))
        volume       = _safe_float(row.get("成交量"))
        market_cap   = _safe_float(row.get("总市值"))
        market_cap_yi = round(market_cap / 1e8, 2) if market_cap else None
        change_pct   = _safe_float(row.get("涨跌幅"))
        vol_ratio    = _safe_float(row.get("量比"))
        turnover     = _safe_float(row.get("换手率"))

        # 取该股票所有子赛道条目（主赛道 + also_in）
        all_entries = universe_map_multi.get(code, [universe_map.get(code, {})])

        for meta in all_entries:
            tradeable  = meta.get("tradeable", True)
            ss_key     = meta.get("subsector_key", "")
            entry_key  = (code, ss_key)

            if entry_key in seen_entry_keys:
                continue

            passed, reason = hard_filter(code, name, price, volume, market_cap_yi,
                                         change_pct, tradeable=tradeable)
            if not passed:
                # 不可交易的科创板标的：记录赛道信号但不进入候选池
                if not tradeable and price and volume and price > 0 and volume > 0:
                    _log(f"  [sector_signal] {code}({name}) {ss_key}: 涨跌{change_pct}%")
                continue

            seen_entry_keys.add(entry_key)
            qscore = calc_quick_score(change_pct, vol_ratio, turnover)

            candidates.append({
                "code":            code,
                "name":            name,
                "price":           price,
                "change_pct":      change_pct,
                "vol_ratio":       vol_ratio,
                "turnover_rate":   turnover,
                "market_cap_yi":   market_cap_yi,
                "pe":              _safe_float(row.get("市盈率-动态")),
                "pb":              _safe_float(row.get("市净率")),
                "quick_score":     qscore,
                "fund_flow_5d":    None,
                "composite_score": qscore,
                "subsector_key":   ss_key,
                "subsector_label": meta.get("subsector_label"),
                "theme":           meta.get("theme"),
                "theme_label":     meta.get("theme_label"),
                "priority":        meta.get("priority", 3),
                "stage":           meta.get("stage"),
                "style_position":  meta.get("style_position"),
                "tier":            meta.get("tier", 3),
                "tradeable":       tradeable,
                # 跨赛道标注：股票出现在多个赛道，标注共振
                "cross_sector":    len(all_entries) > 1,
                "data_mode":       "realtime" if trading else "historical_fallback",
            })

    _log(f"After hard filter: {len(candidates)} candidates")
    if not candidates:
        return []

    # ── 综合得分（资金流移至 STEP3 deep phase，节省 ~6 分钟）────
    # composite_score = quick_score；深度资金流由 stock_data_fetcher 统一拉取
    candidates.sort(key=lambda x: x["quick_score"], reverse=True)
    top80 = candidates[:80]
    for c in top80:
        c["composite_score"] = c["quick_score"]

    top80.sort(key=lambda x: x["composite_score"], reverse=True)
    top40 = _balanced_top40(top80)

    if top40:
        _log(f"Top 40 (balanced): {len(top40)} stocks, "
             f"score range {top40[-1]['composite_score']:.1f} – {top40[0]['composite_score']:.1f}")

    # ── 写入当日缓存 ──────────────────────────────────────────
    with open(cache_path, "w") as f:
        json.dump(top40, f, ensure_ascii=False)
    _log(f"Screen result cached → {cache_path}")

    return top40


# ============================================================
# SECTION 6: Helpers & Main
# ============================================================

def _theme_breakdown(universe: list) -> dict:
    counts: dict = {}
    for s in universe:
        t = s.get("theme", "unknown")
        counts[t] = counts.get(t, 0) + 1
    return counts


UNIVERSE_DEFAULT = "/tmp/tp_universe.json"
SCREEN_CACHE_PREFIX = "/tmp/tp_screen_"
STATIC_UNIVERSE_PATH = os.path.expanduser(
    os.path.join(REPO_ROOT, "skills", "top-picks", "config", "stock_universe.yaml")
)


def _screen_cache_path() -> str:
    return f"{SCREEN_CACHE_PREFIX}{datetime.now().strftime('%Y%m%d')}.json"


def _screen_cache_valid() -> bool:
    return os.path.exists(_screen_cache_path())


def main():
    parser = argparse.ArgumentParser(description="Top Picks Screener — 四大主线漏斗选股")
    parser.add_argument("--phase", choices=["universe", "screen"], required=True)
    parser.add_argument("--universe",       default=UNIVERSE_DEFAULT)
    parser.add_argument("--force-rebuild",  action="store_true",
                        help="强制重建宇宙池，忽略7天缓存")
    parser.add_argument("--force-rescreen", action="store_true",
                        help="强制重跑粗筛，忽略当日缓存")
    args = parser.parse_args()

    if args.phase == "universe":
        try:
            universe = build_universe(force_rebuild=args.force_rebuild)
            with open(UNIVERSE_DEFAULT, "w") as f:
                json.dump(universe, f, ensure_ascii=False, indent=2)
            _log(f"Universe saved → {UNIVERSE_DEFAULT}")
            using_static = (not args.force_rebuild and os.path.exists(STATIC_UNIVERSE_PATH))
            print(json.dumps({
                "status":          "ok",
                "source":          "static_pool" if using_static else "dynamic_concept_boards",
                "total":           len(universe),
                "saved_to":        UNIVERSE_DEFAULT,
                "cache_used":      (not args.force_rebuild and
                                    _universe_cache_valid(UNIVERSE_DEFAULT)),
                "theme_breakdown": _theme_breakdown(universe),
            }, ensure_ascii=False, indent=2))
        except Exception as e:
            print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
            sys.exit(1)

    elif args.phase == "screen":
        try:
            cache_hit = (not args.force_rescreen and _screen_cache_valid())
            top40 = fast_screen(args.universe, force_rescreen=args.force_rescreen)
            trading = is_trading_day()
            print(json.dumps({
                "status":      "ok",
                "screen_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "data_mode":   "screen_cached" if cache_hit else ("realtime" if trading else "historical_fallback"),
                "data_date":   "今日缓存" if cache_hit else ("实时" if trading else get_last_trading_date_str()),
                "total":       len(top40),
                "top40":       top40,
            }, ensure_ascii=False, indent=2))
        except Exception as e:
            print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
            sys.exit(1)


if __name__ == "__main__":
    main()
