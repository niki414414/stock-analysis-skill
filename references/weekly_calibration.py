#!/usr/bin/env python3
"""
weekly_calibration.py — 赛道周校准自动扫描 v2
核心逻辑：信号股 20日收益 vs 创业板ETF 20日收益 = 超额收益
  超额 ≥ +8%          → 主升期信号
  超额 +2% ~ +8%      → 整理期
  超额 -5% ~ +2%      → 整理/调整边界
  超额 ≤ -5%          → 调整期信号
依据：框架目标是判断stage标签是否过期，本质是资本配置问题，
     超额收益同时包含绝对趋势和相对资金流向，奥卡姆剃刀最简解。
用法：python3 weekly_calibration.py
"""

import os, sys, warnings, datetime
warnings.filterwarnings("ignore")

YAML_PATH  = os.path.expanduser("~/.claude/skills/stock-analysis/config/market_status.yaml")
ENV_PATH   = os.path.expanduser("~/.claude/skills/.env")
GEM_CODE   = "159915"   # 创业板ETF — 统一基准
GEM_NAME   = "创业板ETF"

# ── 环境变量 ──────────────────────────────────────────────────
def _load_env():
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k and v and k not in os.environ:
                os.environ[k] = v
_load_env()

# ── 价格数据 ──────────────────────────────────────────────────
def _ts_suffix(code):
    return code + (".SH" if code.startswith(("5","6")) else ".SZ")

def fetch_closes(code, days=30):
    end   = datetime.date.today()
    start = end - datetime.timedelta(days=days + 20)

    token = os.environ.get("TUSHARE_TOKEN")
    if token:
        try:
            import tushare as ts
            df = ts.pro_api(token).daily(
                ts_code=_ts_suffix(code),
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
            if df is not None and len(df) >= 20:
                return df.sort_values("trade_date")["close"].tolist()[-days:]
        except Exception:
            pass

    try:
        import efinance as ef
        df = ef.stock.get_quote_history(
            code, beg=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d")
        )
        if df is not None and len(df) >= 20:
            return df.sort_values("日期")["收盘"].tolist()[-days:]
    except Exception:
        pass

    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="qfq",
        )
        if df is not None and len(df) >= 20:
            return df.sort_values("日期")["收盘"].tolist()[-days:]
    except Exception:
        pass

    return None

def pct20(closes):
    """20日收益率 %"""
    if not closes or len(closes) < 20:
        return None
    return (closes[-1] / closes[-20] - 1) * 100

# ── stage建议 ────────────────────────────────────────────────
def stage_hint(alpha):
    if alpha is None:
        return "❓", "数据不足"
    if alpha >= 8:
        return "✅", f"超额{alpha:+.1f}%  → 主升期信号"
    if alpha >= 2:
        return "🟡", f"超额{alpha:+.1f}%  → 整理期"
    if alpha >= -5:
        return "⚠️", f"超额{alpha:+.1f}%  → 整理/调整边界"
    return "🔴", f"超额{alpha:+.1f}%  → 调整期信号"

def needs_review(alpha, cur_stage):
    """判断阶段标签是否与信号矛盾"""
    if alpha is None:
        return False
    if cur_stage in ("主升期",) and alpha < 2:
        return True
    if cur_stage in ("整理期",) and (alpha >= 8 or alpha <= -5):
        return True
    if cur_stage in ("调整期", "调整") and alpha >= 8:
        return True
    if cur_stage in ("启动期",) and alpha <= -5:
        return True
    return False

# ── 主程序 ───────────────────────────────────────────────────
def main():
    try:
        import yaml
    except ImportError:
        print("❌ 需要 pyyaml：pip3 install pyyaml"); sys.exit(1)

    if not os.path.exists(YAML_PATH):
        print(f"❌ 找不到配置：{YAML_PATH}"); sys.exit(1)

    with open(YAML_PATH) as f:
        config = yaml.safe_load(f)

    sectors = config.get("weekly_calibration", {}).get("sectors", {})
    today   = datetime.date.today().strftime("%Y-%m-%d")

    # 先拉创业板ETF作为统一基准
    print(f"  拉取基准 {GEM_NAME}({GEM_CODE})…", end="\r", flush=True)
    gem_closes = fetch_closes(GEM_CODE)
    gem_pct    = pct20(gem_closes)
    if gem_pct is None:
        print(f"❌ 创业板ETF数据获取失败，无法计算超额收益"); sys.exit(1)

    results = []
    for key, sec in sectors.items():
        label     = sec.get("label", key)
        sig_code  = sec.get("signal_stock", "")
        sig_name  = sec.get("signal_stock_name", sig_code)
        cur_stage = sec.get("current_stage", "—")

        print(f"  拉取 {label[:14]}…", end="\r", flush=True)

        sig_closes = fetch_closes(sig_code) if sig_code else None
        sig_pct    = pct20(sig_closes)
        alpha      = round(sig_pct - gem_pct, 1) if (sig_pct is not None) else None

        icon, desc = stage_hint(alpha)
        review     = needs_review(alpha, cur_stage)

        results.append(dict(
            label=label, sig_name=sig_name,
            sig_pct=sig_pct, gem_pct=gem_pct, alpha=alpha,
            icon=icon, desc=desc,
            cur_stage=cur_stage, review=review,
        ))

    # ── 输出报告 ────────────────────────────────────────────
    W = 74
    print(" " * W, end="\r")
    print(f"\n{'═'*W}")
    print(f"  📊 赛道周校准报告  {today}")
    print(f"  基准：{GEM_NAME}({GEM_CODE})  20日收益 {gem_pct:+.1f}%")
    print(f"{'═'*W}")
    print(f"  {'赛道':<18} {'信号股':<8} {'信号股20d':>9} {'超额':>7}  {'结论':<28} {'当前stage'}")
    print(f"{'─'*W}")

    review_list = []
    for r in results:
        sig_str   = f"{r['sig_pct']:+.1f}%" if r['sig_pct'] is not None else "❓"
        alpha_str = f"{r['alpha']:+.1f}%"   if r['alpha']   is not None else "❓"
        row = (
            f"  {r['label'][:17]:<18}"
            f" {r['sig_name'][:7]:<8}"
            f" {sig_str:>9}"
            f" {alpha_str:>7} "
            f" {r['icon']} {r['desc']:<26}"
            f" {r['cur_stage']}"
        )
        print(row)
        if r['review']:
            review_list.append(r)

    print(f"{'═'*W}")

    if review_list:
        print(f"\n⚠️  Stage标签与超额信号矛盾，建议复核：\n")
        for r in review_list:
            direction = ""
            if r['alpha'] is not None:
                if r['cur_stage'] == "主升期" and r['alpha'] < 2:
                    direction = f"→ 考虑降级为整理期/调整期"
                elif r['cur_stage'] == "整理期" and r['alpha'] >= 8:
                    direction = f"→ 考虑升级为主升期"
                elif r['cur_stage'] == "整理期" and r['alpha'] <= -5:
                    direction = f"→ 考虑降级为调整期"
                elif r['cur_stage'] == "调整期" and r['alpha'] >= 8:
                    direction = f"→ 考虑升级为整理期"
                elif r['cur_stage'] == "启动期" and r['alpha'] <= -5:
                    direction = f"→ 考虑降级为调整期"
            print(f"   • {r['label']}（当前：{r['cur_stage']}，超额{r['alpha']:+.1f}%）{direction}")
        print(f"\n   → 打开 market_status.yaml 修改对应赛道的 stage 字段")
    else:
        print(f"\n  ✅ 所有赛道 stage 标签与超额信号一致，无需调整\n")

    print(f"\n  阈值说明：超额≥+8%=主升  +2~+8%=整理  -5~+2%=边界  ≤-5%=调整")
    print(f"  超额 = 信号股20日收益 − {GEM_NAME}20日收益\n")

if __name__ == "__main__":
    main()
