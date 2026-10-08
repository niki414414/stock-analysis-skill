#!/usr/bin/env python3
"""每日市场/板块/个股快照 —— 持续追加，不重算历史。

设计目标：解决"每次分析都要临时拉一段历史窗口现算"的低效问题。
每个交易日收盘后跑一次（由schedule定时任务触发），往三张CSV各append一行：
  market_breadth.csv  大盘广度（复用 stock_data_fetcher.fetch_market_breadth）
  sector_daily.csv    固定9个板块代理ETF当日涨跌
  stock_daily.csv     watchlist.csv里所有个股/ETF当日涨跌

watchlist.csv 是动态清单，不锁定持仓数量——每次/stock-analysis分析一只新股票时
会自动append进去（见 SKILL.md STEP 2 的追加逻辑），也可以手动编辑这个CSV直接加。

用法：
  python3 daily_snapshot.py                  # 快照最近一个已收盘交易日
  python3 daily_snapshot.py --date 20260714  # 补录指定交易日（回补用）
"""
import argparse
import csv
import os
import sys
from datetime import datetime, timedelta

from pathlib import Path
_CODE_ROOT = Path(__file__).resolve().parents[3]
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))
from skills.shared.paths import workspace_root, config_file
from skills.shared.datasource import load_env as _shared_load_env, get_pro as _shared_get_pro

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "references"))

TZ_CODEX_HOME = str(workspace_root())
OUT_DIR = os.path.join(TZ_CODEX_HOME, "技能数据", "market_daily_snapshot")
WATCHLIST_PATH = os.path.join(OUT_DIR, "watchlist.csv")

SECTOR_ETF_MAP = {
    "半导体": "512480.SH",
    "创新药": "159992.SZ",
    "商业航天": "563790.SH",
    "证券": "512880.SH",
    "银行": "512800.SH",
    "机器人": "562500.SH",
    "有色金属": "512400.SH",
    "化工": "159870.SZ",
    "能源": "159930.SZ",
}


def _load_env_file():
    _shared_load_env(config_file())


def _get_pro():
    return _shared_get_pro()


def _resolve_trade_date(pro, date_arg):
    if date_arg:
        return date_arg
    today = datetime.now()
    cal = pro.trade_cal(exchange="SSE",
                         start_date=(today - timedelta(days=10)).strftime("%Y%m%d"),
                         end_date=today.strftime("%Y%m%d"))
    open_days = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist(), reverse=True)
    if not open_days:
        return today.strftime("%Y%m%d")
    # 探测当天数据是否已发布，不猜测收盘时间("hour<16"不可靠：沙盒系统时钟不等于
    # 北京时间，且A股实际15:00收盘不是16:00)
    if open_days[0] == today.strftime("%Y%m%d"):
        probe = pro.daily(trade_date=open_days[0], fields="ts_code")
        if probe is None or probe.empty:
            return open_days[1] if len(open_days) > 1 else open_days[0]
    return open_days[0]


def _append_row(path, fieldnames, row):
    # 去重由调用方在写入前用 _already_logged() 判断，这里只负责追加写入
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def _already_logged(path, trade_date, key_col, key_val):
    if not os.path.exists(path):
        return False
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r.get("trade_date") == trade_date and r.get(key_col) == key_val:
                return True
    return False


def snapshot_market(pro, trade_date):
    import importlib.util
    fetcher_path = os.path.join(os.path.dirname(__file__), "..", "references", "stock_data_fetcher.py")
    spec = importlib.util.spec_from_file_location("fetcher", fetcher_path)
    fetcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetcher)

    breadth = fetcher.fetch_market_breadth(as_of_date=trade_date)
    if breadth.get("source") != "tushare":
        print(f"[市场广度] {trade_date} 获取失败: {breadth}")
        return

    path = os.path.join(OUT_DIR, "market_breadth.csv")
    fields = ["trade_date", "index_pct_chg", "advancers", "decliners", "advance_pct",
              "limit_up", "limit_down", "regime_label"]
    if _already_logged(path, trade_date, "trade_date", trade_date):
        print(f"[市场广度] {trade_date} 已存在，跳过")
        return
    row = {k: breadth.get(k) for k in fields}
    _append_row(path, fields, row)
    print(f"[市场广度] {trade_date} 已记录: {breadth.get('regime_label')}")


def snapshot_sectors(pro, trade_date):
    path = os.path.join(OUT_DIR, "sector_daily.csv")
    fields = ["trade_date", "sector", "etf_code", "pct_chg", "close"]
    for sector, code in SECTOR_ETF_MAP.items():
        if _already_logged(path, trade_date, "sector", sector):
            continue
        df = pro.fund_daily(ts_code=code, trade_date=trade_date)
        if df is None or df.empty:
            print(f"[板块] {sector}({code}) {trade_date} 无数据")
            continue
        row = {
            "trade_date": trade_date, "sector": sector, "etf_code": code,
            "pct_chg": float(df.iloc[0]["pct_chg"]), "close": float(df.iloc[0]["close"]),
        }
        _append_row(path, fields, row)
    print(f"[板块] {trade_date} 完成，共{len(SECTOR_ETF_MAP)}个板块")


def _load_watchlist():
    if not os.path.exists(WATCHLIST_PATH):
        return []
    with open(WATCHLIST_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def snapshot_stocks(pro, trade_date):
    watchlist = _load_watchlist()
    if not watchlist:
        print("[个股] watchlist为空，跳过")
        return
    path = os.path.join(OUT_DIR, "stock_daily.csv")
    fields = ["trade_date", "code", "name", "pct_chg", "close"]
    for item in watchlist:
        code, name, typ = item["code"], item["name"], item.get("type", "stock")
        if _already_logged(path, trade_date, "code", code):
            continue
        try:
            if typ == "fund":
                df = pro.fund_daily(ts_code=code, trade_date=trade_date)
            else:
                df = pro.daily(ts_code=code, trade_date=trade_date)
        except Exception as e:
            print(f"[个股] {name}({code}) {trade_date} 获取失败: {e}")
            continue
        if df is None or df.empty:
            print(f"[个股] {name}({code}) {trade_date} 无数据（可能停牌）")
            continue
        row = {
            "trade_date": trade_date, "code": code, "name": name,
            "pct_chg": float(df.iloc[0]["pct_chg"]), "close": float(df.iloc[0]["close"]),
        }
        _append_row(path, fields, row)
    print(f"[个股] {trade_date} 完成，共{len(watchlist)}只（含ETF）")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYYMMDD，指定交易日回补；默认取最近已收盘交易日")
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    pro = _get_pro()
    trade_date = _resolve_trade_date(pro, args.date)
    print(f"=== 快照交易日: {trade_date} ===")
    snapshot_market(pro, trade_date)
    snapshot_sectors(pro, trade_date)
    snapshot_stocks(pro, trade_date)
    print("=== 完成 ===")


if __name__ == "__main__":
    main()
