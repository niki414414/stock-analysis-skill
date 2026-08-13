import importlib.util
from pathlib import Path

import pandas as pd


PATH = Path(__file__).parents[1] / "skills/market-outlook/scripts/ma5_short_backtest.py"
SPEC = importlib.util.spec_from_file_location("ma5_backtest", PATH)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def synthetic_bars():
    rows, previous = [], 10.0
    for i in range(25):
        close = previous + 0.05
        if i == 17:
            close = MOD.SCANNER.china_price_limit(previous)
        open_price = previous
        rows.append({
            "ts_code": "600001.SH", "trade_date": f"202607{i+1:02d}",
            "open": open_price, "high": max(open_price, close) * 1.01,
            "low": min(open_price, close) * 0.99, "close": close,
            "pre_close": previous, "vol": 1000, "amount": 100000,
            "adj_factor": 1.0,
        })
        previous = close
    return pd.DataFrame(rows)


def test_signal_is_confirmed_before_entry_day():
    rows = MOD.simulate_stock(
        synthetic_bars(), "20260719", "20260722",
        gap_low=-0.02, gap_high=0.02, threshold_premium=0.05,
    )
    assert rows
    first = rows[0]
    assert first["entry_date"] > first["signal_date"]
    assert "return_1d_pct" in first


def test_summary_separates_signals_from_executable_entries():
    frame = pd.DataFrame([
        {"executable": False},
        {"executable": True, "return_1d_pct": 2.0, "return_2d_pct": -1.0,
         "mfe_1d_pct": 3.0, "mae_1d_pct": -1.0, "mfe_2d_pct": 4.0, "mae_2d_pct": -2.0},
    ])
    result = MOD.summarize(frame)
    assert result["signals"] == 2
    assert result["executed"] == 1
    assert result["holding_1d"]["win_rate"] == 1.0


def test_market_activity_uses_liquidity_and_limit_activity_not_breadth():
    rows = []
    for day in range(1, 22):
        date = f"202607{day:02d}"
        for i in range(100):
            ret = 0.10 if day == 21 and i < 60 else 0.01
            rows.append({
                "trade_date": date, "amount": 1000, "pre_close": 10,
                "close": 10 * (1 + ret),
            })
    result = MOD.compute_market_activity(pd.DataFrame(rows))
    assert result.iloc[-1]["market_activity"] == "active"
    assert result.iloc[-1]["limit_up_count"] == 60
