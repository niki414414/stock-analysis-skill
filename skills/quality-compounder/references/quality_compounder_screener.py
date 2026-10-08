#!/usr/bin/env python3
"""
Quality Compounder Screener — 沪深300十年质量-价值筛选器

Phase 1 (constituents): 拉沪深300最新成分股 + 行业 + 上市日期（30天缓存）
Phase 2 (screen):       Stage 1 全量化漏斗——剔除金融/地产 → 剔除上市不足10年 →
                         PE<30 → 十年净利润CAGR+稳定性双门槛 → 候选池
Phase 3 (quality):      Stage 2 量化代理指标——现金流质量/杠杆/毛利率趋势/
                         周转率趋势/沪深300内行业排名/主营集中度（仅展示+标注，不剔除）

Usage:
    python3 quality_compounder_screener.py --phase constituents [--force-rebuild]
    python3 quality_compounder_screener.py --phase screen [--json]
    python3 quality_compounder_screener.py --phase quality [--json]
"""

import os
import sys
import json
import argparse
import warnings
import math
from datetime import datetime, timedelta

from pathlib import Path
_CODE_ROOT = Path(__file__).resolve().parents[3]
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))
from skills.shared.paths import config_file
from skills.shared.datasource import load_env as _shared_load_env, get_pro as _shared_get_pro

warnings.filterwarnings("ignore")

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(SKILL_DIR, "cache")
CONFIG_PATH = os.path.join(SKILL_DIR, "config", "thresholds.yaml")
CONSTITUENTS_CACHE = os.path.join(CACHE_DIR, "constituents_cache.json")
FUNDAMENTALS_CACHE = os.path.join(CACHE_DIR, "fundamentals_cache.json")


def _log(msg):
    print(f"[INFO] {msg}", file=sys.stderr)


def _load_env_file():
    _shared_load_env(config_file())


_load_env_file()


def _safe_float(val):
    if val is None:
        return None
    try:
        f = float(str(val).replace(",", "").replace("%", "").strip())
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    except (ValueError, TypeError):
        return None


def load_config():
    import yaml
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_pro():
    return _shared_get_pro()


def latest_trade_date(pro):
    """找最近一个已收盘交易日，避免盘中/非交易日拉到空数据。
    探测当天数据是否已发布，不猜测收盘时间("hour<16"不可靠：沙盒系统时钟不等于
    北京时间，且A股实际15:00收盘不是16:00)。"""
    today = datetime.now()
    cal = pro.trade_cal(exchange="SSE",
                         start_date=(today - timedelta(days=10)).strftime("%Y%m%d"),
                         end_date=today.strftime("%Y%m%d"))
    open_days = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist(), reverse=True)
    if not open_days:
        return today.strftime("%Y%m%d")
    if open_days[0] == today.strftime("%Y%m%d"):
        probe = pro.daily(trade_date=open_days[0], fields="ts_code")
        if probe is None or probe.empty:
            return open_days[1] if len(open_days) > 1 else open_days[0]
    return open_days[0]


# ============================================================
# Phase 1: constituents
# ============================================================

def fetch_constituents(pro, force_rebuild=False, cache_days=30):
    if not force_rebuild and os.path.exists(CONSTITUENTS_CACHE):
        age_days = (datetime.now().timestamp() - os.path.getmtime(CONSTITUENTS_CACHE)) / 86400
        if age_days < cache_days:
            with open(CONSTITUENTS_CACHE, encoding="utf-8") as f:
                data = json.load(f)
            _log(f"读取成分股缓存（{age_days:.1f}天前，trade_date={data.get('trade_date')}），共{len(data['members'])}只")
            return data

    _log("拉取沪深300最新成分股...")
    today = datetime.now()
    w = pro.index_weight(index_code="000300.SH",
                          start_date=(today - timedelta(days=60)).strftime("%Y%m%d"),
                          end_date=today.strftime("%Y%m%d"))
    if w is None or w.empty:
        _log("index_weight 返回空，无法获取成分股")
        sys.exit(1)
    trade_date = w["trade_date"].max()
    latest = w[w["trade_date"] == trade_date].rename(columns={"con_code": "ts_code"})

    basic = pro.stock_basic(exchange="", list_status="L",
                             fields="ts_code,name,industry,list_date")
    merged = latest.merge(basic, on="ts_code", how="left")

    members = []
    for _, row in merged.iterrows():
        members.append({
            "ts_code": row["ts_code"],
            "name": row.get("name"),
            "industry": row.get("industry"),
            "list_date": str(row.get("list_date")) if row.get("list_date") else None,
        })

    data = {"trade_date": str(trade_date), "fetched_at": datetime.now().isoformat(), "members": members}
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CONSTITUENTS_CACHE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _log(f"沪深300成分股缓存完成，共{len(members)}只，trade_date={trade_date}")
    return data


# ============================================================
# Phase 2: screen (Stage 1 量化漏斗)
# ============================================================

def _exclude_by_industry(members, keywords):
    kept, dropped = [], []
    for m in members:
        industry = m.get("industry") or ""
        if any(kw in industry for kw in keywords):
            dropped.append(m)
        else:
            kept.append(m)
    _log(f"行业剔除（金融/地产）：{len(members)} -> {len(kept)}（剔除{len(dropped)}只）")
    return kept


def _exclude_by_listing_age(members, lookback_years):
    cutoff = (datetime.now() - timedelta(days=365 * lookback_years)).strftime("%Y%m%d")
    kept = [m for m in members if m.get("list_date") and m["list_date"] < cutoff]
    _log(f"上市年限剔除（需上市>{lookback_years}年）：{len(members)} -> {len(kept)}")
    return kept


def _filter_by_pe(pro, members, pe_max, trade_date):
    # daily_basic 的 ts_code 参数不支持逗号分隔批量查询，改为单日全市场拉取后本地过滤
    # （与 income_vip 的批量思路一致：一次调用换全市场，而不是逐只/分批查询）
    pe_map = {}
    try:
        df = pro.daily_basic(trade_date=trade_date, fields="ts_code,pe_ttm,total_mv")
    except Exception as e:
        raise RuntimeError(f"daily_basic({trade_date})取数失败；不能解释为无候选") from e
    if df is None or df.empty:
        raise RuntimeError(f"daily_basic({trade_date})为空；不能解释为无候选")
    if df is not None and not df.empty:
        for _, row in df.iterrows():
            pe_map[row["ts_code"]] = {"pe_ttm": _safe_float(row.get("pe_ttm")),
                                       "total_mv": _safe_float(row.get("total_mv"))}

    kept = []
    # A returned null PE may mean a loss-making company; an absent row is a
    # coverage gap. Do not confuse these two cases.
    missing = [m["ts_code"] for m in members if m["ts_code"] not in pe_map]
    if missing:
        raise RuntimeError(f"PE数据不完整（{len(missing)}只）：{','.join(missing[:10])}；筛选未完成")
    for m in members:
        info = pe_map.get(m["ts_code"])
        pe = info.get("pe_ttm") if info else None
        if pe is not None and 0 < pe < pe_max:
            m = dict(m)
            m["pe_ttm"] = pe
            m["total_mv"] = info.get("total_mv")
            kept.append(m)
    _log(f"PE过滤（0<PE<{pe_max}）：{len(members)} -> {len(kept)}")
    return kept


def _fetch_annual_netprofit(pro, members, lookback_years):
    """
    用 income_vip 按财年批量拉全市场，一个period一次调用（远比逐只income()快）。
    只保留 end_date 以1231结尾的年度累计数，同一 end_date 有多条修正版本时取最后一条。
    """
    this_year = datetime.now().year
    # 最近一个完整财年：若当前月份<5月，去年年报可能还没全部披露完，仍以此年年报季结束(4月30日)为准，
    # 但为了跟"2025年报5月发布"的用户描述对齐，统一取 this_year - 1 作为最新完整财年
    last_fy = this_year - 1
    years = list(range(last_fy - lookback_years + 1, last_fy + 1))
    _log(f"拉取{len(years)}个财年的全市场年报净利润/营收（income_vip，年度批量调用）：{years[0]}-{years[-1]}")

    code_set = {m["ts_code"] for m in members}
    series = {code: {} for code in code_set}
    if not code_set:
        return series, years

    for y in years:
        period = f"{y}1231"
        try:
            df = pro.income_vip(period=period,
                                 fields="ts_code,end_date,n_income_attr_p,revenue")
        except Exception as e:
            raise RuntimeError(f"income_vip({period})取数失败；质量筛选未完成") from e
        if df is None or df.empty:
            raise RuntimeError(f"income_vip({period})为空；质量筛选未完成")
        df = df[df["end_date"] == period]
        df = df[df["ts_code"].isin(code_set)]
        # 同一 end_date 可能有多条（不同披露批次），保留最后一条（一般是最新修正版）
        df = df.drop_duplicates(subset="ts_code", keep="last")
        for _, row in df.iterrows():
            series[row["ts_code"]][y] = {
                "net_profit": _safe_float(row.get("n_income_attr_p")),
                "revenue": _safe_float(row.get("revenue")),
            }
    return series, years


def _compute_growth_metrics(series, years, cfg):
    """对每只股票计算CAGR/下滑年数/最近年排名，缺失任一年份直接判定不合格。"""
    results = {}
    for code, yearly in series.items():
        if not all(y in yearly and yearly[y]["net_profit"] is not None and yearly[y]["revenue"] is not None
                   for y in years):
            results[code] = {"eligible": False, "reason": "十年年报数据不完整"}
            continue

        np_series = [yearly[y]["net_profit"] for y in years]
        rev_series = [yearly[y]["revenue"] for y in years]

        if np_series[0] <= 0 or np_series[-1] <= 0:
            results[code] = {"eligible": False, "reason": "期初或期末净利润为负，CAGR无意义"}
            continue

        n_intervals = len(years) - 1
        cagr_np = (np_series[-1] / np_series[0]) ** (1 / n_intervals) - 1
        cagr_rev = (rev_series[-1] / rev_series[0]) ** (1 / n_intervals) - 1 if rev_series[0] > 0 else None

        down_years = sum(1 for i in range(1, len(np_series)) if np_series[i] < np_series[i - 1])
        rank_last = sorted(np_series, reverse=True).index(np_series[-1]) + 1  # 1=最高

        eligible = (
            cagr_np > cfg["benchmark_rate"]
            and down_years <= cfg["max_down_years"]
            and rank_last <= cfg["top_n_rank_required"]
            and (not cfg.get("require_positive_revenue_cagr") or (cagr_rev is not None and cagr_rev > 0))
        )
        results[code] = {
            "eligible": eligible,
            "cagr_np": round(cagr_np, 4),
            "cagr_revenue": round(cagr_rev, 4) if cagr_rev is not None else None,
            "down_years": down_years,
            "rank_last_year": rank_last,
            "np_series": {str(y): np_series[i] for i, y in enumerate(years)},
        }
    return results


def run_screen(force_rebuild=False):
    cfg = load_config()
    pro = get_pro()

    data = fetch_constituents(pro, force_rebuild=force_rebuild, cache_days=cfg["constituents_cache_days"])
    members = data["members"]

    members = _exclude_by_industry(members, cfg["exclude_industry_keywords"])
    members = _exclude_by_listing_age(members, cfg["lookback_years"])

    trade_date = latest_trade_date(pro)
    members = _filter_by_pe(pro, members, cfg["pe_ttm_max"], trade_date)

    series, years = _fetch_annual_netprofit(pro, members, cfg["lookback_years"])
    metrics = _compute_growth_metrics(series, years, cfg)

    candidates = []
    for m in members:
        met = metrics.get(m["ts_code"], {"eligible": False, "reason": "未取得财务数据"})
        if met.get("eligible"):
            candidates.append({**m, **met})

    _log(f"Stage 1 通过：{len(members)} -> {len(candidates)} 只候选")

    result = {
        "trade_date": trade_date,
        "years": years,
        "universe_after_hard_filters": len(members),
        "candidates": sorted(candidates, key=lambda c: c["cagr_np"], reverse=True),
    }
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(FUNDAMENTALS_CACHE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


# ============================================================
# Phase 3: quality (Stage 2 量化代理指标)
# ============================================================

def _fetch_quality_indicators(pro, ts_code, lookback_years):
    end_year = datetime.now().year - 1
    start_date = f"{end_year - lookback_years + 1}0101"
    end_date = f"{end_year}1231"
    try:
        df = pro.fina_indicator(ts_code=ts_code, start_date=start_date, end_date=end_date,
                                 fields="ts_code,end_date,inv_turn,ar_turn,grossprofit_margin,"
                                        "netprofit_margin,debt_to_assets,ocf_to_profit")
    except Exception as e:
        return {"error": str(e)}
    if df is None or df.empty:
        return {"error": "无数据"}
    df = df[df["end_date"].str.endswith("1231")]
    df = df.drop_duplicates(subset="end_date", keep="last").sort_values("end_date")
    if df.empty:
        return {"error": "无年度数据"}

    ocf_vals = [v for v in df["ocf_to_profit"].tolist() if _safe_float(v) is not None]
    avg_ocf = round(sum(ocf_vals) / len(ocf_vals), 1) if ocf_vals else None

    latest = df.iloc[-1]
    earliest = df.iloc[0]
    return {
        "avg_ocf_to_profit_5y": avg_ocf,
        "debt_to_assets_latest": _safe_float(latest.get("debt_to_assets")),
        "grossprofit_margin_latest": _safe_float(latest.get("grossprofit_margin")),
        "grossprofit_margin_5y_ago": _safe_float(earliest.get("grossprofit_margin")),
        "netprofit_margin_latest": _safe_float(latest.get("netprofit_margin")),
        "netprofit_margin_5y_ago": _safe_float(earliest.get("netprofit_margin")),
        "inv_turn_latest": _safe_float(latest.get("inv_turn")),
        "inv_turn_5y_ago": _safe_float(earliest.get("inv_turn")),
        "ar_turn_latest": _safe_float(latest.get("ar_turn")),
        "ar_turn_5y_ago": _safe_float(earliest.get("ar_turn")),
    }


def _fetch_mainbz_concentration(pro, ts_code):
    """主营业务集中度，best-effort——部分公司取不到分产品数据，优雅降级。"""
    try:
        df = pro.fina_mainbz(ts_code=ts_code, type="P")
    except Exception:
        return None
    if df is None or df.empty:
        return None
    latest_period = df["end_date"].max()
    df = df[df["end_date"] == latest_period]
    if df.empty or "bz_sales" not in df.columns:
        return None
    sales = [_safe_float(v) for v in df["bz_sales"].tolist() if _safe_float(v) is not None]
    total = sum(sales) if sales else 0
    if not total:
        return None
    top_pct = round(max(sales) / total * 100, 1)
    top_pct = min(top_pct, 100.0)  # 分产品口径可能有内部抵消项，导致求和口径误差，封顶避免误读
    return {"period": str(latest_period), "top_segment_pct": top_pct}


def _annotate_quality(q, cfg):
    notes = []
    if q.get("avg_ocf_to_profit_5y") is not None and q["avg_ocf_to_profit_5y"] < cfg["ocf_to_profit_flag"]:
        notes.append(f"经营现金流/净利润5年均值{q['avg_ocf_to_profit_5y']}%，低于{cfg['ocf_to_profit_flag']}%，盈利质量需关注")
    if q.get("debt_to_assets_latest") is not None and q["debt_to_assets_latest"] > cfg["debt_to_assets_flag"]:
        notes.append(f"资产负债率{q['debt_to_assets_latest']}%，偏高（粗筛线{cfg['debt_to_assets_flag']}%，需结合行业判断）")
    gm_now, gm_then = q.get("grossprofit_margin_latest"), q.get("grossprofit_margin_5y_ago")
    if gm_now is not None and gm_then is not None and gm_now < gm_then - 2:
        notes.append(f"毛利率5年走弱：{gm_then}% -> {gm_now}%，主业盈利能力转弱")
    it_now, it_then = q.get("inv_turn_latest"), q.get("inv_turn_5y_ago")
    if it_now is not None and it_then is not None and it_now < it_then * 0.85:
        notes.append(f"存货周转率5年转慢：{it_then} -> {it_now}，存货压力上升")
    at_now, at_then = q.get("ar_turn_latest"), q.get("ar_turn_5y_ago")
    if at_now is not None and at_then is not None and at_now < at_then * 0.85:
        notes.append(f"应收账款周转率5年转慢：{at_then} -> {at_now}，回款压力上升")
    return notes


def run_quality(cfg=None, candidates=None):
    cfg = cfg or load_config()
    pro = get_pro()

    if candidates is None:
        if not os.path.exists(FUNDAMENTALS_CACHE):
            _log("未找到 Stage 1 候选缓存，先跑 --phase screen")
            sys.exit(1)
        with open(FUNDAMENTALS_CACHE, encoding="utf-8") as f:
            candidates = json.load(f)["candidates"]

    # 行业内排名（仅沪深300成分股范围内，非全市场）
    by_industry = {}
    for c in candidates:
        by_industry.setdefault(c.get("industry") or "未知", []).append(c)
    for _, group in by_industry.items():
        group.sort(key=lambda c: c.get("total_mv") or 0, reverse=True)

    out = []
    for c in candidates:
        q = _fetch_quality_indicators(pro, c["ts_code"], cfg["quality_lookback_years"])
        mainbz = _fetch_mainbz_concentration(pro, c["ts_code"])
        notes = _annotate_quality(q, cfg) if "error" not in q else [f"质量指标获取失败：{q['error']}"]

        industry_group = by_industry.get(c.get("industry") or "未知", [])
        rank_in_industry = next((i + 1 for i, x in enumerate(industry_group) if x["ts_code"] == c["ts_code"]), None)

        out.append({
            **c,
            "quality": q,
            "mainbz_concentration": mainbz if mainbz else "数据不可得，需人工核实",
            "industry_rank_within_csi300": f"{rank_in_industry}/{len(industry_group)}（仅沪深300内，非全市场）",
            "order_backlog_note": "本工具不覆盖，需人工核实（合同负债增速可作参考代理，仅对制造/建筑/军工类公司有意义）",
            "quality_notes": notes if notes else ["未触发预警阈值"],
        })
    return out


# ============================================================
# Output formatting
# ============================================================

def print_screen_table(result):
    print(f"\n沪深300 十年质量-价值筛选 — Stage 1（trade_date={result['trade_date']}）")
    print(f"硬过滤后基数：{result['universe_after_hard_filters']} 只 -> 候选：{len(result['candidates'])} 只\n")
    if not result["candidates"]:
        print("无候选通过Stage 1门槛，可考虑放宽 config/thresholds.yaml 里的阈值")
        return
    print(f"{'代码':<10}{'名称':<10}{'行业':<8}{'PE':>6}{'CAGR净利润':>10}{'CAGR营收':>10}{'下滑年数':>8}{'2025排名':>8}")
    for c in result["candidates"]:
        print(f"{c['ts_code']:<10}{c['name']:<10}{c['industry']:<8}{c['pe_ttm']:>6.1f}"
              f"{c['cagr_np']*100:>9.1f}%{(c['cagr_revenue'] or 0)*100:>9.1f}%{c['down_years']:>8}{c['rank_last_year']:>8}")


def print_quality_table(rows):
    print("\nStage 2 — 量化代理指标决策表\n")
    for r in rows:
        print(f"【{r['ts_code']} {r['name']}】行业内排名: {r['industry_rank_within_csi300']}")
        mainbz = r["mainbz_concentration"]
        if isinstance(mainbz, dict):
            print(f"  主营集中度: 最大单一产品/地区收入占比 {mainbz['top_segment_pct']}%（{mainbz['period']}）")
        else:
            print(f"  主营集中度: {mainbz}")
        print(f"  订单情况: {r['order_backlog_note']}")
        for note in r["quality_notes"]:
            print(f"  - {note}")
        print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True, choices=["constituents", "screen", "quality"])
    ap.add_argument("--force-rebuild", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.phase == "constituents":
        pro = get_pro()
        cfg = load_config()
        data = fetch_constituents(pro, force_rebuild=args.force_rebuild, cache_days=cfg["constituents_cache_days"])
        if args.json:
            print(json.dumps(data, ensure_ascii=False))
        else:
            print(f"沪深300成分股：{len(data['members'])}只，trade_date={data['trade_date']}")

    elif args.phase == "screen":
        result = run_screen(force_rebuild=args.force_rebuild)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print_screen_table(result)

    elif args.phase == "quality":
        cfg = load_config()
        rows = run_quality(cfg=cfg)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False))
        else:
            print_quality_table(rows)


if __name__ == "__main__":
    main()
