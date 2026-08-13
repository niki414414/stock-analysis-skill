import importlib.util
from pathlib import Path

import pandas as pd


PATH = Path(__file__).parents[1] / "skills/market-outlook/scripts/limit_up_trend_scanner.py"
SPEC = importlib.util.spec_from_file_location("limit_up_trend_scanner", PATH)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def make_bars(closes, limit_index=14):
    rows = []
    previous = 10.0
    for i, close in enumerate(closes):
        if i == limit_index:
            close = MOD.china_price_limit(previous)
        elif i > limit_index:
            close = previous + 0.05
        rows.append({
            "ts_code": "600001.SH", "trade_date": f"202607{i + 1:02d}",
            "open": previous, "high": close, "low": min(previous, close),
            "close": close, "pre_close": previous, "vol": 1000 + i * 10,
            "amount": 100000, "adj_factor": 1.0,
        })
        previous = close
    return pd.DataFrame(rows)


def test_main_board_and_st_filters():
    assert MOD.is_main_board_code("600000.SH")
    assert MOD.is_main_board_code("002001.SZ")
    assert not MOD.is_main_board_code("300001.SZ")
    assert not MOD.is_main_board_code("688001.SH")
    assert not MOD.is_main_board_code("830001.BJ")
    row = pd.Series({"ts_code": "600001.SH", "name": "*ST样本", "list_date": "20200101"})
    assert not MOD.eligible_stock(row, "20260804")


def test_limit_price_uses_exchange_rounding():
    assert MOD.china_price_limit(10.01) == 11.01
    assert MOD.is_limit_up(11.01, 10.01)
    assert not MOD.is_limit_up(11.00, 10.01)


def test_stable_and_rising_signals_require_recent_limit_up():
    closes = [10 + i * 0.05 for i in range(25)]
    result = MOD.analyze_stock(make_bars(closes, limit_index=18), {
        "ts_code": "600001.SH", "name": "样本", "industry": "测试",
    })
    assert result is not None
    assert result["close_above_ma5_5d"] is True
    assert result["close_above_ma10_5d"] is True
    assert result["ma5_rising_5d"] is True
    assert result["s1_ma10_stable"] is True
    assert result["s2_ma5_rising"] is True
    assert result["signal"] == "S1+S2"
    assert result["limit_up_count_10d"] == 1
    assert result["next_ma5_threshold"] > 0


def test_old_limit_up_does_not_qualify():
    closes = [10 + i * 0.05 for i in range(25)]
    result = MOD.analyze_stock(make_bars(closes, limit_index=5), {
        "ts_code": "600001.SH", "name": "样本", "industry": "测试",
    })
    assert result is None


def test_s1_only_and_s2_only_are_both_eligible():
    base = make_bars([10 + i * 0.05 for i in range(25)], limit_index=18)
    meta = {"ts_code": "600001.SH", "name": "样本", "industry": "测试"}

    original_prepare = MOD.prepare_bars
    try:
        prepared = original_prepare(base)

        s1 = prepared.copy()
        s1.loc[s1.index[-5:], "adj_close"] = s1.loc[s1.index[-5:], "ma10"] + 1
        s1.loc[s1.index[-5:], "ma5"] = [20, 19, 18, 17, 16]
        s1.loc[s1.index[-5:], "ma10"] = [10, 10, 10, 10, 10]
        MOD.prepare_bars = lambda _: s1
        result_s1 = MOD.analyze_stock(base, meta)
        assert result_s1["signal"] == "S1_MA10_STABLE"

        s2 = prepared.copy()
        s2.loc[s2.index[-5:], "adj_close"] = [12, 13, 14, 15, 16]
        s2.loc[s2.index[-5:], "ma5"] = [11, 12, 13, 14, 15]
        s2.loc[s2.index[-5:], "ma10"] = [20, 20, 20, 20, 20]
        MOD.prepare_bars = lambda _: s2
        result_s2 = MOD.analyze_stock(base, meta)
        assert result_s2["signal"] == "S2_MA5_RISING"
        assert "仅S2" in result_s2["risk_flags"]
    finally:
        MOD.prepare_bars = original_prepare


def test_activity_sort_does_not_prioritize_low_ma5_bias():
    frame = pd.DataFrame([
        {"ts_code": "LOWBIAS", "limit_up_count_10d": 1, "volume_ratio_5_20": 1.0,
         "days_since_limit_up": 2, "avg_amount_5d_qianyuan": 100000, "bias_ma5_pct": 0.1},
        {"ts_code": "ACTIVE", "limit_up_count_10d": 2, "volume_ratio_5_20": 2.0,
         "days_since_limit_up": 3, "avg_amount_5d_qianyuan": 100000, "bias_ma5_pct": 8.0},
    ])
    result = MOD.sort_by_activity(frame)
    assert result.iloc[0]["ts_code"] == "ACTIVE"


def test_large_bearish_candidates_are_demoted_without_being_removed():
    frame = pd.DataFrame([
        {"ts_code": "BEAR", "limit_up_count_10d": 3, "volume_ratio_5_20": 3.0,
         "days_since_limit_up": 0, "avg_amount_5d_qianyuan": 200000,
         "large_bearish_day_5d": True},
        {"ts_code": "CLEAN", "limit_up_count_10d": 1, "volume_ratio_5_20": 1.0,
         "days_since_limit_up": 2, "avg_amount_5d_qianyuan": 100000,
         "large_bearish_day_5d": False},
    ])
    result = MOD.sort_by_activity(frame)
    assert result["ts_code"].tolist() == ["CLEAN", "BEAR"]
    assert set(result["ts_code"]) == {"CLEAN", "BEAR"}
    assert result.loc[result["ts_code"].eq("BEAR"), "activity_rank"].iloc[0] == 1


def test_a_tier_observation_candidate_precedes_more_active_b_tier():
    frame = pd.DataFrame([
        {"ts_code": "B_ACTIVE", "limit_up_count_10d": 3, "volume_ratio_5_20": 3.0,
         "days_since_limit_up": 0, "avg_amount_5d_qianyuan": 200000,
         "large_bearish_day_5d": False, "risk_tier": "B_需等待"},
        {"ts_code": "A_OBSERVE", "limit_up_count_10d": 1, "volume_ratio_5_20": 1.0,
         "days_since_limit_up": 2, "avg_amount_5d_qianyuan": 100000,
         "large_bearish_day_5d": False, "risk_tier": "A_结构较好"},
    ])
    result = MOD.sort_by_activity(frame)
    assert result["ts_code"].tolist() == ["A_OBSERVE", "B_ACTIVE"]
    assert result.loc[result["ts_code"].eq("B_ACTIVE"), "activity_rank"].iloc[0] == 1


def test_limit_context_is_optional_and_fills_empty_strings():
    result = pd.DataFrame([{"ts_code": "600001.SH", "name": "样本"}])
    enriched = MOD.attach_limit_context(result, pd.DataFrame())
    assert enriched.iloc[0]["limit_theme"] == ""
    assert enriched.iloc[0]["limit_up_reason"] == ""
