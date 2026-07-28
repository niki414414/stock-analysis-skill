#!/usr/bin/env python3
"""
Stock Data Fetcher + Technical Indicator Calculator
Outputs structured JSON for Claude Code analysis.
No AI/LLM calls -- pure data + math.

Data source priority (graceful degradation):
  A-share: Tushare Pro (if TUSHARE_TOKEN set) > efinance > akshare > yfinance
  HK:      efinance > akshare > yfinance
  US:      yfinance (primary)

News search priority (via --news flag):
  Tavily (if TAVILY_API_KEY set) > SerpAPI (if SERPAPI_KEY set) > skip (use WebSearch in Claude)

Usage:
    python3 stock_data_fetcher.py --stocks "600519,TSLA,HK00700" [--days 120] [--news]

Environment variables (optional, for enhanced data):
    TUSHARE_TOKEN    - Tushare Pro token (free signup at tushare.pro)
    TAVILY_API_KEY   - Tavily API key (1000 free calls/month)
    SERPAPI_KEY       - SerpAPI key (100 free calls/month)
"""

import os
import sys
import json
import argparse
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")


def _load_env_file():
    """Load the workspace .env into os.environ (only sets missing vars)."""
    workspace_root = os.path.abspath(os.path.expanduser(
        os.environ.get("TZ_CODEX_HOME", "~/Desktop/tz-codex")
    ))
    env_path = os.path.join(workspace_root, "repo", ".env")
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

# Data source availability detection
_AVAILABLE_SOURCES = {}

def _check_source(name):
    """Lazy-check if a data source library is importable."""
    if name not in _AVAILABLE_SOURCES:
        try:
            __import__(name)
            _AVAILABLE_SOURCES[name] = True
        except ImportError:
            _AVAILABLE_SOURCES[name] = False
    return _AVAILABLE_SOURCES[name]

def _log(msg):
    """Log to stderr so it doesn't pollute JSON stdout."""
    print(f"[INFO] {msg}", file=sys.stderr)


# ============================================================
# SECTION 1: Stock Code Parser
# ============================================================

def classify_stock(code: str) -> tuple:
    """
    Returns (market, normalized_code, display_code)
    market: 'cn_a', 'cn_hk', 'us'
    """
    code = code.strip()
    upper = code.upper()

    # 港股: HK00700 -> ('cn_hk', '00700', 'HK00700')
    if upper.startswith("HK") and upper[2:].isdigit():
        return ("cn_hk", upper[2:], upper)

    # A股: 600519 -> ('cn_a', '600519', '600519')
    if upper.isdigit() and len(upper) == 6:
        return ("cn_a", upper, upper)

    # 美股: TSLA -> ('us', 'TSLA', 'TSLA')
    if upper.isalpha() and 1 <= len(upper) <= 5:
        return ("us", upper, upper)

    # 带后缀的A股: 600519.SH -> strip
    if "." in upper:
        base, suffix = upper.rsplit(".", 1)
        if suffix in ("SH", "SZ", "SS") and base.isdigit():
            return ("cn_a", base, base)

    # 带前缀的A股: SH600519 -> strip
    if upper[:2] in ("SH", "SZ") and upper[2:].isdigit():
        return ("cn_a", upper[2:], upper[2:])

    return ("unknown", code, code)


def to_yfinance_code(code: str, market: str) -> str:
    """Convert to Yahoo Finance ticker format."""
    if market == "cn_hk":
        num = code.lstrip("0") or "0"
        return f"{num.zfill(4)}.HK"
    if market == "us":
        return code
    # A股
    if code.startswith(("600", "601", "603", "605", "688")):
        return f"{code}.SS"
    if code.startswith(("51", "52", "56", "58")):
        return f"{code}.SS"
    return f"{code}.SZ"


# ============================================================
# SECTION 2: Data Fetchers (with graceful degradation)
# ============================================================

def _df_to_ohlcv(df, days):
    """Convert a normalized DataFrame to OHLCV list."""
    import pandas as pd
    for c in ["open", "close", "high", "low", "volume", "amount", "pct_chg"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").tail(days).reset_index(drop=True)
    ohlcv = []
    for _, row in df.iterrows():
        ohlcv.append({
            "date": str(row.get("date", "")),
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": _safe_float(row.get("close")),
            "volume": _safe_float(row.get("volume")),
            "amount": _safe_float(row.get("amount")),
            "pct_chg": _safe_float(row.get("pct_chg")),
        })
    return ohlcv


# --- Tushare Pro (Priority 0, needs TUSHARE_TOKEN) ---

def _fetch_tushare_a(code: str, days: int):
    """Fetch A-share via Tushare Pro. Returns (ohlcv, source) or raises."""
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise EnvironmentError("TUSHARE_TOKEN not set")
    import tushare as ts
    pro = ts.pro_api(token)
    ts_code = f"{code}.SH" if code.startswith(("600", "601", "603", "605", "688")) else f"{code}.SZ"
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        raise ValueError(f"Tushare returned no data for {code}")
    col_map = {
        "trade_date": "date", "open": "open", "close": "close",
        "high": "high", "low": "low", "vol": "volume",
        "amount": "amount", "pct_chg": "pct_chg",
    }
    df = df.rename(columns=col_map)
    df["date"] = df["date"].apply(lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(str(x)) == 8 else x)
    _log(f"[{code}] Using Tushare Pro (premium)")
    return _df_to_ohlcv(df, days), "tushare"


# --- efinance (Priority 1, free) ---

def _fetch_efinance_a(code: str, days: int):
    """Fetch A-share via efinance (EastMoney). Returns (ohlcv, source) or raises."""
    import efinance as ef
    df = ef.stock.get_quote_history(code)
    if df is None or df.empty:
        raise ValueError(f"efinance returned no data for {code}")
    col_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "涨跌幅": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[{code}] Using efinance (free)")
    return _df_to_ohlcv(df, days), "efinance"


def _fetch_efinance_hk(code: str, days: int):
    """Fetch HK stock via efinance."""
    import efinance as ef
    df = ef.stock.get_quote_history(code, stock_type="hk")
    if df is None or df.empty:
        raise ValueError(f"efinance returned no data for HK{code}")
    col_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "涨跌幅": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[HK{code}] Using efinance (free)")
    return _df_to_ohlcv(df, days), "efinance"


# --- akshare (Priority 2, free) ---

def _fetch_akshare_a(code: str, days: int):
    """Fetch A-share via akshare."""
    import akshare as ak
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=start_date, end_date=end_date, adjust="qfq")
    except Exception:
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=start_date, end_date=end_date, adjust="")
    if df is None or df.empty:
        raise ValueError(f"akshare returned no data for {code}")
    col_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "涨跌幅": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[{code}] Using akshare (free)")
    return _df_to_ohlcv(df, days), "akshare"


def _fetch_akshare_hk(code: str, days: int):
    """Fetch HK stock via akshare."""
    import akshare as ak
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    try:
        df = ak.stock_hk_hist(symbol=code, period="daily",
                              start_date=start_date, end_date=end_date, adjust="qfq")
    except Exception:
        df = ak.stock_hk_hist(symbol=code, period="daily",
                              start_date=start_date, end_date=end_date, adjust="")
    if df is None or df.empty:
        raise ValueError(f"akshare returned no data for HK{code}")
    col_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "涨跌幅": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[HK{code}] Using akshare (free)")
    return _df_to_ohlcv(df, days), "akshare"


# --- yfinance (Priority 3, free, fallback for all markets) ---

def _fetch_yfinance(code: str, market: str, days: int):
    """Fetch any stock via yfinance (universal fallback)."""
    import yfinance as yf
    yf_code = to_yfinance_code(code, market)
    ticker = yf.Ticker(yf_code)
    hist = ticker.history(period=f"{days}d")
    if hist is None or hist.empty:
        raise ValueError(f"yfinance returned no data for {yf_code}")
    ohlcv = []
    for idx, row in hist.iterrows():
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        ohlcv.append({
            "date": date_str,
            "open": _safe_float(row.get("Open")),
            "high": _safe_float(row.get("High")),
            "low": _safe_float(row.get("Low")),
            "close": _safe_float(row.get("Close")),
            "volume": _safe_float(row.get("Volume")),
            "amount": None, "pct_chg": None,
        })
    for i in range(1, len(ohlcv)):
        prev = ohlcv[i - 1]["close"]
        if prev and prev > 0:
            ohlcv[i]["pct_chg"] = round((ohlcv[i]["close"] - prev) / prev * 100, 2)
    _log(f"[{code}] Using yfinance (free, fallback)")
    return ohlcv, "yfinance"


# --- Realtime quote fetchers ---

def _fetch_realtime_a(code: str) -> dict:
    """Fetch A-share realtime quote with fallback.
    Priority: efinance (single-stock, fast) > akshare bulk > empty.
    Note: when market is closed, APIs may return last trading day's data — that is acceptable.
    """
    # Priority 1: efinance single-stock (lighter, less network load)
    if _check_source("efinance"):
        try:
            import efinance as ef
            qt = ef.stock.get_realtime_quotes([code])
            if qt is not None and not qt.empty:
                r = qt.iloc[0]
                result = {
                    "name": str(r.get("股票名称", code)),
                    "price": _safe_float(r.get("最新价")),
                    "change_pct": _safe_float(r.get("涨跌幅")),
                    "change_amount": _safe_float(r.get("涨跌额")),
                    "volume": _safe_float(r.get("成交量")),
                    "amount": _safe_float(r.get("成交额")),
                    "amplitude": _safe_float(r.get("振幅")),
                    "turnover_rate": _safe_float(r.get("换手率")),
                    "pe_ratio": _safe_float(r.get("市盈率(动态)") or r.get("市盈率")),
                    "pb_ratio": _safe_float(r.get("市净率")),
                    "total_mv": _safe_float(r.get("总市值")),
                    "circ_mv": _safe_float(r.get("流通市值")),
                    "high": _safe_float(r.get("最高")),
                    "low": _safe_float(r.get("最低")),
                    "open": _safe_float(r.get("今开")),
                    "pre_close": _safe_float(r.get("昨收")),
                    "volume_ratio": _safe_float(r.get("量比")),
                }
                if result.get("price"):
                    return result
        except Exception:
            pass
    # Priority 2: akshare bulk spot (downloads all A-shares — slower but richer fields)
    if _check_source("akshare"):
        try:
            import akshare as ak
            spot_df = ak.stock_zh_a_spot_em()
            row = spot_df[spot_df["代码"] == code]
            if not row.empty:
                r = row.iloc[0]
                return {
                    "name": str(r.get("名称", code)),
                    "price": _safe_float(r.get("最新价")),
                    "change_pct": _safe_float(r.get("涨跌幅")),
                    "change_amount": _safe_float(r.get("涨跌额")),
                    "volume": _safe_float(r.get("成交量")),
                    "amount": _safe_float(r.get("成交额")),
                    "amplitude": _safe_float(r.get("振幅")),
                    "turnover_rate": _safe_float(r.get("换手率")),
                    "pe_ratio": _safe_float(r.get("市盈率-动态")),
                    "pb_ratio": _safe_float(r.get("市净率")),
                    "total_mv": _safe_float(r.get("总市值")),
                    "circ_mv": _safe_float(r.get("流通市值")),
                    "high": _safe_float(r.get("最高")),
                    "low": _safe_float(r.get("最低")),
                    "open": _safe_float(r.get("今开")),
                    "pre_close": _safe_float(r.get("昨收")),
                    "volume_ratio": _safe_float(r.get("量比")),
                }
        except Exception:
            pass
    return {}


def _fetch_realtime_hk(code: str) -> dict:
    """Fetch HK realtime quote."""
    if _check_source("akshare"):
        try:
            import akshare as ak
            spot_df = ak.stock_hk_spot_em()
            matched = spot_df[spot_df["代码"] == code]
            if not matched.empty:
                r = matched.iloc[0]
                return {
                    "name": str(r.get("名称", f"HK{code}")),
                    "price": _safe_float(r.get("最新价")),
                    "change_pct": _safe_float(r.get("涨跌幅")),
                    "volume": _safe_float(r.get("成交量")),
                    "pe_ratio": _safe_float(r.get("市盈率")),
                    "pb_ratio": _safe_float(r.get("市净率")),
                    "total_mv": _safe_float(r.get("总市值")),
                }
        except Exception:
            pass
    return {}


def _fetch_realtime_us(code: str) -> dict:
    """Fetch US realtime quote via yfinance."""
    try:
        import yfinance as yf
        info = yf.Ticker(code).info
        return {
            "name": info.get("shortName") or info.get("longName") or code,
            "price": _safe_float(info.get("currentPrice") or info.get("regularMarketPrice")),
            "change_pct": _safe_float(info.get("regularMarketChangePercent")),
            "volume": _safe_float(info.get("regularMarketVolume")),
            "pe_ratio": _safe_float(info.get("trailingPE")),
            "pb_ratio": _safe_float(info.get("priceToBook")),
            "total_mv": _safe_float(info.get("marketCap")),
            "high": _safe_float(info.get("dayHigh")),
            "low": _safe_float(info.get("dayLow")),
            "open": _safe_float(info.get("regularMarketOpen")),
            "pre_close": _safe_float(info.get("regularMarketPreviousClose")),
            "week_52_high": _safe_float(info.get("fiftyTwoWeekHigh")),
            "week_52_low": _safe_float(info.get("fiftyTwoWeekLow")),
            "avg_volume": _safe_float(info.get("averageVolume")),
            "dividend_yield": _safe_float(info.get("dividendYield")),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
        }
    except Exception:
        return {}


# --- Priority router ---

def fetch_cn_a(code: str, days: int) -> dict:
    """Fetch A-share with priority: Tushare > efinance > akshare > yfinance."""
    ohlcv = None
    source = "unknown"
    errors = []

    # Priority 0: Tushare Pro (if token configured)
    if os.environ.get("TUSHARE_TOKEN") and _check_source("tushare"):
        try:
            ohlcv, source = _fetch_tushare_a(code, days)
        except Exception as e:
            errors.append(f"tushare: {e}")

    # Priority 1: efinance
    if ohlcv is None and _check_source("efinance"):
        try:
            ohlcv, source = _fetch_efinance_a(code, days)
        except Exception as e:
            errors.append(f"efinance: {e}")

    # Priority 2: akshare
    if ohlcv is None and _check_source("akshare"):
        try:
            ohlcv, source = _fetch_akshare_a(code, days)
        except Exception as e:
            errors.append(f"akshare: {e}")

    # Priority 3: yfinance (universal fallback)
    if ohlcv is None and _check_source("yfinance"):
        try:
            ohlcv, source = _fetch_yfinance(code, "cn_a", days)
        except Exception as e:
            errors.append(f"yfinance: {e}")

    if ohlcv is None:
        raise ValueError(f"All data sources failed for A-share {code}: {'; '.join(errors)}")

    realtime = _fetch_realtime_a(code)
    name = realtime.get("name", code)
    return {"ohlcv": ohlcv, "realtime": realtime, "name": name, "source": source}


def fetch_hk(code: str, days: int) -> dict:
    """Fetch HK stock with priority: efinance > akshare > yfinance."""
    ohlcv = None
    source = "unknown"
    errors = []

    if _check_source("efinance"):
        try:
            ohlcv, source = _fetch_efinance_hk(code, days)
        except Exception as e:
            errors.append(f"efinance: {e}")

    if ohlcv is None and _check_source("akshare"):
        try:
            ohlcv, source = _fetch_akshare_hk(code, days)
        except Exception as e:
            errors.append(f"akshare: {e}")

    if ohlcv is None and _check_source("yfinance"):
        try:
            ohlcv, source = _fetch_yfinance(code, "cn_hk", days)
        except Exception as e:
            errors.append(f"yfinance: {e}")

    if ohlcv is None:
        raise ValueError(f"All data sources failed for HK{code}: {'; '.join(errors)}")

    realtime = _fetch_realtime_hk(code)
    name = realtime.get("name", f"HK{code}")
    return {"ohlcv": ohlcv, "realtime": realtime, "name": name, "source": source}


def fetch_us(code: str, days: int) -> dict:
    """Fetch US stock via yfinance (primary source for US)."""
    ohlcv, source = _fetch_yfinance(code, "us", days)
    realtime = _fetch_realtime_us(code)
    if not realtime and ohlcv:
        last = ohlcv[-1]
        realtime = {"name": code, "price": last["close"], "change_pct": last.get("pct_chg")}
    name = realtime.get("name", code)
    return {"ohlcv": ohlcv, "realtime": realtime, "name": name, "source": source}


# ============================================================
# SECTION 2.5: News Search (optional, with graceful degradation)
# ============================================================

def search_news(stock_name: str, code: str, max_results: int = 5) -> list:
    """
    Search news with priority: Tavily > SerpAPI > empty (let Claude WebSearch).
    Returns list of {"title": ..., "content": ..., "url": ..., "date": ...}
    """
    # Priority 0: Tavily
    tavily_key = os.environ.get("TAVILY_API_KEY")
    if tavily_key:
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=tavily_key)
            query = f"{stock_name} {code} stock news"
            resp = client.search(query=query, max_results=max_results, search_depth="basic")
            results = []
            for r in resp.get("results", [])[:max_results]:
                results.append({
                    "title": r.get("title", ""),
                    "content": r.get("content", "")[:200],
                    "url": r.get("url", ""),
                    "source": "tavily",
                })
            if results:
                _log(f"[{code}] News via Tavily ({len(results)} results)")
                return results
        except Exception as e:
            _log(f"[{code}] Tavily failed: {e}")

    # Priority 1: SerpAPI
    serpapi_key = os.environ.get("SERPAPI_KEY")
    if serpapi_key:
        try:
            from serpapi import GoogleSearch
            params = {
                "q": f"{stock_name} stock news",
                "api_key": serpapi_key,
                "num": max_results,
            }
            search = GoogleSearch(params)
            data = search.get_dict()
            results = []
            for r in data.get("organic_results", [])[:max_results]:
                results.append({
                    "title": r.get("title", ""),
                    "content": r.get("snippet", "")[:200],
                    "url": r.get("link", ""),
                    "source": "serpapi",
                })
            if results:
                _log(f"[{code}] News via SerpAPI ({len(results)} results)")
                return results
        except Exception as e:
            _log(f"[{code}] SerpAPI failed: {e}")

    # No API keys configured — return empty, let Claude use WebSearch
    _log(f"[{code}] No news API configured, skipping (Claude will use WebSearch)")
    return []


# ============================================================
# SECTION 2.6: Capital Flow & Institutional Activity (机构资金监控)
# All functions degrade gracefully — return empty dict on any failure.
# ============================================================

def fetch_north_bound_flow_20d() -> dict:
    """
    Fetch 20-day northbound capital (沪深港通北向) net flow.
    Used for STEP 0 market regime detection.
    Returns net flow in 亿元, direction, and consecutive inflow/outflow days.
    """
    if not _check_source("akshare"):
        return {"net_flow_20d": None, "direction": "unknown", "consecutive_days": None, "source": "unavailable"}
    try:
        import akshare as ak
        # Try multiple interface names (akshare renames APIs across versions)
        df = None
        for fn_name in [
            "stock_hsgt_north_net_flow_in_em",
            "stock_em_north_south_flow",
            "stock_hsgt_fund_flow_summary_em",
            "stock_connect_position_sh",
        ]:
            try:
                fn = getattr(ak, fn_name, None)
                if fn is None:
                    continue
                df = fn()
                if df is not None and not df.empty:
                    _log(f"[Market] Northbound: using {fn_name}")
                    break
            except Exception:
                continue

        if df is None or df.empty:
            raise ValueError("All northbound flow interfaces returned empty")

        # Sort by date column (first column) and take last 20 rows
        date_col = df.columns[0]
        df = df.sort_values(date_col).tail(20).reset_index(drop=True)

        # Find net flow column: prefer columns containing 净流入 or net
        net_col = None
        for col in df.columns:
            if "净流入" in str(col) and "历史" not in str(col) and "累计" not in str(col):
                net_col = col
                break
        if net_col is None:
            # Fallback: use column index 3 (typical position for daily net)
            net_col = df.columns[min(3, len(df.columns) - 1)]

        def parse_flow(val):
            """Parse values that may be strings like '123.4亿' or numbers."""
            if val is None:
                return None
            s = str(val).replace(",", "").replace("亿", "").replace(" ", "")
            try:
                return float(s)
            except (ValueError, TypeError):
                return None

        net_series = df[net_col].apply(parse_flow).dropna()
        if net_series.empty:
            raise ValueError("Cannot parse net flow values")

        net_20d = round(float(net_series.sum()), 2)

        # Count consecutive days in the dominant direction (from most recent)
        consecutive = 0
        dominant_positive = net_20d >= 0
        for v in net_series.iloc[::-1]:
            if dominant_positive and v > 0:
                consecutive += 1
            elif not dominant_positive and v < 0:
                consecutive -= 1
            else:
                break

        # Direction thresholds: ±50亿 as neutral band
        if net_20d > 50:
            direction = "inflow"
        elif net_20d < -50:
            direction = "outflow"
        else:
            direction = "neutral"

        _log(f"[Market] Northbound 20d net: {net_20d}亿 | {direction} | consecutive: {consecutive}d")
        return {
            "net_flow_20d": net_20d,
            "direction": direction,
            "consecutive_days": consecutive,
            "source": "akshare",
        }
    except Exception as e:
        _log(f"[Market] Northbound flow failed: {e}")
        return {"net_flow_20d": None, "direction": "unknown", "consecutive_days": None, "source": "failed"}


def fetch_market_breadth(as_of_date: str = None) -> dict:
    """全市场涨跌家数 + 涨停跌停 + 沪深300涨跌，计算"普涨/抱团分化"分歧度。

    背景：指数上涨不代表个股普涨——2026-07-09四大指数集体高开但超4600只个股下跌，
    是资金抱团集中在少数主线，不是普涨。这种"指数强、个股弱"的分歧本来只能靠临时
    WebSearch才能发现，现在做成结构化字段，每次Layer 1市场水位检查自动带出来。

    分歧度判断（regime_label）：
      指数涨+个股跌多 → "抱团/集中"：追高风险大，追的是少数主线不是全market
      指数涨+个股涨多 → "普涨"：广度健康，正常右侧逻辑适用
      指数跌+个股涨多 → "权重股压制"：非真普跌，可能是指数成分股结构性拖累
      指数跌+个股跌多 → "普跌"：真实退潮

    as_of_date: YYYYMMDD，指定回测某个历史交易日；None=取最近一个已收盘交易日（实盘用）
    """
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        return {"source": "unavailable", "regime_label": "unknown"}
    try:
        import tushare as ts
        pro = ts.pro_api(token)

        if as_of_date:
            trade_date = as_of_date
        else:
            # 找最近一个已收盘交易日（避免盘中/非交易日拉到空数据）
            today = datetime.now()
            cal = pro.trade_cal(exchange="SSE",
                                start_date=(today - timedelta(days=10)).strftime("%Y%m%d"),
                                end_date=today.strftime("%Y%m%d"))
            open_days = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist(), reverse=True)
            trade_date = open_days[1] if (open_days and open_days[0] == today.strftime("%Y%m%d") and today.hour < 16) else (open_days[0] if open_days else today.strftime("%Y%m%d"))

        df = pro.daily(trade_date=trade_date, fields="ts_code,pct_chg")
        if df is None or df.empty:
            raise ValueError(f"daily() 在 {trade_date} 返回空")

        advancers = int((df["pct_chg"] > 0).sum())
        decliners = int((df["pct_chg"] < 0).sum())
        total = advancers + decliners
        advance_pct = round(advancers / total * 100, 1) if total else None

        limit_up = limit_down = None
        try:
            lu = pro.limit_list_d(trade_date=trade_date, limit_type="U")
            ld = pro.limit_list_d(trade_date=trade_date, limit_type="D")
            limit_up, limit_down = len(lu), len(ld)
        except Exception:
            pass

        index_pct_chg = None
        try:
            idx = pro.index_daily(ts_code="000300.SH", start_date=trade_date, end_date=trade_date)
            if idx is not None and not idx.empty:
                index_pct_chg = round(float(idx.iloc[0]["pct_chg"]), 2)
        except Exception:
            pass

        # 分歧度判定（阈值以50%为界——只要涨跌家数比过半就足够定性，
        # 不需要额外留一个"震荡/中性"死区来吸收45-55%之间明显的分歧case）
        if index_pct_chg is None or advance_pct is None:
            regime_label = "unknown"
        elif index_pct_chg > 0.3 and advance_pct < 50:
            regime_label = "抱团/集中（指数强、个股弱，追高风险大）"
        elif index_pct_chg > 0.3 and advance_pct >= 50:
            regime_label = "普涨（广度健康）"
        elif index_pct_chg < -0.3 and advance_pct > 50:
            regime_label = "权重股压制（非真普跌）"
        elif index_pct_chg < -0.3 and advance_pct <= 50:
            regime_label = "普跌（真实退潮）"
        else:
            regime_label = "震荡/中性（指数涨跌不足0.3%，看涨跌家数比：" + (
                "偏强" if advance_pct > 55 else "偏弱" if advance_pct < 45 else "均衡") + "）"

        _log(f"[Market] Breadth {trade_date}: 指数{index_pct_chg}% 上涨{advancers}/下跌{decliners} "
             f"({advance_pct}%) 涨停{limit_up}/跌停{limit_down} → {regime_label}")

        return {
            "trade_date": trade_date,
            "index_pct_chg": index_pct_chg,
            "advancers": advancers,
            "decliners": decliners,
            "advance_pct": advance_pct,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "regime_label": regime_label,
            "source": "tushare",
        }
    except Exception as e:
        _log(f"[Market] Breadth fetch failed: {e}")
        return {"source": "failed", "regime_label": "unknown"}


def _fetch_tushare_moneyflow_df(code: str, days: int = 30):
    """
    Shared helper: fetch raw Tushare moneyflow DataFrame (5000积分+).
    Amounts in 万元. Returns sorted DataFrame or None on failure.
    """
    token = os.environ.get("TUSHARE_TOKEN")
    if not token or not _check_source("tushare"):
        return None
    try:
        import tushare as ts
        pro = ts.pro_api(token)
        ts_code = f"{code}.SH" if code.startswith(("600", "601", "603", "605", "688")) else f"{code}.SZ"
        start = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        df = pro.moneyflow(ts_code=ts_code, start_date=start, end_date=end)
        if df is not None and not df.empty and "net_mf_amount" in df.columns:
            return df.sort_values("trade_date")
    except Exception as e:
        _log(f"[{code}] Tushare moneyflow helper failed: {e}")
    return None


def fetch_stock_fund_flow(code: str) -> dict:
    """
    Fetch individual stock fund flow by order size (主力/大单/散户净流入).
    Priority: Tushare moneyflow (5000积分+) > akshare East Money.
    Values returned in 亿元. Covers the most recent trading day available.
    """
    # Priority 0: Tushare moneyflow (more reliable, net_mf_amount in 万元)
    mf_df = _fetch_tushare_moneyflow_df(code, days=5)
    if mf_df is not None and not mf_df.empty:
        latest = mf_df.iloc[-1]
        main_net = _safe_float(latest.get("net_mf_amount"))
        main_net_yi = round(main_net / 10000, 4) if main_net is not None else None
        buy_elg  = _safe_float(latest.get("buy_elg_amount"))
        sell_elg = _safe_float(latest.get("sell_elg_amount"))
        buy_lg   = _safe_float(latest.get("buy_lg_amount"))
        sell_lg  = _safe_float(latest.get("sell_lg_amount"))
        buy_sm   = _safe_float(latest.get("buy_sm_amount"))
        sell_sm  = _safe_float(latest.get("sell_sm_amount"))
        super_net_yi  = round((buy_elg - sell_elg) / 10000, 4) if buy_elg is not None and sell_elg is not None else None
        large_net_yi  = round((buy_lg  - sell_lg)  / 10000, 4) if buy_lg  is not None and sell_lg  is not None else None
        retail_net_yi = round((buy_sm  - sell_sm)  / 10000, 4) if buy_sm  is not None and sell_sm  is not None else None
        direction = "净流入" if (main_net_yi or 0) > 0 else "净流出"
        _log(f"[{code}] Main force (Tushare): {direction} {main_net_yi}亿")

        # 展示用：大单/小单资金方向背离（不参与打分）
        # 口径警示：主力/散户是按单笔委托金额分桶（超大单+大单=主力，小单=散户），
        # 不是投资者身份识别——算法拆单会让机构资金显示为小单，固定金额门槛对高价股
        # 天然更容易把普通散户单归入大单。方向一致时（同步流入/流出）不构成身份判断，
        # 不加免责声明；方向背离时最容易被误读成"机构行为"，必须带上口径说明。
        main_retail_divergence = None
        if main_net_yi is not None and retail_net_yi is not None:
            if main_net_yi > 0 and retail_net_yi < 0:
                main_retail_divergence = ("大单净流入+小单净流出（模式类似吸筹，"
                    "但按委托金额分桶不代表投资者身份，算法拆单/高价股金额门槛会失真）")
            elif main_net_yi < 0 and retail_net_yi > 0:
                main_retail_divergence = ("大单净流出+小单净流入（模式类似派发，"
                    "但按委托金额分桶不代表投资者身份，算法拆单/高价股金额门槛会失真）")
            elif main_net_yi > 0 and retail_net_yi > 0:
                main_retail_divergence = "大单小单同步净流入"
            elif main_net_yi < 0 and retail_net_yi < 0:
                main_retail_divergence = "大单小单同步净流出"

        return {
            "main_force_net_yi": main_net_yi,
            "super_large_net_yi": super_net_yi,
            "large_net_yi": large_net_yi,
            "retail_net_yi": retail_net_yi,
            "main_retail_divergence": main_retail_divergence,
            "direction": "inflow" if (main_net_yi or 0) > 0 else "outflow",
            "source": "tushare_moneyflow",
        }
    # Priority 1: akshare East Money fallback
    if not _check_source("akshare"):
        return {}
    try:
        import akshare as ak
        market = "sh" if code.startswith(("600", "601", "603", "605", "688", "51", "58")) else "sz"
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        if df is None or df.empty:
            raise ValueError(f"No fund flow data for {code}")

        latest = df.iloc[-1]

        def pick(must_include, must_exclude=None):
            """Find first column containing all must_include keywords, none of must_exclude."""
            for col in df.columns:
                col_s = str(col)
                if not all(k in col_s for k in must_include):
                    continue
                if must_exclude and any(e in col_s for e in must_exclude):
                    continue
                raw = latest.get(col)
                return _safe_float(str(raw).replace(",", "")) if raw is not None else None
            return None

        def to_yi(val):
            """Auto-detect unit and convert to 亿元.
            East Money API returns raw 元 values (e.g. 6.5e9).
            Heuristic: >1e7 = 元 → /1e8; >1e3 = 万元 → /1e4; else already 亿元.
            """
            if val is None:
                return None
            if abs(val) > 1e7:
                return round(val / 1e8, 4)   # 元 → 亿元
            elif abs(val) > 1e3:
                return round(val / 1e4, 4)   # 万元 → 亿元
            return round(val, 4)             # already 亿元

        # must_exclude prevents "大单" from matching "超大单"
        main_net  = to_yi(pick(["主力", "净额"]) or pick(["主力", "净流入", "额"]))
        super_net = to_yi(pick(["超大单", "净额"]) or pick(["超大", "净"]))
        large_net = to_yi(pick(["大单", "净额"], must_exclude=["超大"]) or
                          pick(["大单", "净"], must_exclude=["超大"]))
        small_net = to_yi(pick(["小单", "净额"]) or pick(["小单", "净"]))

        # Synthesize main force if not directly available
        if main_net is None and super_net is not None and large_net is not None:
            main_net = round((super_net or 0) + (large_net or 0), 4)

        direction = "净流入" if (main_net or 0) > 0 else "净流出"
        _log(f"[{code}] Main force: {direction} {main_net}亿")

        main_retail_divergence = None
        if main_net is not None and small_net is not None:
            if main_net > 0 and small_net < 0:
                main_retail_divergence = ("大单净流入+小单净流出（模式类似吸筹，"
                    "但按委托金额分桶不代表投资者身份，算法拆单/高价股金额门槛会失真）")
            elif main_net < 0 and small_net > 0:
                main_retail_divergence = ("大单净流出+小单净流入（模式类似派发，"
                    "但按委托金额分桶不代表投资者身份，算法拆单/高价股金额门槛会失真）")
            elif main_net > 0 and small_net > 0:
                main_retail_divergence = "大单小单同步净流入"
            elif main_net < 0 and small_net < 0:
                main_retail_divergence = "大单小单同步净流出"

        return {
            "main_force_net_yi": main_net,
            "super_large_net_yi": super_net,
            "large_net_yi": large_net,
            "retail_net_yi": small_net,
            "main_retail_divergence": main_retail_divergence,
            "direction": "inflow" if (main_net or 0) > 0 else "outflow",
            "source": "akshare_eastmoney",
        }
    except Exception as e:
        _log(f"[{code}] Fund flow failed: {e}")
        return {}


def fetch_fund_flow_multiday(code: str) -> dict:
    """
    Fetch multi-day main force fund flow sums (3d, 5d, 20d) in 亿元.
    Priority: Tushare moneyflow (5000积分+) > akshare fallback.
    """
    # Priority 0: Tushare moneyflow (net_mf_amount in 万元)
    mf_df = _fetch_tushare_moneyflow_df(code, days=30)
    if mf_df is not None and not mf_df.empty:
        series_yi = mf_df["net_mf_amount"].apply(
            lambda x: float(x) / 10000 if x is not None else None
        ).dropna()
        result = {"source": "tushare_moneyflow"}
        for n, key in [(3, "fund_flow_3d"), (5, "fund_flow_5d"), (20, "fund_flow_20d")]:
            tail = series_yi.tail(n)
            val = round(float(tail.sum()), 4) if len(tail) > 0 else None
            result[key] = val
        # Sanity check: realistic upper bounds per period
        thresholds = {"fund_flow_3d": 120, "fund_flow_5d": 200, "fund_flow_20d": 800}
        for key, max_yi in thresholds.items():
            if result.get(key) is not None and abs(result[key]) > max_yi:
                _log(f"[{code}] {key}={result[key]}亿 exceeds threshold, discarding")
                result[key] = None

        # 展示用：资金流动量（5日日均 vs 20日日均，判断加速/减速，不参与打分）
        f5d, f20d = result.get("fund_flow_5d"), result.get("fund_flow_20d")
        if f5d is not None and f20d is not None:
            avg5, avg20 = f5d / 5, f20d / 20
            result["fund_flow_avg5d_yi"] = round(avg5, 4)
            result["fund_flow_avg20d_yi"] = round(avg20, 4)
            if avg5 > 0 and avg20 > 0:
                result["fund_flow_momentum"] = "加速流入" if avg5 > avg20 * 1.2 else \
                    ("流入放缓" if avg5 < avg20 * 0.8 else "流入稳定")
            elif avg5 < 0 and avg20 < 0:
                result["fund_flow_momentum"] = "加速流出" if avg5 < avg20 * 1.2 else \
                    ("流出放缓" if avg5 > avg20 * 0.8 else "流出稳定")
            elif avg5 > 0 and avg20 <= 0:
                result["fund_flow_momentum"] = "由流出转流入"
            elif avg5 <= 0 and avg20 > 0:
                result["fund_flow_momentum"] = "由流入转流出"

        # 展示用：资金流稳定性（近5日/20日标准差，区分持续建仓 vs 大进大出博弈）
        tail5 = series_yi.tail(5)
        tail20 = series_yi.tail(20)
        if len(tail5) >= 3:
            result["fund_flow_std5d_yi"] = round(float(tail5.std()), 4)
        if len(tail20) >= 5:
            std20 = float(tail20.std())
            result["fund_flow_std20d_yi"] = round(std20, 4)
            mean_abs20 = float(tail20.abs().mean())
            if mean_abs20 > 0:
                result["fund_flow_stability"] = "稳定持续" if std20 < mean_abs20 * 1.2 else "大进大出"

        _log(f"[{code}] Fund flow 5d={result.get('fund_flow_5d')}亿 20d={result.get('fund_flow_20d')}亿 (Tushare)")
        return result
    # Priority 1: akshare fallback
    if not _check_source("akshare"):
        return {}
    try:
        import akshare as ak
        market = "sh" if code.startswith(("600", "601", "603", "605", "688", "51", "58")) else "sz"
        df = None
        # Try multiple interface signatures (akshare renames across versions)
        for try_fn in [
            lambda: ak.stock_individual_fund_flow(stock=code, market=market),
            lambda: ak.stock_individual_fund_flow(stock=code, market=market, period="daily"),
            lambda: ak.stock_fund_flow_individual(symbol=code),
        ]:
            try:
                df = try_fn()
                if df is not None and not df.empty:
                    break
            except Exception:
                continue
        if df is None or df.empty:
            return {}

        # Find the main force net flow column (same logic as fetch_stock_fund_flow)
        main_col = None
        for col in df.columns:
            col_s = str(col)
            if "主力" in col_s and ("净额" in col_s or ("净" in col_s and "流入" in col_s)):
                main_col = col
                break

        if main_col is None:
            return {}

        def row_to_yi(raw_val):
            v = _safe_float(str(raw_val).replace(",", ""))
            if v is None:
                return None
            if abs(v) > 1e7:
                return v / 1e8
            elif abs(v) > 1e3:
                return v / 1e4
            return v

        series_yi = df[main_col].apply(row_to_yi).dropna()
        result = {}
        for n_days, key in [(3, "fund_flow_3d"), (5, "fund_flow_5d"), (20, "fund_flow_20d")]:
            tail = series_yi.tail(n_days)
            result[key] = round(float(tail.sum()), 4) if len(tail) > 0 else None

        # Sanity check: realistic upper bounds for individual stock main-force flow
        thresholds = {"fund_flow_3d": 120, "fund_flow_5d": 200, "fund_flow_20d": 800}
        for key, max_yi in thresholds.items():
            if result.get(key) is not None and abs(result[key]) > max_yi:
                _log(f"[{code}] {key}={result[key]}亿 exceeds realistic range ({max_yi}亿), discarding")
                result[key] = None

        _log(f"[{code}] Fund flow 5d={result.get('fund_flow_5d')}亿 20d={result.get('fund_flow_20d')}亿")
        return result
    except Exception as e:
        _log(f"[{code}] Multi-day fund flow failed: {e}")
        return {}


def fetch_tushare_chip_dist(code: str) -> dict:
    """
    Fetch real chip distribution from Tushare cyq_perf (6000积分).
    Returns winner_rate (获利盘%), weight_avg (加权成本), cost percentiles.
    Much more accurate than VWAP approximation in calc_chip_concentration.
    """
    token = os.environ.get("TUSHARE_TOKEN")
    if not token or not _check_source("tushare"):
        return {"source": "no_tushare_token"}
    try:
        import tushare as ts
        pro = ts.pro_api(token)
        ts_code = f"{code}.SH" if code.startswith(("600", "601", "603", "605", "688")) else f"{code}.SZ"
        start = (datetime.now() - timedelta(days=16)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        df = pro.cyq_perf(ts_code=ts_code, start_date=start, end_date=end)
        if df is None or df.empty:
            return {"source": "no_data"}
        df = df.sort_values("trade_date")
        latest = df.iloc[-1]

        winner_rate = _safe_float(latest.get("winner_rate"))
        weight_avg  = _safe_float(latest.get("weight_avg"))
        cost_5pct   = _safe_float(latest.get("cost_5pct"))
        cost_15pct  = _safe_float(latest.get("cost_15pct"))
        cost_50pct  = _safe_float(latest.get("cost_50pct"))
        cost_85pct  = _safe_float(latest.get("cost_85pct"))
        cost_95pct  = _safe_float(latest.get("cost_95pct"))

        if winner_rate is None:
            return {"source": "no_winner_rate"}

        if winner_rate >= 70:
            interp = "大多数筹码获利，升势健康"
        elif winner_rate >= 50:
            interp = "多数筹码获利，结构尚可"
        elif winner_rate >= 30:
            interp = "约半数筹码套牢，上方存在解套压力"
        else:
            interp = "套牢盘比例高，解套抛压明显"

        _log(f"[{code}] Chip dist (Tushare): winner={winner_rate}%, weight_avg={weight_avg}")

        # 展示用：获利盘比例的5日变化速度，而非静态值（不参与打分）
        winner_rate_chg_5d = None
        winner_rate_trend = None
        if len(df) >= 6:
            prior = _safe_float(df.iloc[-6].get("winner_rate"))
            if prior is not None and winner_rate is not None:
                winner_rate_chg_5d = round(winner_rate - prior, 4)
                if winner_rate_chg_5d > 5:
                    winner_rate_trend = "获利盘快速上升"
                elif winner_rate_chg_5d < -5:
                    winner_rate_trend = "获利盘快速下降（套牢盘增加）"
                else:
                    winner_rate_trend = "获利盘稳定"

        return {
            "winner_rate": winner_rate,
            "winner_rate_chg_5d": winner_rate_chg_5d,
            "winner_rate_trend": winner_rate_trend,
            "weight_avg": weight_avg,
            "cost_5pct":  cost_5pct,
            "cost_15pct": cost_15pct,
            "cost_50pct": cost_50pct,
            "cost_85pct": cost_85pct,
            "cost_95pct": cost_95pct,
            "interpretation": interp,
            "trade_date": str(latest.get("trade_date", "")),
            "source": "tushare_cyq_perf",
        }
    except Exception as e:
        _log(f"[{code}] Tushare cyq_perf failed: {e}")
        return {"source": "failed", "error": str(e)}


def fetch_stock_sector(code: str) -> str:
    """Get primary industry/sector classification for an A-share stock."""
    # Try tushare first (most accurate for CN stocks)
    token = os.environ.get("TUSHARE_TOKEN")
    if token and _check_source("tushare"):
        try:
            import tushare as ts
            pro = ts.pro_api(token)
            ts_code = f"{code}.SH" if code.startswith(("600", "601", "603", "605", "688")) else f"{code}.SZ"
            df = pro.stock_basic(ts_code=ts_code, fields="ts_code,name,industry")
            if df is not None and not df.empty:
                val = str(df.iloc[0].get("industry", ""))
                if val and val.lower() not in ("nan", "none", ""):
                    _log(f"[{code}] Sector (tushare): {val}")
                    return val
        except Exception as e:
            _log(f"[{code}] Sector via tushare failed: {e}")

    # Fallback: akshare individual stock info
    if _check_source("akshare"):
        try:
            import akshare as ak
            info = ak.stock_individual_info_em(symbol=code)
            if info is not None and not info.empty:
                for _, row in info.iterrows():
                    label = str(row.iloc[0])
                    if "所属" in label or "行业" in label:
                        val = str(row.iloc[1])
                        if val and val.lower() not in ("nan", "none", ""):
                            _log(f"[{code}] Sector (akshare): {val}")
                            return val
        except Exception as e:
            _log(f"[{code}] Sector via akshare failed: {e}")

    return "未知"


# Tushare industry name → akshare board name mapping.
# stock_board_industry_cons_em uses East Money's naming scheme, not tushare's.
_TUSHARE_TO_BOARD = {
    "火力发电": "电力行业", "水力发电": "电力行业", "核电": "核电",
    "电力供应": "电力行业", "电网": "电力行业",
    "煤炭开采": "煤炭", "天然气": "燃气", "石油开采": "石油石化",
    "半导体及元件": "半导体", "电子制造": "元件", "集成电路": "半导体",
    "通信设备": "通信", "通信服务": "通信",
    "软件开发": "软件开发", "计算机设备": "计算机",
    "机械制造": "通用机械", "专用设备": "专用设备",
    "汽车零部件": "汽车零部件", "汽车整车": "乘用车",
    "银行": "银行", "证券": "证券", "保险": "保险",
    "房地产开发": "房地产", "医药制造": "医疗器械",
}


def fetch_sector_breadth(sector: str) -> dict:
    """
    Fetch sector breadth: % of stocks in the sector with positive change today.
    Used for dimension 2 sub-indicator B (跟风股活跃度).
    Returns follow_stock_up_pct in [0, 1] or None on failure.
    Tries the tushare industry name, then the translated akshare board name.
    """
    if not _check_source("akshare") or not sector or sector == "未知":
        return {"follow_stock_up_pct": None, "source": "unavailable"}
    import akshare as ak
    board_name = _TUSHARE_TO_BOARD.get(sector, sector)
    df = None
    for name in dict.fromkeys([sector, board_name]):  # try original, then translated (deduplicated)
        try:
            df = ak.stock_board_industry_cons_em(symbol=name)
            if df is not None and not df.empty:
                break
        except Exception:
            continue
    try:
        if df is None or df.empty:
            raise ValueError(f"No component data for sector {sector}")

        change_col = None
        for col in df.columns:
            if "涨跌幅" in str(col):
                change_col = col
                break
        if change_col is None:
            raise ValueError("Cannot find 涨跌幅 column")

        changes = df[change_col].apply(_safe_float).dropna()
        if len(changes) == 0:
            raise ValueError("No valid change data")

        total = len(changes)
        up_count = int((changes > 0).sum())
        up_pct = round(up_count / total, 3)

        _log(f"[{sector}] Breadth: {up_count}/{total} up = {up_pct:.1%}")
        return {
            "follow_stock_up_pct": up_pct,
            "total_stocks": total,
            "up_count": up_count,
            "source": "akshare_board",
        }
    except Exception as e:
        _log(f"[{sector}] Sector breadth failed: {e}")
        return {"follow_stock_up_pct": None, "source": "failed"}


# ETF代码映射（tushare行业名称 → 相关ETF代码列表）
# 仅用于维度3 ETF资金共识子指标；用户可按需扩充
# 2026-07-13 全表核对：用户在同花顺发现"通信ETF银华(159994)"不在原映射里，抽查后发现原表
# 半数以上代码配错了（可能是历史某次编写时手滑/记错）——516090其实是新能源ETF易方达，
# 159869其实是游戏ETF华夏，跟"通信设备"毫无关系；类似问题还出现在计算机设备/软件开发/
# 电气设备/汽车零部件/电源设备。逐个用 akshare fund_etf_spot_em() 按名称关键词+成交额排序
# 重新核实替换，标注"0713修正"的都是本次改动。电源设备、非金属矿物没有精确对应的ETF，
# 用主题相近的做松散代理，标注了精度打折，不是权威匹配。
_SECTOR_ETF_MAP = {
    "通信设备":    ["515880", "515050", "159994"],  # 通信ETF国泰/华夏/银华，按成交额排序（0713修正：原代码是新能源+游戏ETF，完全配错）
    "半导体及元件": ["512480", "159516"],   # 半导体ETF国联安 / 半导体设备ETF国泰（0713修正：原159996是家电ETF，配错）
    "电子制造":    ["512480", "159516"],    # 半导体ETF国联安 / 半导体设备ETF国泰，已核实正确
    "元器件":      ["515260"],              # 电子ETF华宝，覆盖PCB/消费电子/半导体（2026-07-13验证有效）
    "计算机设备":  ["159998", "512720"],    # 计算机ETF天弘/国泰（0713修正：原516780是稀土ETF，159449代码不存在，配错）
    "软件开发":    ["159852", "515230"],    # 软件ETF嘉实/国泰（0713修正：原代码跟"计算机设备"一样是错的）
    "电气设备":    ["159326", "159611"],    # 电网设备ETF华夏 / 电力ETF广发（0713修正：原516890是新材料ETF，配错）
    "机械制造":    ["562500", "159551"],    # 机器人ETF华夏/国泰，已核实正确
    "汽车零部件":  ["516110", "516520"],    # 汽车ETF国泰 / 智能驾驶ETF华泰柏瑞（0713修正：原159608是稀有金属ETF，配错）
    "电源设备":    ["159732", "159997"],    # 消费电子ETF华夏 / 电子ETF天弘（0713修正：原159941是纳指ETF，离谱；电源设备无专门ETF，此为松散近似代理，精度打折）
    "非金属矿物":  ["512400"],              # 有色金属ETF南方（0713修正：原512480是半导体ETF，配错；非金属矿物无精确对应ETF，此为松散近似代理，精度存疑）
}


def _fetch_etf_share_change(etf_codes: list) -> dict:
    """
    展示用：ETF真实净申购/赎回（份额变化），比折溢价率更直接反映资金真实进出
    （折溢价率是套利驱动，可能不代表方向性资金；份额变化是真实申赎的直接证据）。
    数据源：Tushare fund_share。不参与打分，仅展示。
    """
    token = os.environ.get("TUSHARE_TOKEN")
    if not token or not _check_source("tushare"):
        return {}
    try:
        import tushare as ts
        pro = ts.pro_api(token)
        start = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        share_chg_pct_list = []
        for code in etf_codes:
            ts_code = f"{code}.SH" if code.startswith(("51", "52", "56", "58")) else f"{code}.SZ"
            df = pro.fund_share(ts_code=ts_code, start_date=start, end_date=end)
            if df is None or df.empty or "fd_share" not in df.columns:
                continue
            df = df.sort_values("trade_date")
            if len(df) < 2:
                continue
            latest_share = _safe_float(df.iloc[-1].get("fd_share"))
            prior_share = _safe_float(df.iloc[-2].get("fd_share"))
            if latest_share is None or prior_share is None or prior_share == 0:
                continue
            chg_pct = round((latest_share - prior_share) / prior_share * 100, 4)
            share_chg_pct_list.append(chg_pct)
        if not share_chg_pct_list:
            return {}
        avg_chg = round(sum(share_chg_pct_list) / len(share_chg_pct_list), 4)
        return {
            "etf_share_chg_pct": avg_chg,
            "etf_share_trend": "真实净申购（份额增加）" if avg_chg > 0.5 else
                               ("真实净赎回（份额减少）" if avg_chg < -0.5 else "份额基本平稳"),
            "etf_share_source": "tushare_fund_share",
        }
    except Exception as e:
        _log(f"ETF share change failed: {e}")
        return {}


def fetch_etf_fund_flow(sector: str) -> dict:
    """
    Fetch ETF fund flow proxy for the sector (维度3子指标D).
    Returns premium rate (折溢价率) and volume trend for mapped ETFs.
    Degrades gracefully — returns {} if sector has no ETF mapping.
    """
    etf_codes = _SECTOR_ETF_MAP.get(sector, [])
    if not etf_codes or not _check_source("akshare"):
        return {}
    try:
        import akshare as ak
        spot_df = ak.fund_etf_spot_em()
        if spot_df is None or spot_df.empty:
            raise ValueError("ETF spot data empty")

        # Find code column
        code_col = next((c for c in spot_df.columns if "代码" in str(c)), None)
        if code_col is None:
            raise ValueError("No code column in ETF spot data")

        premium_rates = []
        vol_ratios = []

        for etf_code in etf_codes:
            row = spot_df[spot_df[code_col].astype(str).str.strip() == etf_code]
            if row.empty:
                continue
            r = row.iloc[0]

            # Premium rate (折溢价率)
            for prem_col in spot_df.columns:
                if "折溢价" in str(prem_col) or "溢价" in str(prem_col):
                    val = _safe_float(str(r.get(prem_col, "")).replace("%", ""))
                    if val is not None:
                        premium_rates.append(val / 100 if abs(val) > 1 else val)
                    break

            # Volume ratio (量比) or turnover as vol proxy
            for vol_col in spot_df.columns:
                if "量比" in str(vol_col):
                    val = _safe_float(r.get(vol_col))
                    if val is not None:
                        vol_ratios.append(val)
                    break

        if not premium_rates and not vol_ratios:
            return {}

        avg_premium = round(sum(premium_rates) / len(premium_rates), 4) if premium_rates else None
        avg_vol_ratio = round(sum(vol_ratios) / len(vol_ratios), 2) if vol_ratios else None

        result = {
            "etf_premium_rate": avg_premium,
            "etf_vol_ratio": avg_vol_ratio,
            "etf_vol_trend": "rising" if (avg_vol_ratio or 0) > 1.2 else
                             "falling" if (avg_vol_ratio or 0) < 0.7 else "normal",
            "etf_codes_checked": etf_codes,
            "source": "akshare_etf_spot",
        }
        result.update(_fetch_etf_share_change(etf_codes))  # 展示用：真实份额变化，不覆盖折溢价率
        _log(f"[{sector}] ETF flow: premium={avg_premium}, vol_ratio={avg_vol_ratio}, "
             f"share_chg={result.get('etf_share_chg_pct')}")
        return result
    except Exception as e:
        _log(f"[{sector}] ETF fund flow failed: {e}")
        return {}


def fetch_dragon_tiger(code: str, lookback_days: int = 10) -> dict:
    """
    Check recent 龙虎榜 (top-trader list) for institutional seat activity.
    Scans the last `lookback_days` calendar days.
    Returns whether institutions appeared and their net direction.
    """
    if not _check_source("akshare"):
        return {"has_institution": False, "institution_direction": "none"}
    try:
        import akshare as ak
        from datetime import datetime, timedelta

        inst_buy_total = 0.0
        inst_sell_total = 0.0
        found_dates = []

        for delta in range(lookback_days):
            check_date = (datetime.now() - timedelta(days=delta)).strftime("%Y%m%d")
            try:
                df = ak.stock_lhb_detail_em(date=check_date, flag="全部")
                if df is None or df.empty:
                    continue
                # Filter rows for this stock code
                code_col = next((c for c in df.columns if "代码" in str(c)), df.columns[0])
                rows = df[df[code_col].astype(str).str.strip() == code]
                if rows.empty:
                    continue

                # Check for institutional seat mentions across all text
                row_text = " ".join(str(v) for v in rows.values.flatten())
                if "机构" not in row_text:
                    continue

                found_dates.append(check_date)

                # Extract buy/sell amounts for institutional rows
                for _, row in rows.iterrows():
                    for col in df.columns:
                        col_s = str(col)
                        val = _safe_float(str(row.get(col, "")).replace(",", "")) or 0.0
                        if "机构" in row_text:
                            if "买入" in col_s and "净" not in col_s:
                                inst_buy_total += val
                            elif "卖出" in col_s and "净" not in col_s:
                                inst_sell_total += val
            except Exception:
                continue  # Skip dates with no data (non-trading days etc.)

        if not found_dates:
            return {"has_institution": False, "institution_direction": "none", "last_date": None}

        # Convert 万元 → 亿元
        net = round((inst_buy_total - inst_sell_total) / 10000, 4)
        if inst_buy_total > inst_sell_total * 1.2:
            direction = "buy"
        elif inst_sell_total > inst_buy_total * 1.2:
            direction = "sell"
        else:
            direction = "mixed"

        _log(f"[{code}] Dragon-tiger: institution {direction}, net {net}亿, dates={found_dates[:3]}")
        return {
            "has_institution": True,
            "institution_direction": direction,
            "institution_net_yi": net,
            "last_date": found_dates[0],
            "appearances": len(found_dates),
            "source": "akshare_lhb",
        }
    except Exception as e:
        _log(f"[{code}] Dragon-tiger failed: {e}")
        return {"has_institution": False, "institution_direction": "none"}


# ============================================================
# SECTION 2.7: Tushare Fundamental Data (基本面数据 — PE分位/ROE/增速/PEG)
# Requires TUSHARE_TOKEN. Each sub-fetch degrades independently.
# ============================================================

def fetch_tushare_fundamentals(code: str) -> dict:
    """
    Fetch fundamental metrics via Tushare Pro for A-shares.
    Returns PE percentile (估值三维判断), ROE, growth (质量因子), PEG.
    Returns {"source": "no_tushare_token"} silently if token not set.
    """
    token = os.environ.get("TUSHARE_TOKEN")
    if not token or not _check_source("tushare"):
        return {"source": "no_tushare_token"}

    try:
        import tushare as ts
        pro = ts.pro_api(token)
        ts_code = f"{code}.SH" if code.startswith(("600", "601", "603", "605", "688")) else f"{code}.SZ"
        result = {"source": "tushare", "ts_code": ts_code}

        end_date = datetime.now().strftime("%Y%m%d")
        start_2y = (datetime.now() - timedelta(days=730)).strftime("%Y%m%d")

        # --- Sub-fetch 1: Daily basic (PE/PB history → percentile) ---
        try:
            basic_df = pro.daily_basic(
                ts_code=ts_code, start_date=start_2y, end_date=end_date,
                fields="trade_date,pe_ttm,pb,ps_ttm,dv_ttm,turnover_rate",
            )
            if basic_df is not None and not basic_df.empty:
                basic_df = basic_df.sort_values("trade_date")
                latest = basic_df.iloc[-1]
                pe_ttm = _safe_float(latest.get("pe_ttm"))
                result["pe_ttm"] = pe_ttm
                result["pb"] = _safe_float(latest.get("pb"))
                result["ps_ttm"] = _safe_float(latest.get("ps_ttm"))
                result["dividend_yield"] = _safe_float(latest.get("dv_ttm"))

                # PE historical percentile over 2-year window
                pe_series = basic_df["pe_ttm"].apply(_safe_float).dropna()
                pe_series = pe_series[pe_series > 0]  # strip negative PE (loss-making periods)
                if pe_ttm and pe_ttm > 0 and len(pe_series) >= 60:
                    pct = round((pe_series <= pe_ttm).sum() / len(pe_series) * 100, 1)
                    result["pe_percentile_2y"] = pct
                    result["pe_2y_min"] = round(float(pe_series.min()), 2)
                    result["pe_2y_max"] = round(float(pe_series.max()), 2)
                    result["pe_2y_median"] = round(float(pe_series.median()), 2)
                    _log(f"[{code}] PE TTM={pe_ttm}, 2Y percentile={pct}%")
        except Exception as e:
            _log(f"[{code}] Tushare daily_basic failed: {e}")

        # --- Sub-fetch 2: Financial indicators (ROE, growth, margins) ---
        start_3y = (datetime.now() - timedelta(days=1100)).strftime("%Y%m%d")
        try:
            fina_df = pro.fina_indicator(
                ts_code=ts_code, start_date=start_3y, end_date=end_date,
                fields="ann_date,roe,roe_yearly,grossprofit_margin,netprofit_yoy,revenue_yoy,eps,debt_to_assets",
            )
            if fina_df is not None and not fina_df.empty:
                fina_df = fina_df.sort_values("ann_date")
                latest_f = fina_df.iloc[-1]
                result["roe"] = _safe_float(latest_f.get("roe") or latest_f.get("roe_yearly"))
                result["gross_margin"] = _safe_float(latest_f.get("grossprofit_margin"))
                result["net_profit_yoy"] = _safe_float(latest_f.get("netprofit_yoy"))
                result["revenue_yoy"] = _safe_float(latest_f.get("revenue_yoy"))
                result["eps"] = _safe_float(latest_f.get("eps"))
                result["debt_to_assets"] = _safe_float(latest_f.get("debt_to_assets"))

                # PEG = PE TTM / net profit YoY growth (only meaningful when growth > 0)
                pe = result.get("pe_ttm")
                growth = result.get("net_profit_yoy")
                if pe and growth and growth > 0:
                    result["peg"] = round(pe / growth, 2)

                _log(f"[{code}] ROE={result.get('roe')}% NP-YoY={result.get('net_profit_yoy')}% PEG={result.get('peg')}")
        except Exception as e:
            _log(f"[{code}] Tushare fina_indicator failed: {e}")

        # --- Sub-fetch 3: Company forecast/guidance (业绩预告) ---
        try:
            fc_df = pro.forecast(ts_code=ts_code, start_date=start_2y, end_date=end_date)
            if fc_df is not None and not fc_df.empty:
                fc_df = fc_df.sort_values("ann_date")
                lf = fc_df.iloc[-1]
                result["forecast"] = {
                    "type": str(lf.get("type", "")),          # 预增/预减/扭亏 etc.
                    "np_change_min": _safe_float(lf.get("p_change_min")),
                    "np_change_max": _safe_float(lf.get("p_change_max")),
                    "ann_date": str(lf.get("ann_date", "")),
                }
                _log(f"[{code}] Forecast type: {result['forecast']['type']}")
        except Exception as e:
            _log(f"[{code}] Tushare forecast skipped (may need higher points): {e}")

        return result

    except Exception as e:
        _log(f"[{code}] Tushare fundamentals failed: {e}")
        return {"source": "tushare_failed", "error": str(e)}


# ============================================================
# SECTION 2.75: Pledge Ratio 股权质押率
# 质押率>40% = 高质押风险，在第三层条件1触发警告
# ============================================================

def fetch_pledge_ratio(code: str) -> dict:
    """
    获取最新股权质押比例（pledge_stat）。
    质押率>40% → 高风险（大股东强平压力）
    质押率20-40% → 中等
    质押率<20% → 正常
    """
    token = os.environ.get("TUSHARE_TOKEN")
    if not token or not _check_source("tushare"):
        return {"source": "no_tushare_token"}
    try:
        import tushare as ts
        pro = ts.pro_api(token)
        ts_code = f"{code}.SH" if code.startswith(("600", "601", "603", "605", "688")) else f"{code}.SZ"
        df = pro.pledge_stat(ts_code=ts_code)
        if df is None or df.empty:
            return {"pledge_ratio": None, "source": "no_data"}
        df = df.sort_values("end_date", ascending=False)
        latest = df.iloc[0]
        ratio = _safe_float(latest.get("pledge_ratio"))
        end_date = str(latest.get("end_date", ""))
        if ratio is None:
            return {"pledge_ratio": None, "source": "tushare", "end_date": end_date}
        if ratio > 40:
            risk = "high"
            signal = f"⚠️ 质押率{ratio:.1f}%，高质押风险——大股东有强平压力"
        elif ratio > 20:
            risk = "medium"
            signal = f"质押率{ratio:.1f}%，中等，留意大股东动向"
        else:
            risk = "low"
            signal = f"质押率{ratio:.1f}%，正常"
        _log(f"[{code}] Pledge ratio: {ratio:.1f}% ({risk})")
        return {
            "pledge_ratio": ratio,
            "risk_level": risk,
            "signal": signal,
            "end_date": end_date,
            "source": "tushare",
        }
    except Exception as e:
        _log(f"[{code}] Pledge ratio failed: {e}")
        return {"source": "failed", "error": str(e)}


# ============================================================
# SECTION 2.8: Financial Penetration 财报穿透层
# 合同负债趋势 / 存货周转天数 / 在建工程+资本开支
# Tushare only — silently returns {} if no token.
# ============================================================

def fetch_financial_penetration(code: str) -> dict:
    """
    穿透财报三项领先指标，判断订单积累/去库存/产能扩张真实性。
    仅A股，仅Tushare，无token时静默返回。
    """
    token = os.environ.get("TUSHARE_TOKEN")
    if not token or not _check_source("tushare"):
        return {"source": "no_tushare_token"}

    try:
        import tushare as ts
        pro = ts.pro_api(token)
        ts_code = f"{code}.SH" if code.startswith(("600", "601", "603", "605", "688")) else f"{code}.SZ"
        end_date = datetime.now().strftime("%Y%m%d")
        start_2y = (datetime.now() - timedelta(days=800)).strftime("%Y%m%d")
        result = {"source": "tushare"}

        def to_yi(val):
            if val is None:
                return None
            v = float(val)
            return round(v / 1e8, 3) if abs(v) >= 1e6 else round(v, 3)

        def extract_quarters(df, val_col):
            """返回最近4个报告期 [{period, value_yi, qoq_pct}]，数据不足则返回空列表。"""
            if df is None or df.empty or val_col not in df.columns:
                return []
            df = df.copy()
            df["_v"] = df[val_col].apply(_safe_float)
            df = df.dropna(subset=["_v", "end_date"]).sort_values("end_date")
            df = df.drop_duplicates("end_date").tail(5)
            rows = df[["end_date", "_v"]].values.tolist()
            out = []
            for i in range(len(rows)):
                period, v = rows[i]
                prev_v = rows[i - 1][1] if i > 0 else None
                qoq = (round((v - prev_v) / abs(prev_v) * 100, 1)
                       if prev_v and abs(prev_v) > 0 else None)
                out.append({"period": period, "value_yi": to_yi(v), "qoq_pct": qoq})
            return out[-4:]

        def trend_label(quarters, higher_is_better=True):
            recent = [q["qoq_pct"] for q in quarters[-3:] if q["qoq_pct"] is not None]
            if len(recent) < 2:
                return "数据不足"
            pos = sum(1 for x in recent if x > 0)
            if higher_is_better:
                if pos >= 2:
                    return "加速增长" if recent[-1] and recent[-1] > 15 else "持续增长"
                return "波动" if pos == 1 else "持续下滑"
            else:
                neg = sum(1 for x in recent if x < 0)
                return "持续改善" if neg >= 2 else ("持续恶化" if pos >= 2 else "波动")

        # ── 合同负债 / 预收账款 ──────────────────────────────────────
        try:
            bs = pro.balancesheet(
                ts_code=ts_code, start_date=start_2y, end_date=end_date,
                fields="ann_date,end_date,contract_liab,advance_receipts",
            )
            if bs is not None and not bs.empty:
                col = ("contract_liab"
                       if bs["contract_liab"].notna().sum() >= bs["advance_receipts"].notna().sum()
                       else "advance_receipts")
                quarters = extract_quarters(bs, col)
                if quarters:
                    qoq = quarters[-1].get("qoq_pct")
                    signal = (f"环比+{qoq:.0f}%，订单积累加速" if qoq and qoq > 0
                              else f"环比{qoq:.0f}%，需关注订单积累放缓" if qoq else "暂无环比数据")
                    result["contract_liab"] = {
                        "quarters": quarters,
                        "trend": trend_label(quarters),
                        "signal": signal,
                        "field": col,
                    }
        except Exception as e:
            _log(f"[{code}] contract_liab failed: {e}")

        # ── 存货周转天数 ─────────────────────────────────────────────
        try:
            fi = pro.fina_indicator(
                ts_code=ts_code, start_date=start_2y, end_date=end_date,
                fields="ann_date,end_date,inv_turn_days",
            )
            if fi is not None and not fi.empty:
                quarters = extract_quarters(fi, "inv_turn_days")
                if quarters:
                    qoq = quarters[-1].get("qoq_pct")
                    signal = ("周转天数缩短，出货加速（去库存/需求旺）" if qoq and qoq < -5
                              else "周转天数延长，关注需求疲软或主动备货" if qoq and qoq > 10
                              else "周转天数基本稳定")
                    result["inv_turn_days"] = {
                        "quarters": quarters,
                        "trend": trend_label(quarters, higher_is_better=False),
                        "signal": signal,
                    }
        except Exception as e:
            _log(f"[{code}] inv_turn_days failed: {e}")

        # ── 毛利率季度趋势（同比比较，用于Phase 2 Bridge）─────────────
        try:
            gp_fi = pro.fina_indicator(
                ts_code=ts_code, start_date=start_2y, end_date=end_date,
                fields="ann_date,end_date,grossprofit_margin",
            )
            if gp_fi is not None and not gp_fi.empty:
                quarters = extract_quarters(gp_fi, "grossprofit_margin")
                # 同比：当前季 vs 4个季度前
                yoy_improving = False
                if len(quarters) >= 5:
                    curr = quarters[-1].get("value_yi")
                    year_ago = quarters[-5].get("value_yi")
                    if curr and year_ago:
                        yoy_improving = bool(curr > year_ago)
                        yoy_chg = (curr - year_ago)
                        signal = (f"毛利率同比+{yoy_chg:.1f}ppt，盈利能力改善" if yoy_improving
                                  else f"毛利率同比{yoy_chg:.1f}ppt，需关注成本压力")
                    else:
                        signal = "同比数据不足"
                else:
                    yoy_improving = None
                    signal = "数据不足（<5个季度）"
                result["grossmargin_trend"] = {
                    "quarters": quarters[-6:],
                    "yoy_improving": yoy_improving,
                    "trend": trend_label(quarters),
                    "signal": signal,
                }
        except Exception as e:
            _log(f"[{code}] grossmargin_trend failed: {e}")

        # ── 机构持仓变化（Phase 2 Bridge：聪明钱是否在低位建仓）────────
        try:
            fh = pro.top10_floatholders(ts_code=ts_code,
                                        start_date=start_2y, end_date=end_date)
            if fh is not None and not fh.empty:
                fh = fh.sort_values(["end_date", "hold_ratio"], ascending=[True, False])
                periods = sorted(fh["end_date"].unique())
                inst_signal = "数据不足"
                if len(periods) >= 2:
                    # 比较最新期 vs 上期的前10大机构合计持仓比例
                    latest_ratio = fh[fh["end_date"]==periods[-1]]["hold_ratio"].astype(float).sum()
                    prev_ratio   = fh[fh["end_date"]==periods[-2]]["hold_ratio"].astype(float).sum()
                    chg = latest_ratio - prev_ratio
                    inst_signal = (f"机构合计持仓环比+{chg:.2f}%，聪明钱在增持" if chg > 0.5
                                   else f"机构合计持仓环比{chg:.2f}%，持仓稳定" if chg > -0.5
                                   else f"机构合计持仓环比{chg:.2f}%，有减持迹象")
                    inst_increasing = bool(chg > 0.3)
                else:
                    inst_increasing = None
                result["inst_holding"] = {
                    "latest_period": periods[-1] if periods else None,
                    "total_ratio_latest": float(fh[fh["end_date"]==periods[-1]]["hold_ratio"].astype(float).sum()) if periods else None,
                    "signal": inst_signal,
                    "increasing": inst_increasing,
                }
        except Exception as e:
            _log(f"[{code}] inst_holding failed: {e}")

        # ── 在建工程 + 资本开支 ──────────────────────────────────────
        expansion = {}
        try:
            bs2 = pro.balancesheet(
                ts_code=ts_code, start_date=start_2y, end_date=end_date,
                fields="ann_date,end_date,const_materials",
            )
            if bs2 is not None and not bs2.empty:
                quarters = extract_quarters(bs2, "const_materials")
                if quarters:
                    qoq = quarters[-1].get("qoq_pct")
                    signal = (f"在建工程环比+{qoq:.0f}%，产能扩张提速" if qoq and qoq > 20
                              else f"在建工程环比{qoq:.0f}%，扩张放缓" if qoq and qoq < 0
                              else "在建工程稳定增长")
                    expansion["const_in_progress"] = {
                        "quarters": quarters,
                        "trend": trend_label(quarters),
                        "signal": signal,
                    }
        except Exception as e:
            _log(f"[{code}] const_materials failed: {e}")

        try:
            cf = pro.cashflow(
                ts_code=ts_code, start_date=start_2y, end_date=end_date,
                fields="ann_date,end_date,c_pay_acq_const_fiolta",
            )
            if cf is not None and not cf.empty:
                quarters = extract_quarters(cf, "c_pay_acq_const_fiolta")
                if quarters:
                    qoq = quarters[-1].get("qoq_pct")
                    signal = (f"资本开支环比+{qoq:.0f}%，扩产决心强" if qoq and qoq > 30
                              else f"资本开支环比{qoq:.0f}%，扩张力度收缩" if qoq and qoq < 0
                              else "资本开支稳步增长")
                    expansion["capex"] = {
                        "quarters": quarters,
                        "trend": trend_label(quarters),
                        "signal": signal,
                    }
        except Exception as e:
            _log(f"[{code}] capex failed: {e}")

        if expansion:
            result["expansion"] = expansion

        _log(f"[{code}] Financial penetration: {[k for k in result if k != 'source']}")
        return result

    except Exception as e:
        _log(f"[{code}] Financial penetration failed: {e}")
        return {"source": "failed", "error": str(e)}


# ============================================================
# SECTION 3: Technical Indicator Calculations
# ============================================================

def _safe_float(val) -> float:
    """Safely convert to float."""
    if val is None:
        return None
    try:
        import math
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, 4)
    except (ValueError, TypeError):
        return None


def calc_ema(data: list, period: int) -> list:
    """Calculate Exponential Moving Average."""
    if not data or len(data) < period:
        return [None] * len(data)
    result = [None] * (period - 1)
    multiplier = 2.0 / (period + 1)
    # First EMA = SMA of first 'period' values
    sma = sum(data[:period]) / period
    result.append(sma)
    for i in range(period, len(data)):
        ema = (data[i] - result[-1]) * multiplier + result[-1]
        result.append(ema)
    return result


def calc_ma(closes: list, periods: list) -> dict:
    """Calculate Simple Moving Averages."""
    result = {}
    for p in periods:
        key = f"MA{p}"
        if len(closes) >= p:
            ma_val = sum(closes[-p:]) / p
            result[key] = round(ma_val, 4)
        else:
            result[key] = None

    # MA alignment status
    ma5 = result.get("MA5")
    ma10 = result.get("MA10")
    ma20 = result.get("MA20")

    if all(v is not None for v in [ma5, ma10, ma20]):
        if ma5 > ma10 > ma20:
            result["alignment"] = "bullish"
            spread = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0
            result["alignment_detail"] = "strong_bullish" if spread > 5 else "bullish"
        elif ma5 < ma10 < ma20:
            result["alignment"] = "bearish"
            spread = (ma20 - ma5) / ma20 * 100 if ma20 > 0 else 0
            result["alignment_detail"] = "strong_bearish" if spread > 5 else "bearish"
        elif ma5 > ma10 and ma10 <= ma20:
            result["alignment"] = "weak_bullish"
            result["alignment_detail"] = "weak_bullish"
        elif ma5 < ma10 and ma10 >= ma20:
            result["alignment"] = "weak_bearish"
            result["alignment_detail"] = "weak_bearish"
        else:
            result["alignment"] = "consolidation"
            result["alignment_detail"] = "consolidation"
    else:
        result["alignment"] = "insufficient_data"
        result["alignment_detail"] = "insufficient_data"

    return result


def calc_macd(closes: list, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """Calculate MACD: DIF, DEA, Histogram, and cross signals."""
    if len(closes) < slow + signal:
        return {"DIF": None, "DEA": None, "hist": None, "signal": "insufficient_data"}

    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)

    dif_list = []
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            dif_list.append(ema_fast[i] - ema_slow[i])
        else:
            dif_list.append(None)

    # DEA = EMA of DIF
    valid_dif = [d for d in dif_list if d is not None]
    if len(valid_dif) < signal:
        return {"DIF": None, "DEA": None, "hist": None, "signal": "insufficient_data"}

    dea_list = calc_ema(valid_dif, signal)

    # Current values
    curr_dif = valid_dif[-1] if valid_dif else None
    curr_dea = dea_list[-1] if dea_list else None
    prev_dif = valid_dif[-2] if len(valid_dif) >= 2 else None
    prev_dea = dea_list[-2] if len(dea_list) >= 2 else None

    hist = round((curr_dif - curr_dea) * 2, 4) if curr_dif is not None and curr_dea is not None else None

    # Cross signal detection
    macd_signal = "neutral"
    if all(v is not None for v in [curr_dif, curr_dea, prev_dif, prev_dea]):
        curr_diff = curr_dif - curr_dea
        prev_diff = prev_dif - prev_dea

        if prev_diff <= 0 and curr_diff > 0:
            macd_signal = "golden_cross_above_zero" if curr_dif > 0 else "golden_cross"
        elif prev_diff >= 0 and curr_diff < 0:
            macd_signal = "death_cross"
        elif curr_dif > 0 and curr_dea > 0:
            macd_signal = "bullish"
        elif curr_dif < 0 and curr_dea < 0:
            macd_signal = "bearish"

        # Zero axis cross
        if prev_dif is not None and curr_dif is not None:
            if prev_dif < 0 and curr_dif >= 0:
                macd_signal = "crossing_above_zero"
            elif prev_dif > 0 and curr_dif <= 0:
                macd_signal = "crossing_below_zero"

    return {
        "DIF": round(curr_dif, 4) if curr_dif is not None else None,
        "DEA": round(curr_dea, 4) if curr_dea is not None else None,
        "hist": hist,
        "signal": macd_signal,
    }


def calc_rsi(closes: list, periods: list) -> dict:
    """Calculate RSI using Wilder's method."""
    result = {}
    for period in periods:
        key = f"RSI{period}"
        if len(closes) < period + 1:
            result[key] = None
            continue

        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(0, d) for d in deltas]
        losses = [max(0, -d) for d in deltas]

        # First average
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Smoothed averages (Wilder's method)
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        result[key] = round(rsi, 2)

    # RSI zone
    rsi12 = result.get("RSI12")
    if rsi12 is not None:
        if rsi12 >= 80:
            result["zone"] = "overbought"
        elif rsi12 >= 60:
            result["zone"] = "strong"
        elif rsi12 >= 40:
            result["zone"] = "neutral"
        elif rsi12 >= 20:
            result["zone"] = "weak"
        else:
            result["zone"] = "oversold"
    else:
        result["zone"] = "unknown"

    return result


def calc_volume_analysis(volumes: list, closes: list) -> dict:
    """Analyze volume patterns."""
    if len(volumes) < 6 or len(closes) < 2:
        return {"vol_ratio": None, "trend": "insufficient_data"}

    # 5-day average volume (excluding today)
    avg_vol_5 = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else volumes[-1]
    curr_vol = volumes[-1]

    vol_ratio = round(curr_vol / avg_vol_5, 2) if avg_vol_5 > 0 else None

    # Price change direction
    price_up = closes[-1] >= closes[-2]

    # Volume trend classification
    if vol_ratio is None:
        trend = "unknown"
    elif vol_ratio >= 1.5 and price_up:
        trend = "heavy_volume_up"
    elif vol_ratio >= 1.5 and not price_up:
        trend = "heavy_volume_down"
    elif vol_ratio <= 0.7 and not price_up:
        trend = "shrink_pullback"
    elif vol_ratio <= 0.7 and price_up:
        trend = "shrink_up"
    else:
        trend = "normal"

    return {"vol_ratio": vol_ratio, "trend": trend}


def calc_amplitude_nd(ohlcv: list, n: int = 3) -> float:
    """Calculate N-day amplitude: (max_high - min_low) / min_low * 100."""
    if len(ohlcv) < n:
        return None
    recent = ohlcv[-n:]
    highs = [bar["high"] for bar in recent if bar["high"] is not None]
    lows = [bar["low"] for bar in recent if bar["low"] is not None]
    if not highs or not lows:
        return None
    min_l = min(lows)
    if min_l <= 0:
        return None
    return round((max(highs) - min_l) / min_l * 100, 2)


def calc_volume_heat(realtime: dict) -> dict:
    """
    Calculate volume-based market heat indicators for a stock.
    动力自重比 = 今日成交额 / 流通市值 (%) — how much of the float traded today.
    High ratio = real money is moving this stock, not just price drift.
    """
    if not realtime:
        return {}

    def to_yi(val):
        """Auto-detect unit and convert to 亿元 (same heuristic as fund flow)."""
        if val is None:
            return None
        v = float(val)
        if abs(v) > 1e7:
            return v / 1e8   # 元 → 亿元
        elif abs(v) > 1e3:
            return v / 1e4   # 万元 → 亿元
        return v

    result = {}
    amount_yi = to_yi(realtime.get("amount"))
    circ_mv_yi = to_yi(realtime.get("circ_mv"))
    turnover_rate = realtime.get("turnover_rate")
    volume_ratio = realtime.get("volume_ratio")

    if amount_yi and circ_mv_yi and circ_mv_yi > 0:
        pwr = round(amount_yi / circ_mv_yi * 100, 4)  # expressed as %
        result["power_weight_ratio"] = pwr
        if pwr > 5:
            result["heat_level"] = "极热（游资爆炒，信号不稳）"
            result["heat_score"] = 2  # extreme is risky, not bullish
        elif pwr > 2:
            result["heat_level"] = "高热（资金高度关注）"
            result["heat_score"] = 4
        elif pwr > 0.8:
            result["heat_level"] = "偏热（活跃）"
            result["heat_score"] = 3
        elif pwr > 0.3:
            result["heat_level"] = "正常"
            result["heat_score"] = 2
        else:
            result["heat_level"] = "冷清（无资金关注）"
            result["heat_score"] = 1

    if turnover_rate is not None:
        result["turnover_rate"] = turnover_rate
        if turnover_rate > 10:
            result["turnover_level"] = "极高 >10%"
        elif turnover_rate > 5:
            result["turnover_level"] = "高 5-10%"
        elif turnover_rate > 2:
            result["turnover_level"] = "正常 2-5%"
        elif turnover_rate > 0.5:
            result["turnover_level"] = "偏低 0.5-2%"
        else:
            result["turnover_level"] = "极低 <0.5%"

    if volume_ratio is not None:
        result["volume_ratio"] = volume_ratio
        if volume_ratio > 3:
            result["volume_ratio_level"] = "极大量 >3x"
        elif volume_ratio > 1.5:
            result["volume_ratio_level"] = "放量 1.5-3x"
        elif volume_ratio > 0.8:
            result["volume_ratio_level"] = "正常量"
        else:
            result["volume_ratio_level"] = "缩量 <0.8x"

    return result


def calc_fund_flow_ratios(capital_flow: dict, fund_flow_multiday: dict, realtime: dict) -> dict:
    """
    资金流的跨字段比值分析——全部为展示用途，不参与trend_score打分，
    不构成新的买卖判断依据（2026-07-13新增，research_log.md有对应条目）。

    - fund_flow_intensity: 资金净流入 / 流通市值，归一化后可跨股票比较
      （对比 calc_volume_heat 的 power_weight_ratio 只归一化了成交额，没归一化资金流本身）
    - price_flow_divergence: 涨跌幅方向 与 主力资金方向 是否一致
    """
    result = {}

    def to_yi(val):
        if val is None:
            return None
        v = float(val)
        if abs(v) > 1e7:
            return v / 1e8
        elif abs(v) > 1e3:
            return v / 1e4
        return v

    main_net_yi = (capital_flow or {}).get("fund_flow", {}).get("main_force_net_yi")
    circ_mv_yi = to_yi((realtime or {}).get("circ_mv"))

    if main_net_yi is not None and circ_mv_yi and circ_mv_yi > 0:
        intensity = round(main_net_yi / circ_mv_yi * 100, 4)
        result["fund_flow_intensity"] = intensity
        if intensity > 3:
            result["fund_flow_intensity_level"] = "极强净流入（相对流通市值）"
        elif intensity > 1:
            result["fund_flow_intensity_level"] = "明显净流入"
        elif intensity > -1:
            result["fund_flow_intensity_level"] = "中性"
        elif intensity > -3:
            result["fund_flow_intensity_level"] = "明显净流出"
        else:
            result["fund_flow_intensity_level"] = "极强净流出（相对流通市值）"

    change_pct = (realtime or {}).get("change_pct")
    if change_pct is not None and main_net_yi is not None:
        if change_pct > 0 and main_net_yi < 0:
            result["price_flow_divergence"] = "涨但主力流出（缺乏真实承接，警惕对倒拉升）"
        elif change_pct < 0 and main_net_yi > 0:
            result["price_flow_divergence"] = "跌但主力流入（可能是逢跌吸筹）"
        elif change_pct > 0 and main_net_yi > 0:
            result["price_flow_divergence"] = "涨且主力流入（一致，健康）"
        elif change_pct < 0 and main_net_yi < 0:
            result["price_flow_divergence"] = "跌且主力流出（一致，无异常）"

    return result


def calc_chip_concentration(ohlcv: list, current_price: float) -> dict:
    """
    Simplified chip concentration analysis using volume profile.
    Approximates where "chips" (floating shares) sit relative to current price.

    This is a heuristic using volume-weighted average price over recent windows.
    True chip distribution requires tick-level data — this is an approximation.

    Key outputs:
    - in_profit_vol_pct: % of recent 60-bar volume traded at prices <= current price
      (higher = more chip holders are in profit = less trapped-long selling pressure)
    - chip_activity: recent 5d avg volume / 20d avg volume
      (< 0.8 = chips locking up / 少量换手 = holder conviction)
    - vwap_20d / vwap_60d: cost center at two windows
    """
    if not ohlcv or len(ohlcv) < 20 or not current_price:
        return {"source": "insufficient_data"}

    def calc_vwap(bars):
        total_value, total_vol = 0.0, 0.0
        for b in bars:
            if b.get("volume") and b.get("high") and b.get("low") and b.get("close"):
                typical = (b["high"] + b["low"] + b["close"]) / 3
                total_value += typical * b["volume"]
                total_vol += b["volume"]
        return round(total_value / total_vol, 4) if total_vol > 0 else None

    recent_20 = ohlcv[-20:]
    recent_60 = ohlcv[-60:] if len(ohlcv) >= 60 else ohlcv
    vwap_20 = calc_vwap(recent_20)
    vwap_60 = calc_vwap(recent_60)

    # % of 60-bar volume traded at or below current price (proxy for "in profit")
    profitable_vol = sum(
        b["volume"] for b in recent_60
        if b.get("close") and b.get("volume") and b["close"] <= current_price
    )
    total_vol_60 = sum(b["volume"] for b in recent_60 if b.get("volume"))
    in_profit_vol_pct = round(profitable_vol / total_vol_60 * 100, 1) if total_vol_60 > 0 else None

    # Chip activity: recent 5d avg vs 20d avg volume
    chip_activity = None
    vol_5d = [b["volume"] for b in ohlcv[-5:] if b.get("volume")]
    vol_20d = [b["volume"] for b in ohlcv[-20:] if b.get("volume")]
    if vol_5d and vol_20d:
        avg_5d = sum(vol_5d) / len(vol_5d)
        avg_20d = sum(vol_20d) / len(vol_20d)
        if avg_20d > 0:
            chip_activity = round(avg_5d / avg_20d, 2)

    # VWAP position
    vwap_position = None
    if vwap_20 and vwap_20 > 0:
        diff_pct = (current_price - vwap_20) / vwap_20 * 100
        if diff_pct > 5:
            vwap_position = f"高于20日VWAP+{diff_pct:.1f}%（强势）"
        elif diff_pct > 0:
            vwap_position = f"高于20日VWAP+{diff_pct:.1f}%（偏强）"
        elif diff_pct > -5:
            vwap_position = f"接近20日VWAP（{diff_pct:.1f}%）"
        else:
            vwap_position = f"低于20日VWAP{diff_pct:.1f}%（偏弱）"

    # Qualitative interpretation
    interpretation = "中性"
    if in_profit_vol_pct is not None and chip_activity is not None:
        if in_profit_vol_pct >= 70 and chip_activity <= 0.8:
            interpretation = "筹码集中锁仓，升势健康"
        elif in_profit_vol_pct >= 70 and chip_activity >= 1.3:
            interpretation = "高位活跃换手，获利盘松动——关注出货风险"
        elif in_profit_vol_pct >= 55:
            interpretation = "多数筹码获利，结构尚可"
        elif in_profit_vol_pct >= 40:
            interpretation = "约半数筹码套牢，上方存在解套压力"
        else:
            interpretation = "套牢盘比例高，解套抛压明显"

    return {
        "vwap_20d": vwap_20,
        "vwap_60d": vwap_60,
        "in_profit_vol_pct": in_profit_vol_pct,
        "chip_activity": chip_activity,
        "vwap_position": vwap_position,
        "interpretation": interpretation,
        "source": "vwap_approximation",
    }


def calc_momentum_signal(ohlcv: list, chip_data: dict) -> dict:
    """
    Pool 2 动能信号检查清单（3条件）。
    独立于基本面5维度评分，用于捕捉政策/资金驱动的短期动能行情。
    时间框架：5-15个交易日。
    """
    if not ohlcv or len(ohlcv) < 25:
        return {"available": False, "reason": "K线数据不足25日"}

    closes = [b["close"] for b in ohlcv if b.get("close")]
    amounts = [b.get("amount", 0) or 0 for b in ohlcv]

    # ── 条件1: 量能异常 ──────────────────────────────────────────────────────────
    today_amount = amounts[-1]
    recent_20_amounts = [a for a in amounts[-21:-1] if a > 0]
    if recent_20_amounts and today_amount > 0:
        avg_20d = sum(recent_20_amounts) / len(recent_20_amounts)
        vol_ratio_20d = round(today_amount / avg_20d, 2) if avg_20d > 0 else 0.0
    else:
        vol_ratio_20d = 0.0
    cond1_met = vol_ratio_20d >= 2.0

    # ── 条件2: MA60水上且倾角向上 ────────────────────────────────────────────────
    above_ma60 = None
    ma60_slope_pct = None
    cond2_met = False
    if len(closes) >= 65:
        ma60_today = sum(closes[-60:]) / 60
        ma60_5d_ago = sum(closes[-65:-5]) / 60
        above_ma60 = closes[-1] > ma60_today
        ma60_slope_pct = round((ma60_today - ma60_5d_ago) / ma60_5d_ago * 100, 3) if ma60_5d_ago > 0 else 0.0
        cond2_met = above_ma60 and ma60_slope_pct > 0.15
    elif len(closes) >= 60:
        ma60_today = sum(closes[-60:]) / 60
        above_ma60 = closes[-1] > ma60_today

    # ── 条件3: 筹码锁定 ──────────────────────────────────────────────────────────
    chip_activity = (chip_data or {}).get("chip_activity")
    cond3_met = chip_activity is not None and chip_activity < 0.8

    # ── 信号强度 ─────────────────────────────────────────────────────────────────
    n_met = sum([cond1_met, cond2_met, cond3_met])
    strength_map = {
        3: ("强",   "⚡⚡⚡", "5-10个交易日"),
        2: ("中",   "⚡⚡",   "3-7个交易日"),
        1: ("弱",   "⚡",    None),
        0: ("无信号", "—",   None),
    }
    strength, emoji, window = strength_map[n_met]

    def _cond1_desc():
        return f"今日成交额/20日均 = {vol_ratio_20d:.2f}x（阈值≥2.0）"

    def _cond2_desc():
        if above_ma60 is None:
            return "数据不足"
        pos = "水上" if above_ma60 else "水下"
        if ma60_slope_pct is not None:
            return f"MA60{pos}，倾角{ma60_slope_pct:+.3f}%/5日（阈值>+0.15%）"
        return f"MA60{pos}（斜率数据不足，保守判为未满足）"

    def _cond3_desc():
        if chip_activity is None:
            return "数据不足"
        return f"筹码活跃度={chip_activity:.2f}（近5日/20日均量比，阈值<0.8）"

    return {
        "available": True,
        "signal_strength": strength,
        "signal_emoji": emoji,
        "conditions_met": n_met,
        "holding_window": window,
        "cond1_vol_anomaly": {
            "met": cond1_met,
            "vol_ratio_20d": vol_ratio_20d,
            "description": _cond1_desc(),
        },
        "cond2_ma60": {
            "met": cond2_met,
            "above_ma60": above_ma60,
            "ma60_slope_pct_5d": ma60_slope_pct,
            "description": _cond2_desc(),
        },
        "cond3_chip_lock": {
            "met": cond3_met,
            "chip_activity": chip_activity,
            "description": _cond3_desc(),
        },
    }


def calc_bias(closes: list, ma_data: dict) -> dict:
    """Calculate bias ratio (乖离率)."""
    if not closes:
        return {}
    curr = closes[-1]
    result = {}
    for key in ["MA5", "MA10", "MA20"]:
        ma_val = ma_data.get(key)
        if ma_val and ma_val > 0:
            bias = round((curr - ma_val) / ma_val * 100, 2)
            result[f"bias_{key.lower()}"] = bias
    return result


def calc_support(closes: list, ma_data: dict) -> dict:
    """Check if price is supported by MA lines."""
    if not closes:
        return {"support_ma5": False, "support_ma10": False}
    curr = closes[-1]
    ma5 = ma_data.get("MA5")
    ma10 = ma_data.get("MA10")

    support_ma5 = False
    support_ma10 = False

    if ma5 and curr > 0:
        # Price within 1% of MA5
        support_ma5 = abs(curr - ma5) / curr * 100 <= 1.0
    if ma10 and curr > 0:
        support_ma10 = abs(curr - ma10) / curr * 100 <= 1.5

    return {"support_ma5": support_ma5, "support_ma10": support_ma10}


# ============================================================
# SECTION 4: Composite Trend Scoring (100 points)
# ============================================================

def calc_trend_score(ma_data: dict, macd_data: dict, rsi_data: dict,
                     vol_data: dict, bias_data: dict, support_data: dict) -> dict:
    """
    Composite scoring system (100 points total):
    - Trend/MA alignment: 30 pts
    - Bias (乖离率): 20 pts
    - Volume: 15 pts
    - MACD: 15 pts
    - RSI: 10 pts
    - Support: 10 pts
    """
    breakdown = {}

    # 1. Trend score (30 pts)
    alignment = ma_data.get("alignment_detail", "consolidation")
    trend_scores = {
        "strong_bullish": 30, "bullish": 26, "weak_bullish": 18,
        "consolidation": 12, "weak_bearish": 8, "bearish": 4,
        "strong_bearish": 0, "insufficient_data": 12,
    }
    breakdown["trend"] = trend_scores.get(alignment, 12)

    # 2. Bias score (20 pts) - prefer slightly below MA5
    # Note: in momentum/quant markets, strong stocks naturally have high bias.
    # Moderate positive bias (5-15%) is a momentum characteristic, not purely a risk.
    bias_ma5 = bias_data.get("bias_ma5", 0)
    if bias_ma5 is None:
        breakdown["bias"] = 10
    elif -3 <= bias_ma5 < 0:
        breakdown["bias"] = 20  # Slightly below MA5 = ideal dip
    elif 0 <= bias_ma5 < 2:
        breakdown["bias"] = 18  # Close to MA5
    elif 2 <= bias_ma5 < 5:
        breakdown["bias"] = 14  # Slightly above
    elif 5 <= bias_ma5 < 15:
        breakdown["bias"] = 10  # Momentum stock — not punished harshly (was 4)
    elif bias_ma5 >= 15:
        breakdown["bias"] = 6   # Strong momentum, check volume before chasing
    elif -5 <= bias_ma5 < -3:
        breakdown["bias"] = 14  # Pulling back more
    else:
        breakdown["bias"] = 6   # Far below MA5

    # 3. Volume score (15 pts)
    vol_trend = vol_data.get("trend", "normal")
    vol_scores = {
        "shrink_pullback": 15, "heavy_volume_up": 12, "normal": 10,
        "shrink_up": 6, "heavy_volume_down": 0, "insufficient_data": 8,
        "unknown": 8,
    }
    breakdown["volume"] = vol_scores.get(vol_trend, 8)

    # 4. MACD score (15 pts)
    macd_signal = macd_data.get("signal", "neutral")
    macd_scores = {
        "golden_cross_above_zero": 15, "crossing_above_zero": 13,
        "golden_cross": 12, "bullish": 10, "neutral": 7,
        "bearish": 3, "death_cross": 0, "crossing_below_zero": 1,
        "insufficient_data": 7,
    }
    breakdown["macd"] = macd_scores.get(macd_signal, 7)

    # 5. RSI score (10 pts)
    rsi_zone = rsi_data.get("zone", "neutral")
    rsi_scores = {
        "oversold": 10, "strong": 8, "neutral": 5,
        "weak": 3, "overbought": 0, "unknown": 5,
    }
    breakdown["rsi"] = rsi_scores.get(rsi_zone, 5)

    # 6. Support score (10 pts)
    sup_score = 0
    if support_data.get("support_ma5"):
        sup_score += 5
    if support_data.get("support_ma10"):
        sup_score += 5
    breakdown["support"] = sup_score

    total = sum(breakdown.values())

    # Signal generation
    alignment_val = ma_data.get("alignment", "consolidation")
    bullish_alignments = ["bullish", "strong_bullish", "weak_bullish"]

    if total >= 75 and alignment_val in ["bullish", "strong_bullish"]:
        signal = "strong_buy"
    elif total >= 60 and alignment_val in bullish_alignments:
        signal = "buy"
    elif total >= 45:
        signal = "hold"
    elif total >= 30:
        signal = "wait"
    elif alignment_val in ["bearish", "strong_bearish"]:
        signal = "strong_sell"
    else:
        signal = "sell"

    signal_cn = {
        "strong_buy": "强烈买入", "buy": "买入", "hold": "持有",
        "wait": "观望", "sell": "卖出", "strong_sell": "强烈卖出",
    }

    return {
        "total": total,
        "breakdown": breakdown,
        "signal": signal,
        "signal_cn": signal_cn.get(signal, signal),
    }


# ============================================================
# SECTION 5: Main Orchestrator
# ============================================================

def analyze_stock(code: str, days: int = 120, fetch_news: bool = False) -> dict:
    """Full analysis pipeline for a single stock."""
    market, normalized, display = classify_stock(code)

    if market == "unknown":
        raise ValueError(f"Cannot classify stock code: {code}")

    # Fetch data with graceful degradation
    if market == "cn_a":
        raw = fetch_cn_a(normalized, days)
    elif market == "cn_hk":
        raw = fetch_hk(normalized, days)
    else:
        raw = fetch_us(normalized, days)

    ohlcv = raw["ohlcv"]
    if not ohlcv or len(ohlcv) < 10:
        raise ValueError(f"Insufficient data for {code}: only {len(ohlcv)} bars")

    closes = [bar["close"] for bar in ohlcv if bar["close"] is not None]
    volumes = [bar["volume"] for bar in ohlcv if bar["volume"] is not None]

    if len(closes) < 10:
        raise ValueError(f"Insufficient valid close prices for {code}")

    # Calculate all indicators
    ma = calc_ma(closes, [5, 10, 20, 60])
    macd = calc_macd(closes)
    rsi = calc_rsi(closes, [6, 12, 24])
    vol = calc_volume_analysis(volumes, closes)
    bias = calc_bias(closes, ma)
    support = calc_support(closes, ma)
    score = calc_trend_score(ma, macd, rsi, vol, bias, support)

    # News search (optional)
    news = []
    if fetch_news:
        stock_name = raw.get("name", display)
        news = search_news(stock_name, display)

    # Fundamental data + capital flow (A-share only)
    fundamentals = {}
    capital_flow = {}
    fund_flow_multiday = {}
    financial_penetration = {}
    sector = "未知"
    sector_breadth = {}
    etf_fund_flow = {}
    chip_dist_real = {}
    if market == "cn_a":
        fundamentals = fetch_tushare_fundamentals(normalized)
        capital_flow["fund_flow"] = fetch_stock_fund_flow(normalized)
        capital_flow["dragon_tiger"] = fetch_dragon_tiger(normalized)
        capital_flow["pledge"] = fetch_pledge_ratio(normalized)
        fund_flow_multiday = fetch_fund_flow_multiday(normalized)
        sector = fetch_stock_sector(normalized)
        sector_breadth = fetch_sector_breadth(sector)
        etf_fund_flow = fetch_etf_fund_flow(sector)
        financial_penetration = fetch_financial_penetration(normalized)
        chip_dist_real = fetch_tushare_chip_dist(normalized)

    # If realtime is empty (e.g. market closed on weekends), derive basics from last OHLCV bar.
    # This ensures price and change_pct are always available for downstream analysis.
    realtime = raw.get("realtime", {})
    if not realtime.get("price") and ohlcv:
        last_bar = ohlcv[-1]
        _log(f"[{code}] Realtime unavailable — using last OHLCV bar ({last_bar.get('date', '?')})")
        realtime = {
            "price": last_bar.get("close"),
            "change_pct": last_bar.get("pct_chg"),
            "note": f"derived_from_ohlcv_{last_bar.get('date', 'unknown')}",
        }
        raw["realtime"] = realtime

    # Amplitude metrics
    amplitude_3d = calc_amplitude_nd(ohlcv, 3)
    amplitude_5d = calc_amplitude_nd(ohlcv, 5)

    # Dimension 2 sub-indicator A: high_baseline (max high excluding last 20 bars)
    # Excludes the most recent 20 trading days to avoid false "new high" signals.
    all_highs = [bar["high"] for bar in ohlcv if bar["high"] is not None]
    if len(all_highs) >= 60:
        baseline_highs = all_highs[:-20]  # strip last 20 bars
        high_baseline = round(max(baseline_highs), 4) if baseline_highs else None
    else:
        high_baseline = None  # insufficient history → score_a = 5 (neutral)

    # Volume heat (动力自重比 + 换手率 + 量比)
    volume_heat = calc_volume_heat(raw.get("realtime", {}))

    # Chip concentration: VWAP approximation, overridden by Tushare cyq_perf if available
    current_price = raw.get("realtime", {}).get("price") or (ohlcv[-1]["close"] if ohlcv else None)
    chip_concentration = calc_chip_concentration(ohlcv, current_price)
    if chip_dist_real.get("source") == "tushare_cyq_perf":
        # Override VWAP estimate fields with real Tushare data
        chip_concentration.update({
            "in_profit_vol_pct": chip_dist_real.get("winner_rate"),   # real 获利盘%
            "in_profit_chg_5d":  chip_dist_real.get("winner_rate_chg_5d"),   # 展示用：变化速度而非静态值
            "in_profit_trend":   chip_dist_real.get("winner_rate_trend"),
            "weight_avg_real":   chip_dist_real.get("weight_avg"),    # 加权平均成本
            "cost_50pct":        chip_dist_real.get("cost_50pct"),    # 中位成本
            "cost_85pct":        chip_dist_real.get("cost_85pct"),    # 85%成本（上方压力参考）
            "cost_15pct":        chip_dist_real.get("cost_15pct"),    # 15%成本（支撑参考）
            "interpretation":    chip_dist_real.get("interpretation"),
            "source":            "tushare_cyq_perf+vwap",
        })

    # Pool 2 momentum signal (动能视角检查清单)
    momentum_signal = calc_momentum_signal(ohlcv, chip_concentration)

    # 资金流比值分析（展示用，不参与打分，2026-07-13新增）
    fund_flow_ratios = calc_fund_flow_ratios(capital_flow, fund_flow_multiday, realtime)

    result = {
        "code": display,
        "market": market,
        "name": raw.get("name", display),
        "sector": sector,
        "data_source": raw.get("source", "unknown"),
        "realtime": raw.get("realtime", {}),
        "fundamentals": fundamentals,
        "indicators": {
            "ma": ma,
            "macd": macd,
            "rsi": rsi,
            "volume": vol,
            "bias": bias,
            "support": support,
        },
        "trend_score": score,
        "amplitude_3d": amplitude_3d,
        "amplitude_5d": amplitude_5d,
        "capital_flow": capital_flow,
        "fund_flow_multiday": fund_flow_multiday,
        "sector_breadth": sector_breadth,
        "etf_fund_flow": etf_fund_flow,
        "financial_penetration": financial_penetration,
        "high_baseline": high_baseline,
        "volume_heat": volume_heat,
        "chip_concentration": chip_concentration,
        "momentum_signal": momentum_signal,
        "fund_flow_ratios": fund_flow_ratios,
        "recent_bars": ohlcv[-10:],
        "total_bars": len(ohlcv),
        "fetch_time": datetime.now().isoformat(),
    }
    if news:
        result["news"] = news
    return result


def main():
    parser = argparse.ArgumentParser(description="Stock Data Fetcher")
    parser.add_argument("--stocks", required=True, help="Comma-separated stock codes")
    parser.add_argument("--days", type=int, default=120, help="History trading days")
    parser.add_argument("--news", action="store_true", help="Also search news (requires TAVILY_API_KEY or SERPAPI_KEY)")
    args = parser.parse_args()

    codes = [c.strip() for c in args.stocks.split(",") if c.strip()]
    results = []
    errors = []

    # Report available data sources
    sources_status = {}
    for lib in ["tushare", "efinance", "akshare", "yfinance"]:
        sources_status[lib] = "available" if _check_source(lib) else "not installed"
    sources_status["tushare_token"] = "configured" if os.environ.get("TUSHARE_TOKEN") else "not set (add to repo/.env)"
    sources_status["tavily_api"] = "configured" if os.environ.get("TAVILY_API_KEY") else "not set"
    sources_status["serpapi"] = "configured" if os.environ.get("SERPAPI_KEY") else "not set"
    _log(f"Data sources: {json.dumps(sources_status)}")

    # Fetch market environment once (STEP 0 data — northbound flow + breadth divergence)
    _log("Fetching market environment data (northbound capital + breadth)...")
    market_env = {
        "north_bound": fetch_north_bound_flow_20d(),
        "breadth": fetch_market_breadth(),
    }
    _log(f"Market env: northbound direction={market_env['north_bound'].get('direction')}, "
         f"20d_net={market_env['north_bound'].get('net_flow_20d')}亿, "
         f"breadth_regime={market_env['breadth'].get('regime_label')}")

    for code in codes:
        try:
            result = analyze_stock(code, args.days, fetch_news=args.news)
            results.append(result)
        except Exception as e:
            errors.append({"code": code, "error": str(e), "type": type(e).__name__})

    output = {
        "analysis_date": datetime.now().strftime("%Y-%m-%d"),
        "analysis_time": datetime.now().strftime("%H:%M:%S"),
        "data_sources": sources_status,
        "market_environment": market_env,
        "stocks": results,
        "errors": errors,
        "total_requested": len(codes),
        "total_success": len(results),
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


# ── 以下两个函数供 market_state_fetcher.py 调用 ──────────────────────────

def fetch_margin_trend(pro=None) -> dict:
    """拉取沪深两市融资余额趋势（近20个自然日）。

    Tushare ``margin`` 返回交易所汇总数据，余额字段为 ``rzye``（元）。
    同一交易日可能同时包含上交所和深交所记录，必须按交易日求和后再比较；
    不能把 ``margin_detail`` 的逐股票记录直接当成时间序列。
    """
    try:
        pro = pro or _get_tushare_pro()
        df = pro.margin(
            start_date=(datetime.now() - timedelta(days=30)).strftime("%Y%m%d"),
            end_date=datetime.now().strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return {"direction": "unknown", "consecutive_days": 0,
                    "latest_balance_yi": None, "source": "no_data"}
        if "trade_date" not in df.columns or "rzye" not in df.columns:
            return {"direction": "unknown", "consecutive_days": 0,
                    "latest_balance_yi": None, "source": "missing_fields"}
        daily = (
            df.assign(rzye=df["rzye"].astype(float))
            .groupby("trade_date", as_index=False)["rzye"].sum()
            .sort_values("trade_date")
        )
        balances = daily["rzye"].tolist()
        if len(balances) < 5:
            return {"direction": "unknown", "consecutive_days": 0,
                    "latest_balance_yi": None, "source": "too_few"}
        # 方向判断：近5日均值 vs 前5日均值
        recent = sum(balances[-5:]) / 5
        prev = sum(balances[-10:-5]) / 5 if len(balances) >= 10 else recent
        if recent > prev * 1.005:
            direction = "up"
        elif recent < prev * 0.995:
            direction = "down"
        else:
            direction = "flat"
        # 连续同向天数
        consecutive = 0
        for i in range(len(balances) - 1, 0, -1):
            if direction == "up" and balances[i] > balances[i-1]:
                consecutive += 1
            elif direction == "down" and balances[i] < balances[i-1]:
                consecutive += 1
            else:
                break
        return {
            "direction": direction,
            "consecutive_days": consecutive,
            "latest_balance_yi": round(balances[-1] / 1e8, 2),
            "latest_date": str(daily["trade_date"].iloc[-1]),
            "source": "tushare_margin",
        }
    except Exception as e:
        return {"direction": "unknown", "consecutive_days": 0,
                "historical_percentile_6y": None, "source": f"error:{e}"}


def fetch_market_moneyflow_dc(pro=None) -> dict:
    """汇总全市场大单/小单资金流代理（最近可用交易日）。

    使用 Tushare ``moneyflow`` 的逐股票L2订单数据。大单+特大单只能作为
    机构/主力的代理，小单只能作为散户代理，输出中不得把代理写成真实身份。
    金额字段单位为万元，汇总后除以10000换算为亿元。
    """
    try:
        pro = pro or _get_tushare_pro()
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
        df = pro.moneyflow(start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return {"pattern": "unknown", "source": "no_data"}
        required = {
            "trade_date", "buy_sm_amount", "sell_sm_amount",
            "buy_lg_amount", "sell_lg_amount",
            "buy_elg_amount", "sell_elg_amount", "net_mf_amount",
        }
        if not required.issubset(df.columns):
            return {"pattern": "unknown", "source": "missing_fields"}
        latest_date = str(df["trade_date"].astype(str).max())
        latest = df[df["trade_date"].astype(str) == latest_date].copy()
        for col in required - {"trade_date"}:
            latest[col] = latest[col].astype(float)
        large_net_wan = (
            latest["buy_lg_amount"].sum() + latest["buy_elg_amount"].sum()
            - latest["sell_lg_amount"].sum() - latest["sell_elg_amount"].sum()
        )
        small_net_wan = (
            latest["buy_sm_amount"].sum() - latest["sell_sm_amount"].sum()
        )
        if large_net_wan > 0 and small_net_wan < 0:
            pattern = "large_order_inflow_small_order_outflow"
        elif large_net_wan < 0 and small_net_wan > 0:
            pattern = "large_order_outflow_small_order_inflow"
        elif large_net_wan > 0 and small_net_wan > 0:
            pattern = "broad_inflow"
        elif large_net_wan < 0 and small_net_wan < 0:
            pattern = "broad_outflow"
        else:
            pattern = "mixed_flat"
        return {
            "pattern": pattern,
            "latest_date": latest_date,
            "large_order_net_yi": round(large_net_wan / 10000, 2),
            "small_order_net_yi": round(small_net_wan / 10000, 2),
            "market_net_yi": round(latest["net_mf_amount"].sum() / 10000, 2),
            "identity_caveat": "大单/特大单与小单仅为订单规模代理，不代表可识别的机构/散户身份",
            "source": "tushare_moneyflow",
        }
    except Exception as e:
        return {"pattern": "unknown", "source": f"error:{e}"}


def _get_tushare_pro():
    """获取 tushare pro 对象，复用 token 获取逻辑。"""
    import tushare as ts
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        workspace_root = os.path.abspath(os.path.expanduser(
            os.environ.get("TZ_CODEX_HOME", "~/Desktop/tz-codex")
        ))
        env_path = os.path.join(workspace_root, "repo", ".env")
        if os.path.exists(env_path):
            for line in open(env_path):
                if "TUSHARE_TOKEN" in line:
                    token = line.split("=", 1)[-1].strip()
    if not token:
        tp = os.path.expanduser("~/.tushare_token")
        if os.path.exists(tp):
            token = open(tp).read().strip()
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未配置；请写入 repo/.env 或环境变量")
    return ts.pro_api(token)


if __name__ == "__main__":
    main()
