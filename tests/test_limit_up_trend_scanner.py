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
            "open": previous, "high": max(previous, close) * 1.01,
            "low": min(previous, close) * 0.99,
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
    result = MOD.analyze_stock(make_bars(closes, limit_index=16), {
        "ts_code": "600001.SH", "name": "样本", "industry": "测试",
    })
    assert result is not None
    assert result["close_above_ma5_5d"] is True
    assert result["close_above_ma10_5d"] is True
    assert result["ma5_rising_5d"] is True
    assert result["s1_ma10_stable"] is True
    assert result["s2_ma5_rising"] is True
    assert result["signal"] == "S1+S2"
    assert result["consecutive_days_above_ma5"] >= 5
    assert result["trend_stage"] in {"STANDARD_5D", "MATURE_8D", "EXTENDED_16D"}
    assert result["auction_reference_low"] <= result["auction_reference_high"]
    assert result["auction_reference_low"] > result["next_ma5_threshold"]
    assert result["auction_reference_high"] < result["close"]
    assert result["limit_up_count_10d"] == 1
    assert result["next_ma5_threshold"] > 0
    assert result["sustained_strength"] is True
    assert result["continuity_tier"] == "A_持续强势"


def test_next_price_tick_above_respects_strict_inequality():
    assert MOD.next_price_tick_above(10.2) == 10.21
    assert MOD.next_price_tick_above(10.201) == 10.21
    assert MOD.next_price_tick_above(10.209) == 10.21


def test_old_limit_up_does_not_qualify():
    closes = [10 + i * 0.05 for i in range(25)]
    result = MOD.analyze_stock(make_bars(closes, limit_index=5), {
        "ts_code": "600001.SH", "name": "样本", "industry": "测试",
    })
    assert result is None


def test_weak_s1_or_s2_cannot_bypass_common_ma5_shape_floor():
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
        assert result_s1 is None

        s2 = prepared.copy()
        s2.loc[s2.index[-5:], "adj_close"] = [12, 13, 14, 15, 16]
        s2.loc[s2.index[-5:], "ma5"] = [11, 12, 13, 14, 15]
        s2.loc[s2.index[-5:], "ma10"] = [20, 20, 20, 20, 20]
        MOD.prepare_bars = lambda _: s2
        result_s2 = MOD.analyze_stock(base, meta)
        assert result_s2 is None
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


def test_dual_lane_output_lists_sustained_strength_before_raw_activity():
    frame = pd.DataFrame([
        {"ts_code": "ACTIVE", "full_ma_lane": False, "active_lane": True,
         "shape_score": 50, "activity_score": 100, "continuity_tier": "C_活跃观察"},
        {"ts_code": "STEADY", "full_ma_lane": True, "active_lane": False,
         "shape_score": 70, "activity_score": 40, "continuity_tier": "A_持续强势"},
    ])
    result = MOD.select_dual_lane(frame, per_lane=1, total=2)
    assert result["ts_code"].tolist() == ["STEADY", "ACTIVE"]


def test_limit_context_is_optional_and_fills_empty_strings():
    result = pd.DataFrame([{"ts_code": "600001.SH", "name": "样本"}])
    enriched = MOD.attach_limit_context(result, pd.DataFrame())
    assert enriched.iloc[0]["limit_theme"] == ""
    assert enriched.iloc[0]["limit_up_reason"] == ""


def test_limit_context_replaces_placeholder_columns():
    result = pd.DataFrame([{
        "ts_code": "600001.SH", "name": "样本", "limit_theme": None,
        "limit_up_reason": None, "limit_status": None,
    }])
    context = pd.DataFrame([{
        "ts_code": "600001.SH", "limit_theme": "机器人",
        "limit_up_reason": "订单", "limit_status": "首板",
    }])
    enriched = MOD.attach_limit_context(result, context)
    assert enriched.iloc[0]["limit_theme"] == "机器人"
    assert enriched.iloc[0]["limit_up_reason"] == "订单"


def test_two_day_early_stage_is_observation_candidate():
    base = make_bars([10 + i * 0.02 for i in range(25)], limit_index=16)
    prepared = MOD.prepare_bars(base)
    prepared.loc[prepared.index[-5:], "ma10"] = 10.0
    prepared.loc[prepared.index[-5:], "ma5"] = [9.8, 9.85, 9.9, 10.0, 10.1]
    prepared.loc[prepared.index[-5:], "adj_close"] = [9.9, 9.8, 9.95, 10.05, 10.2]
    prepared.loc[prepared.index[-5:], "amplitude_pct"] = 3.0
    original_prepare = MOD.prepare_bars
    try:
        MOD.prepare_bars = lambda _: prepared
        result = MOD.analyze_stock(base, {
            "ts_code": "600001.SH", "name": "早期样本", "industry": "测试",
        })
    finally:
        MOD.prepare_bars = original_prepare
    assert result is not None
    assert result["signal"] == "E2_MA5_EARLY"
    assert result["trend_stage"] == "EARLY_2D"
    assert "早期2日试验层" in result["risk_flags"]
    assert result["continuity_tier"] == "C_活跃观察"
