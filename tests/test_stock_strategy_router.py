import importlib.util
from pathlib import Path
from types import SimpleNamespace


PATH = Path(__file__).parents[1] / "scripts/stock_strategy_router.py"
SPEC = importlib.util.spec_from_file_location("stock_strategy_router", PATH)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def test_aliases_route_to_three_distinct_tasks():
    assert MOD.TASK_ALIASES["短线"] == "short-ma5"
    assert MOD.TASK_ALIASES["推土机"] == "short-ma5"
    assert MOD.TASK_ALIASES["波段"] == "catalyst-swing"
    assert MOD.TASK_ALIASES["底仓"] == "quality-core"


def test_router_surfaces_stock_data_warnings(monkeypatch, capsys):
    monkeypatch.setattr(MOD.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout="[]", stderr="[WARNING] 600001 行情滞后，已排除\n",
    ))
    assert MOD.run_json_command(["python", "scanner.py"]) == []
    assert "行情滞后，已排除" in capsys.readouterr().err


def test_short_normalization_does_not_require_six_layers():
    row = {
        "trade_date": "20260805", "ts_code": "600001.SH", "name": "样本",
        "next_ma5_threshold": 10.195, "auction_reference_low": 10.20,
        "auction_reference_high": 10.5, "auction_band_valid": True,
        "close": 10.51, "trend_stage": "STANDARD_5D",
        "risk_tier": "A_结构较好", "risk_flags": "",
    }
    result = MOD.normalize_short([row])[0]
    assert result["holding_period"] == "隔夜；买入后的下一交易日闭环"
    assert result["needs_six_layer"] is False
    assert result["execution_state"] == "待人工确认"
    assert "形态" in result["chart_teaching"]
    assert "不形成委托计划" in result["next_action"]
    assert result["price_plan"]["reference_bid"] is None
    assert result["decision_stage"] == "observe_only"
    assert result["actionable_now"] is False


def test_short_only_labels_clean_quotable_candidate_as_conditional():
    common = {
        "trade_date": "20260921", "ts_code": "600001.SH", "name": "样本",
        "auction_band_valid": True, "auction_reference_low": 10.1,
        "auction_reference_high": 10.3, "close": 10.4,
        "risk_tier": "A_结构较好", "risk_flags": "",
    }
    ready = MOD.normalize_short([{**common, "continuity_tier": "A_持续强势"}])[0]
    no_quote = MOD.normalize_short([{**common, "auction_band_valid": False,
                                     "continuity_tier": "A_持续强势"}])[0]
    weak = MOD.normalize_short([{**common, "continuity_tier": "C_活跃观察"}])[0]
    assert ready["decision_stage"] == "conditional_watch"
    assert ready["actionable_now"] is False
    assert "试验限价买入报价上限" in ready["next_action"]
    assert no_quote["decision_stage"] == "observe_only"
    assert no_quote["price_plan"]["reference_bid"] is None
    assert weak["decision_stage"] == "observe_only"


def test_report_explicitly_allows_no_actionable_candidate(tmp_path):
    path = tmp_path / "report.md"
    MOD.write_report({"short-ma5": []}, {"short-ma5": []}, path, "20260925")
    assert "不能为凑名单放宽条件" in path.read_text(encoding="utf-8")


def test_short_reference_bid_is_quotable_and_does_not_promise_execution_price():
    row = {
        "auction_band_valid": True, "auction_reference_low": 7.02,
        "auction_reference_high": 7.15, "next_ma5_threshold": 7.015,
        "close": 7.16,
    }
    quote = MOD.short_reference_bid(row)
    assert quote == {"reference_bid": 7.10, "confirmation_low": 7.09, "confirmation_high": 7.11}
    plan = MOD.short_execution_plan(row)
    assert "7.10元" in plan
    assert "更低价成交" in plan
    assert "9:30后重算动态MA5并确认承接" in plan


def test_short_reference_bid_does_not_invent_price_when_band_has_no_tick():
    row = {
        "auction_band_valid": True, "auction_reference_low": 7.02,
        "auction_reference_high": 7.03, "next_ma5_threshold": 7.015,
        "close": 7.04,
    }
    assert MOD.short_reference_bid(row)["reference_bid"] is None
    assert "竞价不预埋" in MOD.short_execution_plan(row)


def test_swing_and_core_require_six_layers():
    swing = MOD.normalize_swing([{"code": "600001.SH", "name": "样本", "event_name": "事件",
                                  "trade_date": "20260805"}])[0]
    core = MOD.normalize_core({"trade_date": "20260805", "candidates": [{"ts_code": "600002.SH", "name": "底仓"}]})[0]
    assert swing["needs_six_layer"] is True
    assert swing["trade_date"] == "20260805"
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


def test_early_two_day_stage_is_demoted_below_mature_candidate():
    common = {
        "risk_tier": "A_结构较好", "large_bearish_day_5d": False,
        "bias_ma5_pct": 1, "days_since_limit_up": 2,
        "volume_ratio_5_20": 1.5, "avg_amount_5d_qianyuan": 200000,
        "close": 10,
    }
    rows = [
        {"name": "早期", "details": {**common, "signal": "E2_MA5_EARLY", "activity_rank": 1}},
        {"name": "成熟", "details": {**common, "signal": "S1+S2", "activity_rank": 2}},
    ]
    focus = MOD.select_focus("short-ma5", rows, limit=2)
    assert [row["name"] for row in focus] == ["成熟", "早期"]


def test_sustained_strength_precedes_raw_activity():
    common = {
        "risk_tier": "A_结构较好", "large_bearish_day_5d": False,
        "bias_ma5_pct": 1, "days_since_limit_up": 4, "close": 20,
    }
    rows = [
        {"name": "突然活跃", "details": {**common, "continuity_tier": "C_活跃观察",
            "volume_ratio_5_20": 2.0, "avg_amount_5d_qianyuan": 300000, "activity_rank": 1}},
        {"name": "持续强势", "details": {**common, "continuity_tier": "A_持续强势",
            "volume_ratio_5_20": 0.9, "avg_amount_5d_qianyuan": 100000, "activity_rank": 20}},
    ]
    focus = MOD.select_focus("short-ma5", rows, limit=2)
    assert [row["name"] for row in focus] == ["持续强势", "突然活跃"]
