"""Price adjustment shared by the stock analysis and market brief consumers."""
from __future__ import annotations

import numpy as np
import pandas as pd


def adjust_to_latest(bars: pd.DataFrame, factors: pd.DataFrame) -> pd.DataFrame:
    """Adjust OHLC to the last supplied bar; keep actual quotes in raw_*.

    Never forward-fill missing factors or anchor a historical run to a future
    factor. Volume, amount and provider daily returns retain their own units.
    """
    if bars is None or bars.empty or factors is None or factors.empty:
        raise ValueError("行情或复权因子为空，不能生成可用价格结构")
    prices = bars.copy().sort_values("trade_date").drop_duplicates("trade_date")
    adj = factors.copy()
    prices["trade_date"] = prices["trade_date"].astype(str)
    adj["trade_date"] = adj["trade_date"].astype(str)
    if "ts_code" in prices and prices["ts_code"].nunique() != 1:
        raise ValueError("复权计算一次只接受一只股票")
    keys = ["trade_date"]
    if "ts_code" in prices and "ts_code" in adj:
        keys.insert(0, "ts_code")
    prices = prices.drop(columns=["adj_factor"], errors="ignore").merge(
        adj[keys + ["adj_factor"]], on=keys, how="left", validate="one_to_one"
    ).sort_values("trade_date").reset_index(drop=True)
    factor = pd.to_numeric(prices["adj_factor"], errors="coerce")
    if not (np.isfinite(factor) & factor.gt(0)).all():
        raise ValueError("复权因子缺失或无效，不能退回未复权价格")
    ratio = factor / factor.iloc[-1]
    for column in ("open", "high", "low", "close"):
        raw = pd.to_numeric(prices[column], errors="coerce")
        if not (np.isfinite(raw) & raw.gt(0)).all():
            raise ValueError(f"{column}存在无效价格")
        prices[f"raw_{column}"] = raw
        prices[column] = raw * ratio
    prices["price_basis"] = "qfq_latest_bar"
    return prices
