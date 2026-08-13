import importlib.util
from pathlib import Path


PATH = Path(__file__).parents[1] / "scripts/stock_strategy_router.py"
SPEC = importlib.util.spec_from_file_location("stock_strategy_router", PATH)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def test_aliases_route_to_three_distinct_tasks():
    assert MOD.TASK_ALIASES["短线"] == "short-ma5"
    assert MOD.TASK_ALIASES["波段"] == "catalyst-swing"
    assert MOD.TASK_ALIASES["底仓"] == "quality-core"


def test_short_normalization_does_not_require_six_layers():
    row = {
        "trade_date": "20260805", "ts_code": "600001.SH", "name": "样本",
        "next_ma5_threshold": 10.2, "risk_tier": "A_结构较好", "risk_flags": "",
    }
    result = MOD.normalize_short([row])[0]
    assert result["holding_period"] == "1-2天"
    assert result["needs_six_layer"] is False
    assert result["execution_state"] == "待人工确认"
    assert "形态" in result["chart_teaching"]


def test_swing_and_core_require_six_layers():
    swing = MOD.normalize_swing([{"code": "600001.SH", "name": "样本", "event_name": "事件"}])[0]
    core = MOD.normalize_core({"trade_date": "20260805", "candidates": [{"ts_code": "600002.SH", "name": "底仓"}]})[0]
    assert swing["needs_six_layer"] is True
    assert core["needs_six_layer"] is True
    assert swing["holding_period"] != core["holding_period"]


def test_short_focus_prefers_clean_a_tier_near_ma5():
    def candidate(name, bias, bearish=False, risk="A_结构较好", close=25, days=2):
        return {"name": name, "details": {
            "bias_ma5_pct": bias, "large_bearish_day_5d": bearish, "risk_tier": risk,
            "close": close, "days_since_limit_up": days,
        }}

    rows = [
        candidate("乖离过大", 6.8), candidate("顺钠股份", -0.57, close=12),
        candidate("放量大阴", 0.2, bearish=True), candidate("兴欣新材", 0.17),
        candidate("美利云", -0.12),
    ]
    focus = MOD.select_focus("short-ma5", rows, limit=3)
    assert [row["name"] for row in focus] == ["顺钠股份", "兴欣新材", "美利云"]
    assert len(rows) == 5


def test_short_focus_demotes_stock_that_just_hit_limit_up():
    def candidate(name, days, close=10):
        return {"name": name, "details": {
            "bias_ma5_pct": 1, "large_bearish_day_5d": False,
            "risk_tier": "A_结构较好", "close": close,
            "days_since_limit_up": days,
        }}

    rows = [candidate("今日涨停", 0), candidate("低价回踩", 2), candidate("普通回踩", 3, 30)]
    focus = MOD.select_focus("short-ma5", rows, limit=3)
    assert [row["name"] for row in focus] == ["低价回踩", "普通回踩", "今日涨停"]
    assert "不列为次日低吸优先" in focus[-1]["focus_reason"]


def test_swing_focus_prefers_confirmed_ready_then_preheat():
    def candidate(name, state, repair="confirmed", score=8):
        return {"name": name, "current_state": state, "details": {
            "event_bucket": "red", "sector_repair_state": repair, "lr_score": score,
        }}

    rows = [
        candidate("高分预热", "预热"), candidate("未确认就绪", "就绪", "unconfirmed"),
        candidate("就绪甲", "就绪"), candidate("就绪乙", "就绪"), candidate("预热乙", "预热"),
    ]
    focus = MOD.select_focus("catalyst-swing", rows, limit=3)
    assert [row["name"] for row in focus] == ["就绪甲", "就绪乙", "高分预热"]


def test_short_chart_teaching_explains_shape_in_plain_language():
    hint = MOD.short_chart_teaching({
        "signal": "S1+S2", "days_since_limit_up": 2, "bias_ma5_pct": 1.2,
        "volume_ratio_5_20": 1.4, "risk_flags": "",
    })
    assert "稳定推进" in hint["形态"]
    assert "重点看承接" in hint["位置"]
    assert "资金活跃" in hint["量能"]
    assert "板块身份" in hint
    assert "涨停原因" in hint


def test_short_activity_is_ranked_before_low_price_preference():
    def candidate(name, ratio, amount, close, activity_rank):
        return {"name": name, "details": {
            "risk_tier": "A_结构较好", "large_bearish_day_5d": False,
            "bias_ma5_pct": 1, "days_since_limit_up": 3,
            "volume_ratio_5_20": ratio, "avg_amount_5d_qianyuan": amount,
            "close": close, "activity_rank": activity_rank,
        }}
    rows = [
        candidate("低价但不活跃", 0.8, 80000, 5, 80),
        candidate("高价但活跃", 1.6, 300000, 35, 5),
    ]
    focus = MOD.select_focus("short-ma5", rows, limit=2)
    assert [row["name"] for row in focus] == ["高价但活跃", "低价但不活跃"]
    assert focus[0]["focus_reason"].startswith("活跃确认")
