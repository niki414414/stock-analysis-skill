import importlib.util
from pathlib import Path

import pandas as pd


PATH = Path(__file__).parents[1] / "skills/top-picks/references/catalyst_left_side_scanner.py"
SPEC = importlib.util.spec_from_file_location("catalyst_scanner", PATH)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def bars(limit=False, weak=False):
    closes = [10 + i * (0.03 if not weak else -0.02) for i in range(12)]
    return [{
        "close": close, "pct_chg": 10.0 if limit and i == 7 else 0.3,
        "high": close * 1.01, "low": close * 0.99, "vol": 1000,
    } for i, close in enumerate(closes)]


def long_bars(limit=False, weak=False):
    closes = [10 + i * (0.03 if not weak else -0.02) for i in range(25)]
    return [{
        "close": close, "pct_chg": 10.0 if limit and i == 20 else 0.3,
        "high": close * 1.01, "low": close * 0.99, "vol": 1000,
    } for i, close in enumerate(closes)]


def test_sector_repair_requires_breadth_and_limit_activity():
    candidates = pd.DataFrame([
        {"code": "1", "sector": "科技"}, {"code": "2", "sector": "科技"},
        {"code": "3", "sector": "科技"},
    ])
    result = MOD.compute_sector_repair(candidates, {
        "1": bars(limit=True), "2": bars(), "3": bars(),
    })["科技"]
    assert result["state"] == "confirmed"
    assert result["limit_up_members_10d"] == 1


def test_small_sector_sample_is_not_false_confirmation():
    candidates = pd.DataFrame([{"code": "1", "sector": "小赛道"}])
    result = MOD.compute_sector_repair(candidates, {"1": bars(limit=True)})["小赛道"]
    assert result["state"] == "insufficient_sample"


def test_market_response_is_state_not_an_extra_score():
    confirmed = pd.Series({"sector_repair": {"state": "confirmed"}, "lr_label": "就绪"})
    early = pd.Series({"sector_repair": {"state": "early_repair"}, "lr_label": "观望"})
    missing = pd.Series({"sector_repair": {"state": "insufficient_sample"}, "lr_label": "就绪"})
    assert MOD.classify_market_response(confirmed) == "confirmed"
    assert MOD.classify_market_response(early) == "early"
    assert MOD.classify_market_response(missing) == "insufficient_data"


def test_reactivation_requires_basket_breadth_and_limit_confirmation():
    candidates = pd.DataFrame([
        {"code": "1", "event_id": "E1", "activation_pending": True, "benefit_tier": "明确映射"},
        {"code": "2", "event_id": "E1", "activation_pending": True, "benefit_tier": "角色关联"},
        {"code": "3", "event_id": "E1", "activation_pending": True, "benefit_tier": "角色关联"},
    ])
    result = MOD.compute_event_reactivation(candidates, {
        "1": long_bars(limit=True), "2": long_bars(), "3": long_bars(),
    })["E1"]
    assert result["confirmed"] is True
    assert result["limit_up_members_10d"] == 1


def test_reactivation_rejects_single_thematic_stock():
    candidates = pd.DataFrame([
        {"code": "1", "event_id": "E1", "activation_pending": True, "benefit_tier": "主题观察"},
    ])
    result = MOD.compute_event_reactivation(candidates, {"1": long_bars(limit=True)})["E1"]
    assert result["confirmed"] is False


def test_reactivation_rejects_already_overheated_basket():
    candidates = pd.DataFrame([
        {"code": "1", "event_id": "E1", "activation_pending": True, "benefit_tier": "明确映射"},
        {"code": "2", "event_id": "E1", "activation_pending": True, "benefit_tier": "明确映射"},
        {"code": "3", "event_id": "E1", "activation_pending": True, "benefit_tier": "明确映射"},
    ])
    hot = []
    for i in range(25):
        close = 10 if i < 19 else 10 + (i - 18) * 0.5
        hot.append({"close": close, "pct_chg": 10.0 if i == 20 else 4.0,
                    "high": close * 1.01, "low": close * 0.99, "vol": 2000})
    result = MOD.compute_event_reactivation(candidates, {"1": hot, "2": hot, "3": hot})["E1"]
    assert result["confirmed"] is False
