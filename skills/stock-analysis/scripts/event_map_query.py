#!/usr/bin/env python3
"""查询产业事件地图周报CSV，供框架Layer2/3使用。

支持两个数据源：
  tech   （默认）科技主线  → $TZ_CODEX_HOME/技能数据/科技产业事件/csvMMDD/
  nonfin          非科技主线 → $TZ_CODEX_HOME/技能数据/非科技产业事件地图/

用法：
  python3 event_map_query.py events --sector AI算力
  python3 event_map_query.py events --sector 有色金属 --source nonfin
  python3 event_map_query.py mapping --sector AI算力 --keyword 光模块
  python3 event_map_query.py mapping --sector 银行 --source nonfin
  python3 event_map_query.py forward --sector AI算力
  python3 event_map_query.py forward --sector 创新药 --source nonfin
  python3 event_map_query.py corrections
  python3 event_map_query.py company 光模块        # 查公司归属赛道（含股票代码）
  python3 event_map_query.py pool                  # 输出公司池全量A股代码（JSON）
  python3 event_map_query.py pool --sector AI光通信与高速互联  # 按赛道筛选
"""
import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

import pandas as pd

WORKSPACE_ROOT = os.path.abspath(os.path.expanduser(
    os.environ.get("TZ_CODEX_HOME", "~/Desktop/tz-codex")
))
DATA_ROOT = os.path.join(WORKSPACE_ROOT, "技能数据")
TECH_DIR = os.path.join(DATA_ROOT, "科技产业事件")
NONFIN_DIR = os.path.join(DATA_ROOT, "非科技产业事件地图")
COMPANY_POOL = os.path.join(DATA_ROOT, "公司.xlsx")
CODE_MAP_CSV = os.path.join(DATA_ROOT, "company_code_map.csv")
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _sqlite_store():
    from skills.shared.event_store import EventStore
    return EventStore()


def _print_sqlite_rows(rows, as_json=False):
    print("数据源: SQLite影子库")
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif not rows:
        print("未找到匹配记录。")
    else:
        print(pd.DataFrame(rows).fillna("").to_string(index=False))

# 公司表一级赛道 → 事件地图一级赛道 映射字典
# 公司表里一个赛道可能对应事件地图多个赛道（用列表），查询时逐个搜
SECTOR_MAP = {
    "AI光通信与高速互联":     ["光通信与高速连接"],
    "AI电源与元器件":        ["被动元件", "电力与算电"],
    "AI电源与数据中心基础设施":  ["电力与算电", "AI算力"],
    "PCB与高速材料":         ["新材料", "光通信与高速连接"],
    "先进封装与玻璃基板":      ["先进封装"],
    "半导体制造与材料":       ["半导体", "存储"],
    "商业航天与6G":          ["商业航天"],
    "国产算力与AI基础设施":    ["AI算力", "AI应用基础设施"],
    "AI应用":                ["AI应用"],
    "AI应用基础设施":         ["AI应用基础设施", "AI算力"],
    "机器人与Physical AI":   ["机器人"],
    "液冷与散热":            ["电力与算电"],
    "电池储能与固态/钠电":     ["固态电池"],
    "科技上游战略资源":       ["半导体", "新材料"],
    "端侧AI与消费电子":      ["消费电子与端侧AI"],
    "被动元件与高端薄膜":      ["被动元件", "新材料"],
}
# 反向映射：事件地图赛道 → 公司表赛道列表（自动生成）
SECTOR_MAP_REVERSE = {}
for _company_sector, _event_sectors in SECTOR_MAP.items():
    for _es in _event_sectors:
        SECTOR_MAP_REVERSE.setdefault(_es, []).append(_company_sector)

# ── 科技版列定义 ─────────────────────────────────────────────────
EVENT_COLS = [
    "事件ID", "一级赛道", "二级事件", "事件名称", "事件时间", "当前状态",
    "重要程度", "是否已被交易", "是否存在预期差", "主要影响方向", "后续观察指标", "备注",
]
MAPPING_COLS = [
    "事件ID", "事件名称", "一级赛道", "一级产业", "二级环节",
    "三级零部件/材料/设备", "代表公司/公司类型", "当前市场关注度", "是否已炒作",
    "预期差", "验证指标", "风险点",
]
FORWARD_COLS = [
    "前瞻ID", "事件", "时间", "发生概率", "重要程度", "一级赛道",
    "可能受益方向", "可能受损方向", "是否已被交易", "预期差", "后续观察指标", "事件窗口",
]
CORRECTION_COLS = [
    "更新日期", "问题", "判断", "对应事件ID", "后续动作", "优先级", "状态", "备注",
]

# ── 非科技版列定义（mapping/forward 列名不同）────────────────────
NONFIN_MAPPING_COLS = [
    "事件ID", "一级赛道", "二级事件", "产业链位置", "受益方向",
    "代表公司类型", "弹性来源", "风险点", "备注",
]
NONFIN_FORWARD_COLS = [
    "事件ID", "观察周期", "关键日期/窗口", "观察指标",
    "验证逻辑", "可能结果", "跟踪优先级", "备注",
]
# mapping keyword 搜索列（非科技版）
NONFIN_MAPPING_SEARCH_COLS = ["产业链位置", "受益方向", "代表公司类型", "弹性来源"]


def find_latest_csv_dir(source="tech"):
    """找最新一期事件地图目录。"""
    if source == "tech":
        best_date, best_dir = None, None
        for d in glob.glob(os.path.join(TECH_DIR, "csv*")):
            if not os.path.isdir(d):
                continue
            for f in glob.glob(os.path.join(d, "*_*.csv")):
                m = re.search(r"_(\d{8})", os.path.basename(f))
                if m and (best_date is None or m.group(1) > best_date):
                    best_date, best_dir = m.group(1), d
        if best_dir is None:
            raise FileNotFoundError(
                f"未找到科技事件地图csv目录，请检查: {TECH_DIR}（应有 csvMMDD/ 子文件夹）"
            )
        return best_dir
    else:  # nonfin
        candidates = glob.glob(os.path.join(NONFIN_DIR, "非科技主线产业事件地图_*"))
        candidates = [c for c in candidates if os.path.isdir(c)]
        if not candidates:
            raise FileNotFoundError(
                f"未找到非科技事件地图目录，请检查: {NONFIN_DIR}（应有 非科技主线产业事件地图_* 子文件夹）"
            )
        # 按文件夹名末尾日期排序取最新
        def _date(p):
            m = re.search(r"(\d{8})$", os.path.basename(p))
            return m.group(1) if m else "0"
        return sorted(candidates, key=_date)[-1]


def load_csv(csv_dir, prefix):
    matches = glob.glob(os.path.join(csv_dir, f"{prefix}_*.csv"))
    if not matches:
        raise FileNotFoundError(f"{csv_dir} 下未找到 {prefix}_*.csv")
    f = matches[0]
    return pd.read_csv(f), f


def query_events(sector_keyword=None, exclude_full_traded=True, source="tech"):
    df, src = load_csv(find_latest_csv_dir(source), "events")
    if sector_keyword:
        df = df[df["一级赛道"].str.contains(sector_keyword, na=False)]
    if exclude_full_traded:
        df = df[~df["是否已被交易"].str.contains("充分交易", na=False)]
    cols = [c for c in EVENT_COLS if c in df.columns]
    return df[cols], src


def query_mapping(sector_keyword=None, keyword=None, source="tech"):
    csv_dir = find_latest_csv_dir(source)
    df, src = load_csv(csv_dir, "mapping")

    if source == "nonfin":
        if sector_keyword:
            df = df[df["一级赛道"].str.contains(sector_keyword, na=False)]
        if keyword:
            mask = pd.Series(False, index=df.index)
            for col in NONFIN_MAPPING_SEARCH_COLS:
                if col in df.columns:
                    mask = mask | df[col].astype(str).str.contains(keyword, na=False)
            df = df[mask]
        cols = [c for c in NONFIN_MAPPING_COLS if c in df.columns]
    else:
        if sector_keyword:
            df = df[df["一级赛道"].str.contains(sector_keyword, na=False)]
        if keyword:
            search_cols = ["一级产业", "二级环节", "三级零部件/材料/设备", "代表公司/公司类型"]
            mask = pd.Series(False, index=df.index)
            for col in search_cols:
                if col in df.columns:
                    mask = mask | df[col].astype(str).str.contains(keyword, na=False)
            df = df[mask]
        cols = [c for c in MAPPING_COLS if c in df.columns]

    return df[cols], src


def query_forward(sector_keyword=None, source="tech"):
    csv_dir = find_latest_csv_dir(source)
    df, src = load_csv(csv_dir, "forward")

    if source == "nonfin":
        # 非科技版 forward 无 一级赛道 列，通过 events 表的事件ID做关联筛选
        if sector_keyword:
            try:
                ev_df, _ = load_csv(csv_dir, "events")
                matched_ids = ev_df[
                    ev_df["一级赛道"].str.contains(sector_keyword, na=False)
                ]["事件ID"].tolist()
                df = df[df["事件ID"].isin(matched_ids)]
            except Exception:
                pass  # 关联失败时返回全量
        cols = [c for c in NONFIN_FORWARD_COLS if c in df.columns]
    else:
        if sector_keyword:
            df = df[df["一级赛道"].str.contains(sector_keyword, na=False)]
        cols = [c for c in FORWARD_COLS if c in df.columns]

    return df[cols], src


def query_corrections(source="tech"):
    if source == "nonfin":
        return pd.DataFrame({"提示": ["非科技事件地图暂无动态修正清单（corrections），"
                                       "请在事件维护时直接更新 events 表的当前状态字段。"]}), "N/A"
    df, src = load_csv(find_latest_csv_dir(source), "corrections")
    cols = [c for c in CORRECTION_COLS if c in df.columns]
    return df[cols], src


STATUS_COLS = [
    "sector_id", "theme", "sub_sector", "stage", "wave_number", "wave_position",
    "signal_stock_code", "catalyst_quality", "ai_correlation", "priority",
    "style_position", "event_map_status", "last_updated", "note",
]


def _load_code_map() -> dict:
    """加载 company_code_map.csv（tushare stock_basic 生成），返回 公司名→代码 字典。"""
    if not os.path.exists(CODE_MAP_CSV):
        return {}
    df = pd.read_csv(CODE_MAP_CSV)
    return {str(r["name"]): str(r["code"]).zfill(6) for _, r in df.iterrows()}


_CODE_MAP_CACHE = None


def get_code_map() -> dict:
    global _CODE_MAP_CACHE
    if _CODE_MAP_CACHE is None:
        _CODE_MAP_CACHE = _load_code_map()
    return _CODE_MAP_CACHE


def query_company(keyword):
    """查公司表：输入公司名/代码/关键词，返回赛道归属+事件地图对应赛道+股票代码。"""
    if not os.path.exists(COMPANY_POOL):
        return pd.DataFrame({"error": [f"公司表不存在: {COMPANY_POOL}"]}), "N/A"
    df = pd.read_excel(COMPANY_POOL, sheet_name="科技公司池")
    mask = pd.Series(False, index=df.index)
    for col in ["公司名称", "二级环节", "三级环节/定位", "一级赛道"]:
        if col in df.columns:
            mask = mask | df[col].astype(str).str.contains(keyword, na=False)
    result = df[mask]
    if result.empty:
        # Try non-tech pool
        try:
            df2 = pd.read_excel(COMPANY_POOL, sheet_name="非科技公司池")
            mask2 = pd.Series(False, index=df2.index)
            for col in ["公司名称", "二级环节", "三级环节/定位", "一级赛道"]:
                if col in df2.columns:
                    mask2 = mask2 | df2[col].astype(str).str.contains(keyword, na=False)
            result = df2[mask2]
        except Exception:
            pass
    if result.empty:
        return result, COMPANY_POOL
    result = result.copy()
    result["事件地图赛道"] = result["一级赛道"].map(
        lambda x: "/".join(SECTOR_MAP.get(x, ["未映射"]))
    )
    code_map = get_code_map()
    result["股票代码"] = result["公司名称"].map(lambda x: code_map.get(x, ""))
    cols = ["公司名称", "股票代码", "一级赛道", "事件地图赛道", "二级环节", "三级环节/定位",
            "市场/属性", "关联事件ID"]
    cols = [c for c in cols if c in result.columns]
    return result[cols], COMPANY_POOL


def query_pool(sector_filter=None, source="all"):
    """从公司.xlsx导出A股代码列表，供signal_scanner消费。

    返回 DataFrame 含 code, name, sector, sub_sector, pool_source 列。
    """
    if not os.path.exists(COMPANY_POOL):
        return pd.DataFrame({"error": [f"公司表不存在: {COMPANY_POOL}"]}), "N/A"

    code_map = get_code_map()
    all_rows = []

    sheets = []
    if source in ("all", "tech"):
        sheets.append(("科技公司池", "tech"))
    if source in ("all", "nonfin"):
        sheets.append(("非科技公司池", "nonfin"))

    for sheet_name, src_tag in sheets:
        try:
            df = pd.read_excel(COMPANY_POOL, sheet_name=sheet_name)
        except Exception:
            continue
        a_stock = df[df["市场/属性"].astype(str).str.contains("A股", na=False)]
        if sector_filter:
            a_stock = a_stock[a_stock["一级赛道"].astype(str).str.contains(sector_filter, na=False)]
        for _, row in a_stock.iterrows():
            name = str(row.get("公司名称", ""))
            code = code_map.get(name, "")
            if not code:
                continue
            all_rows.append({
                "code": code,
                "name": name,
                "sector": str(row.get("一级赛道", "")),
                "sub_sector": str(row.get("二级环节", "")),
                "pool_source": src_tag,
            })

    result = pd.DataFrame(all_rows)
    if not result.empty:
        result = result.drop_duplicates(subset=["code"])
    return result, COMPANY_POOL


def _parse_importance(val: str) -> int:
    """★数量 → 数字分。"""
    if not val:
        return 0
    return str(val).count("★")


def _parse_probability(val: str) -> int:
    val = str(val).strip()
    if val == "高":
        return 3
    if val in ("中高",):
        return 2
    return 1


def _parse_traded_penalty(val: str) -> int:
    val = str(val)
    if "充分交易" in val:
        return -3
    if "快速交易" in val:
        return -2
    if "部分交易" in val:
        return -1
    if "初步交易" in val or "未充分" in val or "较少" in val:
        return 1
    return 0


def _parse_window_urgency(val: str) -> float:
    val = str(val)
    if "30天" in val and "90" not in val:
        return 3.0
    if "30" in val and "90" in val:
        return 2.0
    if "90天" in val:
        return 1.5
    if "Q3" in val or "Q4" in val:
        return 1.0
    return 0.5


def query_catalyst_candidates(source="tech"):
    """从forward表筛选高价值催化事件，匹配公司池输出受益股票代码列表。

    催化评分 = 重要程度 × 发生概率 + 已交易折扣 + 窗口紧迫度
    匹配方式：forward.可能受益方向关键词 ↔ 公司.xlsx 二级环节/三级环节
    """
    try:
        fwd_df, fwd_src = query_forward(source=source)
    except FileNotFoundError:
        return [], "N/A"

    if fwd_df.empty:
        return [], fwd_src

    code_map = get_code_map()

    # Load company pool (both tech + nonfin)
    company_rows = []
    for sheet in ("科技公司池", "非科技公司池"):
        try:
            df = pd.read_excel(COMPANY_POOL, sheet_name=sheet)
            a_stock = df[df["市场/属性"].astype(str).str.contains("A股", na=False)]
            for _, row in a_stock.iterrows():
                name = str(row.get("公司名称", ""))
                code = code_map.get(name, "")
                if not code:
                    continue
                company_rows.append({
                    "code": code,
                    "name": name,
                    "sector": str(row.get("一级赛道", "")),
                    "sub2": str(row.get("二级环节", "")),
                    "sub3": str(row.get("三级环节/定位", "")),
                    "event_ids": str(row.get("关联事件ID", "")),
                })
        except Exception:
            continue

    # Score each forward event
    scored_events = []
    for _, fwd in fwd_df.iterrows():
        imp = _parse_importance(fwd.get("重要程度", ""))
        prob = _parse_probability(fwd.get("发生概率", ""))
        traded = _parse_traded_penalty(fwd.get("是否已被交易", ""))
        urgency = _parse_window_urgency(fwd.get("事件窗口", ""))

        catalyst_score = imp * prob + traded + urgency
        if catalyst_score < 8:
            continue

        benefit_text = str(fwd.get("可能受益方向", ""))
        keywords = [k.strip() for k in
                    benefit_text.replace("、", ",").replace("/", ",").replace("，", ",").split(",")
                    if len(k.strip()) >= 2]

        scored_events.append({
            "fwd_id": str(fwd.get("前瞻ID", "")),
            "event": str(fwd.get("事件", "")),
            "sector": str(fwd.get("一级赛道", "")),
            "window": str(fwd.get("事件窗口", "")),
            "time": str(fwd.get("时间", "")),
            "importance": imp,
            "probability": str(fwd.get("发生概率", "")),
            "traded": str(fwd.get("是否已被交易", "")),
            "expectation_gap": str(fwd.get("预期差", ""))[:80],
            "catalyst_score": catalyst_score,
            "keywords": keywords,
        })

    # Match companies to scored events
    # Build: code → list of catalyst events
    code_catalysts: dict = {}

    # 赛道匹配表：forward.一级赛道 → 公司.xlsx 可匹配的一级赛道集合
    sector_compat: dict = {}
    for evt_sector in set(e["sector"] for e in scored_events):
        compat = set(SECTOR_MAP_REVERSE.get(evt_sector, []))
        sector_compat[evt_sector] = compat

    for evt in scored_events:
        matched_codes = set()
        allowed_sectors = sector_compat.get(evt["sector"], set())

        for kw in evt["keywords"]:
            if len(kw) < 3:
                continue
            for comp in company_rows:
                if allowed_sectors and comp["sector"] not in allowed_sectors:
                    continue
                if kw in comp["sub2"] or kw in comp["sub3"]:
                    matched_codes.add(comp["code"])

        for code in matched_codes:
            if code not in code_catalysts:
                comp_info = next((c for c in company_rows if c["code"] == code), {})
                code_catalysts[code] = {
                    "code": code,
                    "name": comp_info.get("name", ""),
                    "sector": comp_info.get("sector", ""),
                    "sub_sector": comp_info.get("sub2", ""),
                    "catalysts": [],
                    "max_catalyst_score": 0,
                }
            entry = code_catalysts[code]
            entry["catalysts"].append({
                "fwd_id": evt["fwd_id"],
                "event": evt["event"],
                "window": evt["window"],
                "time": evt["time"],
                "catalyst_score": evt["catalyst_score"],
                "traded": evt["traded"],
                "expectation_gap": evt["expectation_gap"],
            })
            entry["max_catalyst_score"] = max(entry["max_catalyst_score"], evt["catalyst_score"])

    result = sorted(code_catalysts.values(), key=lambda x: -x["max_catalyst_score"])
    return result, fwd_src


def query_status(keyword=None, stage_filter=None, source="tech"):
    csv_dir = find_latest_csv_dir(source)
    df, src = load_csv(csv_dir, "sectors_status")
    if keyword:
        mask = pd.Series(False, index=df.index)
        for col in ["theme", "sub_sector", "sector_id", "note"]:
            if col in df.columns:
                mask = mask | df[col].astype(str).str.contains(keyword, na=False)
        df = df[mask]
    if stage_filter:
        df = df[df["stage"].str.contains(stage_filter, na=False)]
    cols = [c for c in STATUS_COLS if c in df.columns]
    return df[cols], src


def _build_event_company_index():
    """构建事件ID → A股公司列表的索引（从公司.xlsx的关联事件ID字段）。"""
    try:
        tech = pd.read_excel(COMPANY_POOL, sheet_name='科技公司池')
        nonfin = pd.read_excel(COMPANY_POOL, sheet_name='非科技公司池')
        all_co = pd.concat([tech, nonfin], ignore_index=True)
        a_stock = all_co[all_co['市场/属性'].astype(str).str.contains('A股', na=False)]
    except Exception:
        return {}

    code_map = get_code_map()
    index: dict = {}
    for _, row in a_stock.iterrows():
        event_ids_str = str(row.get('关联事件ID', ''))
        if not event_ids_str or event_ids_str == 'nan':
            continue
        name = str(row.get('公司名称', ''))
        code = code_map.get(name, '')
        sub2 = str(row.get('二级环节', ''))
        role = str(row.get('角色', ''))
        for eid in event_ids_str.replace('；', ';').replace('，', ';').split(';'):
            eid = eid.strip()
            if not eid:
                continue
            index.setdefault(eid, []).append({
                'name': name, 'code': code,
                'sub2': sub2, 'role': role,
            })
    return index


def compute_scored_events(source="tech", sector=None):
    """对所有活跃事件计算窗口位置，返回 (scored列表, 数据源路径)。

    这是"催化是否新鲜/该不该操作"的唯一计算入口——window日历展示、
    scanner候选打分、stock-analysis第二层催化预处理都必须调这个函数，
    禁止任何消费方各自重新计算一遍。
    """
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from catalyst_window_model import score_event, parse_event_date, classify_bucket

    from datetime import date
    today = date.today()

    events_df, src = query_events(sector, exclude_full_traded=True, source=source)
    if events_df.empty:
        return [], src

    company_index = _build_event_company_index()

    # 双时钟：构建 event_id → 最新corrections日期 的索引
    correction_dates: dict = {}
    try:
        corr_df, _ = query_corrections(source=source)
        if not corr_df.empty and "对应事件ID" in corr_df.columns and "更新日期" in corr_df.columns:
            for _, row in corr_df.iterrows():
                raw_ids = str(row.get("对应事件ID", "")).strip()
                if not raw_ids or raw_ids == "nan" or raw_ids.upper() == "ALL":
                    continue
                parsed = parse_event_date(str(row.get("更新日期", "")))
                if parsed is None:
                    continue
                # 支持分号分隔的多事件ID（如 "MLCC-2026-001;CAP-2026-001"）
                for eid in raw_ids.replace("；", ";").split(";"):
                    eid = eid.strip()
                    if not eid:
                        continue
                    if eid not in correction_dates or parsed > correction_dates[eid]:
                        correction_dates[eid] = parsed
    except Exception:
        pass  # corrections不可用时降级为单时钟

    scored = []
    for _, row in events_df.iterrows():
        event_id = str(row.get("事件ID", ""))
        result = score_event(
            event_id=event_id,
            event_name=str(row.get("事件名称", "")),
            event_time=str(row.get("事件时间", "")),
            event_status=str(row.get("当前状态", "")),
            importance=str(row.get("重要程度", "")),
            expectation_gap=str(row.get("是否存在预期差", "")),
            today=today,
            latest_correction_date=correction_dates.get(event_id),
        )
        result['companies'] = company_index.get(event_id, [])
        result['bucket'] = classify_bucket(result)
        scored.append(result)

    scored.sort(key=lambda x: (-x.get("urgency", 0), -x.get("final_score", 0)))
    return scored, src


def query_window(source="tech", top=50, sector=None):
    """催化窗口日历：对所有活跃事件计算当前窗口位置，并关联公司池输出受益股。"""
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from catalyst_window_model import format_calendar_output

    try:
        scored, src = compute_scored_events(source=source, sector=sector)
    except FileNotFoundError as e:
        return str(e), "N/A"

    if not scored:
        return "无活跃事件", "N/A"

    from datetime import date
    output = format_calendar_output(scored[:top], today=date.today())
    return output, src


def query_window_json(source="tech", top=300, sector=None):
    """催化窗口日历的JSON形态，供scanner/其他脚本消费（而非重新计算）。"""
    scored, src = compute_scored_events(source=source, sector=sector)
    out = []
    for e in scored[:top]:
        edate = e.get("event_date")
        out.append({
            "event_id":     e["event_id"],
            "event_name":   e["event_name"],
            "catalyst_type": e.get("catalyst_type"),
            "event_date":   edate.isoformat() if edate else None,
            "bucket":       e.get("bucket", "gray"),
            "urgency":      e.get("urgency", 0),
            "final_score":  e.get("final_score", 0),
            "action":       e.get("action"),
            "phase_label":  e.get("phase_label"),
            "t_current":    e.get("t_current"),
            "t_to_peak":    e.get("t_to_peak"),
        })
    return out, src


def main():
    SOURCE_HELP = "数据源：tech=科技主线（默认），nonfin=非科技主线（有色/储能/绿能/机械/银行/证券/创新药）"

    parser = argparse.ArgumentParser(description="产业事件地图周报查询（科技/非科技双源）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_events = sub.add_parser("events", help="查询事件总库（默认排除充分交易的事件）")
    p_events.add_argument("--sector", help="一级赛道关键词，如 AI算力 / 有色金属")
    p_events.add_argument("--include-full-traded", action="store_true", help="包含已充分交易的事件")
    p_events.add_argument("--source", choices=["tech", "nonfin"], default="tech", help=SOURCE_HELP)

    p_mapping = sub.add_parser("mapping", help="查询产业链映射（环节/代表公司类型）")
    p_mapping.add_argument("--sector", help="一级赛道关键词")
    p_mapping.add_argument("--keyword", help="在产业链字段中搜索关键词")
    p_mapping.add_argument("--source", choices=["tech", "nonfin"], default="tech", help=SOURCE_HELP)

    p_forward = sub.add_parser("forward", help="查询前瞻验证窗口")
    p_forward.add_argument("--sector", help="一级赛道关键词")
    p_forward.add_argument("--source", choices=["tech", "nonfin"], default="tech", help=SOURCE_HELP)

    p_corrections = sub.add_parser("corrections", help="查询本周动态修正清单（科技源专用）")
    p_corrections.add_argument("--source", choices=["tech", "nonfin"], default="tech", help=SOURCE_HELP)

    p_status = sub.add_parser("status", help="查询赛道阶段状态（替代market_status.yaml）")
    p_status.add_argument("--keyword", help="赛道/主题关键词，如 半导体 / AI应用 / 设备")
    p_status.add_argument("--stage", help="按阶段筛选，如 主升期 / 启动期 / 过热期")
    p_status.add_argument("--source", choices=["tech", "nonfin"], default="tech", help=SOURCE_HELP)

    p_company = sub.add_parser("company", help="查公司归属赛道（输入公司名/代码/关键词）")
    p_company.add_argument("keyword", help="公司名称或关键词，如 雅克科技 / 前驱体 / 光模块")

    # SQLite影子期新增命令：不改变既有company/events/mapping等CSV命令语义。
    p_db_company = sub.add_parser("db-company", help="从SQLite查询公司产业角色与关联催化")
    p_db_company.add_argument("keyword", help="公司名称或股票代码")
    p_db_company.add_argument("--json", action="store_true")

    p_db_sector = sub.add_parser("sector", help="从SQLite查询赛道公司及其关联催化")
    p_db_sector.add_argument("keyword", help="赛道或细分角色关键词，如 AI应用 / Agent安全")
    p_db_sector.add_argument("--json", action="store_true")

    p_db_event = sub.add_parser("event", help="从SQLite查询事件详情及关联公司")
    p_db_event.add_argument("event_id", help="事件ID，如 AI-SAAS-2026-001")
    p_db_event.add_argument("--json", action="store_true")

    p_db_catalysts = sub.add_parser("catalysts", help="从SQLite查询结构化前瞻催化")
    p_db_catalysts.add_argument("--sector", help="按赛道筛选")
    p_db_catalysts.add_argument("--json", action="store_true")

    p_db_search = sub.add_parser("search", help="从SQLite跨公司、事件和产业角色搜索")
    p_db_search.add_argument("keyword")
    p_db_search.add_argument("--json", action="store_true")

    p_db_audit = sub.add_parser("db-audit", help="查看SQLite导入异常摘要")
    p_db_audit.add_argument("--json", action="store_true")

    p_pool = sub.add_parser("pool", help="导出公司池A股代码列表（JSON，供signal_scanner消费）")
    p_pool.add_argument("--sector", help="按一级赛道筛选，如 AI光通信与高速互联")
    p_pool.add_argument("--source", choices=["all", "tech", "nonfin"], default="all",
                        help="科技/非科技/全部（默认all）")
    p_pool.add_argument("--json", action="store_true", help="输出JSON格式（默认表格）")

    p_catalyst = sub.add_parser("catalyst", help="催化预警：即将兑现的高价值事件 + 受益股票代码")
    p_catalyst.add_argument("--source", choices=["tech", "nonfin"], default="tech", help=SOURCE_HELP)
    p_catalyst.add_argument("--json", action="store_true", help="输出JSON格式")
    p_catalyst.add_argument("--top", type=int, default=30, help="输出前N只（默认30）")

    p_window = sub.add_parser("window", help="催化窗口日历：当前活跃事件的时间位置+操作建议（基于A-E类型模型）")
    p_window.add_argument("--source", choices=["tech", "nonfin"], default="tech", help=SOURCE_HELP)
    p_window.add_argument("--sector", help="按一级赛道筛选，如 半导体 / 机器人")
    p_window.add_argument("--top", type=int, default=50, help="最多显示N个事件（默认50）")
    p_window.add_argument("--json", action="store_true",
                          help="JSON输出（供scanner等脚本消费，禁止其他脚本重新计算窗口位置）")

    args = parser.parse_args()

    if args.cmd in ("db-company", "sector", "event", "catalysts", "search", "db-audit"):
        store = _sqlite_store()
        if args.cmd == "db-company":
            rows = store.company(args.keyword)
        elif args.cmd == "sector":
            rows = store.sector(args.keyword)
        elif args.cmd == "event":
            rows = store.event(args.event_id)
        elif args.cmd == "catalysts":
            rows = store.catalysts(args.sector)
        elif args.cmd == "search":
            rows = store.search(args.keyword)
        else:
            rows = store.anomalies()
        _print_sqlite_rows(rows, args.json)
        return

    if args.cmd == "window":
        sector = getattr(args, "sector", None)
        if args.json:
            import json
            try:
                records, src = query_window_json(args.source, args.top, sector)
            except FileNotFoundError as e:
                print(json.dumps({"error": str(e)}, ensure_ascii=False))
                return
            print(json.dumps(records, ensure_ascii=False, indent=2))
            return
        output, src = query_window(args.source, args.top, sector)
        print(f"数据源: {src}")
        print(output)
        return
    elif args.cmd == "catalyst":
        candidates, src = query_catalyst_candidates(args.source)
        print(f"数据源: {src}")
        if not candidates:
            print("未找到催化候选。")
        elif args.json:
            import json
            print(json.dumps(candidates[:args.top], ensure_ascii=False, indent=2))
        else:
            print(f"催化预警候选 Top {args.top}（催化评分≥8）\n")
            for i, c in enumerate(candidates[:args.top], 1):
                cats = c["catalysts"]
                top_cat = max(cats, key=lambda x: x["catalyst_score"])
                print(f"{i:2d}. {c['code']} {c['name']:<8} "
                      f"催化分={c['max_catalyst_score']:.0f}  "
                      f"事件数={len(cats)}  "
                      f"最强: {top_cat['event'][:30]} ({top_cat['window']})")
        return
    elif args.cmd == "pool":
        result, src = query_pool(args.sector, args.source)
        print(f"数据源: {src}")
        if result.empty:
            print("未找到匹配记录。")
        elif args.json:
            import json
            records = result.to_dict(orient="records")
            print(json.dumps(records, ensure_ascii=False, indent=2))
        else:
            print(f"共 {len(result)} 只A股")
            print(result.to_string(index=False))
        return
    elif args.cmd == "company":
        result, src = query_company(args.keyword)
    elif args.cmd == "status":
        source = args.source
        result, src = query_status(args.keyword, args.stage, source)
    else:
        source = args.source
        if args.cmd == "events":
            result, src = query_events(args.sector, not args.include_full_traded, source)
        elif args.cmd == "mapping":
            result, src = query_mapping(args.sector, args.keyword, source)
        elif args.cmd == "forward":
            result, src = query_forward(args.sector, source)
        else:
            result, src = query_corrections(source)

    print(f"数据源: {src}\n")
    if result.empty:
        print("未找到匹配记录。")
    else:
        print(result.to_string(index=False))


if __name__ == "__main__":
    main()
